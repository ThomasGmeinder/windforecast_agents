#!/usr/bin/env python3
"""
verify.py — out-of-sample forecast verification: the REFEREE the system was missing.

Scores the logged forecasts against the measured actuals with CRPS (+ MAE / RMSE /
bias), compares them to two baselines (persistence and climatology), and reports skill
scores.

Leak control, on BOTH sides of the comparison:
  - baselines for a day use only data from strictly earlier days;
  - model hours that had already elapsed when the forecast was issued are NOT scored
    (a 06:00 run does not "predict" 00:00-05:00 — that model run already assimilated
    them). See _is_leaked; the count skipped is reported as n_leaked_skipped.
  - the forecast OF RECORD for a date is the earliest one logged, so a better-informed
    same-day re-run cannot replace it.

Reads offline from the logs (no network):
  logs/<lake>_forecast.jsonl  — the predictive distribution (mean_kn + deciles q_kn)
  logs/<lake>_diffs.jsonl     — the matched measured actuals (actual_kn per hour)

CLI:
  python lib/verify.py            # run the self-test suite (correctness + discrimination)
  python lib/verify.py <lake>     # print the real out-of-sample scorecard for one lake
  python lib/verify.py all        # scorecard for every lake

CRPS (Continuous Ranked Probability Score) generalizes MAE to probabilistic/ensemble
forecasts: for a point forecast it EQUALS the absolute error; for a distribution it also
rewards honest spread (penalizes over-confidence, rewards well-placed uncertainty). Units
are knots, lower is better. Skill score SS = 1 − CRPS/CRPS_baseline; SS > 0 beats it.
"""
import os, sys, json, math, datetime
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc
import postproc

QLEVELS = (10, 25, 50, 75, 90)   # the persisted decile levels (percent)
_ND = NormalDist()
_SQRT_PI = math.sqrt(math.pi)
LOW_CONF_DAYS = 10               # below this many days, flag the scorecard as low-confidence


# ---------------------------------------------------------------- CRPS estimators
def crps_ensemble(members, y):
    """Exact CRPS of an m-member ensemble vs observation y (Hersbach decomposition):
        CRPS = (1/m)Σ|xi−y| − (1/2m²)ΣΣ|xi−xj|
    Computed in O(m log m) via the sorted identity ΣΣ|xi−xj| = 2Σ(2i−m−1)·x(i).
    A 1-member ensemble reduces to |x−y|, so a POINT forecast's CRPS equals its absolute
    error. This one estimator scores the model, persistence and climatology alike."""
    m = len(members)
    if m == 0:
        return None
    xs = sorted(members)
    term1 = sum(abs(x - y) for x in xs) / m
    if m == 1:
        return term1
    s = 0.0
    for i, x in enumerate(xs, start=1):
        s += (2 * i - m - 1) * x
    term2 = s / (m * m)          # = (1/2m²)·ΣΣ|xi−xj|
    return term1 - term2


def crps_gaussian(mu, sigma, y):
    """Closed-form CRPS of N(mu,sigma) vs y (Gneiting & Raftery 2007). Cross-check /
    fallback when only mean+spread are available."""
    if sigma <= 0:
        return abs(mu - y)
    w = (y - mu) / sigma
    return sigma * (w * (2 * _ND.cdf(w) - 1) + 2 * _ND.pdf(w) - 1 / _SQRT_PI)


def crps_quantile(levels01, values, y):
    """CRPS as 2·∫₀¹ pinball_α dα, trapezoid over the probability grid `levels01` (in
    (0,1)). Independent cross-check of crps_ensemble; accurate with a dense grid."""
    def pinball(alpha, q):
        u = y - q
        return u * alpha if u >= 0 else u * (alpha - 1)   # max(α·u, (α−1)·u)
    xs = sorted(zip(levels01, values))
    s = 0.0
    for (a0, q0), (a1, q1) in zip(xs, xs[1:]):
        s += 0.5 * (pinball(a0, q0) + pinball(a1, q1)) * (a1 - a0)
    return 2 * s


