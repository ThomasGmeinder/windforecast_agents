#!/usr/bin/env python3
"""
learn.py — the self-learning bias-correction mechanism, with a detailed daily audit.

Every morning, BEFORE the new forecasts are made, for each lake this module:
  1. COMPARES yesterday's issued forecast (and the underlying raw model) hour-by-hour
     against yesterday's ACTUAL measured wind (DWD 10-min obs).
  2. LOGS the per-hour diffs (machine-readable JSONL + a human-readable report).
  3. EXPLAINS the differences and derives plain-language "lessons learned".
  4. UPDATES the prediction mechanism (RLS regression corrected = a + b*model, per
     regime x hour-of-day) and logs
     the exact BEFORE -> AFTER change for every bucket it touched.

forecast.apply_bias() then applies that regression to the raw model, so today's
forecast benefits from what was learned minutes earlier in the same run.

Learning is on RAW model error (actual - raw) so the correction converges instead
of chasing its own tail. Idempotent: each date is learned at most once per lake.

Actuals: Walchensee and Kochelsee use the on-lake addicted-sports feeds (Urfeld and
Trimini respectively; DWD Garmisch only as a fallback); Ammersee uses DWD Wielenbach
(lake-level, ~11 km, sheltered). Direction is compared and logged but not yet used to
correct the forecast.
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc
import postproc
import verify  # single authority for the lead-time (hindcast) filter

LEARN_DIR = os.path.join(wd.LOG_DIR, "learning")
os.makedirs(LEARN_DIR, exist_ok=True)

LARGE_ERR_KN = 5.0  # |forecast − measured| above this = a "large miss" worth explaining


def is_large_miss(err_kn):
    """Single authority for the big-miss / diff-table predicate: strictly LARGER
    than the threshold (matches "difference larger than a defined value"). Used by
    both this module and render.py so the logged/displayed tables never drift."""
    return err_kn is not None and abs(err_kn) > LARGE_ERR_KN


def _dir_err(a, b):
    if a is None or b is None:
        return None
    d = abs((a - b) % 360)
    return round(min(d, 360 - d))


def _forecast_for(lake, date):
    """The forecast OF RECORD for `date`, from the SINGLE authority in verify.py.

    This module used to rank the candidate records itself. That duplicate carried exactly
    the bug verify.py had already fixed — lexical comparison of stamps that may hold
    different UTC offsets, and a missing stamp coerced to "" beating every real one — so
    the learner and the verifier disagreed about which forecast was issued. One authority,
    no copies."""
    rec, _stamp = verify.forecast_of_record(lake, date)
    return rec


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def _lessons(agg, bucket_updates):
    """Derive plain-language lessons from the day's aggregates (deterministic)."""
    L = []
    mi, mr = agg["mae_issued_kn"], agg["mae_raw_kn"]
    if mi is not None and mr is not None:
        if mi < mr - 0.2:
            L.append(f"The learned correction HELPED: issued-forecast error {mi} kn vs "
                     f"raw-model error {mr} kn (−{round(mr - mi, 2)} kn).")
        elif mi > mr + 0.2:
            L.append(f"The correction HURT yesterday: issued {mi} kn vs raw {mr} kn "
                     f"(+{round(mi - mr, 2)} kn) — likely a regime shift vs the days it learned from.")
        else:
            L.append(f"Correction was roughly neutral (issued {mi} kn vs raw {mr} kn).")
    b = agg["mbe_issued_kn"]
    if b is not None:
        if b <= -1:
            L.append(f"Issued forecast OVER-predicted by {abs(b)} kn on average → biases nudged down.")
        elif b >= 1:
            L.append(f"Issued forecast UNDER-predicted by {b} kn on average → biases nudged up.")
        else:
            L.append(f"Overall speed bias small ({b:+} kn mean error).")
    for reg, d in sorted(agg["by_regime"].items()):
        if d["n"] >= 2 and abs(d["mbe_kn"]) >= 1:
            verb = "under" if d["mbe_kn"] > 0 else "over"
            L.append(f"'{reg}' hours ({d['n']}h) {verb}-predicted by "
                     f"{abs(d['mbe_kn'])} kn → those regime buckets shifted most.")
    if agg["dir_mae_deg"] is not None and agg["dir_mae_deg"] >= 45:
        L.append(f"Wind DIRECTION was off by {agg['dir_mae_deg']}° on average "
                 f"(terrain/thermal veer the model misses); direction not yet auto-corrected.")
    if agg["gust_ratio_day"] is not None and abs(agg["gust_ratio_day"] - 1) >= 0.15:
        rel = "stronger" if agg["gust_ratio_day"] > 1 else "weaker"
        L.append(f"Measured gusts ran {agg['gust_ratio_day']}× the model ({rel}); gust ratio updated.")
    if agg.get("regime_acc") is not None:
        L.append(f"Regime call was right {agg['regime_hits']}/{agg['regime_n']} hours "
                 f"({int(agg['regime_acc']*100)}%) vs the measured wind direction.")
        for k, v in agg.get("confusion", {}).items():
            p, a2 = k.split("->")
            if {p, a2} == {"foehn", "thermal"}:
                L.append(f"⚠ predicted '{p}' but measured direction was '{a2}' ({v}×) — the "
                         f"föhn/thermal ANTI-CORRELATION; re-check the Kochelsee↔Walchensee split.")
            elif p != a2 and v >= 2:
                L.append(f"Regime miss: predicted '{p}', measured '{a2}' ({v}×).")
    if agg["worst"]:
        w = agg["worst"]
        L.append(f"Worst hour was {w['hour']:02d}:00 ({w['regime']}): predicted "
                 f"{w['issued_kn']} kn, measured {w['actual_kn']} kn (Δ {w['err_issued_kn']:+} kn).")
    return L or ["No notable pattern; errors within normal noise."]


