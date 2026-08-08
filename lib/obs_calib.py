#!/usr/bin/env python3
"""
obs_calib.py — calibrate a FALLBACK observation source against the real on-lake truth.

WHY: Ammersee's preferred actual is the on-lake Ammerseeboje, but it is offline (broken
since 2026-06-15), so the pipeline falls back to shore stations — DWD Wielenbach ~11 km
inland, and BSV Herrsching on the east shore. Both are sheltered and read far low: over
11,774 overlapping hours Wielenbach averaged 3.15 kn where the lake had 8.19. Learning and
grading Ammersee against that raw means training on a "truth" that is wrong by more than
the forecast error we are trying to remove.

Both sources are calibrated here against the SAME target (the buoy archive) so their
validations compare directly. Neither wins alone — on 1,273 held-out hours calibrated DWD
scored MAE 2.788 kn and calibrated BSV 2.915 — but the MEAN OF THE TWO scored 2.639, and
that blend is what winddata.measured_source publishes. Opposite shores, largely independent
local noise, so averaging cancels some of it.

WHAT: fit, per hour-of-day, a linear map  lake ~= a_h + b_h * station  from the paired
history, and apply it whenever the fallback is used. Per hour matters because the gap is
diurnal (the lake breeze the inland station cannot see).

The fit is mostly an OFFSET, not a scaling (global fit a=+5.1, b=1.06): above a baseline
the two track nearly 1:1, but the lake carries a wind the sheltered station simply never
records — which is why naive ratio-scaling barely helped (+6% vs +42%).

HONESTY: a corrected station value is an ESTIMATE of lake wind, not truth. The model file
records its own out-of-sample validation so the claim is auditable, and the correction is
only applied if that validation showed a real improvement. The source string always says
when a value was corrected.

CLI:
  python lib/obs_calib.py build ammersee    # fetch pairs, fit, validate, persist (network)
  python lib/obs_calib.py show  ammersee
"""
import os, sys, json, statistics, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc

MIN_PAIRS_PER_HOUR = 40      # below this an hour falls back to the global fit
MIN_IMPROVEMENT = 0.05       # require >=5% out-of-sample MAE gain before applying at all
TRAIN_FRAC = 0.7             # temporal split: earliest 70% of DATES train, rest tests


# Which observation source is being calibrated onto lake-equivalent wind. Both are fitted
# against the SAME target (the on-lake buoy archive) so their validation numbers are
# directly comparable, and each gets its own model file.
#   dwd  DWD Wielenbach 05538, 11 km inland.   Measured r=0.171 against the buoy.
#   bsv  BSV Herrsching, on the east shore.    Measured r=0.486 — three times the tracking,
#        and it reports gusts and direction, which the buoy never did.
SOURCES = ("dwd", "bsv")


def path(lake, source="dwd"):
    # the dwd filename predates the source split; keep it so old models still load
    stem = "fallback_calib" if source == "dwd" else f"{source}_calib"
    return os.path.join(fc.MODELS_DIR, f"{lake}_{stem}.json")


def _observed(lake, source):
    """{(iso_date, hour) -> mean_kn} for the source being calibrated."""
    if source == "dwd":
        return {k: v["mean_kn"] for k, v in wd.dwd_obs_all(wd.STA_OBS[lake]).items()}
    if source == "bsv":
        import bsv
        out = {}
        for name in sorted(os.listdir(bsv.CACHE)) if os.path.isdir(bsv.CACHE) else []:
            if not name.endswith(".json"):
                continue
            d = name[:-5]
            try:
                for h, v in bsv.hourly(d).items():
                    out[(d, h)] = v["mean_kn"]
            except Exception:
                continue        # a single unreadable cached day must not abort the fit
        return out
    raise ValueError(f"unknown source {source!r}")


def _fit(rows):
    """Ordinary least squares lake ~= a + b*station over [(station, lake), ...]."""
    if len(rows) < 12:
        return None
    x = [r[0] for r in rows]
    y = [r[1] for r in rows]
    mx, my = statistics.mean(x), statistics.mean(y)
    den = sum((i - mx) ** 2 for i in x)
    if den < 1e-9:
        return None
    b = sum((i - mx) * (j - my) for i, j in zip(x, y)) / den
    return [round(my - b * mx, 4), round(b, 4)]


def _apply(coef, v):
    return max(0.0, coef[0] + coef[1] * v)


