#!/usr/bin/env python3
"""
tuner.py — the self-tuning loop: the subsystem that owns the LLM analyst end to end.

One call (`tuner.run`) does the whole cycle, so the caller never assembles it:

  1. PERCEIVE  gather the evidence: a multi-day error window, the learned regression
               state, the CRPS scorecard, the current params, and the analyst's OWN
               past proposals together with what the error actually did since.
  2. REFLECT   the analyst reviews each open hypothesis and confirms or retracts it;
               the verdict is written back to the ledger.
  3. PROPOSE   new bounded parameter proposals are recorded as open hypotheses.
  4. ACT       each proposal is BACKTESTED; a change is applied to the lake's params
               only if it verifiably lowers paired MAE on enough replayable days. Otherwise
               it stays a logged proposal. Nothing is ever applied on faith.
               The write goes to config/params_<lake>.json — per-lake, because the
               evidence was only ever gathered for that one lake.

Everything is guarded: no API key, no data, or a failed call degrades to a clean skip —
the deterministic forecast/learning path has already run and is never blocked.
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc
import verify
import ledger
import analyst

ERROR_WINDOW_DAYS = 14      # how much error history the analyst sees
MAX_REL_STEP = 0.25         # a single applied change may move a param at most 25%
REVIEW_AFTER_DAYS = 3       # how long a hypothesis waits before it is judged
JUMP_AUDIT_KN = 4.0         # correction / harm large enough to require explicit analyst scrutiny


# ---------------------------------------------------------------- perceive
def _multiday_errors(lake, days=ERROR_WINDOW_DAYS):
    """Recent per-hour forecast-vs-measured errors, newest days last."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
    rows = []
    if os.path.exists(path):
        for line in open(path):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    dates = sorted({r["date"] for r in rows})[-days:]
    keep = {"date", "hour", "regime", "issued_kn", "raw_kn", "actual_kn",
            "err_issued_kn", "err_raw_kn", "dir_err_deg", "observed_flow", "actual_regime",
            "leaked"}
    return [{k: v for k, v in r.items() if k in keep} for r in rows if r["date"] in dates]


def _jump_audit(rows):
    """Large misses or corrections that made the raw forecast materially worse.

    The LLM cannot safely infer a calibration overshoot from an issued error alone: a
    6 kn miss may originate in the raw model, whereas a 4 kn local correction that turns
    a 1 kn raw miss into a 6 kn issued miss needs a different diagnosis.  Give it the
    paired evidence explicitly rather than asking it to reconstruct it from a long log.
    """
    out = []
    for r in rows:
        if r.get("leaked") or r.get("issued_kn") is None or r.get("raw_kn") is None:
            continue
        issued_err, raw_err = r.get("err_issued_kn"), r.get("err_raw_kn")
        if issued_err is None or raw_err is None:
            continue
        correction = r["issued_kn"] - r["raw_kn"]
        harm = abs(issued_err) - abs(raw_err)
        large_miss = abs(issued_err) > JUMP_AUDIT_KN
        large_correction = abs(correction) >= JUMP_AUDIT_KN
        correction_hurt = harm >= JUMP_AUDIT_KN
        if not (large_miss or large_correction or correction_hurt):
            continue
        out.append({"date": r.get("date"), "hour": r.get("hour"), "bucket":
                    f"{r.get('regime', '?')}|{int(r.get('hour', 0)):02d}",
                    "raw_kn": r["raw_kn"], "issued_kn": r["issued_kn"],
                    "actual_kn": r.get("actual_kn"), "raw_error_kn": raw_err,
                    "issued_error_kn": issued_err, "correction_kn": round(correction, 2),
                    "correction_harm_kn": round(harm, 2),
                    "observed_flow": r.get("observed_flow", r.get("actual_regime")),
                    "flags": [name for name, yes in (("large_miss", large_miss),
                                                       ("large_correction", large_correction),
                                                       ("correction_hurt", correction_hurt)) if yes]})
    return out[-30:]


def _regression_state(lake):
    """Compact view of the learned per-(scenario x hour) regression: what the linear
    correction has already absorbed, so the analyst reasons about the RESIDUAL."""
    try:
        with open(fc.bias_path(lake)) as f:
            bias = json.load(f)
    except Exception:
        return {}
    out = []
    for key, st in sorted((bias.get("buckets") or {}).items()):
        if st.get("n"):
            out.append({"bucket": key, "a": round(st.get("a", 0), 2),
                        "b": round(st.get("b", 1), 2), "n": st["n"],
                        "mae_kn": round(st.get("mae_kn", 0), 2)})
    return {"buckets": out[:40], "n_buckets": len(out)}


