#!/usr/bin/env python3
"""
learn.py — the self-learning bias-correction mechanism, with a detailed daily audit.

Every morning, BEFORE the new forecasts are made, for each lake this module:
  1. COMPARES yesterday's issued forecast (and the underlying raw model) hour-by-hour
     against yesterday's ACTUAL measured wind (DWD 10-min obs).
  2. LOGS the per-hour diffs (machine-readable JSONL + a human-readable report).
  3. EXPLAINS the differences and derives plain-language "lessons learned".
  4. UPDATES the prediction mechanism (EWMA bias per regime x hour-of-day) and logs
     the exact BEFORE -> AFTER change for every bucket it touched.

forecast.apply_bias() then adds bias[regime,hour] to the raw model, so today's
forecast benefits from what was learned minutes earlier in the same run.

Learning is on RAW model error (actual - raw) so the correction converges instead
of chasing its own tail. Idempotent: each date is learned at most once per lake.

Honest limits: Kochelsee/Walchensee "actuals" are DWD Garmisch (a distant valley
station), not on-lake; Ammersee uses Wielenbach (lake-level, ~11 km, sheltered).
Direction is compared and logged but not yet used to correct the forecast.
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc

LEARN_DIR = os.path.join(wd.LOG_DIR, "learning")
os.makedirs(LEARN_DIR, exist_ok=True)


def _dir_err(a, b):
    if a is None or b is None:
        return None
    d = abs((a - b) % 360)
    return round(min(d, 360 - d))


def _forecast_for(lake, date):
    """The last logged forecast made FOR `date` (YYYY-MM-DD)."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    if not os.path.exists(path):
        return None
    rec = None
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("date") == date and r.get("hourly"):
                rec = r
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
    diffs, bucket_updates = [], []
    ei, er, signed, dir_errs, gratios = [], [], [], [], []
    by_regime = {}

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
        diffs.append({"hour": hour, "regime": regime, "actual_regime": areg,
                      "regime_match": rmatch, "issued_kn": issued, "raw_kn": raw,
                      "actual_kn": act, "err_issued_kn": err_issued, "err_raw_kn": err_raw,
                      "issued_gust_kn": hp.get("gust_kn"), "actual_gust_kn": actg,
                      "dir_pred": hp.get("dir"), "dir_actual": actd, "dir_err_deg": derr})
        ei.append(abs(err_issued)); er.append(abs(err_raw)); signed.append(err_issued)
        if derr is not None:
            dir_errs.append(derr)
        rg = hp.get("raw_gust_kn") or raw
        if rg and rg > 0:
            gratios.append(actg / rg)
        rr = by_regime.setdefault(regime, {"n": 0, "sum": 0.0})
        rr["n"] += 1; rr["sum"] += err_issued

        # ---- update the mechanism (EWMA on RAW error), record before -> after ----
        key = fc._bucket_key(regime, hour)
        b = buckets.setdefault(key, {"n": 0, "bias_kn": 0.0, "gust_ratio": 1.0, "mae_kn": 0.0})
        before = dict(b)
        model_err = act - raw
        if b["n"] == 0:
            b["bias_kn"] = model_err
            b["mae_kn"] = abs(model_err)
        else:
            b["bias_kn"] = (1 - alpha) * b["bias_kn"] + alpha * model_err
            resid = abs(act - (raw + before["bias_kn"]))
            b["mae_kn"] = (1 - alpha) * b["mae_kn"] + alpha * resid
        if rg and rg > 0:
            ratio = actg / rg
            b["gust_ratio"] = ratio if before["n"] == 0 else (1 - alpha) * b["gust_ratio"] + alpha * ratio
        b["n"] += 1
        bucket_updates.append({
            "key": key, "regime": regime, "hour": hour,
            "raw_kn": raw, "actual_kn": act, "model_err_kn": round(model_err, 2),
            "bias_before": round(before["bias_kn"], 2), "bias_after": round(b["bias_kn"], 2),
            "gust_ratio_before": round(before["gust_ratio"], 2), "gust_ratio_after": round(b["gust_ratio"], 2),
            "n_after": b["n"]})

    for reg, d in by_regime.items():
        d["mbe_kn"] = round(d["sum"] / d["n"], 2)
        d["n"] = d["n"]
    worst = max(diffs, key=lambda r: abs(r["err_issued_kn"])) if diffs else None
    # regime validation: predicted regime vs the measured-direction regime
    evals = [(d["regime"], d["actual_regime"]) for d in diffs if d["actual_regime"] != "uncertain"]
    hits = sum(1 for p, a2 in evals if p == a2)
    confusion = {}
    for p, a2 in evals:
        confusion[f"{p}->{a2}"] = confusion.get(f"{p}->{a2}", 0) + 1
    agg = {"n_hours": len(diffs), "mae_issued_kn": _mean(ei), "mae_raw_kn": _mean(er),
           "mbe_issued_kn": _mean(signed),
           "dir_mae_deg": (round(sum(dir_errs) / len(dir_errs)) if dir_errs else None),
           "gust_ratio_day": (round(sum(gratios) / len(gratios), 2) if gratios else None),
           "by_regime": {k: {"n": v["n"], "mbe_kn": v["mbe_kn"]} for k, v in by_regime.items()},
           "regime_acc": (round(hits / len(evals), 2) if evals else None),
           "regime_hits": hits, "regime_n": len(evals), "confusion": confusion,
           "worst": worst}
    lessons = _lessons(agg, bucket_updates)

    bias.setdefault("processed_dates", []).append(date)
    bias["processed_dates"] = bias["processed_dates"][-400:]
    with open(fc.bias_path(lake), "w") as f:
        json.dump(bias, f, indent=2)

    return {"lake": lake, "date": date, "source": source, "agg": agg,
            "diffs": diffs, "bucket_updates": bucket_updates, "lessons": lessons,
            "buckets_total": len(buckets)}