def update_from_day(lake, date):
    """Learn from one past day. Returns a rich result dict (per-hour diffs, aggregates,
    lessons, bucket before->after updates) or {'skipped': reason}."""
    bias = fc.load_bias(lake)
    if date in bias.get("processed_dates", []):
        return {"lake": lake, "date": date, "skipped": "already processed"}
    rec = _forecast_for(lake, date)
    if not rec:
        return {"lake": lake, "date": date, "skipped": "no forecast was logged for that day"}
    try:
        actual, source = wd.actual_hourly(lake, date)
    except Exception as e:
        return {"lake": lake, "date": date, "skipped": f"actual-obs fetch failed: {e}"}
    if not actual:
        return {"lake": lake, "date": date, "skipped": "no actual obs available for that day"}

    alpha = bias.get("alpha", 0.3)
    buckets = bias.setdefault("buckets", {})
    diffs, scored, bucket_updates = [], [], []
    ei, er, signed, dir_errs, gratios = [], [], [], [], []
    by_regime = {}

    run_stamp = rec.get("run_stamp")
    n_leaked = 0
    for hp in rec["hourly"]:
        hour = hp["hour"]
        if hour not in actual:
            continue
        raw = hp.get("raw_kn")
        issued = hp.get("mean_kn", raw)
        if raw is None:
            continue
        a = actual[hour]
        act, actg, actd = a["mean_kn"], a["gust_kn"], a["dir"]
        regime = hp.get("regime", "gradient")
        err_issued = round(issued - act, 1)
        err_raw = round(raw - act, 1)
        derr = _dir_err(hp.get("dir"), actd)
        # true regime from the MEASURED direction (terrain sector) + measured wind
        areg = "calm" if act < 2 else (fc.terrain_regime(lake, actd) or "uncertain")
        rmatch = areg not in ("uncertain",) and areg == regime
        # An hour that had already elapsed when the forecast was issued is not a forecast:
        # we must not TRAIN on it (it fits the buckets to nowcast skill the real 05:00 run
        # will never have). But the MEASUREMENT is still true and is the only copy we keep —
        # verify's persistence and climatology baselines and the site's measured table all
        # read this file — so the row is still written, flagged, and merely excluded from
        # training and from the day's accuracy aggregates.
        leaked = verify.is_leaked(date, hour, run_stamp)
        diffs.append({"hour": hour, "regime": regime, "actual_regime": areg,
                      "regime_match": rmatch, "issued_kn": issued, "raw_kn": raw,
                      "actual_kn": act, "err_issued_kn": err_issued, "err_raw_kn": err_raw,
                      "issued_gust_kn": hp.get("gust_kn"), "actual_gust_kn": actg,
                      "dir_pred": hp.get("dir"), "dir_actual": actd, "dir_err_deg": derr,
                      "leaked": leaked})
        if leaked:
            n_leaked += 1
            continue                       # measurement kept; training and scoring skipped
        scored.append(diffs[-1])
        ei.append(abs(err_issued)); er.append(abs(err_raw)); signed.append(err_issued)
        if derr is not None:
            dir_errs.append(derr)
        rg = hp.get("raw_gust_kn") or raw
        # Same floor the learned ratio uses (postproc.update_gust): a reported "gusts ran
        # 3.2x the model" derived from a 0.4 kn model gust is not a finding, and it must
        # not disagree with what the bucket actually learned.
        if rg and rg >= postproc.GUST_MIN_LEARN_KN and actg is not None:
            gratios.append(actg / rg)
        rr = by_regime.setdefault(regime, {"n": 0, "sum": 0.0})
        rr["n"] += 1; rr["sum"] += err_issued

        # ---- update the RLS regression (corrected = a + b·raw), record before -> after ----
        key = fc._bucket_key(regime, hour)
        st = buckets.setdefault(key, postproc.new_state())
        a0, b0, n0, gr0 = st["a"], st["b"], st["n"], st.get("gust_ratio", 1.0)
        postproc.update(st, raw, act)  # updates a, b, covariance, n, mae in place
        # gust: the multiplicative half of the correction. postproc owns the bucket state
        # and therefore owns this update — including the near-zero-denominator refusal and
        # the sane-range clamp. Doing the arithmetic here was how an unguarded actg/rg got
        # in, and a second copy of the rule would drift from the one postproc self-tests.
        postproc.update_gust(st, rg, actg, alpha)
        bucket_updates.append({
            "key": key, "regime": regime, "hour": hour,
            "raw_kn": raw, "actual_kn": act, "model_err_kn": round(act - raw, 2),
            "a_before": round(a0, 2), "a_after": st["a"], "b_before": round(b0, 2), "b_after": st["b"],
            "gust_ratio_before": round(gr0, 2), "gust_ratio_after": st.get("gust_ratio", 1.0),
            "n_after": st["n"]})

    if not scored:
        # The lead-time filter can legitimately remove every matched hour (e.g. a forecast
        # issued late in the day). Report that honestly and leave the day UNPROCESSED — the
        # aggregates would all be None and format_report would crash formatting them, which
        # killed learning and the tuner for the whole lake.
        return {"lake": lake, "date": date,
                "skipped": (f"all {n_leaked} matched hour(s) had already elapsed when the "
                            f"forecast was issued — measurements logged, nothing to learn from"),
                "diffs": diffs, "_bias": bias
                if n_leaked else "no overlapping hours between forecast and measurements"}

    for reg, d in by_regime.items():
        d["mbe_kn"] = round(d["sum"] / d["n"], 2)
        d["n"] = d["n"]
    worst = max(scored, key=lambda r: abs(r["err_issued_kn"])) if scored else None
    # regime validation: predicted regime vs the measured-direction regime
    evals = [(d["regime"], d["actual_regime"]) for d in scored if d["actual_regime"] != "uncertain"]
    hits = sum(1 for p, a2 in evals if p == a2)
    confusion = {}
    for p, a2 in evals:
        confusion[f"{p}->{a2}"] = confusion.get(f"{p}->{a2}", 0) + 1
    agg = {"n_hours": len(scored), "mae_issued_kn": _mean(ei), "mae_raw_kn": _mean(er),
           "mbe_issued_kn": _mean(signed),
           "dir_mae_deg": (round(sum(dir_errs) / len(dir_errs)) if dir_errs else None),
           "gust_ratio_day": (round(sum(gratios) / len(gratios), 2) if gratios else None),
           "by_regime": {k: {"n": v["n"], "mbe_kn": v["mbe_kn"]} for k, v in by_regime.items()},
           "regime_acc": (round(hits / len(evals), 2) if evals else None),
           "regime_hits": hits, "regime_n": len(evals), "confusion": confusion,
           "worst": worst}
    lessons = _lessons(agg, bucket_updates)

    # Large-miss table: hours where |forecast − measured| exceeded the threshold,
    # each with the difference explained, the lesson, and how the fix is applied.
    bmap = {u["key"]: u for u in bucket_updates}
    large_misses = []
    for d in scored:
        if not is_large_miss(d["err_issued_kn"]):
            continue
        under = d["err_issued_kn"] < 0  # err = forecast − measured; <0 ⇒ under-predicted
        bu = bmap.get(fc._bucket_key(d["regime"], d["hour"]), {})
        n_after = bu.get("n_after") or 0
        a_a, b_a = bu.get("a_after"), bu.get("b_after")
        explanation = (f"{'under' if under else 'over'}-predicted — forecast "
                       f"{d['issued_kn']} kn vs measured {d['actual_kn']} kn ({d['err_issued_kn']:+} kn)")
        lesson = (f"the model may {'underplay' if under else 'overplay'} the "
                  f"'{d['regime']}' regime around {d['hour']:02d}:00 — one day is weak evidence")
        how = (f"the correction for ({d['regime']}×{d['hour']:02d}h) is a regression "
               f"corrected = {a_a:+.1f} + {b_a:.2f}·model — it **scales with** the model's own wind "
               f"(so it can't double-count or blindly add a fixed amount), refined recursively over "
               f"days ({n_after} obs; full weight after {fc.N_MIN_OBS})")
        large_misses.append({"hour": d["hour"], "regime": d["regime"], "issued_kn": d["issued_kn"],
                             "actual_kn": d["actual_kn"], "err_kn": d["err_issued_kn"],
                             "explanation": explanation, "lesson": lesson, "how_applied": how})

    # NOTE: the bias file (which carries processed_dates, the idempotency marker) is NOT
    # written here. It is committed by run_and_log AFTER the diffs are safely on disk —
    # writing the marker first meant a failure in between marked the day "learned" while
    # its comparison data was lost forever, and idempotency guaranteed it never came back.
    bias.setdefault("processed_dates", []).append(date)
    bias["processed_dates"] = bias["processed_dates"][-400:]

    return {"lake": lake, "date": date, "source": source, "agg": agg,
            "diffs": diffs, "bucket_updates": bucket_updates, "lessons": lessons,
            "large_misses": large_misses, "large_err_kn": LARGE_ERR_KN,
            "n_leaked_skipped": n_leaked,
            "buckets_total": len(buckets), "_bias": bias}


