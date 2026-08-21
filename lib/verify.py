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
    them). See is_leaked; the count skipped is reported as n_leaked_skipped.
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
import os, sys, json, math, random, datetime
from statistics import NormalDist

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc
import postproc
import climatology

QLEVELS = (10, 25, 50, 75, 90)   # the persisted decile levels (percent)
_ND = NormalDist()
_SQRT_PI = math.sqrt(math.pi)
LOW_CONF_DAYS = 10               # below this many days, flag the scorecard as low-confidence
LEAD_BINS = ((0, 60, "0–1h"), (60, 180, "1–3h"), (180, 360, "3–6h"),
             (360, 720, "6–12h"), (720, 1441, "12–24h"),
             (1440, 2881, "24–48h"), (2880, 4321, "48–72h"),
             (4320, 5761, "72–96h"))


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


def hourly_forecast_of_record(lake):
    """Frozen measured rows from timestamped hourly records, selected by valid time."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
    if not os.path.exists(path):
        return []
    records = []
    for line in open(path):
        try:
            r = json.loads(line)
            r["_issued"] = datetime.datetime.fromisoformat(r["issue_time"])
            records.append(r)
        except Exception:
            continue
    by_valid = {}
    for rec in records:
        for row in rec.get("hourly", []):
            try:
                valid = datetime.datetime.fromisoformat(row["valid_time"])
            except Exception:
                continue
            if rec["_issued"] >= valid or row.get("measured_kn") is None or row.get("legacy_calendar_backfill"):
                continue
            old = by_valid.get(row["valid_time"])
            if old is None or rec["_issued"] > old[0]:
                by_valid[row["valid_time"]] = (rec["_issued"], row)
    return [(vt, issue, row) for vt, (issue, row) in sorted(by_valid.items())]


def evaluate_hourly(lake):
    """Hourly forecast-of-record CRPS/MAE with lead-time bins.

    Baselines are intentionally omitted until hourly observations have enough immutable
    history to construct them without mixing legacy daily truth sources.
    """
    recs = []
    for vt, issue, row in hourly_forecast_of_record(lake):
        y, mean = row.get("measured_kn"), row.get("mean_kn")
        if y is None or mean is None:
            continue
        lead = row.get("lead_minutes")
        if lead is None:
            lead = max(0, round((datetime.datetime.fromisoformat(vt) - issue).total_seconds() / 60))
        recs.append({"valid_time": vt, "date": vt[:10], "hour": int(vt[11:13]),
                     "lead_minutes": lead, "regime": row.get("regime", "?"),
                     "y": y, "mean": mean, "crps": crps_ensemble(forecast_members(row), y),
                     "crps_pers": None, "crps_clim": None,
                     "ae": abs(mean-y), "se": (mean-y)**2, "signed": mean-y})
    overall = _summarize(lake, recs)
    overall["by_lead"] = {name: _summarize(lake, [r for r in recs if lo <= r["lead_minutes"] < hi])
                          for lo, hi, name in LEAD_BINS}
    overall["hourly"] = True
    return overall


def _hourly_walk_forward(lake, params, rows):
    bias, errs = {"buckets": {}}, []
    for vt, _issue, row in rows:
        y, raw = row.get("measured_kn"), row.get("raw_kn")
        if y is None or raw is None or not row.get("inputs"):
            continue
        hour = int(vt[11:13])
        regime, cs, _, _, _ = fc.replay_hour(
            lake, hour, fc.row_from_logged(row), row.get("dp"), {"dtheta": row.get("dtheta")},
            raw, row.get("raw_gust_kn") or raw, params=params, bias=bias,
            gust_ceiling_kn=row.get("gust_ceiling_kn"))
        errs.append((vt[:10], abs(cs - y)))
        st = bias["buckets"].setdefault(fc._bucket_key(regime, hour), postproc.new_state())
        postproc.update(st, raw, y)
    return errs


def hourly_backtest(lake, param, value, params=None):
    """Paired hourly walk-forward MAE gate for an hourly parameter proposal."""
    base = dict(params or fc.params_for(lake))
    if param not in base:
        return {"error": f"unknown param {param}"}
    cand = dict(base); cand[param] = value
    rows = hourly_forecast_of_record(lake)
    cur, alt = _hourly_walk_forward(lake, base, rows), _hourly_walk_forward(lake, cand, rows)
    if not cur:
        return {"mae_current": None, "mae_candidate": None, "mae_skill": None,
                "n_days": 0, "n_pairs": 0, "enough_data": False,
                "delta_kn": None, "ci_lo": None, "ci_hi": None, "significant": False}
    by_day = {}
    for (d, e0), (_, e1) in zip(cur, alt):
        by_day.setdefault(d, []).append(e1 - e0)
    diffs = [x for xs in by_day.values() for x in xs]
    delta = sum(diffs) / len(diffs)
    lo, hi = _block_bootstrap_ci(by_day)
    c0, c1 = sum(x for _, x in cur) / len(cur), sum(x for _, x in alt) / len(alt)
    nd = len(by_day)
    return {"mae_current": round(c0, 3), "mae_candidate": round(c1, 3),
            "mae_skill": round(1 - c1 / c0, 4) if c0 else None,
            "n_days": nd, "n_pairs": len(cur),
            "enough_data": nd >= N_MIN_BACKTEST_DAYS and len(cur) >= N_MIN_BACKTEST_PAIRS,
            "delta_kn": round(delta, 4), "ci_lo": None if lo is None else round(lo, 4),
            "ci_hi": None if hi is None else round(hi, 4),
            "significant": bool(hi is not None and hi < 0 and delta <= -MIN_EFFECT_KN)}


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
            d, raw = r["date"], r.get("run_stamp")
            # Rank by the PARSED instant, not the raw string: stamps can carry different
            # offsets, so lexical order is not chronological. An unusable stamp ranks LAST
            # so it can never beat a properly stamped record into the verification set.
            rank = _rank(raw)
            if d not in best or rank < best[d][0]:
                best[d] = (rank, raw, r)
    out = {}
    for d, (_when, raw, r) in best.items():   # NOT '_rank': that shadows the module fn
        out[d] = {h["hour"]: {**h, "_run_stamp": raw,
                              "_backfilled": bool(r.get("backfilled"))}
                  for h in r["hourly"]}
    return out


def parse_stamp(run_stamp):
    """A run_stamp as an Europe/Berlin-local datetime, or None if unusable.

    Forecast hours are always Europe/Berlin (Open-Meteo is queried that way), so the issue
    time must be compared in that same frame. Stamps written from 2026-08-03 on carry an
    explicit offset. LEGACY naive stamps are assumed UTC: that is where the authoritative
    cloud runs produced them, and for a laptop-made stamp the assumption only shifts the
    issue time LATER, i.e. it drops more hours. Erring toward dropping never flatters the
    model, which is the direction a referee must fail in."""
    if not run_stamp:
        return None
    try:
        dt = datetime.datetime.fromisoformat(run_stamp)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)      # legacy: assume UTC
    return dt.astimezone(wd.BERLIN)


_LAST_RANK = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)


def _rank(run_stamp):
    """Sort key for 'which run came first'. In UTC, never Berlin wall-clock: two aware
    datetimes in the same zone compare on naive fields and ignore `fold`, so 02:30 CEST
    and 02:30 CET would tie on the autumn changeover. An unusable stamp ranks LAST so it
    can never win the forecast-of-record slot."""
    when = parse_stamp(run_stamp)
    return when.astimezone(datetime.timezone.utc) if when is not None else _LAST_RANK


def forecast_of_record(lake, date):
    """THE single authority for 'which logged forecast counts as the one issued for `date`'.

    Returns (record, run_stamp) or (None, None). Both the verifier and the learner must
    agree on this; when learn.py kept its own copy of the ranking logic the two disagreed,
    and the copy still had the string-compare / stampless-record bug this module had
    already fixed."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    best = None
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("date") != date or not r.get("hourly"):
                continue
            rank = _rank(r.get("run_stamp"))
            if best is None or rank < best[0]:
                best = (rank, r)
    return (best[1], best[1].get("run_stamp")) if best else (None, None)


