#!/usr/bin/env python3
"""
bsv.py — measured wind for Ammersee from the BSV Herrsching station.

WHY THIS EXISTS
The Ammerseeboje (the only buoy in GKD's entire wind network) has been offline since
2026-06-15 with an electronics defect, repair "einige Wochen". While it is down Ammersee
has no on-water truth, so it is graded against shore stations. BSV is one of the two that
feed the blend; unlike the buoy it also measures GUSTS and DIRECTION.

WHAT THE NUMBERS ACTUALLY SAY — measured on 4,746 paired hours against the buoy, with the
held-out comparison on 1,273 of them, both sources calibrated:

    BSV Herrsching, calibrated   MAE 2.915 kn     raw r=0.651
    DWD Wielenbach, calibrated   MAE 2.788 kn     raw r=0.660
    mean of the two              MAE 2.639 kn   <- what measured_source publishes

BSV does NOT beat Wielenbach. The two track the lake almost identically and DWD is
marginally ahead; blending them beats either, because they sit on opposite shores and much
of their error is independent local noise.

An earlier version of this docstring claimed BSV tracked three times better (r=0.486 vs
0.171) and that Wielenbach was useless. That came from a 109-hour sample and did not
survive the full test. The wrong numbers are recorded here on purpose: they were
convincing, they were cherry-picked without meaning to be, and they nearly got a working
station deleted. Do not re-rank these sources from a short window.

HONEST LIMITS
BSV is a sheltered shore station and its range is compressed — on 2026-06-05 the buoy
spanned 1.4–17.9 kn across the day (13x) while BSV spanned 2.4–6.2 (2.6x). Calibration can
stretch the average back out but cannot recover a 17.9 kn morning from a 5.3 kn reading.
The blend is the best AVAILABLE truth while the buoy is down; it is not a substitute for
it. When the buoy returns it resumes as the speed truth automatically, with BSV supplying
direction and gusts — that last preference is a physical argument (a real sensor at the
lake beats one 11 km inland), NOT a measured one: the buoy reports no direction or gust to
validate either against.

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
# The DURABLE store, committed to git. cache/ is gitignored and lives only on whichever
# machine did the fetching, so without this the whole history would exist nowhere the day
# the club's server goes away — and the calibration could never be rebuilt or improved.
# One line per date, hours as compact [mean_kn, gust_kn, dir] triples.
ARCHIVE = os.path.join(wd.LOG_DIR, "ammersee_bsv_archive.jsonl")
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


_ARCHIVE_CACHE = None


def load_archive():
    """{date -> {hour -> {...}}} from the committed archive. Read once per process."""
    global _ARCHIVE_CACHE
    if _ARCHIVE_CACHE is not None:
        return _ARCHIVE_CACHE
    out = {}
    if os.path.exists(ARCHIVE):
        for line in open(ARCHIVE):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out[r["date"]] = {int(h): {"mean_kn": v[0], "gust_kn": v[1],
                                           "dir": v[2], "n": v[3]}
                                  for h, v in r["hours"].items()}
            except Exception:
                continue                      # one bad line must not blank the archive
    _ARCHIVE_CACHE = out
    return out


def save_archive(days):
    """Merge `days` ({date -> hourly-dict}) into the committed archive, atomically.
    Existing dates are REPLACED, so a re-fetch corrects rather than duplicates."""
    merged = dict(load_archive())
    merged.update({d: v for d, v in days.items() if v})
    tmp = ARCHIVE + ".tmp"
    with open(tmp, "w") as f:
        for d in sorted(merged):
            hrs = {str(h): [v["mean_kn"], v["gust_kn"], v["dir"], v.get("n", 0)]
                   for h, v in sorted(merged[d].items())}
            f.write(json.dumps({"date": d, "hours": hrs}, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ARCHIVE)
    global _ARCHIVE_CACHE
    _ARCHIVE_CACHE = merged
    return len(merged)


def _aggregate(samples):
    """15-min samples -> hourly. Mean of the speeds, MAX of the gusts (a gust is an
    extreme; averaging it away defeats the point), circular mean of direction."""
    by_hour = {}
    for h, spd, gust, deg in samples:
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


def hourly(date, use_cache=True):
    """{hour -> {'mean_kn','gust_kn','dir','n'}} for one date, or {} if unusable.

    Reads the committed ARCHIVE first for past dates — that is what makes a CI runner, with
    no cache/ directory at all, able to work offline over the whole history, and what keeps
    the data alive if the club's server disappears. Today is always fetched, since it is
    still filling up."""
    today = datetime.datetime.now(wd.BERLIN).strftime("%Y-%m-%d")
    if use_cache and date < today:
        got = load_archive().get(date)
        if got:
            return got
    return _aggregate(fetch_day(date, use_cache))


def sync_archive(quiet=True):
    """Fold every complete day sitting in the transient cache into the committed archive.
    Called by the daily run, so each morning's fetch becomes durable automatically."""
    today = datetime.datetime.now(wd.BERLIN).strftime("%Y-%m-%d")
    have = load_archive()
    add = {}
    if os.path.isdir(CACHE):
        for name in sorted(os.listdir(CACHE)):
            if not name.endswith(".json"):
                continue
            d = name[:-5]
            if d >= today or d in have:
                continue                      # incomplete, or already durable
            try:
                h = _aggregate(fetch_day(d, use_cache=True))
            except Exception:
                continue
            if len(h) >= MIN_HOURS_DAY:
                add[d] = h
    if add:
        n = save_archive(add)
        if not quiet:
            print(f"  archived {len(add)} new day(s); {n} total")
    return {"added": len(add), "total": len(load_archive())}


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


def _selftest():
    """The committed archive must serve past days with NO network and NO cache.

    That is the whole point of it: cache/ is gitignored and the CI runner is ephemeral, so
    if hourly() could only answer by fetching, the history would exist on one laptop and
    would die with the club's server."""
    import tempfile
    arch = load_archive()
    assert arch, f"no archive at {ARCHIVE} — run `python lib/bsv.py archive`"
    saved_cache, saved_fetch = CACHE, fetch_day
    try:
        globals()["CACHE"] = tempfile.mkdtemp(prefix="bsv_selftest_")
        def _no_network(*a, **k):
            raise AssertionError("archived day hit the network")
        globals()["fetch_day"] = _no_network
        ds = sorted(arch)
        for d in (ds[0], ds[len(ds) // 2], ds[-1]):
            h = hourly(d)
            assert h, f"{d} is archived but returned nothing"
            for v in h.values():
                assert v["gust_kn"] >= 0 and v["mean_kn"] >= 0
                assert v["dir"] is None or 0 <= v["dir"] < 360, f"bad direction on {d}"
                assert v["gust_kn"] >= v["mean_kn"] - 1e-9, \
                    f"{d}: gust below mean is physically impossible"
    finally:
        globals()["CACHE"], globals()["fetch_day"] = saved_cache, saved_fetch
    n = sum(len(v) for v in arch.values())
    print(f"  PASS bsv archive: {len(arch)} days / {n} hours served offline, "
          f"{ds[0]} .. {ds[-1]}")
    print("ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
        sys.exit(0)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "archive":
        r = sync_archive(quiet=False)
        a = load_archive()
        ds = sorted(a)
        print(f"=== BSV archive ({ARCHIVE}) ===")
        print(f"  {r['added']} new day(s) folded in; {len(ds)} days total"
              + (f", {ds[0]} .. {ds[-1]}" if ds else ""))
        print(f"  {sum(len(v) for v in a.values())} hourly records")
        sys.exit(0)
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
