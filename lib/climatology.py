#!/usr/bin/env python3
"""
climatology.py — build and serve a per-(month x hour-of-day) wind climatology.

WHY: the verifier needs a climatology baseline, and the honest expectation in
docs/IMPROVEMENT_PLAN.md is that climatology will beat the model for a long time. Built
from the logs it needs >=3 prior days at the same hour, so with a few days of history it
is simply unavailable (`climatology n/a`) and the referee runs on one baseline instead of
two. A long observational archive fixes that on day one.

For Ammersee that archive is the on-lake Ammerseeboje (GKD Bayern 16601050, hourly back to
2014, CC BY 4.0) — the same buoy winddata prefers for actuals. A year of hourly values
comes back in a single ranged request, so building the whole archive is ~13 requests.

LEAK SAFETY: a climatology that includes the day being scored has seen the answer. The
model records the last date it covers, and verify.py refuses to use it for any date inside
that range — only strictly after. That is why `covers_end` is stored and checked.

CLI:
  python lib/climatology.py build ammersee     # fetch + persist (network)
  python lib/climatology.py show  ammersee     # summarise what is stored
"""
import os, sys, json, time, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc

QLEVELS = tuple(range(5, 100, 5))     # 5..95 % — a dense grid, still a small file
MIN_SAMPLES = 20                      # per (month, hour) bucket before it is usable
FIRST_YEAR = 2014                     # the Ammerseeboje archive starts 30.01.2014


def path(lake):
    return os.path.join(fc.MODELS_DIR, f"{lake}_climatology.json")


def _quantiles(xs, levels=QLEVELS):
    s = sorted(xs)
    n = len(s)
    out = []
    for p in levels:
        r = (p / 100.0) * (n - 1)
        lo = int(r)
        out.append(round(s[lo] if lo + 1 >= n else s[lo] + (r - lo) * (s[lo + 1] - s[lo]), 2))
    return out


def build(lake, first_year=FIRST_YEAR, pause=1.0):
    """Fetch the station archive year by year and aggregate to (month, hour) quantiles."""
    if lake not in wd.GKD_WIND:
        raise ValueError(f"no GKD archive station configured for {lake}")
    basin, slug, label = wd.GKD_WIND[lake]
    today = datetime.date.today()
    buckets, covered = {}, []
    for yr in range(first_year, today.year + 1):
        beg = f"01.01.{yr}"
        end = "31.12.%d" % yr if yr < today.year else today.strftime("%d.%m.%Y")
        try:
            rows = wd.gkd_wind_range(basin, slug, beg, end)
        except Exception as e:
            print(f"  {yr}: fetch failed ({type(e).__name__}) — skipped")
            continue
        for iso, hour, kn in rows:
            d = datetime.date.fromisoformat(iso)
            buckets.setdefault(f"{d.month:02d}|{hour:02d}", []).append(kn)
            covered.append(iso)
        print(f"  {yr}: {len(rows):5d} hourly values")
        time.sleep(pause)                      # be polite to a public service
    if not covered:
        raise RuntimeError("no data fetched")
    model = {
        "lake": lake, "source": label, "levels": list(QLEVELS),
        "covers_start": min(covered), "covers_end": max(covered),
        "n_total": len(covered), "min_samples": MIN_SAMPLES,
        "built": today.isoformat(),
        "buckets": {k: {"n": len(v), "q": _quantiles(v),
                        "mean": round(sum(v) / len(v), 2)}
                    for k, v in sorted(buckets.items()) if len(v) >= MIN_SAMPLES},
    }
    os.makedirs(fc.MODELS_DIR, exist_ok=True)
    tmp = path(lake) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(model, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path(lake))
    return model


_CACHE = {}


def load(lake):
    """The stored climatology, or None. Cached per process (it never changes mid-run)."""
    if lake in _CACHE:
        return _CACHE[lake]
    p = path(lake)
    m = None
    if os.path.exists(p):
        try:
            with open(p) as f:
                m = json.load(f)
        except Exception:
            m = None
    _CACHE[lake] = m
    return m


def members(lake, date, hour):
    """Climatological ensemble members (knots) for a date+hour, or None.

    Returns None — deliberately, so the caller falls back to the from-logs climatology —
    when there is no model, when the bucket is too thin, or when `date` lies INSIDE the
    archive's coverage (scoring a day with a climatology that contains it is leakage)."""
    m = load(lake)
    if not m:
        return None
    if date <= m.get("covers_end", "9999-12-31"):
        return None                                   # leak guard
    b = (m.get("buckets") or {}).get(f"{date[5:7]}|{hour:02d}")
    return list(b["q"]) if b else None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    lake = sys.argv[2] if len(sys.argv) > 2 else "ammersee"
    if cmd == "build":
        m = build(lake)
        print(f"\nbuilt {path(lake)}")
        print(f"  {m['n_total']} hourly values, {m['covers_start']}..{m['covers_end']}")
        print(f"  {len(m['buckets'])}/288 (month x hour) buckets with >= {MIN_SAMPLES} samples")
    else:
        m = load(lake)
        if not m:
            print(f"no climatology for {lake}; run: python lib/climatology.py build {lake}")
            sys.exit(0)
        print(f"{lake}: {m['n_total']} values {m['covers_start']}..{m['covers_end']} "
              f"from {m['source']}")
        print(f"  {len(m['buckets'])} buckets; sample August diurnal cycle (median kn):")
        mid = m["levels"].index(50)
        for h in range(6, 22, 2):
            b = m["buckets"].get(f"08|{h:02d}")
            if b:
                print(f"    {h:02d}:00  median {b['q'][mid]:5.1f} kn   mean {b['mean']:5.1f}   n={b['n']}")