def is_leaked(date, hour, run_stamp):
    """True if `hour` on `date` had already elapsed when the forecast was issued.

    A forecast issued at 06:00 does not 'predict' 00:00-05:00 of the same day: the model
    run behind it has already assimilated those observations, so scoring them would be a
    hindcast. Only hours strictly after the issue time count as a forecast. A missing or
    unparseable stamp is treated as leaked (fail closed — never flatter the model)."""
    issued = parse_stamp(run_stamp)
    if issued is None:
        return True
    iso = issued.date().isoformat()
    if iso < date:
        return False                       # issued on an earlier day: a genuine forecast
    if iso > date:
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
    n_leaked = n_clim_archive = n_backfilled = 0
    for date in sorted(forecasts):
        if date not in actuals:
            continue
        for hour, hr in forecasts[date].items():
            if hour not in actuals[date]:
                continue
            if hr.get("_backfilled"):
                n_backfilled += 1          # reconstructed: valid for the PAIRED backtest,
                continue                   # but it would flatter a published skill score
            if is_leaked(date, hour, hr.get("_run_stamp")):
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
            # climatology baseline. Preferred: the long observational archive for this
            # (month, hour) — it is available on day one, whereas the from-logs version
            # needs >=3 prior days at the same hour and so is simply absent early on.
            # climatology.members() returns None if the archive would contain `date`
            # itself (that would be leakage), in which case we fall back to prior logs.
            cm = climatology.members(lake, date, hour)
            if cm:
                crps_c = crps_ensemble(cm, y)
                n_clim_archive += 1
            else:
                clim = [actuals[dd][hour] for dd in actuals if dd < date and hour in actuals[dd]]
                crps_c = crps_ensemble(clim, y) if len(clim) >= 3 else None
            recs.append({"date": date, "hour": hour, "regime": hr.get("regime", "?"),
                         "y": y, "mean": mean, "crps": crps_m, "crps_pers": crps_p,
                         "crps_clim": crps_c, "ae": abs(mean - y),
                         "se": (mean - y) ** 2, "signed": mean - y})
    sc = _summarize(lake, recs)
    sc["n_leaked_skipped"] = n_leaked
    sc["n_clim_from_archive"] = n_clim_archive
    sc["n_backfilled_skipped"] = n_backfilled
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
N_MIN_BACKTEST_DAYS = 10    # replayable days required before a change may be APPLIED
N_MIN_BACKTEST_PAIRS = 60   # ...and this many scored hours; days alone can be near-empty
MIN_EFFECT_KN = 0.05        # a change must reduce mean CRPS by at least this to count
BOOTSTRAP_N = 2000          # resamples for the confidence interval
BOOTSTRAP_CONF = 0.95