# ---------------------------------------------------------------- forecast -> members
def forecast_members(hourrec):
    """Turn a logged forecast hour into ensemble members for scoring: prefer the
    persisted deciles q_kn; else derive deciles from Gaussian(mean, spread); else fall
    back to the point mean (a 1-member ensemble → CRPS == absolute error)."""
    q = hourrec.get("q_kn")
    if q:
        return [float(v) for v in q.values()]
    mean = hourrec.get("mean_kn")
    sd = hourrec.get("spread_kn")
    if mean is not None and sd:
        return [mean + _ND.inv_cdf(p / 100.0) * sd for p in QLEVELS]
    return [mean] if mean is not None else []


# ---------------------------------------------------------------- log loading
def _load_forecasts(lake):
    """{date: {hour: hourrec}} of the forecast OF RECORD for each date.

    Keeps the EARLIEST record per date (by run_stamp) — a later same-day re-run knows
    more and must not be able to launder itself into the verification set. Each hour
    carries `_run_stamp` so evaluate() can drop hours that had already elapsed when the
    forecast was issued."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    best = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("hourly"):
                continue
            d, stamp = r["date"], r.get("run_stamp") or ""
            if d not in best or stamp < best[d][0]:
                best[d] = (stamp, r)
    out = {}
    for d, (stamp, r) in best.items():
        out[d] = {h["hour"]: {**h, "_run_stamp": stamp} for h in r["hourly"]}
    return out


def _is_leaked(date, hour, run_stamp):
    """True if `hour` on `date` had already elapsed when the forecast was issued.

    A forecast issued at 06:00 does not 'predict' 00:00-05:00 of the same day: the model
    run behind it has already assimilated those observations, so scoring them would be a
    hindcast. Only hours strictly after the issue time count as a forecast. A missing or
    unparseable stamp is treated as leaked (fail closed — never flatter the model)."""
    if not run_stamp:
        return True
    try:
        issued = datetime.datetime.fromisoformat(run_stamp)
    except Exception:
        return True
    if issued.date().isoformat() < date:
        return False                       # issued on an earlier day: a genuine forecast
    if issued.date().isoformat() > date:
        return True                        # issued after the fact entirely
    return hour <= issued.hour             # same day: only later hours are forecasts


def _load_actuals(lake):
    """{date: {hour: actual_kn}} of measured truth from the diffs log."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
    out = {}
    if os.path.exists(path):
        for line in open(path):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("actual_kn") is not None:
                out.setdefault(d["date"], {})[d["hour"]] = d["actual_kn"]
    return out


