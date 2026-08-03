#!/usr/bin/env python3
"""
simulate.py — run the WHOLE daily loop end to end, safely, in two modes.

The apply path (a parameter change actually reaching the forecaster) had never executed:
real data refuses every candidate, and the unit tests only ever mocked the backtest. This
drives the real code — learn -> tune -> gate -> apply -> review -> forecast -> verify —
with every write redirected into a temp directory, so production state cannot be touched.

    offline   engineered fixture, NO NETWORK, deterministic, seconds.
              Data is built so one specific threshold is genuinely better, which forces
              the gate to APPLY, the ledger to open a hypothesis, and a later day to
              REVIEW it. That is the only way to exercise those branches, because on real
              data the gate correctly refuses everything.
              -> this is the mode CI runs.

    live      real archived forecasts + real measured actuals for a date range, via
              lib/backfill. Exercises the true data path including fallbacks and
              timezones. Needs network and a few minutes.
              -> local only; CI must not depend on third-party services.

CLI:
  python lib/simulate.py offline
  python lib/simulate.py live ammersee 2026-06-01 2026-06-30
"""
import os, sys, json, shutil, tempfile, datetime, contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import winddata as wd
import forecast as fc
import postproc
import verify
import ledger
import learn
import tuner
import analyst
import climatology
import obs_calib

LAKE = "walchensee"          # alpine-rim: exercises regime switching properly


@contextlib.contextmanager
def sandbox():
    """Redirect EVERY write path into a temp dir and restore afterwards.

    Listed explicitly rather than guessed: a module that writes to the real repo during a
    simulation is exactly the failure that wiped a live bias file earlier in this project."""
    tmp = tempfile.mkdtemp(prefix="wind_sim_")
    saved = {
        "log": wd.LOG_DIR, "events": wd.EVENTS_LOG, "models": fc.MODELS_DIR,
        "config": fc.CONFIG_DIR, "params": fc.PARAMS_PATH, "ledger": ledger.LEDGER_PATH,
        "learn": learn.LEARN_DIR, "params_live": fc.PARAMS,
        "analyst": analyst.run_analysis, "clim": dict(climatology._CACHE),
        "calib": dict(obs_calib._CACHE),
    }
    try:
        for sub in ("logs", "logs/learning", "models", "config"):
            os.makedirs(os.path.join(tmp, sub), exist_ok=True)
        wd.LOG_DIR = os.path.join(tmp, "logs")
        wd.EVENTS_LOG = os.path.join(tmp, "logs", "events.jsonl")
        learn.LEARN_DIR = os.path.join(tmp, "logs", "learning")
        fc.MODELS_DIR = os.path.join(tmp, "models")
        fc.CONFIG_DIR = os.path.join(tmp, "config")
        fc.PARAMS_PATH = os.path.join(tmp, "config", "params.json")
        fc.PARAMS = dict(fc._DEFAULTS)
        ledger.LEDGER_PATH = os.path.join(tmp, "logs", "ledger.jsonl")
        climatology._CACHE.clear()
        obs_calib._CACHE.clear()
        yield tmp
    finally:
        wd.LOG_DIR, wd.EVENTS_LOG = saved["log"], saved["events"]
        fc.MODELS_DIR, fc.CONFIG_DIR = saved["models"], saved["config"]
        fc.PARAMS_PATH, fc.PARAMS = saved["params"], saved["params_live"]
        ledger.LEDGER_PATH, learn.LEARN_DIR = saved["ledger"], saved["learn"]
        analyst.run_analysis = saved["analyst"]
        climatology._CACHE.clear(); climatology._CACHE.update(saved["clim"])
        obs_calib._CACHE.clear(); obs_calib._CACHE.update(saved["calib"])
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ offline fixture
def _fixture(n_days=30, hours=tuple(range(8, 20)), raw=6.0, start=(2026, 3, 1)):
    """Days where THERMAL_CLOUD_MAX=38 is genuinely, measurably better.

    Cloud alternates 42 / 30 by day at the SAME hours. At the default threshold 45 both
    land in one thermal bucket and get a blended, wrong correction; at 38 the cloud-42
    days become gradient and each bucket learns its own relationship. The truth is
    deterministic (no RNG) so the whole simulation is reproducible."""
    forecasts, actuals = [], {}
    for k in range(n_days):
        day = datetime.date(*start) + datetime.timedelta(days=k)
        d = day.isoformat()
        cloud, mult = ((42, 2.0) if k % 2 == 0 else (30, 1.0))
        rows, act = [], {}
        for h in hours:
            rows.append({"hour": h, "raw_kn": raw, "raw_gust_kn": raw * 1.8,
                         "mean_kn": raw, "gust_kn": raw * 1.8, "dir": 20,
                         "regime": "thermal", "conf": "med", "foehn_note": None,
                         "spread_kn": None, "q_kn": None, "dtheta": 0.0, "dp": 0.0,
                         "lapse": None,
                         "inputs": {"spd925": 3.0, "spd850": 2.0, "dir850": 10,
                                    "cloud": cloud}})
            act[h] = round(raw * mult, 1)
        forecasts.append({"lake": LAKE, "kind": "forecast", "date": d,
                          "run_stamp": f"{d}T05:00+02:00", "label": "Walchensee",
                          "summary": "fixture", "hourly": rows})
        actuals[d] = act
    return forecasts, actuals


