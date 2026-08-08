#!/usr/bin/env python3
"""
forecast.py — deterministic wind-forecast engine for the Bavarian lakes.

This is the SINGLE AUTHORITY for: regime classification, application of the
learned bias correction, and confidence. Both the automated 6am job (daily_run.py)
and the on-demand LLM agents should get their corrected hourly numbers from here,
so the logic lives in exactly one place.

Units: Open-Meteo is queried in knots, so everything here is in knots.
"""
import os, sys, json, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import postproc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(ROOT, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# lake -> (lat, lon, label, is_alpine_rim)
LAKES = {
    "ammersee":   (47.98, 11.13, "Ammersee",   False),
    "kochelsee":  (47.65, 11.35, "Kochelsee",  True),
    "walchensee": (47.58, 11.33, "Walchensee", True),
}

OM_VARS = ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
           "cloud_cover", "shortwave_radiation", "pressure_msl",
           "wind_speed_925hPa", "wind_direction_925hPa",
           "wind_speed_850hPa", "wind_direction_850hPa"]

# Rule-based scenario thresholds (knots / degrees). Daily RLS learning does not tune
# these values; only the separately backtest-gated tuner may change them.
FOEHN_DP_RIM = 4.0      # hPa, Bozen-Muenchen, reaches Alpine-rim valleys/lakes
FOEHN_DP_FORELAND = 8.0 # hPa, needed to reach Ammersee (rare)
FOEHN_850_KN = 7.0      # ~3.7 m/s
GRADIENT_925_KN = 12.0  # strong enough aloft to govern the surface
THERMAL_CLOUD_MAX = 45  # % total cloud
SW_SECTOR = (120, 240)  # southerly 850 hPa sector for foehn
COLD_POOL_DTHETA = 1.5  # K; Kochel-Walchensee dtheta above this = stable cold pool caps the thermal
                        # (provisional pivot ~ dry-adiabatic 2.0 K minus margin; recalibrated by learn.py)
N_MIN_OBS = 3           # matching days before a (regime×hour) correction is fully trusted/applied
BIAS_CAP_KN = 8.0       # clamp on the learned MEAN bias so one anomalous day can't swing it
# Gust output ceiling. GUST_ENS_CEIL_MULT is how far above the ensemble's OWN highest
# member a corrected gust may go — the point of a bias correction is that the model can be
# systematically wrong, so the bound must be loose enough not to defeat it, while still
# catching an order-of-magnitude error. GUST_ABS_MAX_KN is Beaufort 12: a hard physical
# backstop for a pre-alpine lake, used when no ensemble is available.
GUST_ENS_CEIL_MULT = 1.5
GUST_ABS_MAX_KN = 64.0
# How a gust guard is explained to a reader. ONE wording, used by the text table and the
# HTML, so a bounded gust always reads the same wherever it is shown.
GUST_FLAG_NOTE = {
    "gust_ratio_refused": "gust uncorrected (implausible learned ratio)",
    "gust_capped": "gust capped at ensemble ceiling",
}
BLEND_DISAGREE_KN = 6.0 # kn; range (max−min) across blend sources above this = notable disagreement

# ONE authority for how a wind speed is printed. Mean wind and gusts must use the SAME
# resolution everywhere — headline, HTML tables and text report — or the same forecast
# reads as different numbers depending on where you look.
KN_FMT = ".1f"

# ------------------------------------------------- tunable params: single source of truth
# The regime thresholds above are DEFAULTS. The live values come from config/params.json
# (written only by the backtest-gated tuner); classify_regime reads them so code, the LLM
# analyst, and any applied change all agree on one authoritative set.
CONFIG_DIR = os.path.join(ROOT, "config")
PARAMS_PATH = os.path.join(CONFIG_DIR, "params.json")
TUNABLE = ("FOEHN_DP_RIM", "FOEHN_DP_FORELAND", "FOEHN_850_KN",
           "GRADIENT_925_KN", "THERMAL_CLOUD_MAX", "COLD_POOL_DTHETA")
# sane bounds a proposed value must fall inside to ever be applied
PARAM_BOUNDS = {"FOEHN_DP_RIM": (2.0, 8.0), "FOEHN_DP_FORELAND": (5.0, 14.0),
                "FOEHN_850_KN": (3.0, 15.0), "GRADIENT_925_KN": (6.0, 20.0),
                "THERMAL_CLOUD_MAX": (20.0, 70.0), "COLD_POOL_DTHETA": (0.5, 3.0)}
_DEFAULTS = {k: globals()[k] for k in TUNABLE}


def params_path(lake=None):
    """Per-lake overrides live in their own file. A change is only ever verified against
    ONE lake's history, so it must only take effect for that lake — a single shared file
    would silently apply Walchensee's evidence to Ammersee."""
    return (os.path.join(CONFIG_DIR, f"params_{lake}.json") if lake else PARAMS_PATH)