def _block_bootstrap_ci(by_day, n=BOOTSTRAP_N, conf=BOOTSTRAP_CONF, seed=12345):
    """Confidence interval for the mean paired difference, resampling whole DAYS.

    Resampling individual hours would badly overstate significance: hours within a day
    share one weather situation and one model run, so they are nowhere near independent.
    Resampling days (a block bootstrap) respects that structure. Deterministic seed so a
    gate decision is reproducible — the same evidence must always give the same verdict."""
    days = sorted(by_day)
    if len(days) < 2:
        return None, None
    rnd = random.Random(seed)
    k = len(days)
    means = []
    for _ in range(n):
        pool = []
        for _ in range(k):
            pool.extend(by_day[days[rnd.randrange(k)]])
        if pool:
            means.append(sum(pool) / len(pool))
    if not means:
        return None, None
    means.sort()
    lo = means[int((1 - conf) / 2 * len(means))]
    hi = means[min(len(means) - 1, int((1 + conf) / 2 * len(means)))]
    return lo, hi


def _replayable(date, hour, h, actuals):
    """An hour can be replayed only if it was measured, carries the captured
    classification inputs, has a raw value, and was a genuine forecast at issue time."""
    return (hour in actuals.get(date, {}) and bool(h.get("inputs"))
            and h.get("raw_kn") is not None
            and not is_leaked(date, hour, h.get("_run_stamp")))


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
            regime, cs, _, _, _ = fc.replay_hour(lake, hour, fc.row_from_logged(h),
                                                 h.get("dp"), {"dtheta": h.get("dtheta")},
                                                 raw_s, raw_g, params=params, bias=bias,
                                                 gust_ceiling_kn=h.get("gust_ceiling_kn"))
            errs.append((date, abs(cs - y)))    # predicted knowing only up to yesterday
            learned_today.append((regime, hour, raw_s, y))
        for regime, hour, raw_s, y in learned_today:   # ...only now learn from today
            st = bias["buckets"].setdefault(fc._bucket_key(regime, hour),
                                            postproc.new_state())
            postproc.update(st, raw_s, y)
    return errs