def _seed(tmp, forecasts, actuals):
    with open(os.path.join(wd.LOG_DIR, f"{LAKE}_forecast.jsonl"), "w") as f:
        f.write("\n".join(json.dumps(r) for r in forecasts) + "\n")
    rows = []
    for d, hrs in sorted(actuals.items()):
        for h, kn in sorted(hrs.items()):
            rows.append(json.dumps({"date": d, "lake": LAKE, "source": "fixture",
                                    "hour": h, "actual_kn": kn, "leaked": False}))
    with open(os.path.join(wd.LOG_DIR, f"{LAKE}_diffs.jsonl"), "w") as f:
        f.write("\n".join(rows) + "\n")


def _stub_analyst(proposals, reviews=()):
    def run(evidence, **kw):
        return {"narrative": "simulated", "diagnosis": [],
                "reviews": [r(evidence) if callable(r) else r for r in reviews],
                "proposals": list(proposals)}
    return run


# ------------------------------------------------------------------ the simulation
def offline(verbose=True):
    """Drive the real loop to an APPLY, then to a REVIEW. Returns a result dict."""
    out = {}
    with sandbox() as tmp:
        forecasts, actuals = _fixture()
        _seed(tmp, forecasts, actuals)
        day0 = forecasts[-1]["date"]

        # 1. the gate must now have real replayable history
        bt = verify.backtest(LAKE, "THERMAL_CLOUD_MAX", 38)
        out["backtest"] = bt
        assert bt["enough_data"], f"fixture too small for the gate: {bt}"
        assert bt["significant"], f"engineered improvement not detected: {bt}"

        # 2. PROPOSE -> gate -> APPLY (the branch real data never reaches)
        analyst.run_analysis = _stub_analyst(
            [{"param": "THERMAL_CLOUD_MAX", "proposed": 38,
              "rationale": "cloud-42 days behave like gradient",
              "expected_effect": "lower CRPS on thermal hours"}])
        r1 = tuner.run(LAKE, day0, f"{day0}T05:00+02:00")
        out["apply"] = r1
        assert r1.get("applied"), f"gate refused a genuine improvement: {r1}"
        assert fc.params_for(LAKE)["THERMAL_CLOUD_MAX"] == 38, "param not persisted"
        assert os.path.exists(fc.params_path(LAKE)), "per-lake config not written"
        assert not os.path.exists(os.path.join(tmp, "config", "params_ammersee.json")), \
            "a change verified on one lake leaked to another"

        # 3. the applied change must become an OPEN hypothesis with an effective date
        opens = ledger.open_entries(LAKE)
        out["open"] = opens
        assert len(opens) == 1 and opens[0]["applied"], opens
        assert opens[0]["effective_date"] > opens[0]["issued_date"], opens

        # 4. and must NOT be judged before its review period elapses
        analyst.run_analysis = _stub_analyst([])
        soon = (datetime.date.fromisoformat(day0) + datetime.timedelta(days=1)).isoformat()
        assert tuner.run(LAKE, soon, "s")["n_open"] == 0, "hypothesis judged too early"

        # 5. ...then REVIEW it once due
        due = (datetime.date.fromisoformat(day0) + datetime.timedelta(days=9)).isoformat()
        analyst.run_analysis = _stub_analyst(
            [], reviews=[lambda ev: {"id": ev["open_hypotheses"][0]["id"],
                                     "verdict": "confirmed",
                                     "reasoning": "simulated"}])
        r2 = tuner.run(LAKE, due, "s")
        out["review"] = r2
        assert r2["n_open"] == 1 and r2["reviewed"], r2
        assert ledger.open_entries(LAKE) == [], "hypothesis not closed"

        # 6. audit trail: the change is recoverable from the event log alone
        kinds = [json.loads(l)["kind"] for l in open(wd.EVENTS_LOG)] \
            if os.path.exists(wd.EVENTS_LOG) else []
        out["events"] = kinds
        assert "param_change" in kinds, kinds

        # 7. production state must be self-consistent afterwards
        sc = verify.evaluate(LAKE)
        out["scorecard"] = sc
        assert verify.format_scorecard(sc)
        if verbose:
            print(f"  gate saw {bt['n_days']}d/{bt['n_pairs']}h; "
                  f"Δ{bt['delta_kn']:+.3f} kn CI[{bt['ci_lo']},{bt['ci_hi']}]")
            for a in r1["applied"]:
                print(f"  APPLIED {a['param']}: {a['from']} → {a['to']}"
                      f" (retired {a.get('bias_buckets_retired')} stale buckets)")
            print(f"  hypothesis opened, withheld from early review, then "
                  f"{r2['reviewed'][0]['verdict']} on day +9")
            print(f"  events: {sorted(set(kinds))}")
    return out


