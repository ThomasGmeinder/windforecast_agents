#!/usr/bin/env python3
"""
daily_run.py — the daily pipeline entrypoint.

Every morning it:
  1. LEARNS from yesterday: for each lake, compares yesterday's logged forecast to
     yesterday's actual DWD observations and updates the bias model (learn.py).
  2. FORECASTS the new day: builds today's hourly table per lake using the (now
     updated) bias correction (forecast.py), prints it, writes it to logs/, and
     logs the forecast so tomorrow's run can learn from it.

Run manually:  .venv/bin/python daily_run.py
Scheduled  :   in GitHub Actions (.github/workflows/daily.yml, cron 03:07 UTC). The
               local systemd timer wind-agents-daily.timer fires at 05:00 and only
               DISPATCHES that workflow; it does not run this script.
"""
import os, sys, json, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lib"))
os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")

import winddata as wd
import forecast as fc
import learn
import tuner
import verify

TABLES_DIR = os.path.join(wd.LOG_DIR, "tables")
ANALYST_DIR = os.path.join(wd.LOG_DIR, "analyst")
os.makedirs(TABLES_DIR, exist_ok=True)
os.makedirs(ANALYST_DIR, exist_ok=True)


def _log_forecast(lake, payload):
    """Keep at most one record per (date, run_stamp).

    Deliberately NOT one record per date: a same-day re-run is a DIFFERENT, better-
    informed forecast, and overwriting the morning one destroyed the only evidence of
    what was actually issued at 06:00 — the verifier then scored the evening re-run as
    if it had been the day's forecast. Keeping both lets verify.py take the earliest as
    the forecast of record while the site still shows the freshest table. Re-running at
    the same stamp still replaces, so the log stays idempotent."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    key = (payload["date"], payload.get("run_stamp"))
    kept = []
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("date"), r.get("run_stamp")) != key:
                kept.append(line.rstrip("\n"))
    kept.append(json.dumps({"lake": lake, "kind": "forecast", **payload}))
    with open(path, "w") as f:
        f.write("\n".join(kept) + "\n")


def main():
    # Timezone-AWARE, in the project's one timezone. A naive datetime.now() is Berlin on
    # the laptop but UTC on the GitHub runner, and the forecast hours are always
    # Europe/Berlin — so a naive stamp made the lead-time leak filter under-drop by 2 h on
    # every cloud-produced record. Same UTC-vs-local trap as the foehn dp join.
    now = datetime.datetime.now(wd.BERLIN)
    today = now.date().isoformat()
    yesterday = (now.date() - datetime.timedelta(days=1)).isoformat()
    out = [f"=== Bavarian lake wind — daily run {now.strftime('%Y-%m-%d %H:%M %Z')} ===",
           f"forecast day: {today}   |   learning from: {yesterday}", ""]
    for lake in fc.LAKES:      # touch every lake's config so a broken file is reported
        fc.params_for(lake)
    for w in fc.PARAM_WARNINGS:
        out.append(f"⚠ CONFIG: {w}")   # a corrupt params file must never revert silently

    # 1. LEARN from yesterday — detailed comparison + mechanism update, BEFORE forecasting
    out.append("=" * 72)
    out.append("STEP 1 — SELF-LEARNING FROM YESTERDAY  (runs before any new forecast)")
    out.append("=" * 72)
    for lake in fc.LAKES:
        try:
            res = learn.run_and_log(lake, yesterday)
            out.append(learn.format_report(res))
        except Exception as e:
            out.append(f"### Learning — {lake}: ERROR — {e}")
            res = {"skipped": "learning error"}
        # self-tuning loop (Layer 2): reflect on past hypotheses → propose → backtest-gated
        # apply. Guarded: no key / no data / failed call → clean skip, never blocks.
        if not res.get("skipped"):
            try:
                ares = tuner.run(lake, yesterday, now.isoformat(timespec="minutes"),
                                 agg=res.get("agg"), diffs=res.get("diffs"))
                with open(os.path.join(ANALYST_DIR, f"{lake}_{yesterday}.json"), "w") as f:
                    json.dump({"lake": lake, "date": yesterday, "result": ares}, f, indent=2)
                wd.log_event("analyst", {
                    "lake": lake, "date": yesterday, "skipped": ares.get("skipped"),
                    "n_proposals": len(ares.get("proposals", [])),
                    "narrative": ares.get("narrative", ""),
                    "proposals": ares.get("proposals", []),
                    "reviews": ares.get("reviews", []),
                    "applied": ares.get("applied", []),
                    "refused": ares.get("refused", [])},
                    stamp=now.isoformat(timespec="minutes"))
                out.append(tuner.format_summary(ares))
            except Exception as e:
                out.append(f"  tuner: error — {e}")
        out.append("")

    out.append("=" * 72)
    out.append("STEP 2 — NEW FORECASTS  (built from the just-updated model)")
    out.append("=" * 72)
    out.append("")

    # 2. FORECAST today
    for lake in fc.LAKES:
        try:
            res = fc.build_table(lake, today, run_stamp=now.isoformat(timespec="minutes"))
        except Exception as e:
            out.append(f"[{lake}] forecast FAILED: {e}\n")
            continue
        out.append(fc.format_table(res))
        out.append("")
        # write per-lake table + log the forecast (with RAW values for tomorrow's learning)
        with open(os.path.join(TABLES_DIR, f"{lake}_{today}.txt"), "w") as f:
            f.write(fc.format_table(res) + "\n")
        _log_forecast(lake, {
            "date": today, "run_stamp": now.isoformat(timespec="minutes"),
            "summary": res["summary"], "label": res["label"],
            # fc.LOGGED_ROW_FIELDS is the ONE definition of what survives to disk. It used
            # to be a whitelist hand-maintained here, which meant every new row field had
            # to be remembered in a second place or it silently never reached the site.
            "hourly": [fc.logged_row(r) for r in res["rows"]],
        })
        # log hours where the blended source models disagree by more than the threshold
        hits = [r for r in res["rows"]
                if (r.get("blend_range_kn") or 0) > fc.BLEND_DISAGREE_KN]
        if hits:
            wd.log_event("blend_disagreement", {
                "lake": lake, "date": today, "threshold_kn": fc.BLEND_DISAGREE_KN,
                "hours": [{"hour": r["hour"], "range_kn": r["blend_range_kn"],
                           "sources": r["blend_kn"], "regime": r["regime"]} for r in hits]},
                stamp=now.isoformat(timespec="minutes"))
            out.append(f"  ⚠ blend disagreement > {fc.BLEND_DISAGREE_KN:g} kn at "
                       + ", ".join(f"{r['hour']:02d}h({r['blend_range_kn']:.0f})" for r in hits))

    # 3. VERIFY — objective out-of-sample score vs persistence & climatology baselines
    out.append("")
    out.append("=" * 72)
    out.append("STEP 3 — VERIFICATION  (CRPS out-of-sample vs baselines — the referee)")
    out.append("=" * 72)
    for lake in fc.LAKES:
        try:
            sc = verify.evaluate(lake)
            out.append(verify.format_scorecard(sc))
            if sc["n_pairs"]:
                wd.log_event("verification", {
                    "lake": lake, "date": today,
                    **{k: sc.get(k) for k in ("n_pairs", "n_days", "crps", "mae", "rmse",
                                              "bias", "crps_pers", "crps_clim",
                                              "ss_pers", "ss_clim")}},
                    stamp=now.isoformat(timespec="minutes"))
        except Exception as e:
            out.append(f"### Verification — {lake}: ERROR — {e}")
        out.append("")

    report = "\n".join(out)
    with open(os.path.join(wd.LOG_DIR, "latest_report.txt"), "w") as f:
        f.write(report + "\n")
    print(report)


if __name__ == "__main__":
    main()