def _commit_bias(lake, bias):
    """Persist the learned state atomically (temp file + os.replace) so an interrupted
    write cannot leave a truncated bias file."""
    path = fc.bias_path(lake)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(bias, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def format_report(res):
    """Detailed human-readable morning learning report (markdown)."""
    if res.get("skipped"):
        return f"### Learning — {res['lake']} (from {res['date']}): skipped — {res['skipped']}"
    a = res["agg"]
    leak = (f" · {res['n_leaked_skipped']} hour(s) skipped (already elapsed when issued)"
            if res.get("n_leaked_skipped") else "")
    L = [f"### Learning report — {res['lake']} — learned from {res['date']}",
         f"Actual source: {res['source']} · matched {a['n_hours']} hours{leak}",
         "",
         "**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)",
         "```",
         " Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°",
         " ---|----------|------|------|------|------|------|-------------"]
    for d in [x for x in res["diffs"] if not x.get("leaked")]:
        L.append(f" {d['hour']:02d} | {d['regime']:<8} | {d['issued_kn']:>4.1f} | "
                 f"{d['raw_kn']:>4.1f} | {d['actual_kn']:>4.1f} | {d['err_issued_kn']:>+4.1f} | "
                 f"{d['err_raw_kn']:>+4.1f} | {fc.compass(d['dir_pred']):>4} {fc.compass(d['dir_actual']):>4} "
                 f"{('' if d['dir_err_deg'] is None else str(d['dir_err_deg'])):>3}")
    L.append("```")
    lm = res.get("large_misses", [])
    thr = res.get("large_err_kn", LARGE_ERR_KN)
    L += ["", f"**Large misses (|Δ| > {thr:g} kn) — difference, lesson & fix applied**"]
    if not lm:
        L.append(f"- none: every matched hour was within {thr:g} kn.")
    else:
        L += ["```", " Hr | Regime   | Pred | Meas |   Δ  ",
              " ---|----------|------|------|------"]
        for m in lm:
            L.append(f" {m['hour']:02d} | {m['regime']:<8} | {m['issued_kn']:>4.1f} | "
                     f"{m['actual_kn']:>4.1f} | {m['err_kn']:>+5.1f}")
        L.append("```")
        for m in lm:
            L.append(f"- **{m['hour']:02d}:00 ({m['regime']})** — {m['explanation']}. "
                     f"*Lesson:* {m['lesson']}. *Fix:* {m['how_applied']}.")

    L += ["", "**2. Accuracy summary**",
          f"- Mean abs error: issued forecast **{a['mae_issued_kn']} kn** vs raw model {a['mae_raw_kn']} kn",
          (f"- Mean signed error (bias): {a['mbe_issued_kn']:+} kn "
           f"({'under' if (a['mbe_issued_kn'] or 0) > 0 else 'over'}-predicting)"
           if a['mbe_issued_kn'] is not None else "- Mean signed error (bias): n/a"),
          f"- Direction mean abs error: {a['dir_mae_deg']}°" if a["dir_mae_deg"] is not None else "- Direction: n/a",
          f"- Gust ratio (measured/model): {a['gust_ratio_day']}×" if a["gust_ratio_day"] is not None else "- Gust: n/a",
          "- By regime: " + "; ".join(f"{k} {v['mbe_kn']:+} kn ({v['n']}h)" for k, v in sorted(a["by_regime"].items()))]
    if a.get("regime_acc") is not None:
        L += ["", "**2b. Regime validation** (predicted regime vs measured wind-direction sector)",
              f"- Regime accuracy: **{a['regime_hits']}/{a['regime_n']} hours "
              f"({int(a['regime_acc']*100)}%)**",
              "- Confusion (predicted→measured): " +
              ("; ".join(f"{k} ×{v}" for k, v in sorted(a["confusion"].items())) or "none")]
        mism = [d for d in res["diffs"] if not d.get("leaked") and d["actual_regime"] != "uncertain"
                and not d["regime_match"]]
        if mism:
            L.append("- Mismatched hours: " +
                     "; ".join(f"{d['hour']:02d}h {d['regime']}→{d['actual_regime']} "
                               f"({fc.compass(d['dir_actual'])})" for d in mism[:8]))
    L += ["", "**3. Lessons learned**"] + [f"- {x}" for x in res["lessons"]]
    L += ["", "**4. How the prediction mechanism was updated** "
          "(RLS regression `corrected = a + b·model`, per regime×hour)",
          "```",
          " bucket        | model_err |   a: before->after |   b: before->after | n",
          " --------------|-----------|--------------------|--------------------|--"]
    for u in res["bucket_updates"]:
        L.append(f" {u['key']:<13} | {u['model_err_kn']:>+8.2f}  | "
                 f"{u['a_before']:>+5.2f} -> {u['a_after']:>+5.2f}   | "
                 f"{u['b_before']:>4.2f} -> {u['b_after']:>4.2f}    | {u['n_after']}")
    L.append("```")
    L.append(f"_The correction **scales with** the model (b·model) rather than adding a flat offset, "
             f"so it neither double-counts nor over-adds. {res['buckets_total']} calibrated buckets._")
    return "\n".join(L)


def _write_diffs(lake, date, source, diffs):
    """Persist the day's measured-vs-forecast rows, IDEMPOTENTLY per (lake, date).

    A bare append meant that a retry after a mid-write failure duplicated every row —
    corrupting the measured table and double-feeding the baselines. Existing rows for the
    date are dropped first, mirroring how the forecast log and the event log behave."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
    kept = []
    if os.path.exists(path):
        for line in open(path):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                kept.append(line)          # never discard something we cannot parse
                continue
            if r.get("date") != date:
                kept.append(line)
    for d in diffs:
        kept.append(json.dumps({"date": date, "lake": lake, "source": source, **d}))
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(kept) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return len(diffs)


def run_and_log(lake, date):
    """Learn + write the detailed report and machine-readable diffs. Returns res."""
    res = update_from_day(lake, date)
    # Measurements are persisted even when there was nothing to LEARN (e.g. every hour had
    # already elapsed at issue time): the diffs log is the only store of measured truth and
    # feeds verify's persistence/climatology baselines and the site's measured table.
    if res.get("diffs"):
        _write_diffs(lake, date, res.get("source"), res["diffs"])
    if not res.get("skipped"):
        with open(os.path.join(LEARN_DIR, f"{lake}_{date}.md"), "w") as f:
            f.write(format_report(res) + "\n")
        # durable record of the exact big-miss diff table shown in the HTML for this day
        wd.log_event("diff_table", {
            "lake": lake, "date": date, "source": res.get("source"),
            "threshold_kn": LARGE_ERR_KN, "n_misses": len(res["large_misses"]),
            "misses": res["large_misses"]}, stamp=date)
    # Order matters: the DATA is written above, the idempotency marker only now. If any of
    # it fails the day stays unprocessed and is retried, instead of being marked done with
    # its diffs missing (which was unrecoverable).
    if res.get("_bias") is not None:
        _commit_bias(lake, res["_bias"])
    res.pop("_bias", None)
    return res


if __name__ == "__main__":
    lake = sys.argv[1] if len(sys.argv) > 1 else "ammersee"
    date = sys.argv[2] if len(sys.argv) > 2 else \
        (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    print(format_report(run_and_log(lake, date)))