def live(lake, start, end):
    """Same loop, but on real archived forecasts + real measured actuals. Network."""
    import backfill
    with sandbox() as tmp:
        recs = backfill.build(lake, start, end)
        with open(os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        allobs = wd.dwd_obs_all(wd.STA_OBS[lake])
        wd.dwd_obs_hourly = lambda st, ymd: {
            h: v for (d, h), v in allobs.items()
            if d == f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"}
        got = 0
        for r in recs:
            if not learn.run_and_log(lake, r["date"]).get("skipped"):
                got += 1
        bt = verify.backtest(lake, "THERMAL_CLOUD_MAX", 40)
        print(f"  {len(recs)} day(s) reconstructed, {got} learned")
        print(f"  gate: {bt['n_days']}d/{bt['n_pairs']}h enough={bt['enough_data']} "
              f"Δ{bt['delta_kn']} CI[{bt['ci_lo']},{bt['ci_hi']}]")
        for p, v in (("THERMAL_CLOUD_MAX", 40), ("GRADIENT_925_KN", 13.0)):
            ok, why, _ = tuner.consider(lake, p, v)
            print(f"  {p}={v}: {'APPLY' if ok else 'refuse'} — {why}")
        print(" ", verify.format_scorecard(verify.evaluate(lake)).splitlines()[0])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "offline"
    if mode == "offline":
        print("=== end-to-end loop simulation (offline, hermetic) ===")
        offline()
        print("SIMULATION PASSED — propose → gate → apply → open → withhold → review")
    else:
        live(sys.argv[2], sys.argv[3], sys.argv[4])