def _overlay(base, path):
    """Merge a params file over `base`, ignoring unknown or non-numeric keys. A corrupt
    or unreadable file is reported (never silently ignored) and the base is kept."""
    name = os.path.basename(path)
    if not os.path.exists(path):
        return base, None
    # The whole parse+merge is guarded: valid JSON that is not an object (a list, number
    # or string) would otherwise raise AttributeError on .items() and take the entire
    # forecast run down, which is worse than the silent fallback this replaced.
    try:
        with open(path) as f:
            data = json.load(f)
        if data is None:
            return base, None
        if not isinstance(data, dict):
            return base, f"{name}: not a JSON object ({type(data).__name__}) — keeping previous values"
        bad = []
        merged = dict(base)     # merge into a COPY: a mid-loop error must not half-apply
        for k, v in data.items():
            if not (k in _DEFAULTS and isinstance(v, (int, float))
                    and not isinstance(v, bool)):
                bad.append(k)
                continue
            try:
                # bare NaN/Infinity parse as floats and would poison every comparison in
                # classify_regime (NaN compares False against everything). An int too large
                # for a float makes isfinite raise, so this is guarded per key rather than
                # per file — one bad key must not be reported as an unreadable file.
                ok = math.isfinite(v)
            except (OverflowError, TypeError, ValueError):
                ok = False
            lo, hi = PARAM_BOUNDS.get(k, (float("-inf"), float("inf")))
            if ok and lo <= v <= hi:        # same envelope the tuner's gate enforces
                merged[k] = v
            else:
                bad.append(k)
        base.update(merged)
        return base, (f"{name}: ignored keys {bad}" if bad else None)
    except Exception as e:
        return base, f"{name} unreadable ({type(e).__name__}) — keeping previous values"


PARAM_WARNINGS = []      # surfaced by daily_run so a broken config can't fail silently


def load_params(lake=None):
    """The tunable regime thresholds — single source of truth. Defaults, overlaid with
    config/params.json (global), overlaid with config/params_<lake>.json if `lake`."""
    p = dict(_DEFAULTS)
    warns = []
    for path in ([PARAMS_PATH] + ([params_path(lake)] if lake else [])):
        p, w = _overlay(p, path)
        if w:
            warns.append(w)
    for w in warns:
        if w not in PARAM_WARNINGS:
            PARAM_WARNINGS.append(w)
    return p


def save_params(params, lake=None):
    """Persist the full tunable set (missing keys filled from defaults). Writes the
    per-lake override file when `lake` is given."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    merged = {k: params.get(k, _DEFAULTS[k]) for k in TUNABLE}
    path = params_path(lake)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


PARAMS = load_params()   # global baseline; per-lake values come from params_for(lake)


def params_for(lake):
    """The parameters actually in force for one lake (global + that lake's overrides)."""
    return load_params(lake)


def _quantiles(xs, levels=(10, 25, 50, 75, 90)):
    """Empirical quantiles (linear interpolation, = numpy default) of a small sample,
    as {"10":v,...}. None if fewer than 3 points. Kept dependency-free so the core
    engine needs no numpy; the predictive deciles are persisted for CRPS scoring."""
    s = sorted(xs)
    n = len(s)
    if n < 3:
        return None
    out = {}
    for p in levels:
        r = (p / 100.0) * (n - 1)
        lo = int(r)
        v = s[lo] if lo + 1 >= n else s[lo] + (r - lo) * (s[lo + 1] - s[lo])
        out[str(p)] = round(v, 1)
    return out


def beaufort(kn):
    lim = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]
    b = 0
    for L in lim:
        if kn >= L:
            b += 1
    return b


def scenario_label(value):
    """Public wording for a rule-selected calibration scenario, not a confirmed regime."""
    return {"foehn": "föhn-favourable", "thermal": "thermal-favourable",
            "gradient": "strong-gradient", "calm": "calm/capped"}.get(value, value)


def compass(deg):
    if deg is None:
        return "--"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW",
            "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg % 360) / 22.5 + 0.5) % 16]


# Ensemble-spread bands (knots). ONE authority: _confidence turns these into the hour's
# label, and dir_is_variable uses the SAME numbers to decide whether a wind direction
# carries any information. Two independent spread thresholds would drift apart.
SPREAD_HIGH_KN = 1.5    # below this the ensemble members agree
SPREAD_LOW_KN = 3.0     # at or above this they disagree, and the bearing is noise


def dir_is_variable(spread_kn):
    """True when the ensemble disagrees enough that the wind DIRECTION means nothing.

    At an evening thermal reversal the wind passes through near-zero on its way from the
    up-valley to the down-valley direction, and the modelled bearing swings freely. On
    2026-08-05 at 19:00 Walchensee read 201° and Kochelsee 284° — 8 km apart, in the same
    model run, with ensemble spread of ~5 kn on a ~5 kn wind. Both settled to ~172° by
    21:00. Printing a crisp compass point during that hour claims precision the forecast
    does not have; the honest output is 'variable'."""
    return spread_kn is not None and spread_kn >= SPREAD_LOW_KN


def dir_label(row):
    """How a built row's direction should be DISPLAYED. Single authority, so the text
    table, the HTML table and the landing-page headline can never disagree."""
    return "VAR" if row.get("dir_variable") else compass(row.get("dir"))


