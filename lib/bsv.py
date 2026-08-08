#!/usr/bin/env python3
"""
bsv.py — measured wind for Ammersee from the BSV Herrsching station.

WHY THIS EXISTS
The Ammerseeboje (the only buoy in GKD's entire wind network) has been offline since
2026-06-15 with an electronics defect, repair "einige Wochen". While it is down, Ammersee
was being trained and graded against DWD Wielenbach — a station 11 km inland whose
correlation with the lake is r=0.171. Measured over 110 paired hours against the buoy:

    source                reads   MAE      r
    BSV Herrsching         43 %   4.50 kn  0.486
    DWD Wielenbach         37 %   5.19 kn  0.171

r is what decides whether a source is usable at all. A scale error is fixable — multiply
it out, which is what obs_calib does. Poor tracking is not: scaling noise gives bigger
noise. Wielenbach's worst failure is not that it reads low, it is that on 2026-06-05 its
HIGHEST reading of the day (6.0 kn at 16:00) landed on the lake's calmest hour (1.4 kn),
while at 06:00 the lake blew 17.9 kn and it reported 1.9. BSV tracks about three times
better, and unlike the buoy it also measures GUSTS and DIRECTION.

HONEST LIMITS
BSV is a sheltered shore station and its range is compressed — on that same day the buoy
spanned 1.4–17.9 kn (13x) while BSV spanned 2.4–6.2 kn (2.6x). Calibration can stretch the
average back out but cannot recover a 17.9 kn morning from a 5.3 kn reading. BSV is the
best AVAILABLE truth while the buoy is down; it is not a buoy replacement. When the buoy
returns it should resume as the speed truth, with BSV supplying direction and gusts (which
the buoy lacks and Wielenbach reports badly).

THE SOURCE
A sailing club's Davis Vantage on WeatherLink Cloud, surfaced through a PWS Dashboard.
The graph page embeds its whole series inline as `allLinesArray`, 15-minute resolution,
and accepts an explicit y/m/d, so ANY past day is one request — no polling, no sampler.
History reaches back to roughly mid-2022 (2022-06-15 returns a full day, 2021-06-15 none).

Each row is 16 comma-separated fields:
    0  local timestamp "YYYY-MM-DD HH:MM:SS"      8  humidity %
    1  temperature °C                             9  rain
    2  dewpoint °C                               10-14 misc / unused
    3  pressure hPa                              15  UTC timestamp "...Z"
    4  wind direction, cardinal
    5  wind direction, degrees
    6  wind speed km/h
    7  wind gust km/h

This is a small club's own server. Days are cached on disk and never re-fetched, and
fetches are rate-limited. Do not hammer it.
"""
import os, sys, re, json, time, datetime, urllib.request, ssl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd

STATION = "BSV Herrsching (on-lake, Ammersee east shore)"
BASE = ("http://wetter.bsv-ammersee.de/PWS_graph_xx.php?type=wind"
        "&script=wind_c_block.php&theme=user&lang=de-dl&units=metric&period=day1")
CACHE = os.path.join(wd.CACHE_DIR, "bsv")
KMH_KN = 0.539957          # km/h -> knots
MIN_SAMPLES_HOUR = 2       # below this an hour is not a usable mean (of 4 expected)
MIN_HOURS_DAY = 3          # below this the day is unusable and the caller falls back
FETCH_PAUSE_S = 0.7        # politeness between requests to a small club server
FIRST_DATE = "2022-07-01"  # conservative floor; 2021-06-15 returns nothing


def _parse(raw, date):
    """allLinesArray rows -> [(hour_local, speed_kn, gust_kn, dir_deg), ...].

    The hour comes from the UTC column converted to Europe/Berlin, NOT from the local
    string. Both are present and they agree today, but deriving it from UTC is correct
    across the DST folds by construction — this project has already shipped three separate
    UTC-vs-local join bugs, and the row hands us the unambiguous field for free."""
    out = []
    for line in re.findall(r'allLinesArray\[\d+\]\s*=\s*"([^"]+)"', raw):
        f = line.split(",")
        if len(f) < 16:
            continue
        try:
            utc = datetime.datetime.strptime(f[15].strip(), "%Y-%m-%dT%H:%M:%SZ")
            utc = utc.replace(tzinfo=datetime.timezone.utc)
            local = utc.astimezone(wd.BERLIN)
            if local.strftime("%Y-%m-%d") != date:
                continue                       # row belongs to the neighbouring day
            spd, gust, deg = float(f[6]), float(f[7]), float(f[5])
        except (ValueError, IndexError):
            continue                           # a malformed row must not kill the day
        if spd < 0 or gust < 0:
            continue
        out.append((local.hour, spd * KMH_KN, gust * KMH_KN, deg % 360))
    return out