def _hypothesis_evidence(lake, recs, on_date=None):
    """Open hypotheses — i.e. changes that were ACTUALLY APPLIED and have waited their
    review period — with the measured CRPS before vs after the change took effect.

    Two things this deliberately does NOT do, because both would make the analyst
    accountable for something it did not cause:
      - it never includes refused proposals (nothing changed, so any CRPS movement is
        pure weather); those appear separately under `recent_proposals`;
      - the 'after' window starts at effective_date, the first day the new value could
        influence a forecast, not at issued_date (which always predates it)."""
    out = []
    for e in ledger.open_entries(lake, on_date=on_date):
        eff = e.get("effective_date") or e["issued_date"]
        before = verify.mean_crps(recs, until=eff)
        after = verify.mean_crps(recs, since=eff)
        out.append({"id": e["id"], "param": e["param"], "from": e["from"],
                    "proposed": e["proposed"], "expected_effect": e.get("expected_effect"),
                    "issued_date": e["issued_date"], "effective_date": eff,
                    "crps_before": None if before is None else round(before, 3),
                    "crps_after": None if after is None else round(after, 3),
                    "delta_crps": (None if (before is None or after is None)
                                   else round(after - before, 3)),
                    "note": ("compare crps_before vs crps_after; both are measured, but "
                             "with few days they also reflect weather, so say so if the "
                             "evidence is too thin to judge")})
    return out


def _recent_proposals(lake):
    """What this analyst already proposed and what the gate did with it — so it neither
    repeats itself nor is graded on changes that never took effect."""
    return [{"id": e["id"], "param": e["param"], "proposed": e["proposed"],
             "issued_date": e["issued_date"], "applied": e.get("applied", False),
             "gate_reason": e.get("gate_reason", ""), "status": e.get("status")}
            for e in ledger.recent(lake)]


def build_evidence(lake, date, agg=None, diffs=None):
    """Everything the analyst perceives this morning."""
    sc = verify.evaluate(lake)
    window = _multiday_errors(lake)
    return {
        "lake": lake, "date": date,
        "current_params": fc.params_for(lake),
        "param_bounds": fc.PARAM_BOUNDS,
        "yesterday_aggregate": agg,
        "yesterday_diffs": diffs,
        "error_window": window,
        "jump_audit": _jump_audit(window),
        "regression_state": _regression_state(lake),
        "verification": {k: sc.get(k) for k in
                         ("n_pairs", "n_days", "crps", "mae", "rmse", "bias",
                          "crps_pers", "crps_clim", "ss_pers", "ss_clim",
                          "n_leaked_skipped")},
        "open_hypotheses": _hypothesis_evidence(lake, sc.get("recs", []), on_date=date),
        "recent_proposals": _recent_proposals(lake),
    }


# ---------------------------------------------------------------- act (gated apply)
def _validate(param, value, params=None):
    """Reject anything outside the known, bounded, capped-step envelope."""
    if param not in fc.TUNABLE:
        return f"unknown parameter '{param}'"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "proposed value is not numeric"
    lo, hi = fc.PARAM_BOUNDS[param]
    if not (lo <= value <= hi):
        return f"out of bounds ({lo}..{hi})"
    cur = (params or fc.PARAMS)[param]
    if cur and abs(value - cur) > abs(cur) * MAX_REL_STEP:
        return f"step too large (>{int(MAX_REL_STEP * 100)}% of {cur})"
    return None