# Terrain-locked wind-direction sectors (direction the wind comes FROM, at the lake),
# confirmed with local knowledge. In this basin the surface wind is channelled into a
# few conduits, so direction maps to regime. Kochelsee inherits Urfeld's sectors
# (PROVISIONAL) until separately calibrated.
_SECTORS = [(340, 361, "thermal"), (0, 70, "thermal"),    # Jochberg-Herzogstand nozzle (Walchenseewind)
            (120, 210, "foehn"),                          # Kesselberg fall-wind from S-SE
            (250, 335, "gradient"), (70, 120, "gradient")]  # ridge spillover W-NW / Jachenau E
TERRAIN_SECTORS = {"walchensee": _SECTORS, "kochelsee": _SECTORS}


def terrain_regime(lake, deg):
    """Map a wind direction (deg, the direction wind comes FROM) to the terrain-
    channelled regime, or None for the SW (~210-250deg) transition/uncertain sector."""
    if deg is None:
        return None
    d = deg % 360
    for lo, hi, reg in TERRAIN_SECTORS.get(lake, []):
        if lo <= d < hi:
            return reg
    return None


def classify_regime(lake, hour, row, dp, feat=None, params=None):
    """row: this hour's Open-Meteo values. dp: Bozen-Muenchen hPa or None. feat:
    augmentation drivers (foehn_gradient_hpa, dtheta, ...). params: tunable thresholds
    (defaults to the live PARAMS; pass a candidate set for backtest replay). Returns
    foehn/thermal/gradient/calm."""
    feat = feat or {}
    P = params or PARAMS
    alpine = LAKES[lake][3]
    dir850 = row.get("wind_direction_850hPa")
    spd850 = row.get("wind_speed_850hPa") or 0
    spd925 = row.get("wind_speed_925hPa") or 0
    cloud = row.get("cloud_cover")
    dp = dp if dp is not None else -99
    southerly = dir850 is not None and SW_SECTOR[0] <= dir850 <= SW_SECTOR[1]
    dp_thr = P["FOEHN_DP_RIM"] if alpine else P["FOEHN_DP_FORELAND"]

    if dp >= dp_thr and southerly and spd850 >= P["FOEHN_850_KN"]:
        return "foehn"
    if spd925 >= P["GRADIENT_925_KN"]:
        return "gradient"
    if 9 <= hour <= 19 and cloud is not None and cloud <= P["THERMAL_CLOUD_MAX"]:
        dth = feat.get("dtheta")
        if dth is not None and dth >= P["COLD_POOL_DTHETA"] and (row.get("wind_speed_10m") or 0) < 6:
            return "calm"  # stable cold-air pool in the Kesselberg basin caps the thermal
        return "thermal"
    if (row.get("wind_speed_10m") or 0) < 2:
        return "calm"
    return "gradient"


# ------------------------------------------------------------- learned bias
def bias_path(lake):
    return os.path.join(MODELS_DIR, f"{lake}_bias.json")


def load_bias(lake):
    p = bias_path(lake)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"alpha": 0.3, "buckets": {}, "processed_dates": []}