def fetch_day(date, use_cache=True):
    """Raw 15-minute samples for one Europe/Berlin date. Cached permanently once a day is
    complete; today's is cached only briefly because it is still filling up."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{date}.json")
    today = datetime.datetime.now(wd.BERLIN).strftime("%Y-%m-%d")
    if use_cache and os.path.exists(path):
        fresh = date < today or (time.time() - os.path.getmtime(path) < 900)
        if fresh:
            try:
                with open(path) as fh:
                    return [tuple(x) for x in json.load(fh)]
            except Exception:
                pass                           # unreadable cache is not fatal, refetch
    y, m, d = date.split("-")
    url = f"{BASE}&y={int(y)}&m={int(m)}&d={int(d)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60,
                                 context=ssl._create_unverified_context()
                                 ).read().decode("utf-8", "replace")
    rows = _parse(raw, date)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rows, fh)
    os.replace(tmp, path)
    time.sleep(FETCH_PAUSE_S)
    return rows


def hourly(date, use_cache=True):
    """{hour -> {'mean_kn','gust_kn','dir','n'}} for one date, or {} if unusable.

    Mean of the speed samples, MAX of the gust samples (a gust is an extreme, averaging it
    away would defeat the point), circular mean of direction."""
    by_hour = {}
    for h, spd, gust, deg in fetch_day(date, use_cache):
        by_hour.setdefault(h, []).append((spd, gust, deg))
    out = {}
    for h, v in by_hour.items():
        if len(v) < MIN_SAMPLES_HOUR:
            continue                           # one sample is not an hourly mean
        out[h] = {"mean_kn": round(sum(x[0] for x in v) / len(v), 1),
                  "gust_kn": round(max(x[1] for x in v), 1),
                  "dir": wd._circ_mean_deg([x[2] for x in v]),
                  "n": len(v)}
    return out


def backfill(start, end, quiet=False):
    """Warm the cache for a date range so later calls are offline. Returns a summary.
    Failures are collected, never raised — one bad day must not abort a long backfill."""
    d0 = datetime.date.fromisoformat(max(start, FIRST_DATE))
    d1 = datetime.date.fromisoformat(end)
    got = miss = 0
    days = []
    while d0 <= d1:
        ds = d0.isoformat()
        try:
            n = len(hourly(ds))
            got += 1 if n >= MIN_HOURS_DAY else 0
            miss += 0 if n >= MIN_HOURS_DAY else 1
            days.append((ds, n))
            if not quiet and len(days) % 25 == 0:
                print(f"    ... {ds}  ({got} usable / {len(days)} fetched)", flush=True)
        except Exception as e:
            miss += 1
            days.append((ds, f"ERR {type(e).__name__}"))
        d0 += datetime.timedelta(days=1)
    return {"start": start, "end": end, "usable_days": got, "unusable_days": miss,
            "days": days}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "backfill":
        r = backfill(sys.argv[2], sys.argv[3])
        print(f"=== BSV backfill {r['start']} .. {r['end']} ===")
        print(f"  {r['usable_days']} usable day(s), {r['unusable_days']} unusable")
        bad = [d for d, n in r["days"] if not isinstance(n, int) or n < MIN_HOURS_DAY]
        print(f"  gaps: {bad[:12]}{' ...' if len(bad) > 12 else ''}")
    else:
        date = sys.argv[2] if len(sys.argv) > 2 else \
            (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        h = hourly(date)
        print(f"=== {STATION} — {date} ({len(h)} usable hours) ===")
        for k in sorted(h):
            v = h[k]
            print(f"  {k:02d}h  mean {v['mean_kn']:5.1f} kn   gust {v['gust_kn']:5.1f} kn   "
                  f"dir {wd._circ_mean_deg([v['dir']]):3d}°  ({v['n']} samples)")