def consider(lake, param, value):
    """Decide whether a proposed change may be applied. Returns (ok, reason, backtest)."""
    bad = _validate(param, value, fc.params_for(lake))
    if bad:
        return False, bad, None
    bt = verify.backtest(lake, param, value, params=fc.params_for(lake))
    if bt.get("error"):
        return False, bt["error"], bt
    if not bt["enough_data"]:
        return False, (f"insufficient replayable history ({bt['n_days']}/"
                       f"{verify.N_MIN_BACKTEST_DAYS} days, {bt['n_pairs']}/"
                       f"{verify.N_MIN_BACKTEST_PAIRS} hours)"), bt
    # A positive point estimate is not evidence. The walk-forward score is deliberately
    # noisy (each arm relearns from cold), so requiring only mae_skill > 0 applied a
    # coin-flip-grade "improvement" a large fraction of the time. Demand instead that the
    # whole bootstrap interval of the PAIRED per-hour difference sits below zero, and that
    # the effect is big enough to be worth acting on.
    if not bt.get("significant"):
        # Three genuinely different verdicts. Reporting them all as "not significant" is
        # wrong: a change can be statistically solid and still too small to act on, and a
        # change can have literally no effect because the scenario it governs never fires in
        # the replayed period (Ammersee foehn, for instance).
        d, lo, hi = bt["delta_kn"], bt["ci_lo"], bt["ci_hi"]
        if d == 0 and lo == 0 and hi == 0:
            why = ("no effect at all — this parameter changed no hour in the replayed "
                   "period, so there is nothing to verify")
        elif hi is not None and hi < 0:
            why = (f"real but too small to act on (Δ {d:+.3f} kn, 95% CI [{lo}, {hi}] is "
                   f"below zero, but |Δ| < MIN_EFFECT_KN={verify.MIN_EFFECT_KN})")
        else:
            why = (f"not statistically significant (Δ {d:+.3f} kn, 95% CI [{lo}, {hi}] "
                   f"includes zero)")
        return False, why, bt
    return True, (f"MAE -{abs(bt['delta_kn']):.3f} kn, 95% CI [{bt['ci_lo']}, {bt['ci_hi']}]"
                  f" over {bt['n_days']} days / {bt['n_pairs']} hours "
                  f"(skill {bt['mae_skill']:+.4f})"), bt