def build(lake, source="dwd"):
    """Pair the on-lake buoy archive with `source`, fit per hour, validate on held-out
    LATER dates, and persist only what the validation supports."""
    if lake not in wd.GKD_WIND:
        raise ValueError(f"no on-lake archive configured for {lake}")
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    basin, slug, label = wd.GKD_WIND[lake]
    station = wd.STA_OBS[lake] if source == "dwd" else "bsv-herrsching"

    dwd = _observed(lake, source)                       # {(date,hour) -> mean_kn}
    span = sorted({k[0] for k in dwd})
    beg = datetime.date.fromisoformat(span[0])
    end = datetime.date.fromisoformat(span[-1])
    buoy = {}
    for yr in range(beg.year, end.year + 1):
        b = max(beg, datetime.date(yr, 1, 1)).strftime("%d.%m.%Y")
        e = min(end, datetime.date(yr, 12, 31)).strftime("%d.%m.%Y")
        for iso, h, kn in wd.gkd_wind_range(basin, slug, b, e):
            buoy[(iso, h)] = kn

    pairs = [(k[0], k[1], dwd[k], buoy[k]) for k in sorted(set(dwd) & set(buoy))]
    if len(pairs) < 200:
        raise RuntimeError(f"only {len(pairs)} overlapping hours — too few to calibrate")

    dates = sorted({p[0] for p in pairs})
    cut = dates[int(len(dates) * TRAIN_FRAC)]
    tr = [p for p in pairs if p[0] < cut]
    te = [p for p in pairs if p[0] >= cut]

    glob = _fit([(p[2], p[3]) for p in tr])
    per = {}
    for h in range(24):
        f = _fit([(p[2], p[3]) for p in tr if p[1] == h])
        rows = sum(1 for p in tr if p[1] == h)
        if f and rows >= MIN_PAIRS_PER_HOUR:
            per[str(h)] = f

    def mae(fn):
        return statistics.mean([abs(fn(p) - p[3]) for p in te])
    raw = mae(lambda p: p[2])
    cal = mae(lambda p: _apply(per.get(str(p[1]), glob), p[2]))
    gain = (raw - cal) / raw if raw else 0.0

    model = {
        "lake": lake, "target": label, "station": station, "source": source,
        "built": datetime.date.today().isoformat(),
        "n_pairs": len(pairs), "covers": [dates[0], dates[-1]], "test_from": cut,
        "global": glob, "per_hour": per,
        "validation": {"test_pairs": len(te), "mae_raw_kn": round(raw, 3),
                       "mae_calibrated_kn": round(cal, 3), "improvement": round(gain, 4)},
        "apply": bool(gain >= MIN_IMPROVEMENT),
    }
    os.makedirs(fc.MODELS_DIR, exist_ok=True)
    tmp = path(lake, source) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(model, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path(lake, source))
    return model


_CACHE = {}


def load(lake, source="dwd"):
    if (lake, source) in _CACHE:
        return _CACHE[(lake, source)]
    p = path(lake, source)
    m = None
    if os.path.exists(p):
        try:
            with open(p) as f:
                m = json.load(f)
        except Exception:
            m = None
    if m and not m.get("apply"):
        m = None                      # fitted but not validated -> never used
    _CACHE[(lake, source)] = m
    return m


def correct(lake, hour, mean_kn, gust_kn=None, source="dwd"):
    """Map a fallback-station reading onto lake-equivalent wind.

    Returns (mean, gust, applied). The gust is shifted by the SAME amount as the mean and
    floored at it — correcting the mean alone would otherwise produce a gust below the
    mean, which is impossible."""
    m = load(lake, source)
    if not m or mean_kn is None:
        return mean_kn, gust_kn, False
    coef = (m.get("per_hour") or {}).get(str(hour)) or m.get("global")
    if not coef:
        return mean_kn, gust_kn, False
    cm = round(_apply(coef, mean_kn), 1)
    cg = gust_kn
    if gust_kn is not None:
        cg = round(max(cm, gust_kn + (cm - mean_kn)), 1)
    return cm, cg, True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    lake = sys.argv[2] if len(sys.argv) > 2 else "ammersee"
    source = sys.argv[3] if len(sys.argv) > 3 else "dwd"
    if cmd == "build":
        m = build(lake, source)
        v = m["validation"]
        print(f"built {path(lake, source)}")
        print(f"  {m['n_pairs']} paired hours {m['covers'][0]}..{m['covers'][1]}"
              f" (test from {m['test_from']}, {v['test_pairs']} pairs)")
        print(f"  out-of-sample MAE vs the lake: raw {v['mae_raw_kn']} kn"
              f" -> calibrated {v['mae_calibrated_kn']} kn  ({v['improvement']*100:+.1f}%)")
        print(f"  per-hour fits: {len(m['per_hour'])}/24   global: a={m['global'][0]:+.2f} b={m['global'][1]:.2f}")
        print(f"  APPLIED IN PRODUCTION: {m['apply']}")
    else:
        m = load(lake, source)
        if not m:
            print(f"no active calibration for {lake} / {source}")
            sys.exit(0)
        v = m["validation"]
        print(f"{lake}: {m['station']} -> {m['target']}")
        print(f"  {v['mae_raw_kn']} -> {v['mae_calibrated_kn']} kn MAE ({v['improvement']*100:+.1f}%)")
        for h in (6, 9, 12, 15, 18, 21):
            c = m["per_hour"].get(str(h)) or m["global"]
            print(f"    {h:02d}:00  lake ≈ {c[0]:+.2f} + {c[1]:.2f}·station"
                  f"   (station 5 kn -> {_apply(c,5):.1f} kn)")