def _prev_day(date):
    return (datetime.date.fromisoformat(date) - datetime.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- evaluation
def evaluate(lake, forecasts=None, actuals=None):
    """Leak-free rolling verification. Returns a scorecard dict. `forecasts`/`actuals`
    can be injected for tests; otherwise read from the logs."""
    forecasts = _load_forecasts(lake) if forecasts is None else forecasts
    actuals = _load_actuals(lake) if actuals is None else actuals
    recs = []
    n_leaked = 0
    for date in sorted(forecasts):
        if date not in actuals:
            continue
        for hour, hr in forecasts[date].items():
            if hour not in actuals[date]:
                continue
            if _is_leaked(date, hour, hr.get("_run_stamp")):
                n_leaked += 1              # already-elapsed hour: not a forecast, don't score it
                continue
            y = actuals[date][hour]
            mean = hr.get("mean_kn")
            if mean is None:
                continue
            crps_m = crps_ensemble(forecast_members(hr), y)
            # persistence baseline: previous day's measured wind at this hour (a point)
            pv = actuals.get(_prev_day(date), {}).get(hour)
            crps_p = abs(pv - y) if pv is not None else None
            # climatology baseline: measured winds at this hour on STRICTLY earlier days
            clim = [actuals[dd][hour] for dd in actuals if dd < date and hour in actuals[dd]]
            crps_c = crps_ensemble(clim, y) if len(clim) >= 3 else None
            recs.append({"date": date, "hour": hour, "regime": hr.get("regime", "?"),
                         "y": y, "mean": mean, "crps": crps_m, "crps_pers": crps_p,
                         "crps_clim": crps_c, "ae": abs(mean - y),
                         "se": (mean - y) ** 2, "signed": mean - y})
    sc = _summarize(lake, recs)
    sc["n_leaked_skipped"] = n_leaked
    return sc


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _skill(recs, ref_key):
    """Skill score 1 − mean(CRPS_model)/mean(CRPS_ref) over the subset where BOTH exist."""
    pairs = [(r["crps"], r[ref_key]) for r in recs
             if r["crps"] is not None and r[ref_key] is not None]
    if not pairs:
        return None
    m = sum(a for a, _ in pairs)
    b = sum(x for _, x in pairs)
    return (1 - m / b) if b else None


def _summarize(lake, recs):
    if not recs:
        return {"lake": lake, "n_pairs": 0, "n_days": 0, "recs": recs}

    def grp(key):
        g = {}
        for r in recs:
            g.setdefault(r[key], []).append(r)
        return {k: {"n": len(v), "crps": _mean([x["crps"] for x in v]),
                    "mae": _mean([x["ae"] for x in v])} for k, v in sorted(g.items())}

    return {
        "lake": lake, "n_pairs": len(recs),
        "n_days": len({r["date"] for r in recs}),
        "crps": _mean([r["crps"] for r in recs]),
        "mae": _mean([r["ae"] for r in recs]),
        "rmse": (_mean([r["se"] for r in recs]) ** 0.5),
        "bias": _mean([r["signed"] for r in recs]),
        "crps_pers": _mean([r["crps_pers"] for r in recs]),
        "crps_clim": _mean([r["crps_clim"] for r in recs]),
        "ss_pers": _skill(recs, "crps_pers"),
        "ss_clim": _skill(recs, "crps_clim"),
        "by_regime": grp("regime"), "by_hour": grp("hour"), "recs": recs,
    }


def mean_crps(recs, since=None, until=None):
    """Mean CRPS over scored records, optionally restricted to [since, until).
    Single authority for 'how good were we over this window' — used to measure whether
    an analyst hypothesis actually helped after it was issued."""
    xs = [r["crps"] for r in recs if r.get("crps") is not None
          and (since is None or r["date"] >= since)
          and (until is None or r["date"] < until)]
    return sum(xs) / len(xs) if xs else None


# ---------------------------------------------------------------- backtest (B2)
N_MIN_BACKTEST_DAYS = 10   # replayable days required before a change may be APPLIED


def _replayable(date, hour, h, actuals):
    """An hour can be replayed only if it was measured, carries the captured
    classification inputs, has a raw value, and was a genuine forecast at issue time."""
    return (hour in actuals.get(date, {}) and bool(h.get("inputs"))
            and h.get("raw_kn") is not None
            and not _is_leaked(date, hour, h.get("_run_stamp")))


def _walk_forward(lake, params, forecasts, actuals, dates):
    """Score a parameter set WALK-FORWARD: each day is predicted with a bias model fit
    only on strictly EARLIER days, then that day is learned from. Returns the per-hour
    absolute errors.

    Why not hold one pre-fit bias model fixed across both arms (the earlier approach):
    the buckets were fit under the CURRENT regime labels, so an arm whose labels happened
    to land on better-calibrated buckets scored well regardless of whether the parameter
    was actually better — a harmful change could score near-perfect. Refitting per arm
    makes each arm pay the true cost of its own labelling, and makes the score genuinely
    out-of-sample rather than a replay of the days the model was trained on."""
    bias = {"buckets": {}}
    errs = []
    for date in dates:
        learned_today = []
        for hour, h in sorted(forecasts[date].items()):
            if not _replayable(date, hour, h, actuals):
                continue
            raw_s = h.get("raw_kn")
            raw_g = h.get("raw_gust_kn") or raw_s
            y = actuals[date][hour]
            regime, cs, _, _ = fc.replay_hour(lake, hour, fc.row_from_logged(h),
                                              h.get("dp"), {"dtheta": h.get("dtheta")},
                                              raw_s, raw_g, params=params, bias=bias)
            errs.append(abs(cs - y))            # predicted knowing only up to yesterday
            learned_today.append((regime, hour, raw_s, y))
        for regime, hour, raw_s, y in learned_today:   # ...only now learn from today
            st = bias["buckets"].setdefault(fc._bucket_key(regime, hour),
                                            postproc.new_state())
            postproc.update(st, raw_s, y)
    return errs


def backtest(lake, param, value, forecasts=None, actuals=None, params=None, bias=None):
    """Walk-forward replay of every replayable logged day under `param=value`, versus the
    same days under the CURRENT parameters.

    Returns {"crps_current","crps_candidate","crps_ss","n_days","n_pairs","enough_data"}.
    crps_ss > 0 means the candidate genuinely reduced OUT-OF-SAMPLE error: each arm refits
    its own bias model day by day (see _walk_forward), so the comparison isolates the
    parameter instead of rewarding whichever arm inherited better-calibrated buckets.
    Hours that lack captured `inputs`, or that had already elapsed at issue time, are not
    replayable; n_days counts the days that actually contributed.

    `bias` is accepted for signature compatibility but deliberately IGNORED — a fixed
    pre-fit bias model is exactly what made the old comparison unsound."""
    forecasts = _load_forecasts(lake) if forecasts is None else forecasts
    actuals = _load_actuals(lake) if actuals is None else actuals
    base = dict(params or fc.PARAMS)
    if param not in base:
        return {"error": f"unknown param {param}"}
    cand = dict(base)
    cand[param] = value

    dates = [d for d in sorted(forecasts) if d in actuals]
    n_days = sum(1 for d in dates
                 if any(_replayable(d, h, forecasts[d][h], actuals) for h in forecasts[d]))
    cur = _walk_forward(lake, base, forecasts, actuals, dates)
    alt = _walk_forward(lake, cand, forecasts, actuals, dates)
    if not cur:
        return {"crps_current": None, "crps_candidate": None, "crps_ss": None,
                "n_days": 0, "n_pairs": 0, "enough_data": False}
    c0 = sum(cur) / len(cur)
    c1 = sum(alt) / len(alt)
    return {"crps_current": round(c0, 3), "crps_candidate": round(c1, 3),
            "crps_ss": (round(1 - c1 / c0, 4) if c0 else None),
            "n_days": n_days, "n_pairs": len(cur),
            "enough_data": n_days >= N_MIN_BACKTEST_DAYS}


# ---------------------------------------------------------------- reporting
def _f(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _ss(x):
    return "n/a" if x is None else f"{x:+.2f}"


def format_scorecard(sc, detail=True):
    if sc["n_pairs"] == 0:
        why = (f" ({sc['n_leaked_skipped']} hour(s) excluded — already elapsed when the "
               f"forecast was issued, so scoring them would be a hindcast)"
               if sc.get("n_leaked_skipped") else "")
        return f"### Verification — {sc['lake']}: no scorable forecast/obs pairs yet.{why}"
    conf = "" if sc["n_days"] >= LOW_CONF_DAYS else \
        f"   ⚠ LOW CONFIDENCE — only {sc['n_days']} day(s) of data"
    leak = (f"  ({sc['n_leaked_skipped']} already-elapsed hour(s) excluded as hindcast)"
            if sc.get("n_leaked_skipped") else "")
    L = [f"### Verification — {sc['lake']}  "
         f"({sc['n_pairs']} hourly pairs over {sc['n_days']} day(s)){conf}{leak}",
         f"  model:  CRPS {_f(sc['crps'])} kn | MAE {_f(sc['mae'])} | "
         f"RMSE {_f(sc['rmse'])} | bias {_f(sc['bias'])}",
         f"  baseline CRPS:  persistence {_f(sc['crps_pers'])} | climatology {_f(sc['crps_clim'])}",
         f"  skill (SS>0 beats it):  vs persistence {_ss(sc['ss_pers'])} | "
         f"vs climatology {_ss(sc['ss_clim'])}"]
    if detail:
        byr = "; ".join(f"{k} {_f(v['crps'])}({v['n']}h)" for k, v in sc["by_regime"].items())
        L.append(f"  CRPS by regime: {byr}")
    L.append("  (CRPS in knots, lower better; a point forecast's CRPS == its MAE)")
    return "\n".join(L)


# ---------------------------------------------------------------- self-tests
def _gaussian_members(mu, sd, n=200):
    """A deterministic, well-spread 'ensemble' = n stratified quantiles of N(mu,sd)."""
    return [mu + _ND.inv_cdf((i + 0.5) / n) * sd for i in range(n)]


def _selftest_correctness():
    import numpy as np
    rng = np.random.default_rng(0)
    # A1 — point forecast CRPS == absolute error
    for p, y in [(5.0, 5.0), (3.0, 7.0), (10.0, 2.0)]:
        assert abs(crps_ensemble([p], y) - abs(p - y)) < 1e-9
    # A2 — non-negativity on random ensembles
    for _ in range(300):
        mem = list(rng.normal(5, 2, size=int(rng.integers(1, 12))))
        assert crps_ensemble(mem, float(rng.normal(5, 3))) >= -1e-9
    # A3 — calibration behaviour (why CRPS > MAE)
    y = 5.0
    assert crps_ensemble(_gaussian_members(y + 1, 2), y) < \
           crps_ensemble(_gaussian_members(y + 3, 2), y)          # median toward y helps
    assert crps_ensemble(_gaussian_members(y, 2), y) > crps_ensemble([y], y)  # over-dispersion costs
    assert crps_ensemble(_gaussian_members(y + 5, 3), y) < crps_ensemble([y + 5], y)  # spread rescues a wrong point
    # A4 — three independent routes agree on a Gaussian (+ Monte-Carlo)
    mu, sd, yv = 6.0, 2.5, 4.0
    dense = [(i + 0.5) / 400 for i in range(400)]
    qvals = [mu + _ND.inv_cdf(p) * sd for p in dense]
    r_quant = crps_quantile(dense, qvals, yv)
    r_gauss = crps_gaussian(mu, sd, yv)
    r_ens = crps_ensemble(qvals, yv)
    r_mc = crps_ensemble(list(rng.normal(mu, sd, size=20000)), yv)
    assert abs(r_quant - r_gauss) < 0.02, (r_quant, r_gauss)
    assert abs(r_ens - r_gauss) < 0.02, (r_ens, r_gauss)
    assert abs(r_mc - r_gauss) < 0.06, (r_mc, r_gauss)
    # A5 — perfect forecast -> 0
    assert crps_ensemble([y, y, y], y) < 1e-9
    return {"gauss": r_gauss, "quant": r_quant, "ens": r_ens, "mc": r_mc}


def _selftest_discrimination():
    """A well-calibrated forecaster must beat a biased/over-confident one AND the
    climatology/persistence baselines; a bad one must lose to climatology."""
    import numpy as np
    rng = np.random.default_rng(1)
    hours = [10, 11, 12, 13, 14, 15]
    base = {10: 4, 11: 6, 12: 9, 13: 11, 14: 10, 15: 7}   # diurnal thermal pattern
    dates = [(datetime.date(2026, 1, 1) + datetime.timedelta(days=k)).isoformat()
             for k in range(40)]
    actuals, good, bad = {}, {}, {}
    for d in dates:
        actuals[d], good[d], bad[d] = {}, {}, {}
        stamp = (datetime.date.fromisoformat(d) - datetime.timedelta(days=1)).isoformat() + "T18:00"
        for h in hours:
            truth = max(0.0, base[h] + float(rng.normal(0, 2.0)))   # day-to-day variability
            actuals[d][h] = round(truth, 1)
            gm = truth + float(rng.normal(0, 0.8))                  # good: tracks truth, honest spread
            good[d][h] = {"hour": h, "regime": "thermal", "mean_kn": gm, "_run_stamp": stamp,
                          "q_kn": {str(p): round(gm + _ND.inv_cdf(p / 100) * 1.3, 1) for p in QLEVELS}}
            bm = truth + 4.0                                        # bad: +4 bias, over-confident
            bad[d][h] = {"hour": h, "regime": "thermal", "mean_kn": bm, "_run_stamp": stamp,
                         "q_kn": {str(p): round(bm + _ND.inv_cdf(p / 100) * 0.3, 1) for p in QLEVELS}}
    sg = evaluate("good", good, actuals)
    sb = evaluate("bad", bad, actuals)
    assert sg["crps"] < sb["crps"], (sg["crps"], sb["crps"])
    assert sg["ss_clim"] is not None and sg["ss_clim"] > 0, sg["ss_clim"]
    assert sg["ss_pers"] is not None and sg["ss_pers"] > 0, sg["ss_pers"]
    assert sb["ss_clim"] is not None and sb["ss_clim"] < 0, sb["ss_clim"]
    return sg, sb


def _synthetic_days(specs, n_days=16, hour=13, raw=4.0):
    """Build replayable logged days at one hour. `specs` is a list of
    (spd925, cloud, truth_multiple) cycled across days, so the SAME hour sees different
    conditions on different days — which is what makes the regime label (and therefore
    the threshold under test) actually matter. Without that, the per-(regime x hour)
    bucket would separate the cases on hour alone and no threshold could ever help.
    Every hour is stamped as issued 18:00 the PREVIOUS day, i.e. a genuine day-ahead
    forecast, so the lead-time filter keeps it."""
    forecasts, actuals = {}, {}
    for k in range(n_days):
        day = datetime.date(2026, 5, 1) + datetime.timedelta(days=k)
        d = day.isoformat()
        stamp = (day - datetime.timedelta(days=1)).isoformat() + "T18:00"
        spd925, cloud, mult = specs[k % len(specs)]
        forecasts[d] = {hour: {"hour": hour, "raw_kn": raw, "raw_gust_kn": raw * 2,
                               "mean_kn": raw, "dtheta": 0.0, "dp": 0.0,
                               "_run_stamp": stamp,
                               "inputs": {"spd925": spd925, "spd850": 2.0,
                                          "dir850": 10, "cloud": cloud}}}
        actuals[d] = {hour: raw * mult}
    return forecasts, actuals


def _selftest_backtest():
    base = dict(fc._DEFAULTS)
    # GOOD: cloud-42 days truly behave like gradient (2x), cloud-30 days like thermal (1x).
    # At THERMAL_CLOUD_MAX=45 both land in one bucket and get a blended, wrong correction;
    # lowering to 38 separates them. A sound backtest must reward that.
    f1, a1 = _synthetic_days([(3.0, 42, 2.0), (3.0, 30, 1.0)])
    good = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, f1, a1, base)
    # BAD: base ALREADY separates these correctly (spd925 15 >= 12 -> gradient, 2x;
    # spd925 3 -> thermal, 1x). Raising the gradient threshold to 18 collapses both into
    # one bucket. A sound backtest must punish that.
    f2, a2 = _synthetic_days([(15.0, 30, 2.0), (3.0, 30, 1.0)])
    bad = backtest("walchensee", "GRADIENT_925_KN", 18, f2, a2, base)
    assert good["crps_ss"] is not None and good["crps_ss"] > 0, good
    assert bad["crps_ss"] is not None and bad["crps_ss"] < 0, bad
    assert good["enough_data"] and good["n_days"] == 16, good
    # thin history must be reported as insufficient, never silently accepted
    thin_f = {k: f1[k] for k in sorted(f1)[:3]}
    thin_a = {k: a1[k] for k in sorted(a1)[:3]}
    thin = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, thin_f, thin_a, base)
    assert not thin["enough_data"] and thin["n_days"] == 3, thin
    # rows without captured inputs are skipped rather than silently mis-scored
    noinp = {d: {h: {kk: vv for kk, vv in hr.items() if kk != "inputs"}
                 for h, hr in hh.items()} for d, hh in f1.items()}
    empty = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, noinp, a1, base)
    assert empty["n_pairs"] == 0 and not empty["enough_data"], empty
    # hours already elapsed at issue time must not be replayable either
    leaked = {d: {h: {**hr, "_run_stamp": d + "T20:00"} for h, hr in hh.items()}
              for d, hh in f1.items()}
    lk = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, leaked, a1, base)
    assert lk["n_pairs"] == 0, lk
    return good, bad, thin


