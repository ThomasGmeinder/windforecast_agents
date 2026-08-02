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
  4. ACT       each proposal is BACKTESTED; a change is applied to config/params.json
               only if it verifiably lowers CRPS on enough replayable days. Otherwise
               it stays a logged proposal. Nothing is ever applied on faith.

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
    keep = {"date", "hour", "regime", "issued_kn", "actual_kn", "err_issued_kn",
            "dir_err_deg", "actual_regime"}
    return [{k: v for k, v in r.items() if k in keep} for r in rows if r["date"] in dates]


def _regression_state(lake):
    """Compact view of the learned per-(regime x hour) regression: what the linear
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


def _hypothesis_evidence(lake, recs):
    """Open hypotheses + the MEASURED CRPS before vs after each was issued. This is what
    makes the analyst accountable: it sees whether its own past call actually helped."""
    out = []
    for e in ledger.open_entries(lake):
        issued = e["issued_date"]
        before = verify.mean_crps(recs, until=issued)
        after = verify.mean_crps(recs, since=issued)
        out.append({"id": e["id"], "param": e["param"], "from": e["from"],
                    "proposed": e["proposed"], "expected_effect": e.get("expected_effect"),
                    "issued_date": issued,
                    "crps_before": None if before is None else round(before, 3),
                    "crps_after": None if after is None else round(after, 3),
                    "delta_crps": (None if (before is None or after is None)
                                   else round(after - before, 3))})
    return out


def build_evidence(lake, date, agg=None, diffs=None):
    """Everything the analyst perceives this morning."""
    sc = verify.evaluate(lake)
    return {
        "lake": lake, "date": date,
        "current_params": dict(fc.PARAMS),
        "param_bounds": fc.PARAM_BOUNDS,
        "yesterday_aggregate": agg,
        "yesterday_diffs": diffs,
        "error_window": _multiday_errors(lake),
        "regression_state": _regression_state(lake),
        "verification": {k: sc.get(k) for k in
                         ("n_pairs", "n_days", "crps", "mae", "rmse", "bias",
                          "crps_pers", "crps_clim", "ss_pers", "ss_clim")},
        "open_hypotheses": _hypothesis_evidence(lake, sc.get("recs", [])),
    }


# ---------------------------------------------------------------- act (gated apply)
def _validate(param, value):
    """Reject anything outside the known, bounded, capped-step envelope."""
    if param not in fc.TUNABLE:
        return f"unknown parameter '{param}'"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "proposed value is not numeric"
    lo, hi = fc.PARAM_BOUNDS[param]
    if not (lo <= value <= hi):
        return f"out of bounds ({lo}..{hi})"
    cur = fc.PARAMS[param]
    if cur and abs(value - cur) > abs(cur) * MAX_REL_STEP:
        return f"step too large (>{int(MAX_REL_STEP * 100)}% of {cur})"
    return None


def consider(lake, param, value):
    """Decide whether a proposed change may be applied. Returns (ok, reason, backtest)."""
    bad = _validate(param, value)
    if bad:
        return False, bad, None
    bt = verify.backtest(lake, param, value)
    if bt.get("error"):
        return False, bt["error"], bt
    if not bt["enough_data"]:
        return False, (f"insufficient replayable history "
                       f"({bt['n_days']}/{verify.N_MIN_BACKTEST_DAYS} days)"), bt
    if not bt["crps_ss"] or bt["crps_ss"] <= 0:
        return False, f"backtest shows no improvement (CRPS-SS {bt['crps_ss']})", bt
    return True, f"backtest CRPS-SS {bt['crps_ss']:+.4f} over {bt['n_days']} days", bt


def apply_change(lake, param, value, reason, backtest_result, date, stamp):
    """Write the verified change to the single source of truth + record the evidence."""
    params = dict(fc.PARAMS)
    before = params.get(param)
    params[param] = value
    fc.save_params(params)
    fc.PARAMS = fc.load_params()          # refresh the live view in-process
    wd.log_event("param_change", {"lake": lake, "date": date, "param": param,
                                  "from": before, "to": value, "reason": reason,
                                  "backtest": backtest_result}, stamp=stamp)
    return {"param": param, "from": before, "to": value, "reason": reason}


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

    # REFLECT — write the analyst's self-judgement back to the ledger
    reviewed = []
    for rv in res.get("reviews", []) or []:
        if rv.get("verdict") in ("confirmed", "retracted") and rv.get("id"):
            if ledger.resolve(rv["id"], rv["verdict"], rv.get("reasoning", ""), date):
                reviewed.append({"id": rv["id"], "verdict": rv["verdict"]})

    # PROPOSE + ACT — record each proposal, then let the backtest gate decide
    applied, refused = [], []
    for p in res.get("proposals", []) or []:
        param, value = p.get("param"), p.get("proposed")
        ledger.add(lake, param, fc.PARAMS.get(param), value,
                   p.get("expected_effect") or p.get("rationale", ""), date,
                   REVIEW_AFTER_DAYS)
        ok, reason, bt = consider(lake, param, value)
        if ok:
            applied.append(apply_change(lake, param, value, reason, bt, date, stamp))
        else:
            refused.append({"param": param, "proposed": value, "reason": reason})

    return {"narrative": res.get("narrative", ""), "diagnosis": res.get("diagnosis", []),
            "proposals": res.get("proposals", []), "reviews": res.get("reviews", []),
            "reviewed": reviewed, "applied": applied, "refused": refused,
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
    return "\n".join(L)


def _selftest():
    """Prove the gate refuses everything it should, applies only verified wins, and that
    the memory loop round-trips — all offline, without touching production files."""
    import tempfile
    tmp = tempfile.mkdtemp()
    ledger.LEDGER_PATH = os.path.join(tmp, "ledger.jsonl")
    fc.PARAMS_PATH = os.path.join(tmp, "params.json")
    fc.PARAMS = dict(fc._DEFAULTS)
    wd.EVENTS_LOG = os.path.join(tmp, "events.jsonl")

    # --- validation: every bad proposal is rejected, with a reason
    assert _validate("NOT_A_PARAM", 5) and "unknown" in _validate("NOT_A_PARAM", 5)
    assert "numeric" in _validate("THERMAL_CLOUD_MAX", "forty")
    assert "bounds" in _validate("THERMAL_CLOUD_MAX", 500)
    assert "step too large" in _validate("THERMAL_CLOUD_MAX", 20)   # 45 -> 20 is >25%
    assert _validate("THERMAL_CLOUD_MAX", 40) is None               # small, in-bounds: OK
    print("  PASS validation: unknown / non-numeric / out-of-bounds / oversized step rejected")

    # --- gate: decision follows the backtest, not the model's confidence
    real_backtest = verify.backtest
    cases = [({"crps_ss": 0.2, "n_days": 20, "enough_data": True}, True, "verified win"),
             ({"crps_ss": -0.2, "n_days": 20, "enough_data": True}, False, "no improvement"),
             ({"crps_ss": 0.0, "n_days": 20, "enough_data": True}, False, "flat"),
             ({"crps_ss": 0.9, "n_days": 2, "enough_data": False}, False, "thin history")]
    for fake, expect_ok, label in cases:
        verify.backtest = (lambda _f: (lambda *a, **k: dict(_f)))(fake)
        ok, reason, _ = consider("walchensee", "THERMAL_CLOUD_MAX", 40)
        assert ok is expect_ok, f"{label}: expected ok={expect_ok}, got {ok} ({reason})"
    print("  PASS gate: applies only a backtested win; refuses regressions, flat, thin data")

    # --- apply writes the single source of truth and is picked up live
    verify.backtest = lambda *a, **k: {"crps_ss": 0.2, "n_days": 20, "enough_data": True}
    ok, reason, bt = consider("walchensee", "THERMAL_CLOUD_MAX", 40)
    apply_change("walchensee", "THERMAL_CLOUD_MAX", 40, reason, bt, "2026-08-02", "stamp")
    assert fc.load_params()["THERMAL_CLOUD_MAX"] == 40
    assert fc.PARAMS["THERMAL_CLOUD_MAX"] == 40
    print("  PASS apply: config/params.json updated and live PARAMS refreshed")

    # --- memory loop: proposal recorded, then reviewed and resolved next run
    fc.PARAMS = dict(fc._DEFAULTS)
    real_run = analyst.run_analysis
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "thermal starts late", "reviews": [],
        "proposals": [{"param": "COLD_POOL_DTHETA", "proposed": 1.3,
                       "rationale": "cold pool clears earlier",
                       "expected_effect": "CRPS -0.3 kn on 10-13h"}]}
    verify.backtest = lambda *a, **k: {"crps_ss": 0.1, "n_days": 2, "enough_data": False}
    r1 = run("walchensee", "2026-08-01", "stamp")
    assert len(r1["refused"]) == 1 and "insufficient" in r1["refused"][0]["reason"]
    opens = ledger.open_entries("walchensee")
    assert len(opens) == 1 and opens[0]["param"] == "COLD_POOL_DTHETA", opens
    print("  PASS propose: hypothesis recorded as open; apply correctly withheld on thin data")

    eid = opens[0]["id"]
    analyst.run_analysis = lambda ev, **k: {
        "narrative": "that did not help",
        "reviews": [{"id": ev["open_hypotheses"][0]["id"], "verdict": "retracted",
                     "reasoning": "CRPS unchanged"}],
        "proposals": []}
    r2 = run("walchensee", "2026-08-04", "stamp")
    assert r2["n_open"] == 1, r2                      # it SAW its own past hypothesis
    assert r2["reviewed"] == [{"id": eid, "verdict": "retracted"}], r2["reviewed"]
    assert ledger.open_entries("walchensee") == []    # and closed it out
    print("  PASS reflect: analyst saw its own open hypothesis and retracted it")

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