def rebuild_bias(lake, params, forecasts=None, actual_rows=None):
    """Fit production calibration under ``params`` from replayable history.

    A threshold change alters which scenario/hour bucket receives each observation.
    Build that replacement state *before* activation so production gets the mature state
    evaluated by the walk-forward gate instead of an empty cold start.
    """
    forecasts = _load_forecasts(lake) if forecasts is None else forecasts
    if actual_rows is None:
        actual_rows = {}
        path = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
        if os.path.exists(path):
            for line in open(path):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("actual_kn") is not None:
                    actual_rows.setdefault(row["date"], {})[row["hour"]] = row

    bias = {"alpha": 0.3, "buckets": {}, "processed_dates": []}
    for date in sorted(forecasts):
        day_rows = actual_rows.get(date, {})
        # Tests and callers may provide the verifier's compact {hour: speed} form.
        speeds = {h: (v.get("actual_kn") if isinstance(v, dict) else v)
                  for h, v in day_rows.items()}
        used = False
        for hour, hrec in sorted(forecasts[date].items()):
            if not _replayable(date, hour, hrec, {date: speeds}):
                continue
            obs = day_rows[hour]
            measured = obs.get("actual_kn") if isinstance(obs, dict) else obs
            measured_gust = obs.get("actual_gust_kn") if isinstance(obs, dict) else None
            raw = hrec["raw_kn"]
            raw_gust = hrec.get("raw_gust_kn") or raw
            scenario = fc.classify_regime(
                lake, hour, fc.row_from_logged(hrec), hrec.get("dp"),
                {"dtheta": hrec.get("dtheta")}, params)
            state = bias["buckets"].setdefault(fc._bucket_key(scenario, hour),
                                                postproc.new_state())
            postproc.update(state, raw, measured)
            postproc.update_gust(state, raw_gust, measured_gust)
            used = True
        if used:
            bias["processed_dates"].append(date)
    bias["processed_dates"] = bias["processed_dates"][-400:]
    return bias