def _selftest_leakfilter():
    """The lead-time filter must drop already-elapsed hours and keep genuine forecasts."""
    assert _is_leaked("2026-05-02", 3, "2026-05-02T06:00")        # 03:00 already past at 06:00
    assert not _is_leaked("2026-05-02", 9, "2026-05-02T06:00")    # 09:00 still ahead
    assert not _is_leaked("2026-05-02", 0, "2026-05-01T18:00")    # issued day before
    assert _is_leaked("2026-05-02", 9, "2026-05-03T06:00")        # issued after the fact
    assert _is_leaked("2026-05-02", 9, None)                      # unknown -> fail closed
    assert _is_leaked("2026-05-02", 9, "garbage")                 # unparseable -> fail closed
    # evaluate() must exclude them and say so
    f, a = _synthetic_days([(3.0, 30, 1.0)], n_days=4)
    hind = {d: {h: {**hr, "_run_stamp": d + "T23:00"} for h, hr in hh.items()}
            for d, hh in f.items()}
    sc = evaluate("walchensee", hind, a)
    assert sc["n_pairs"] == 0 and sc["n_leaked_skipped"] == 4, sc
    assert "hindcast" in format_scorecard(sc)
    ok = evaluate("walchensee", f, a)
    assert ok["n_pairs"] == 4 and ok["n_leaked_skipped"] == 0, ok
    # earliest record per date wins, so a later re-run cannot launder the issue time
    import tempfile
    tmp = tempfile.mkdtemp()
    old_dir, wd.LOG_DIR = wd.LOG_DIR, tmp
    try:
        p = os.path.join(tmp, "zz_forecast.jsonl")
        with open(p, "w") as fh:
            for stamp in ("2026-05-02T20:00", "2026-05-01T18:00"):   # late one written FIRST
                fh.write(json.dumps({"date": "2026-05-02", "run_stamp": stamp,
                                     "hourly": [{"hour": 9, "mean_kn": 5.0}]}) + "\n")
        got = _load_forecasts("zz")
        assert got["2026-05-02"][9]["_run_stamp"] == "2026-05-01T18:00", got
    finally:
        wd.LOG_DIR = old_dir
    return True


