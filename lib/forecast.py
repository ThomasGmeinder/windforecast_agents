#!/usr/bin/env python3
"""
forecast.py — deterministic wind-forecast engine for the Bavarian lakes.

This is the SINGLE AUTHORITY for: regime classification, application of the
learned bias correction, and confidence. Both the automated 6am job (daily_run.py)
and the on-demand LLM agents should get their corrected hourly numbers from here,
so the logic lives in exactly one place.

Units: Open-Meteo is queried in knots, so everything here is in knots.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd

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

# regime thresholds (knots / degrees) — Swiss-calibrated foehn values converted
# from m/s; documented as approximate until locally recalibrated by learn.py.
FOEHN_DP_RIM = 4.0      # hPa, Bozen-Muenchen, reaches Alpine-rim valleys/lakes
FOEHN_DP_FORELAND = 8.0 # hPa, needed to reach Ammersee (rare)
FOEHN_850_KN = 7.0      # ~3.7 m/s
GRADIENT_925_KN = 12.0  # strong enough aloft to govern the surface
THERMAL_CLOUD_MAX = 45  # % total cloud
SW_SECTOR = (120, 240)  # southerly 850 hPa sector for foehn
COLD_POOL_DTHETA = 1.5  # K; Kochel-Walchensee dtheta above this = stable cold pool caps the thermal
                        # (provisional pivot ~ dry-adiabatic 2.0 K minus margin; recalibrated by learn.py)


def beaufort(kn):
    lim = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]
    b = 0
    for L in lim:
        if kn >= L:
            b += 1
    return b


def compass(deg):
    if deg is None:
        return "--"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW",
            "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return dirs[int((deg % 360) / 22.5 + 0.5) % 16]


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


def classify_regime(lake, hour, row, dp, feat=None):
    """row: this hour's Open-Meteo values. dp: Bozen-Muenchen hPa or None. feat:
    augmentation drivers (foehn_gradient_hpa, dtheta, ...). Returns foehn/thermal/
    gradient/calm."""
    feat = feat or {}
    alpine = LAKES[lake][3]
    dir850 = row.get("wind_direction_850hPa")
    spd850 = row.get("wind_speed_850hPa") or 0
    spd925 = row.get("wind_speed_925hPa") or 0
    cloud = row.get("cloud_cover")
    dp = dp if dp is not None else -99
    southerly = dir850 is not None and SW_SECTOR[0] <= dir850 <= SW_SECTOR[1]
    dp_thr = FOEHN_DP_RIM if alpine else FOEHN_DP_FORELAND

    if dp >= dp_thr and southerly and spd850 >= FOEHN_850_KN:
        return "foehn"
    if spd925 >= GRADIENT_925_KN:
        return "gradient"
    if 9 <= hour <= 19 and cloud is not None and cloud <= THERMAL_CLOUD_MAX:
        dth = feat.get("dtheta")
        if dth is not None and dth >= COLD_POOL_DTHETA and (row.get("wind_speed_10m") or 0) < 6:
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


def _bucket_key(regime, hour):
    return f"{regime}|{hour:02d}"


def apply_bias(bias, regime, hour, speed_kn, gust_kn):
    """Return (corrected_speed, corrected_gust, learned_flag)."""
    b = bias.get("buckets", {}).get(_bucket_key(regime, hour))
    if not b or b.get("n", 0) < 1:
        return speed_kn, gust_kn, False
    cs = max(0.0, speed_kn + b.get("bias_kn", 0.0))
    cg = max(cs, gust_kn * b.get("gust_ratio", 1.0))
    return round(cs, 1), round(cg, 1), True


# ------------------------------------------------------------- build a table
def _confidence(regime, spread_kn, learned):
    # ensemble spread (kn) -> base label; foehn/fallwind capped; learned nudges up
    if spread_kn is None:
        base = "med"
    elif spread_kn < 1.5:
        base = "high"
    elif spread_kn < 3.0:
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
    # ensemble spread per hour (icon_d2 ensemble)
    try:
        ens = wd.openmeteo_ensemble(lat, lon, ["wind_speed_10m"], forecast_days=3)
        eh = ens["hourly"]
        members = [k for k in eh if k.startswith("wind_speed_10m")]
    except Exception:
        eh, members = None, []
    # foehn dp series (align by ISO hour prefix)
    try:
        dp_series = {r["time"][:13]: r["dp"] for r in wd.foehn_delta_p()}
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

    bias = load_bias(lake)
    rows = []
    for i, t in enumerate(h["time"]):
        if not t.startswith(target_date):
            continue
        hour = int(t[11:13])
        row = {v: h[v][i] for v in OM_VARS}
        dp = dp_series.get(t[:13])
        feat = {**drivers.get(hour, {}), **stab.get(hour, {})}
        regime = classify_regime(lake, hour, row, dp, feat)
        raw_s = row["wind_speed_10m"] or 0.0
        raw_g = row["wind_gusts_10m"] or raw_s
        cs, cg, learned = apply_bias(bias, regime, hour, raw_s, raw_g)
        # ensemble spread
        spread = None
        if eh and members:
            vals = [eh[m][i] for m in members if eh[m][i] is not None]
            if len(vals) > 2:
                mean = sum(vals) / len(vals)
                spread = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5
        rows.append({
            "hour": hour, "dir": row["wind_direction_10m"],
            "raw_kn": round(raw_s, 1), "raw_gust_kn": round(raw_g, 1),
            "mean_kn": cs, "gust_kn": cg,
            "bft": beaufort(cs), "regime": regime, "learned": learned,
            "dp": None if dp is None else round(dp, 1),
            "spread_kn": None if spread is None else round(spread, 1),
            "conf": _confidence(regime, spread, learned),
            "dtheta": feat.get("dtheta"),
            "foehn_grad": feat.get("foehn_gradient_hpa"),
            "lapse": feat.get("lapse_2m_850"),
        })
    summary = _summary(lake, rows)
    return {"lake": lake, "label": label, "date": target_date,
            "run_stamp": run_stamp, "rows": rows, "summary": summary}


def _summary(lake, rows):
    if not rows:
        return "no data"
    day = [r for r in rows if 9 <= r["hour"] <= 19]
    peak = max(day or rows, key=lambda r: r["mean_kn"])
    regimes = {}
    for r in day or rows:
        regimes[r["regime"]] = regimes.get(r["regime"], 0) + 1
    dom = max(regimes, key=regimes.get)
    foehn_hrs = [r["hour"] for r in rows if r["regime"] == "foehn"]
    extra = ""
    if foehn_hrs:
        extra = f"  FOEHN {min(foehn_hrs):02d}-{max(foehn_hrs):02d}h"
        if lake == "walchensee":
            extra += " (NE thermal suppressed)"
        if lake == "kochelsee":
            extra += " (Kesselberg fall-wind)"
    return (f"dominant {dom}; peak {peak['mean_kn']:.0f} kn ({beaufort(peak['mean_kn'])} Bft) "
            f"{compass(peak['dir'])} ~{peak['hour']:02d}h{extra}")


def format_table(res):
    lines = [f"{res['label']} — {res['date']}",
             f"  {res['summary']}",
             "  Hour | Dir  | Mean kn (Bft) | Gust kn | Regime   | Conf | Note"]
    for r in res["rows"]:
        note = ""
        if not r["learned"]:
            note = "raw (no local calib yet)"
        if r["regime"] == "calm":
            note = "cold-pool capped" if (r.get("dtheta") is not None
                                          and r["dtheta"] >= COLD_POOL_DTHETA) else "glassy"
        extra = []
        if r.get("dtheta") is not None and r["regime"] in ("thermal", "calm"):
            extra.append(f"Δθ{r['dtheta']:+.1f}")
        if r["regime"] == "foehn" and r.get("foehn_grad") is not None:
            extra.append(f"fg{r['foehn_grad']:+.1f}")
        note = " ".join(([note] if note else []) + extra)
        mean = f"{r['mean_kn']:.1f} ({r['bft']})"
        lines.append(
            f"  {r['hour']:02d}   | {compass(r['dir']):<4} | "
            f"{mean:>11} | {r['gust_kn']:>5.1f}   | "
            f"{r['regime']:<8} | {r['conf']:<4} | {note}")
    return "\n".join(lines)


if __name__ == "__main__":
    import datetime
    lake = sys.argv[1] if len(sys.argv) > 1 else "walchensee"
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.date.today().isoformat()
    print(format_table(build_table(lake, date)))
