#!/usr/bin/env python3
"""
backfill.py — reconstruct past forecast records so the loop can be exercised NOW.

WHY: the backtest gate needs >= N_MIN_BACKTEST_DAYS of REPLAYABLE history (days whose
logged hours carry the classification inputs). Those only started accruing on 2026-08-02,
so the apply path had never run against real data — it was verified only against synthetic
fixtures and mocked backtests. "Wait a few weeks" is not a good reason to leave the
safety-critical path unexercised when the inputs are actually retrievable.

Open-Meteo's historical-forecast API serves archived ICON-D2 runs, so every input
classify_regime needs can be rebuilt for past dates:
  wind/gust/direction, cloud, 925 & 850 hPa  -> at the lake point
  dtheta                                     -> T2m at Kochelsee and Walchensee
  foehn dp                                   -> pressure_msl at Bozen and Muenchen

TWO HONEST LIMITS, and why the result is still useful:
  1. Lead time. The archive returns the best archived forecast for each hour, and the
     endpoint offers no way to pin a lead time, so a backfilled row is SHORTER-lead than a
     real 05:00 run. Scoring the model on it would flatter it.
  2. Blend. Production averages four sources (ensemble, deterministic, ICON-EU,
     addicted-sports); only archived ICON-D2 is retrievable, so raw_kn is single-source.

Both limits hit the two backtest arms IDENTICALLY — the arms replay the same rows and
differ only in the parameter — so the paired comparison the gate relies on stays valid.
They would NOT be fair to the published scorecard, so every record is written with
"backfilled": true and verify.evaluate() excludes them. The gate uses them; the scorecard
does not.

CLI:
  python lib/backfill.py <lake> <start> <end>     # e.g. ammersee 2026-06-01 2026-06-30
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc

ARCHIVE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ISSUE_HOUR = 5          # pretend each day was issued at 05:00 local, like the real cron


def _arch(lat, lon, hourly, start, end):
    import urllib.parse
    q = urllib.parse.urlencode({"latitude": lat, "longitude": lon,
                                "hourly": ",".join(hourly), "start_date": start,
                                "end_date": end, "models": "icon_d2",
                                "timezone": "Europe/Berlin", "wind_speed_unit": "kn"})
    return json.loads(wd._get(f"{ARCHIVE}?{q}", timeout=90).decode())["hourly"]


def build(lake, start, end):
    """Reconstruct forecast records for [start, end] and return them (newest last)."""
    lat, lon, label, _ = fc.LAKES[lake]
    h = _arch(lat, lon, ["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
                         "cloud_cover", "wind_speed_925hPa", "wind_speed_850hPa",
                         "wind_direction_850hPa"], start, end)
    # dtheta needs both Alpine-rim lakes; dp needs the cross-Alpine pressure pair
    tk = _arch(*fc.LAKES["kochelsee"][:2], ["temperature_2m"], start, end)["temperature_2m"]
    tw = _arch(*fc.LAKES["walchensee"][:2], ["temperature_2m"], start, end)["temperature_2m"]
    ps = _arch(46.50, 11.35, ["pressure_msl"], start, end)["pressure_msl"]      # Bozen
    pn = _arch(48.14, 11.58, ["pressure_msl"], start, end)["pressure_msl"]      # Muenchen
    dz = 800 - 604                                    # Walchensee - Kochelsee, metres

    by_date = {}
    for i, t in enumerate(h["time"]):
        d, hour = t[:10], int(t[11:13])
        row = {v: h[v][i] for v in h if v != "time"}
        if row.get("wind_speed_10m") is None:
            continue
        dth = (None if (tk[i] is None or tw[i] is None)
               else round((tw[i] - tk[i]) + wd.DALR * dz / 1000.0, 2))
        dp = (None if (ps[i] is None or pn[i] is None) else round(ps[i] - pn[i], 1))
        by_date.setdefault(d, []).append({
            "hour": hour,
            "raw_kn": round(row["wind_speed_10m"], 1),
            "raw_gust_kn": round(row.get("wind_gusts_10m") or row["wind_speed_10m"], 1),
            "dir": row.get("wind_direction_10m"),
            "dtheta": dth, "dp": dp,
            "inputs": {"spd925": row.get("wind_speed_925hPa"),
                       "spd850": row.get("wind_speed_850hPa"),
                       "dir850": row.get("wind_direction_850hPa"),
                       "cloud": row.get("cloud_cover")},
        })

    bias = fc.load_bias(lake)
    params = fc.params_for(lake)
    out = []
    for d, hours in sorted(by_date.items()):
        stamp = f"{d}T{ISSUE_HOUR:02d}:00+02:00"
        rows = []
        for r in sorted(hours, key=lambda x: x["hour"]):
            rowlike = fc.row_from_logged(r)
            regime, cs, cg, learned = fc.replay_hour(
                lake, r["hour"], rowlike, r["dp"], {"dtheta": r["dtheta"]},
                r["raw_kn"], r["raw_gust_kn"], params=params, bias=bias)
            rows.append({**r, "regime": regime, "mean_kn": cs, "gust_kn": cg,
                         "conf": "med", "foehn_note": None,
                         "spread_kn": None, "q_kn": None, "lapse": None})
        out.append({"lake": lake, "kind": "forecast", "date": d, "run_stamp": stamp,
                    "backfilled": True, "label": label,
                    "summary": fc._summary(lake, [{**r, "bft": fc.beaufort(r["mean_kn"])}
                                                  for r in rows]),
                    "hourly": rows})
    return out


def write(lake, records):
    """Merge into the forecast log, idempotent per (date, run_stamp), never clobbering a
    REAL record: an existing non-backfilled record for a date always wins."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    kept, real_dates = [], set()
    if os.path.exists(path):
        for line in open(path):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            if not r.get("backfilled"):
                real_dates.add(r.get("date"))
            kept.append(line)
    added = 0
    for rec in records:
        if rec["date"] in real_dates:
            continue                       # a genuine forecast exists; leave it alone
        key = (rec["date"], rec["run_stamp"])
        kept = [l for l in kept
                if (lambda r: not r or (r.get("date"), r.get("run_stamp")) != key)(
                    _safe(l))]
        kept.append(json.dumps(rec))
        added += 1
    with open(path, "w") as f:
        f.write("\n".join(kept) + "\n")
    return added


def _safe(line):
    try:
        return json.loads(line)
    except Exception:
        return None


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__.strip().splitlines()[-1])
        sys.exit(1)
    lake, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
    recs = build(lake, start, end)
    n = write(lake, recs)
    print(f"{lake}: reconstructed {len(recs)} day(s) {start}..{end}, wrote {n} "
          f"(existing real forecasts left untouched)")