def backtest(lake, param, value, forecasts=None, actuals=None, params=None, bias=None):
    """Walk-forward MAE replay of every replayable logged day under `param=value`, versus the
    same days under the CURRENT parameters.

    Returns {"mae_current","mae_candidate","mae_skill","n_days","n_pairs","enough_data"}.
    mae_skill > 0 means the candidate genuinely reduced OUT-OF-SAMPLE error: each arm refits
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
        return {"mae_current": None, "mae_candidate": None, "mae_skill": None,
                "n_days": 0, "n_pairs": 0, "enough_data": False,
                "delta_kn": None, "ci_lo": None, "ci_hi": None, "significant": False}
    c0 = sum(e for _, e in cur) / len(cur)
    c1 = sum(e for _, e in alt) / len(alt)

    # PAIRED comparison: the two arms scored the very same hours, so the per-hour
    # difference removes the weather entirely and leaves only the parameter's effect.
    # Comparing two independent means would drown a real effect in day-to-day variance.
    by_day = {}
    for (d, e0), (_, e1) in zip(cur, alt):
        by_day.setdefault(d, []).append(e1 - e0)      # negative = candidate is better
    diffs = [x for v in by_day.values() for x in v]
    delta = sum(diffs) / len(diffs)
    lo, hi = _block_bootstrap_ci(by_day)
    # significant only if the WHOLE interval sits below zero, and the effect is big
    # enough to be worth acting on rather than merely non-zero
    sig = (hi is not None and hi < 0 and delta <= -MIN_EFFECT_KN)
    return {"mae_current": round(c0, 3), "mae_candidate": round(c1, 3),
            "mae_skill": (round(1 - c1 / c0, 4) if c0 else None),
            "n_days": n_days, "n_pairs": len(cur),
            "enough_data": n_days >= N_MIN_BACKTEST_DAYS and len(cur) >= N_MIN_BACKTEST_PAIRS,
            "delta_kn": round(delta, 4),
            "ci_lo": None if lo is None else round(lo, 4),
            "ci_hi": None if hi is None else round(hi, 4),
            "significant": bool(sig)}


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
         f"  baseline CRPS:  persistence {_f(sc['crps_pers'])} | climatology {_f(sc['crps_clim'])}"
         + (f"  [{sc['n_clim_from_archive']}/{sc['n_pairs']} hrs from the on-lake archive]"
            if sc.get("n_clim_from_archive") else ""),
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


def _synthetic_days(specs, n_days=16, hours=(10, 11, 12, 13, 14, 15), raw=4.0):
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
        spd925, cloud, mult = specs[k % len(specs)]   # one weather situation per DAY
        forecasts[d], actuals[d] = {}, {}
        for hour in hours:
            forecasts[d][hour] = {"hour": hour, "raw_kn": raw, "raw_gust_kn": raw * 2,
                                  "mean_kn": raw, "dtheta": 0.0, "dp": 0.0,
                                  "_run_stamp": stamp,
                                  "inputs": {"spd925": spd925, "spd850": 2.0,
                                             "dir850": 10, "cloud": cloud}}
            actuals[d][hour] = raw * mult
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
    assert good["mae_skill"] is not None and good["mae_skill"] > 0, good
    assert bad["mae_skill"] is not None and bad["mae_skill"] < 0, bad
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
    # A NOISE-LEVEL improvement must be REFUSED. Before the significance test, any
    # mae_skill > 0 was accepted, so a coin-flip difference could reach production.
    import random as _r
    rnd = _r.Random(7)
    f3, a3 = _synthetic_days([(3.0, 30, 1.0)], n_days=20)
    for d in a3:                       # pure noise, no systematic difference
        for h in a3[d]:
            a3[d][h] = round(4.0 + rnd.gauss(0, 1.5), 2)
    noise = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, f3, a3, base)
    assert not noise["significant"], f"noise accepted as improvement: {noise}"
    # ...and a genuine effect is still detected
    assert good["significant"], good
    # hours already elapsed at issue time must not be replayable either
    leaked = {d: {h: {**hr, "_run_stamp": d + "T20:00"} for h, hr in hh.items()}
              for d, hh in f1.items()}
    lk = backtest("walchensee", "THERMAL_CLOUD_MAX", 38, leaked, a1, base)
    assert lk["n_pairs"] == 0, lk
    rebuilt = rebuild_bias("walchensee", {**base, "THERMAL_CLOUD_MAX": 38}, f1, a1)
    assert rebuilt["buckets"] and len(rebuilt["processed_dates"]) == 16, rebuilt
    return good, bad, thin


def _selftest_leakfilter():
    """The lead-time filter must drop already-elapsed hours and keep genuine forecasts."""
    assert is_leaked("2026-05-02", 3, "2026-05-02T06:00")        # 03:00 already past at 06:00
    assert not is_leaked("2026-05-02", 9, "2026-05-02T06:00")    # 09:00 still ahead
    assert not is_leaked("2026-05-02", 0, "2026-05-01T18:00")    # issued day before
    assert is_leaked("2026-05-02", 9, "2026-05-03T06:00")        # issued after the fact
    assert is_leaked("2026-05-02", 9, None)                      # unknown -> fail closed
    assert is_leaked("2026-05-02", 9, "garbage")                 # unparseable -> fail closed
    # TIMEZONE: forecast hours are Europe/Berlin. A naive stamp is assumed UTC (that is
    # where the cloud runs write it), so 04:20 naive == 06:20 Berlin and hours 5-6 must be
    # dropped. Comparing 'hour <= 4' instead would silently leak 2 h/day in production.
    assert is_leaked("2026-08-03", 5, "2026-08-03T04:20")
    assert is_leaked("2026-08-03", 6, "2026-08-03T04:20")
    assert not is_leaked("2026-08-03", 7, "2026-08-03T04:20")
    # an explicit offset must be honoured, not string-compared
    assert is_leaked("2026-08-03", 6, "2026-08-03T06:20+02:00")
    assert not is_leaked("2026-08-03", 7, "2026-08-03T06:20+02:00")
    # earliest-of-record must rank by INSTANT, so a later-but-lexically-smaller stamp loses
    assert parse_stamp("2026-08-03T04:20") == parse_stamp("2026-08-03T06:20+02:00")
    # evaluate() must exclude them and say so
    f, a = _synthetic_days([(3.0, 30, 1.0)], n_days=4)
    n_hours = sum(len(v) for v in f.values())          # derive, never hardcode the fixture
    hind = {d: {h: {**hr, "_run_stamp": d + "T23:00"} for h, hr in hh.items()}
            for d, hh in f.items()}
    sc = evaluate("walchensee", hind, a)
    assert sc["n_pairs"] == 0 and sc["n_leaked_skipped"] == n_hours, sc
    assert "hindcast" in format_scorecard(sc)
    ok = evaluate("walchensee", f, a)
    assert ok["n_pairs"] == n_hours and ok["n_leaked_skipped"] == 0, ok
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


def _selftest_hourly():
    import tempfile
    tmp = tempfile.mkdtemp()
    old_dir, wd.LOG_DIR = wd.LOG_DIR, tmp
    try:
        a = {"issue_time": "2026-08-11T23:55+02:00", "hourly": [
            {"valid_time": "2026-08-12T00:00:00+02:00", "mean_kn": 4.0, "measured_kn": 5.0, "lead_minutes": 5, "regime": "gradient", "q_kn": {"10":3,"50":4,"90":5}},
            {"valid_time": "2026-08-12T01:00:00+02:00", "mean_kn": 5.0, "measured_kn": 6.0, "lead_minutes": 65, "regime": "thermal", "q_kn": {"10":4,"50":5,"90":6}}]}
        a["hourly"].append({"valid_time": "2026-08-12T02:00:00+02:00", "mean_kn": 7.0, "measured_kn": 7.0, "lead_minutes": 125, "regime": "thermal", "q_kn": {"10":6,"50":7,"90":8}})
        b = {"issue_time": "2026-08-12T00:55+02:00", "hourly": [
            {"valid_time": "2026-08-12T01:00:00+02:00", "mean_kn": 6.0, "measured_kn": 6.0, "lead_minutes": 5, "regime": "thermal", "q_kn": {"10":5,"50":6,"90":7}}]}
        with open(os.path.join(tmp, "walchensee_hourly_forecast.jsonl"), "w") as f:
            f.write(json.dumps(a)+"\n"+json.dumps(b)+"\n")
        sc = evaluate_hourly("walchensee")
        assert sc["n_pairs"] == 3 and sc["by_lead"]["0–1h"]["n_pairs"] == 2 and sc["by_lead"]["1–3h"]["n_pairs"] == 1, sc
        assert round(sc["mae"], 3) == round(1/3, 3), sc
    finally:
        wd.LOG_DIR = old_dir


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
              f"(real effect Δ{gd['delta_kn']:+.2f} kn CI[{gd['ci_lo']},{gd['ci_hi']}] "
              f"significant={gd['significant']}; harmful MAE skill {bd['mae_skill']:+.2f}; "
              f"noise correctly rejected; thin history "
              f"'{'insufficient' if not th['enough_data'] else 'ENOUGH?!'}')")
        n_checked = _selftest_realdata()
        print(f"E. real-data honesty .......... PASS  ({n_checked} lake(s) with data asserted)")
        _selftest_hourly()
        print("F. hourly record verifier ..... PASS  (forecast-of-record + lead bins)")
        print("ALL SELF-TESTS PASSED")
    else:
        for lake in (list(fc.LAKES) if arg == "all" else [arg]):
            print(format_scorecard(evaluate(lake)))