def _selftest_realdata():
    """Returns how many lakes actually had data to assert on, so the caller can report it
    — a silent 'PASS' having checked nothing is exactly the false confidence to avoid."""
    checked = 0
    for lake in fc.LAKES:
        sc = evaluate(lake)
        assert isinstance(sc.get("n_leaked_skipped"), int), lake   # always meaningful
        assert format_scorecard(sc)                                # never raises
        if sc["n_pairs"] > 0:
            checked += 1
            assert sc["crps"] is not None and math.isfinite(sc["crps"]), sc
            assert sc["mae"] is not None and sc["rmse"] is not None, sc
            if sc["n_days"] < LOW_CONF_DAYS:
                assert "LOW CONFIDENCE" in format_scorecard(sc)   # must not over-claim
    return checked


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg in (None, "selftest", "test"):
        print("=== verify.py self-tests ===")
        r = _selftest_correctness()
        print(f"A. correctness invariants ..... PASS  "
              f"(3-route CRPS agree: gauss {r['gauss']:.3f} ≈ quant {r['quant']:.3f} "
              f"≈ ens {r['ens']:.3f} ≈ MC {r['mc']:.3f})")
        g, b = _selftest_discrimination()
        print(f"B. discrimination ............. PASS  "
              f"(good CRPS {g['crps']:.2f} < bad {b['crps']:.2f}; "
              f"good SS vs climo {g['ss_clim']:+.2f} / persist {g['ss_pers']:+.2f}; "
              f"bad SS vs climo {b['ss_clim']:+.2f})")
        _selftest_leakfilter()
        print("C. lead-time leak filter ...... PASS  "
              "(already-elapsed hours excluded; earliest record per date wins)")
        gd, bd, th = _selftest_backtest()
        print(f"D. backtest gate .............. PASS  "
              f"(good param SS {gd['crps_ss']:+.2f} over {gd['n_days']}d; "
              f"bad param SS {bd['crps_ss']:+.2f}; thin history correctly "
              f"'{'insufficient' if not th['enough_data'] else 'ENOUGH?!'}')")
        n_checked = _selftest_realdata()
        print(f"E. real-data honesty .......... PASS  ({n_checked} lake(s) with data asserted)")
        print("ALL SELF-TESTS PASSED")
    else:
        for lake in (list(fc.LAKES) if arg == "all" else [arg]):
            print(format_scorecard(evaluate(lake)))