def format_report(res):
    """Detailed human-readable morning learning report (markdown)."""
    if res.get("skipped"):
        return f"### Learning — {res['lake']} (from {res['date']}): skipped — {res['skipped']}"
    a = res["agg"]
    L = [f"### Learning report — {res['lake']} — learned from {res['date']}",
         f"Actual source: {res['source']} · matched {a['n_hours']} hours",
         "",
         "**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)",
         "```",
         " Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°",
         " ---|----------|------|------|------|------|------|-------------"]
    for d in res["diffs"]:
        L.append(f" {d['hour']:02d} | {d['regime']:<8} | {d['issued_kn']:>4.1f} | "
                 f"{d['raw_kn']:>4.1f} | {d['actual_kn']:>4.1f} | {d['err_issued_kn']:>+4.1f} | "
                 f"{d['err_raw_kn']:>+4.1f} | {fc.compass(d['dir_pred']):>4} {fc.compass(d['dir_actual']):>4} "
                 f"{('' if d['dir_err_deg'] is None else str(d['dir_err_deg'])):>3}")
    L.append("```")
    L += ["", "**2. Accuracy summary**",
          f"- Mean abs error: issued forecast **{a['mae_issued_kn']} kn** vs raw model {a['mae_raw_kn']} kn",
          f"- Mean signed error (bias): {a['mbe_issued_kn']:+} kn "
          f"({'under' if (a['mbe_issued_kn'] or 0) > 0 else 'over'}-predicting)",
          f"- Direction mean abs error: {a['dir_mae_deg']}°" if a["dir_mae_deg"] is not None else "- Direction: n/a",
          f"- Gust ratio (measured/model): {a['gust_ratio_day']}×" if a["gust_ratio_day"] is not None else "- Gust: n/a",
          "- By regime: " + "; ".join(f"{k} {v['mbe_kn']:+} kn ({v['n']}h)" for k, v in sorted(a["by_regime"].items()))]
    if a.get("regime_acc") is not None:
        L += ["", "**2b. Regime validation** (predicted regime vs measured wind-direction sector)",
              f"- Regime accuracy: **{a['regime_hits']}/{a['regime_n']} hours "
              f"({int(a['regime_acc']*100)}%)**",
              "- Confusion (predicted→measured): " +
              ("; ".join(f"{k} ×{v}" for k, v in sorted(a["confusion"].items())) or "none")]
        mism = [d for d in res["diffs"] if d["actual_regime"] != "uncertain"
                and not d["regime_match"]]
        if mism:
            L.append("- Mismatched hours: " +
                     "; ".join(f"{d['hour']:02d}h {d['regime']}→{d['actual_regime']} "
                               f"({fc.compass(d['dir_actual'])})" for d in mism[:8]))
    L += ["", "**3. Lessons learned**"] + [f"- {x}" for x in res["lessons"]]
    L += ["", "**4. How the prediction mechanism was updated** (EWMA α=0.3, per regime×hour)",
          "```",
          " bucket        | model_err | bias: before -> after | gustR: before -> after | n",
          " --------------|-----------|-----------------------|------------------------|--"]
    for u in res["bucket_updates"]:
        L.append(f" {u['key']:<13} | {u['model_err_kn']:>+8.2f}  | "
                 f"{u['bias_before']:>+6.2f} -> {u['bias_after']:>+6.2f}      | "
                 f"{u['gust_ratio_before']:>5.2f} -> {u['gust_ratio_after']:>5.2f}          | {u['n_after']}")
    L.append("```")
    L.append(f"_Result: today's forecast adds these per-(regime×hour) biases to the raw model. "
             f"Model now holds {res['buckets_total']} calibrated buckets._")
    return "\n".join(L)


def run_and_log(lake, date):
    """Learn + write the detailed report and machine-readable diffs. Returns res."""
    res = update_from_day(lake, date)
    if not res.get("skipped"):
        with open(os.path.join(LEARN_DIR, f"{lake}_{date}.md"), "w") as f:
            f.write(format_report(res) + "\n")
        dp = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
        with open(dp, "a") as f:
            for d in res["diffs"]:
                f.write(json.dumps({"date": date, "lake": lake,
                                    "source": res.get("source"), **d}) + "\n")
    return res


if __name__ == "__main__":
    lake = sys.argv[1] if len(sys.argv) > 1 else "ammersee"
    date = sys.argv[2] if len(sys.argv) > 2 else \
        (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    print(format_report(run_and_log(lake, date)))