def reset_bias(lake):
    """Retire the learned per-(regime x hour) buckets for a lake AND clear processed_dates
    so the logged history is re-learned under the new labels.

    Called when a regime THRESHOLD changes: the buckets were fit under the old labels, so
    an hour may now land in a different bucket and the old calibration no longer describes
    it. Keeping processed_dates (the first attempt at this) was worse than useless — it
    wiped the calibration and then made it impossible to rebuild, cold-starting production
    permanently, which is not the state the backtest measured. Returns how many buckets
    were dropped."""
    bias = load_bias(lake)
    n = len(bias.get("buckets") or {})
    bias["buckets"] = {}
    bias["processed_dates"] = []
    path = bias_path(lake)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(bias, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return n


def _bucket_key(regime, hour):
    return f"{regime}|{hour:02d}"


def apply_bias(bias, regime, hour, speed_kn, gust_kn, gust_ceiling_kn=None):
    """Return (corrected_speed, corrected_gust, learned_flag, flags).

    The MEAN correction is `a + b·raw`, ramped in over N_MIN_OBS observations and bounded
    by ±BIAS_CAP_KN. That bound is additive because the correction is additive, and it
    binds on ~1% of well-calibrated hours — a proper safety net. Left as it is.

    The GUST correction is multiplicative (`raw × ratio`) and gets two different guards,
    because an additive ±kn bound on a multiplicative correction is the wrong instrument:
    it permitted 3.7x on a 3 kn gust while truncating a legitimate 0.75x correction on a
    43 kn one, and it silently published `raw + cap` as though it had been learned.

      1. PLAUSIBILITY — a stored ratio outside postproc's band is refused outright and the
         raw model gust is published instead. `raw` is a true number; a clamped value is an
         invented one. Since update_gust now clamps what can be LEARNED into the same band,
         this only ever fires on state learned by the old rule, and goes quiet as it relearns.
      2. CEILING — the published gust is bounded by `gust_ceiling_kn` (derived by the caller
         from the ensemble's own gust members for that hour) and hard-limited by
         GUST_ABS_MAX_KN. This is the only guard that catches pathologies which never touch
         the ratio at all: the worst outlier on record is a 54.8 kn RAW blend gust at
         Ammersee, on a lake whose highest measured gust in 1552 hours is 20.8 kn.

    `flags` names any guard that fired, so a bounded value is never mistaken for a learned
    one — it is persisted on the row and rendered as a note."""
    flags = []
    ceiling = GUST_ABS_MAX_KN if gust_ceiling_kn is None else min(gust_ceiling_kn, GUST_ABS_MAX_KN)
    b = bias.get("buckets", {}).get(_bucket_key(regime, hour))
    n = (b or {}).get("n", 0)
    if not b or n < 1:
        # Rounded on THIS path too. It used to return the blended mean untouched, so any
        # hour whose bucket did not exist yet leaked a raw float — 2.9112500000000003 —
        # into the table and the headline, defeating KN_FMT.
        cs, cg, learned = speed_kn, gust_kn, False
    else:
        conf = min(1.0, n / N_MIN_OBS)                              # ramp the correction by evidence
        cs_full = postproc.apply(b, speed_kn, BIAS_CAP_KN)          # corrected = a + b·raw, capped
        cs = speed_kn + conf * (cs_full - speed_kn)                 # one day barely moves it
        ratio = b.get("gust_ratio", 1.0)
        if postproc.GUST_RATIO_LO <= ratio <= postproc.GUST_RATIO_HI:
            cg = max(cs, gust_kn * (1.0 + (ratio - 1.0) * conf))
        else:
            flags.append("gust_ratio_refused")                      # implausible: trust the model
            cg = max(cs, gust_kn)
        learned = n >= N_MIN_OBS
    if cg > ceiling:
        flags.append("gust_capped")
        cg = max(cs, ceiling)          # the ceiling binds, but gust may never fall below mean
    return round(cs, 1), round(cg, 1), learned, tuple(flags)


# Fields of a built row that MUST survive into the forecast log. The site renders from
# that log, NEVER from a live build_table, so a field missing here is a change that
# silently never reaches the page. dir_variable and gust_flags were nearly lost exactly
# that way: the row carried them, the hand-maintained payload in daily_run did not.
LOGGED_ROW_FIELDS = ("hour", "regime", "calib_n", "raw_kn", "raw_gust_kn", "mean_kn", "gust_kn",
                     "dir", "dir_variable", "conf", "foehn_note", "spread_kn", "q_kn",
                     "dtheta", "foehn_grad", "lapse", "dp",
                     "gust_ceiling_kn", "gust_flags", "inputs")


def logged_row(r):
    """Project a built row down to what gets persisted. ONE definition, used by daily_run
    when writing and by the self-test that proves nothing display-critical is dropped."""
    return {k: r.get(k) for k in LOGGED_ROW_FIELDS}


def gust_ceiling(ens_gusts):
    """The upper bound on a publishable gust for one hour, from that hour's ICON-D2-EPS
    gust members — which the blend already downloads and otherwise reduces to a mean.

    Season- and location-adaptive by construction: tight on a calm August evening, wide in
    a January storm. A fixed constant cannot be both, and every hour of logged history here
    is June-August, so a constant tuned on it would clip real winter gusts. Falls back to
    the absolute physical limit when no ensemble is available."""
    if not ens_gusts:
        return GUST_ABS_MAX_KN
    return min(GUST_ABS_MAX_KN, GUST_ENS_CEIL_MULT * max(ens_gusts))


# ------------------------------------------------- the single regime+correction path
def replay_hour(lake, hour, row, dp, feat, raw_s, raw_g, params=None, bias=None,
                gust_ceiling_kn=None):
    """Pure per-hour core: classify the regime, then apply the learned correction.
    Returns (regime, corrected_speed, corrected_gust, learned, flags).

    THE single authority for "raw model + inputs -> issued forecast": used by
    build_table for the live run AND by verify.backtest to replay past days under a
    candidate parameter set, so a backtest can never drift from what production does.
    The gust ceiling is threaded through here rather than applied by build_table, for
    exactly that reason — a bound that existed only in production would make every
    replayed gust disagree with the one actually issued."""
    regime = classify_regime(lake, hour, row, dp, feat, params)
    cs, cg, learned, flags = apply_bias(bias or {}, regime, hour, raw_s, raw_g,
                                        gust_ceiling_kn)
    return regime, cs, cg, learned, flags


def row_from_logged(h):
    """Rebuild the classification `row` from a LOGGED forecast hour (the inverse of the
    'inputs' block daily_run persists). Keeps the field mapping in one place."""
    inp = h.get("inputs") or {}
    return {"wind_speed_925hPa": inp.get("spd925"),
            "wind_speed_850hPa": inp.get("spd850"),
            "wind_direction_850hPa": inp.get("dir850"),
            "cloud_cover": inp.get("cloud"),
            "wind_speed_10m": h.get("raw_kn")}


# ------------------------------------------------------------- build a table
def _confidence(regime, spread_kn, learned):
    # ensemble spread (kn) -> base label; foehn/fallwind capped; learned nudges up
    if spread_kn is None:
        base = "med"
    elif spread_kn < SPREAD_HIGH_KN:
        base = "high"
    elif spread_kn < SPREAD_LOW_KN:
        base = "med"
    else:
        base = "low"
    if regime == "foehn" and base == "high":
        base = "med"  # foehn is inherently unreliable here
    if not learned and base == "high":
        base = "med"  # no local calibration yet -> don't claim high
    return base


def build_table(lake, target_date, run_stamp=None):
    """target_date: 'YYYY-MM-DD'. Returns dict with 'rows' (hourly) and 'summary'."""
    lat, lon, label, alpine = LAKES[lake]
    pt = wd.openmeteo_point(lat, lon, OM_VARS, models="icon_d2", forecast_days=3)
    h = pt["hourly"]
    # multi-member / multi-model inputs so the VALUE is an average, not one run
    try:
        ens = wd.openmeteo_ensemble(lat, lon, ["wind_speed_10m", "wind_gusts_10m"], forecast_days=3)
        eh = ens["hourly"]
        smembers = [k for k in eh if k.startswith("wind_speed_10m")]
        gmembers = [k for k in eh if k.startswith("wind_gusts_10m")]
    except Exception:
        eh, smembers, gmembers = None, [], []
    try:  # ICON-EU as an independent second model in the blend
        euh = wd.openmeteo_point(lat, lon, ["wind_speed_10m", "wind_gusts_10m"],
                                 models="icon_eu", forecast_days=3)["hourly"]
    except Exception:
        euh = None
    # foehn dp series (align by ISO hour prefix)
    try:
        # key on the LOCAL hour: MOSMIX is UTC, the Open-Meteo series is Europe/Berlin
        dp_series = {r["hour_local"]: r["dp"] for r in wd.foehn_delta_p()
                     if r.get("hour_local")}
    except Exception:
        dp_series = {}

    # augmentation: foehn/thermal cause drivers + Kesselberg valley stability
    drivers = {}
    if lake in wd.ADS_SPOT:
        try:
            drivers = wd.addicted_drivers(wd.ADS_SPOT[lake], target_date)
        except Exception:
            drivers = {}
    stab = {}
    if lake in ("kochelsee", "walchensee"):
        try:
            stab = wd.stability_dtheta(target_date)
        except Exception:
            stab = {}
    ads_fc = {}
    if lake in wd.ADS_SPOT:                       # addicted-sports' own spot forecast (extra member)
        try:
            ads_fc = wd.addicted_forecast(wd.ADS_SPOT[lake], target_date)
        except Exception:
            ads_fc = {}
    peiss = wd.hohenpeissenberg_now() if lake in ("kochelsee", "walchensee") else None  # föhn nowcast

    bias = load_bias(lake)
    lake_params = params_for(lake)   # global + this lake's verified overrides
    rows = []
    for i, t in enumerate(h["time"]):
        if not t.startswith(target_date):
            continue
        hour = int(t[11:13])
        row = {v: h[v][i] for v in OM_VARS}
        dp = dp_series.get(t[:13])
        feat = {**drivers.get(hour, {}), **stab.get(hour, {})}
        af = ads_fc.get(hour, {})
        # forecast VALUE = mean of SOURCES (equal weight): ICON-D2 EPS mean, ICON-D2
        # deterministic, ICON-EU, addicted-sports spot forecast.
        ens_s = [eh[m][i] for m in smembers if eh[m][i] is not None] if eh else []
        blend = {}                                    # named contribution per source
        if ens_s:
            blend["eps"] = sum(ens_s) / len(ens_s)    # ICON-D2 ensemble mean
        if row.get("wind_speed_10m") is not None:
            blend["det"] = row["wind_speed_10m"]      # ICON-D2 deterministic point
        if euh and euh["wind_speed_10m"][i] is not None:
            blend["eu"] = euh["wind_speed_10m"][i]    # ICON-EU
        if af.get("avg_kn") is not None:
            blend["ads"] = af["avg_kn"]               # addicted-sports spot forecast
        srcs = list(blend.values())
        raw_s = sum(srcs) / len(srcs) if srcs else (row.get("wind_speed_10m") or 0.0)
        # cross-source disagreement (range) — exposed for the caller to log; no I/O here
        blend_range = round(max(srcs) - min(srcs), 1) if len(srcs) >= 2 else None
        ens_g = [eh[m][i] for m in gmembers if eh[m][i] is not None] if eh else []
        gsrcs = ([sum(ens_g) / len(ens_g)] if ens_g else [])
        if row.get("wind_gusts_10m") is not None:
            gsrcs.append(row["wind_gusts_10m"])
        if euh and euh["wind_gusts_10m"][i] is not None:
            gsrcs.append(euh["wind_gusts_10m"][i])
        if af.get("boe_kn") is not None:
            gsrcs.append(af["boe_kn"])
        raw_g = sum(gsrcs) / len(gsrcs) if gsrcs else (row.get("wind_gusts_10m") or raw_s)
        row["wind_speed_10m"] = raw_s
        # the ensemble's own gust members bound what this hour may publish (see gust_ceiling)
        ceil_kn = gust_ceiling(ens_g)
        regime, cs, cg, learned, gflags = replay_hour(lake, hour, row, dp, feat, raw_s, raw_g,
                                                      params=lake_params, bias=bias,
                                                      gust_ceiling_kn=ceil_kn)
        spread = None
        if len(ens_s) > 2:
            m_ = sum(ens_s) / len(ens_s)
            spread = (sum((x - m_) ** 2 for x in ens_s) / len(ens_s)) ** 0.5
        # predictive deciles for CRPS scoring: take the ensemble's SHAPE/spread but recenter
        # on the ISSUED (blended, bias-corrected) mean cs, so the distribution we score is the
        # forecast we actually publish — not the ensemble-only distribution. None if no ensemble.
        q_kn = _quantiles(ens_s)
        if q_kn is not None:
            shift = cs - sum(ens_s) / len(ens_s)
            # clamp at 0: wind speed cannot be negative, and a negative P10 would both
            # publish an impossible value and distort the CRPS the deciles are scored with
            q_kn = {k: max(0.0, round(v + shift, 1)) for k, v in q_kn.items()}
        conf = _confidence(regime, spread, learned)
        foehn_note = None
        if regime == "foehn":                       # föhn: SE reliable, SW weak; confirm at Peißenberg
            d850 = row.get("wind_direction_850hPa")
            if d850 is not None and 200 <= d850 <= 240:
                foehn_note = "SW föhn — often unreliable"
            if peiss is not None and not peiss["southerly"]:
                foehn_note = f"unconfirmed @Peißenberg ({compass(peiss['dir'])} {peiss['kn']:.0f}kn)"
                conf = "low"
        rows.append({
            "hour": hour, "dir": row["wind_direction_10m"],
            # decided ONCE here from the ensemble spread; every display path reads this
            # flag via dir_label() rather than re-deriving a threshold of its own
            "dir_variable": dir_is_variable(spread),
            "raw_kn": round(raw_s, 1), "raw_gust_kn": round(raw_g, 1),
            # persisted so a replayed day reproduces the gust that was actually issued,
            # and so a bounded value is never later mistaken for a learned one
            "gust_ceiling_kn": round(ceil_kn, 1), "gust_flags": list(gflags),
            "mean_kn": cs, "gust_kn": cg,
            "bft": beaufort(cs), "regime": regime, "learned": learned,
            "calib_n": ((bias.get("buckets", {}).get(_bucket_key(regime, hour)) or {}).get("n", 0)),
            "dp": None if dp is None else round(dp, 1),
            "spread_kn": None if spread is None else round(spread, 1),
            "q_kn": q_kn,
            "blend_kn": {k: round(v, 1) for k, v in blend.items()},
            "blend_range_kn": blend_range,
            "conf": conf, "foehn_note": foehn_note,
            "dtheta": feat.get("dtheta"),
            "foehn_grad": feat.get("foehn_gradient_hpa"),
            "lapse": feat.get("lapse_2m_850"),
            # classification inputs, persisted so past days can be REPLAYED under
            # candidate params by the backtest gate (regime is a fn of these + params)
            "inputs": {"spd925": row.get("wind_speed_925hPa"),
                       "spd850": row.get("wind_speed_850hPa"),
                       "dir850": row.get("wind_direction_850hPa"),
                       "cloud": row.get("cloud_cover")},
        })
    summary = _summary(lake, rows)
    return {"lake": lake, "label": label, "date": target_date, "run_stamp": run_stamp,
            "rows": rows, "summary": summary, "peiss": peiss}


def _summary(lake, rows):
    """The one-line headline shown on the landing page.

    The peak MUST be the maximum of the hours the detail table actually displays — all 24
    of them — at the same resolution the table prints. Scanning only 09-19 and rounding to
    whole knots made the headline contradict the page it links to (e.g. "peak 4 kn ~17h"
    above a table whose real maximum was 7.8 kn at 02h). Mean and gust are reported to the
    same number of decimals so the two are directly comparable."""
    if not rows:
        return "no data"
    peak = max(rows, key=lambda r: r["mean_kn"])
    gust = max(rows, key=lambda r: (r.get("gust_kn") or 0))
    day = [r for r in rows if 9 <= r["hour"] <= 19]
    regimes = {}
    for r in day or rows:                       # "dominant" stays a DAYTIME statement
        regimes[r["regime"]] = regimes.get(r["regime"], 0) + 1
    dom = max(regimes, key=regimes.get)
    foehn_hrs = [r["hour"] for r in rows if r["regime"] == "foehn"]
    extra = ""
    if foehn_hrs:
        extra = f"  FÖHN-FAVOURABLE {min(foehn_hrs):02d}-{max(foehn_hrs):02d}h"
    g = gust.get("gust_kn")
    gtxt = (f" · gusts to {g:{KN_FMT}} kn ~{gust['hour']:02d}h" if g else "")
    return (f"dominant {scenario_label(dom)}; peak {peak['mean_kn']:{KN_FMT}} kn "
            f"({beaufort(peak['mean_kn'])} Bft) {dir_label(peak)} "
            f"~{peak['hour']:02d}h{gtxt}{extra}")


def format_table(res):
    _fmt_params = params_for(res["lake"])   # annotate with the lake's own thresholds
    lines = [f"{res['label']} — {res['date']}",
             f"  {res['summary']}",
             "  Hour | Dir  | Mean kn (Bft) | Gust kn | Scenario   | Conf | Note"]
    for r in res["rows"]:
        note = ""
        if not r["learned"]:
            note = "raw (no local calib yet)"
        if r["regime"] == "calm":
            note = "cold-pool capped" if (r.get("dtheta") is not None
                                          and r["dtheta"] >= _fmt_params["COLD_POOL_DTHETA"]) else "glassy"
        extra = []
        if r.get("dtheta") is not None and r["regime"] in ("thermal", "calm"):
            extra.append(f"Δθ{r['dtheta']:+.1f}")
        if r["regime"] == "foehn" and r.get("foehn_grad") is not None:
            extra.append(f"fg{r['foehn_grad']:+.1f}")
        if r.get("foehn_note"):
            extra.append(r["foehn_note"])
        extra += [GUST_FLAG_NOTE[f] for f in (r.get("gust_flags") or []) if f in GUST_FLAG_NOTE]
        note = " ".join(([note] if note else []) + extra)
        mean = f"{r['mean_kn']:{KN_FMT}} ({r['bft']})"
        lines.append(
            f"  {r['hour']:02d}   | {dir_label(r):<4} | "
            f"{mean:>11} | {r['gust_kn']:>5{KN_FMT}}   | "
            f"{r['regime']:<8} | {r['conf']:<4} | {note}")
    return "\n".join(lines)


def _selftest_summary():
    """The headline must agree with the table it links to, at the same resolution."""
    rows = [{"hour": h, "mean_kn": v, "gust_kn": v * 2, "dir": 340, "regime": "thermal",
             "bft": beaufort(v)}
            for h, v in enumerate([2.0] * 9 + [3.0] * 11 + [6.5] + [1.0] * 3)]
    s = _summary("walchensee", rows)
    peak = max(rows, key=lambda r: r["mean_kn"])          # 6.5 kn at hour 20, OUTSIDE 09-19
    assert f"{peak['mean_kn']:{KN_FMT}} kn" in s, f"headline missed the table max: {s}"
    assert f"~{peak['hour']:02d}h" in s, f"headline quoted the wrong hour: {s}"
    gust = max(rows, key=lambda r: r["gust_kn"])
    assert f"{gust['gust_kn']:{KN_FMT}} kn" in s, f"headline missed the max gust: {s}"
    # mean and gust must carry the SAME number of decimals
    import re as _re
    decs = {len(m.split(".")[1]) for m in _re.findall(r"\d+\.\d+(?= kn)", s)}
    assert len(decs) == 1, f"mean and gust printed at different resolutions: {s}"
    print("  PASS summary: peak+gust match the full table, one shared resolution")
    return s


def _selftest_gust_guards():
    """The two gust guards, each on the real case that motivated it."""
    # 1. PLAUSIBILITY. walchensee gradient|19 held gust_ratio 3.18 on n=3; the evidence
    #    ramp saturated at n/N_MIN_OBS = 1.0 and published 67.9 kn off a 21.4 kn model
    #    gust. An implausible ratio must be refused, not clamped: the raw model gust is a
    #    true number, `raw + cap` is an invented one.
    bias = {"buckets": {"gradient|19": {"n": 3, "a": 5.231, "b": 0.276,
                                        "P": [1.55, -0.43, 0.15], "gust_ratio": 3.18}}}
    cs, cg, learned, flags = apply_bias(bias, "gradient", 19, 9.4, 21.4)
    assert learned and "gust_ratio_refused" in flags, f"implausible ratio not refused: {flags}"
    assert cg == 21.4, f"refusal must publish the raw model gust, got {cg}"
    assert cg >= cs, "gust must never fall below the mean"
    # 2. a ratio INSIDE the band must still be applied in full — the guard must not
    #    deafen the correction. This is the Ammersee case the old +/-8 kn cap distorted:
    #    43.0 kn raw with a perfectly sane 0.75 ratio belongs at 32.2, not 35.0.
    bias = {"buckets": {"gradient|19": {"n": 3, "a": 0.0, "b": 1.0,
                                        "P": [1.0, 0.0, 0.1], "gust_ratio": 0.75}}}
    _, cg2, _, f2 = apply_bias(bias, "gradient", 19, 30.0, 43.0)
    assert not f2 and abs(cg2 - 32.2) < 0.05, \
        f"a sane 0.75x correction on a 43 kn gust was distorted: {cg2} kn {f2}"
    # 3. CEILING. Catches what no ratio bound can: a pathological RAW blend value with a
    #    blameless ratio. Ammersee's worst on record is a 54.8 kn raw gust on a lake whose
    #    highest measured gust in 1552 hours is 20.8 kn.
    _, cg3, _, f3 = apply_bias({"buckets": {}}, "gradient", 16, 12.0, 54.8,
                               gust_ceiling_kn=gust_ceiling([16.0, 18.0, 20.0]))
    assert "gust_capped" in f3, "a 54.8 kn raw gust against a 20 kn ensemble was not capped"
    assert abs(cg3 - 30.0) < 1e-9, f"ceiling should be 1.5 x 20.0 = 30.0, got {cg3}"
    # 4. the ceiling must never be so tight that it fires on ordinary hours
    _, cg4, _, f4 = apply_bias({"buckets": {}}, "thermal", 14, 6.0, 11.0,
                               gust_ceiling_kn=gust_ceiling([9.0, 11.0, 12.0]))
    assert not f4 and cg4 == 11.0, f"ceiling fired on a normal hour: {cg4} {f4}"
    # 5. no ensemble -> the absolute physical backstop, not an unbounded gust
    assert gust_ceiling([]) == GUST_ABS_MAX_KN and gust_ceiling(None) == GUST_ABS_MAX_KN
    assert gust_ceiling([100.0]) == GUST_ABS_MAX_KN, "ensemble may not raise the hard limit"
    # 6. an hour with NO bucket must still come back rounded (KN_FMT is one resolution)
    s6, g6, l6, _ = apply_bias({"buckets": {}}, "gradient", 20,
                               2.9112500000000003, 7.614999999999999)
    assert not l6 and (s6, g6) == (2.9, 7.6), f"unrounded values leaked: {s6}, {g6}"
    # 7. every guard that fires must be explainable to a reader
    for f in ("gust_ratio_refused", "gust_capped"):
        assert f in GUST_FLAG_NOTE, f"{f} would be applied with no explanation shown"
    print(f"  PASS gust guards: ratio 3.18 refused -> raw 21.4 kn (was 67.9); "
          f"sane 0.75x preserved at 32.2 kn (old cap gave 35.0); 54.8 kn raw capped to 30.0")


def _selftest_logged_row():
    """A fix that never reaches the page is not a fix.

    The site renders from the forecast LOG, so every field the display paths read must
    survive the projection to disk. dir_variable and gust_flags were nearly dropped here:
    build_table set them on the row, and the payload written by daily_run listed fields by
    hand and did not include them. This asserts the CONSEQUENCE, not just the list."""
    row = {"hour": 19, "regime": "gradient", "raw_kn": 9.4, "raw_gust_kn": 21.4,
           "mean_kn": 7.8, "gust_kn": 21.4, "dir": 201, "dir_variable": True,
           "conf": "low", "spread_kn": 4.9, "gust_ceiling_kn": 64.0,
           "gust_flags": ["gust_ratio_refused"], "inputs": {"cloud": 40},
           "blend_kn": {"eps": 9.0}}          # deliberately not persisted
    out = logged_row(row)
    assert dir_label(out) == "VAR", "a withheld direction did not survive being logged"
    assert out["gust_flags"] == ["gust_ratio_refused"], "gust guard flags lost on the way to disk"
    assert all(f in GUST_FLAG_NOTE for f in out["gust_flags"]), "a flag would render with no note"
    assert out["gust_ceiling_kn"] == 64.0, "the ceiling must persist so a replay reproduces the gust"
    assert "blend_kn" not in out, "logged_row should project, not copy wholesale"
    # a row straight from build_table must not carry a display field the projection drops
    for f in ("dir_variable", "gust_flags", "gust_ceiling_kn"):
        assert f in LOGGED_ROW_FIELDS, f"{f} is set on the row but would never reach the site"
    print("  PASS persistence: withheld direction and gust guards survive into the log")


def _selftest_dir_variable():
    """Direction must be withheld exactly when the ensemble says it is meaningless.
    Values are the real 2026-08-05 spreads for the two lakes."""
    assert dir_is_variable(4.9) and dir_is_variable(5.2), "the 19:00 reversal must be flagged"
    assert dir_is_variable(4.1), "the 20:00 hour must be flagged"
    assert not dir_is_variable(1.7) and not dir_is_variable(2.6), "21:00 settled — must NOT be flagged"
    assert not dir_is_variable(1.5), "an agreeing ensemble must keep its direction"
    assert not dir_is_variable(None), "no ensemble is not evidence of variability"
    # the flag, not the raw bearing, drives every display path
    assert dir_label({"dir": 201, "dir_variable": True}) == "VAR"
    assert dir_label({"dir": 201, "dir_variable": False}) == "SSW"
    assert dir_label({"dir": 201}) == "SSW", "records logged before the flag existed must still render"
    # and the headline must use the same authority as the table
    rows = [{"hour": 19, "mean_kn": 7.8, "gust_kn": 29.4, "dir": 201, "dir_variable": True,
             "regime": "gradient", "bft": beaufort(7.8)}]
    assert "VAR" in _summary("walchensee", rows), "headline still claims a bearing the table withholds"
    print("  PASS direction: flagged at spread>=%.1f kn, withheld from table AND headline"
          % SPREAD_LOW_KN)


if __name__ == "__main__":
    import datetime
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        print("=== forecast.py self-tests ===")
        print("  ", _selftest_summary())
        _selftest_gust_guards()
        _selftest_dir_variable()
        _selftest_logged_row()
        print("ALL SELF-TESTS PASSED")
        sys.exit(0)
    lake = sys.argv[1] if len(sys.argv) > 1 else "walchensee"
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
    print(format_table(build_table(lake, date)))