def apply_change(lake, param, value, reason, backtest_result, date, stamp):
    """Build replacement calibration, then activate the verified per-lake change."""
    old_params = fc.params_for(lake)
    params = dict(old_params)
    before = params.get(param)
    params[param] = value
    # Build first. If replay fails, neither configuration nor production state changes.
    rebuilt = verify.rebuild_bias(lake, params)
    old_bias = fc.load_bias(lake)
    n_dropped = len(old_bias.get("buckets") or {})
    path = fc.bias_path(lake)
    tmp = path + ".tmp"
    try:
        fc.save_params(params, lake=lake)  # PER-LAKE: verified on this lake's history only
        with open(tmp, "w") as f:
            json.dump(rebuilt, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # A two-file switch cannot be one filesystem rename. Restore both old states if
        # either write fails so config and calibration never intentionally diverge.
        fc.save_params(old_params, lake=lake)
        with open(tmp, "w") as f:
            json.dump(old_bias, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        raise
    wd.log_event("param_change", {"lake": lake, "date": date, "param": param,
                                  "from": before, "to": value, "reason": reason,
                                  "bias_buckets_retired": n_dropped,
                                  "bias_buckets_rebuilt": len(rebuilt["buckets"]),
                                  "backtest": backtest_result}, stamp=stamp)
    return {"param": param, "from": before, "to": value, "reason": reason,
            "bias_buckets_retired": n_dropped,
            "bias_buckets_rebuilt": len(rebuilt["buckets"])}


# ---------------------------------------------------------------- the loop
def run(lake, date, stamp, agg=None, diffs=None):
    """One full perceive -> reflect -> propose -> act cycle for a lake. Never raises."""
    try:
        evidence = build_evidence(lake, date, agg, diffs)
    except Exception as e:
        return {"skipped": f"evidence assembly failed: {type(e).__name__}: {e}"}

    res = analyst.run_analysis(evidence)
    if res.get("skipped"):
        return {"skipped": res["skipped"], "n_open": len(evidence["open_hypotheses"])}

    # REFLECT — write the analyst's self-judgement back to the ledger. `lake=` scoping
    # means a hallucinated id cannot close another lake's hypothesis; unmatched ids are
    # reported rather than silently dropped.
    reviewed, unknown_ids = [], []
    for rv in res.get("reviews", []) or []:
        if rv.get("verdict") not in ("confirmed", "retracted") or not rv.get("id"):
            continue
        if ledger.resolve(rv["id"], rv["verdict"], rv.get("reasoning", ""), date, lake=lake):
            reviewed.append({"id": rv["id"], "verdict": rv["verdict"]})
        else:
            unknown_ids.append(rv["id"])

    # PROPOSE + ACT — gate FIRST, then record the proposal together with the outcome, so
    # the ledger never shows a refused change as an open hypothesis.
    applied, refused, seen = [], [], set()
    for p in res.get("proposals", []) or []:
        param, value = p.get("param"), p.get("proposed")
        if param in seen:          # two proposals for one param would compound past the step cap
            refused.append({"param": param, "proposed": value,
                            "reason": "duplicate proposal for the same parameter in one run"})
            continue
        seen.add(param)
        before = fc.params_for(lake).get(param)   # capture BEFORE any apply overwrites it
        ok, reason, bt = consider(lake, param, value)
        if ok:
            applied.append(apply_change(lake, param, value, reason, bt, date, stamp))
        else:
            refused.append({"param": param, "proposed": value, "reason": reason})
        ledger.add(lake, param, before, value,
                   p.get("expected_effect") or p.get("rationale", ""), date,
                   REVIEW_AFTER_DAYS, applied=ok, gate_reason=reason)

    return {"narrative": res.get("narrative", ""), "diagnosis": res.get("diagnosis", []),
            "proposals": res.get("proposals", []), "reviews": res.get("reviews", []),
            "reviewed": reviewed, "unknown_review_ids": unknown_ids,
            "applied": applied, "refused": refused,
            "n_open": len(evidence["open_hypotheses"]),
            "verification": evidence["verification"]}


def format_summary(r):
    if r.get("skipped"):
        return f"  tuner: skipped — {r['skipped']}"
    L = [f"  tuner: {len(r['proposals'])} proposal(s), {len(r['reviewed'])} hypothesis "
         f"review(s), {len(r['applied'])} applied — {r.get('narrative', '')[:100]}"]
    for a in r["applied"]:
        L.append(f"    ✔ APPLIED {a['param']}: {a['from']} → {a['to']} ({a['reason']})")
    for x in r["refused"]:
        L.append(f"    ✗ held back {x['param']}={x['proposed']} — {x['reason']}")
    for v in r["reviewed"]:
        L.append(f"    ↺ {v['verdict']}: {v['id']}")
    for bad in r.get("unknown_review_ids", []):
        L.append(f"    ? review for unknown id ignored: {bad}")
    return "\n".join(L)


def _selftest():
    """Prove the gate refuses everything it should, applies only verified wins, and that
    the memory loop round-trips — all offline, without touching production files."""
    import tempfile
    tmp = tempfile.mkdtemp()
    # Redirect EVERY write path into the sandbox. CONFIG_DIR matters as much as
    # PARAMS_PATH now that params are per-lake (params_path() builds off CONFIG_DIR),
    # otherwise this test would write config/params_<lake>.json into the real repo.
    ledger.LEDGER_PATH = os.path.join(tmp, "ledger.jsonl")
    fc.CONFIG_DIR = tmp
    fc.PARAMS_PATH = os.path.join(tmp, "params.json")
    fc.PARAMS = dict(fc._DEFAULTS)
    wd.LOG_DIR = tmp
    wd.EVENTS_LOG = os.path.join(tmp, "events.jsonl")
    # MODELS_DIR too: apply_change replaces the lake's learned buckets, and without this
    # the test would overwrite the real model. Seed stale state and prove it is replaced.
    fc.MODELS_DIR = tmp
    with open(fc.bias_path("walchensee"), "w") as f:
        json.dump({"alpha": 0.3, "processed_dates": ["2026-07-31"],
                   "buckets": {"thermal|13": {"n": 4, "a": 0.0, "b": 1.2}}}, f)

    # --- jump audit: distinguish a raw-model miss from a harmful correction
    audit = _jump_audit([
        {"date": "2026-08-01", "hour": 15, "regime": "gradient", "raw_kn": 5.0,
         "issued_kn": 10.5, "actual_kn": 4.0, "err_raw_kn": 1.0, "err_issued_kn": 6.5,
         "observed_flow": "thermal", "leaked": False},
        {"date": "2026-08-01", "hour": 16, "regime": "gradient", "raw_kn": 3.0,
         "issued_kn": 3.2, "actual_kn": 10.0, "err_raw_kn": -7.0, "err_issued_kn": -6.8,
         "observed_flow": "foehn", "leaked": False},
        {"date": "2026-08-01", "hour": 17, "regime": "calm", "raw_kn": 2.0,
         "issued_kn": 2.1, "actual_kn": 2.0, "err_raw_kn": 0.0, "err_issued_kn": 0.1,
         "leaked": False},
    ])
    assert len(audit) == 2, audit
    assert "correction_hurt" in audit[0]["flags"] and "large_miss" in audit[0]["flags"], audit
    assert audit[1]["correction_harm_kn"] < 0, audit
    print("  PASS jump audit: harmful correction separated from raw-model large miss")

    # --- validation: every bad proposal is rejected, with a reason
    assert _validate("NOT_A_PARAM", 5) and "unknown" in _validate("NOT_A_PARAM", 5)
    assert "numeric" in _validate("THERMAL_CLOUD_MAX", "forty")
    assert "bounds" in _validate("THERMAL_CLOUD_MAX", 500)
    assert "step too large" in _validate("THERMAL_CLOUD_MAX", 20)   # 45 -> 20 is >25%
    assert _validate("THERMAL_CLOUD_MAX", 40) is None               # small, in-bounds: OK
    print("  PASS validation: unknown / non-numeric / out-of-bounds / oversized step rejected")

    # --- gate: decision follows the backtest, not the model's confidence
    real_backtest = verify.backtest
    def bt(ss, days=20, enough=True, delta=-0.4, lo=-0.6, hi=-0.2, sig=None):
        return {"mae_skill": ss, "n_days": days, "n_pairs": days * 6, "enough_data": enough,
                "delta_kn": delta, "ci_lo": lo, "ci_hi": hi,
                "significant": (hi < 0 and delta <= -verify.MIN_EFFECT_KN) if sig is None else sig}
    cases = [
        (bt(0.2), True, "verified win: CI entirely below zero"),
        (bt(-0.2, delta=0.4, lo=0.2, hi=0.6), False, "candidate is worse"),
        (bt(0.0, delta=0.0, lo=-0.1, hi=0.1), False, "flat"),
        (bt(0.9, days=2, enough=False), False, "thin history"),
        # THE POINT OF THE SIGNIFICANCE TEST: a positive point estimate whose interval
        # still straddles zero is noise, and used to be applied.
        (bt(0.15, delta=-0.30, lo=-0.75, hi=0.20), False, "positive but CI straddles zero"),
        # ...and an effect too small to be worth acting on, even if significant
        (bt(0.01, delta=-0.01, lo=-0.02, hi=-0.005), False, "significant but trivial effect"),
        (bt(0.0, delta=0.0, lo=0.0, hi=0.0), False, "parameter changed nothing at all"),
    ]
    for fake, expect_ok, label in cases:
        verify.backtest = (lambda _f: (lambda *a, **k: dict(_f)))(fake)
        ok, reason, _ = consider("walchensee", "THERMAL_CLOUD_MAX", 40)
        assert ok is expect_ok, f"{label}: expected ok={expect_ok}, got {ok} ({reason})"
        if not ok and fake["enough_data"]:   # refusal must name the REAL reason
            d, hi = fake["delta_kn"], fake["ci_hi"]
            if d == 0 and hi == 0:
                assert "no effect at all" in reason, reason
            elif hi < 0:
                assert "too small to act on" in reason, reason
            else:
                assert "includes zero" in reason, reason
    print("  PASS gate: applies only a SIGNIFICANT, non-trivial win; refuses regressions,\n"
          "             flat results, thin history, CI-straddles-zero noise, and tiny effects")

    # --- apply writes the single source of truth and is picked up live
    verify.backtest = lambda *a, **k: bt(0.2)
    ok, reason, bt_ = consider("walchensee", "THERMAL_CLOUD_MAX", 40)
    apply_change("walchensee", "THERMAL_CLOUD_MAX", 40, reason, bt_, "2026-08-02", "stamp")
    assert fc.params_for("walchensee")["THERMAL_CLOUD_MAX"] == 40
    # and CRITICALLY: it must NOT have leaked to the other lakes, whose history did not
    # justify it (the change was only ever backtested against walchensee)
    assert fc.params_for("kochelsee")["THERMAL_CLOUD_MAX"] == fc._DEFAULTS["THERMAL_CLOUD_MAX"]
    assert fc.params_for("ammersee")["THERMAL_CLOUD_MAX"] == fc._DEFAULTS["THERMAL_CLOUD_MAX"]
    # No replay rows exist in this isolated fixture, so rebuild produces an empty but
    # internally consistent state rather than leaving the stale bucket in place.
    assert json.load(open(fc.bias_path("walchensee")))["buckets"] == {}, "stale buckets kept"
    assert json.load(open(fc.bias_path("walchensee")))["processed_dates"] == [], \
        "a rebuild with no replay rows must not claim processed history"
    print("  PASS apply: per-lake write, other lakes untouched, stale state replaced")

    # --- memory loop: proposal recorded, then reviewed and resolved next run
    fc.PARAMS = dict(fc._DEFAULTS)
    real_run = analyst.run_analysis
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "thermal starts late", "reviews": [],
        "proposals": [{"param": "COLD_POOL_DTHETA", "proposed": 1.3,
                       "rationale": "cold pool clears earlier",
                       "expected_effect": "CRPS -0.3 kn on 10-13h"}]}
    verify.backtest = lambda *a, **k: bt(0.1, days=2, enough=False)
    r1 = run("walchensee", "2026-08-01", "stamp")
    assert len(r1["refused"]) == 1 and "insufficient" in r1["refused"][0]["reason"]
    # refused => NOT an open hypothesis (nothing changed, so CRPS movement would be weather)
    assert ledger.open_entries("walchensee") == [], ledger.open_entries("walchensee")
    rec = ledger.recent("walchensee")[-1]
    assert rec["status"] == "not_applied" and "insufficient" in rec["gate_reason"], rec
    print("  PASS propose: refused change recorded with its reason, NOT as an open hypothesis")

    # now an APPLIED change: it becomes an open hypothesis and is reviewed once due
    verify.backtest = lambda *a, **k: bt(0.15)
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "raise the cloud ceiling", "reviews": [],
        "proposals": [{"param": "THERMAL_CLOUD_MAX", "proposed": 41,
                       "rationale": "more thermals", "expected_effect": "CRPS -0.2"}]}
    r2 = run("walchensee", "2026-08-05", "stamp")
    assert len(r2["applied"]) == 1, r2
    eid = ledger.open_entries("walchensee")[0]["id"]
    # ...but NOT judged the same day: the review period must elapse first
    analyst.run_analysis = lambda ev, **k: {"narrative": "too soon", "reviews": [], "proposals": []}
    r3 = run("walchensee", "2026-08-06", "stamp")
    assert r3["n_open"] == 0, "hypothesis judged before its review period elapsed"
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "that did not help",
        "reviews": ([{"id": ev["open_hypotheses"][0]["id"], "verdict": "retracted",
                      "reasoning": "CRPS unchanged"}] if ev["open_hypotheses"] else []),
        "proposals": []}
    r4 = run("walchensee", "2026-08-20", "stamp")
    assert r4["n_open"] == 1, r4                      # now due, and it SAW it
    assert r4["reviewed"] == [{"id": eid, "verdict": "retracted"}], r4["reviewed"]
    assert ledger.open_entries("walchensee") == []    # and closed it out
    print("  PASS reflect: applied change became a hypothesis, waited, then was retracted")

    # a hallucinated review id must not silently close anything
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "x", "proposals": [],
        "reviews": [{"id": "kochelsee:FOEHN_DP_RIM:2026-01-01", "verdict": "confirmed",
                     "reasoning": "made up"}]}
    r5 = run("walchensee", "2026-08-21", "stamp")
    assert r5["reviewed"] == [] and r5["unknown_review_ids"], r5
    print("  PASS reflect: hallucinated / cross-lake review id rejected, not silently applied")

    verify.backtest, analyst.run_analysis = real_backtest, real_run
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("selftest", "test"):
        print("=== tuner.py self-tests ===")
        _selftest()
        print("ALL SELF-TESTS PASSED")
    else:
        lake = sys.argv[1] if len(sys.argv) > 1 else "walchensee"
        date = sys.argv[2] if len(sys.argv) > 2 else \
            (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        ev = build_evidence(lake, date)
        print(json.dumps({k: v for k, v in ev.items() if k != "yesterday_diffs"},
                         indent=2, ensure_ascii=False)[:3000])
