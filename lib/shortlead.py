#!/usr/bin/env python3
"""Optional, advisory-only short-lead DWD inputs for Ammersee.

Nothing in this module is a forecast input, an observation truth source, or a
learning input.  It deliberately returns records rather than modifying forecast
rows, making that boundary easy to audit and replay.
"""
import datetime as dt
import io
import json
import math
import re
import struct
import time
import zipfile

import winddata as wd

MEMMINGEN = "03244"
MEMMINGEN_MAX_AGE_MINUTES = 30
WEST_FLOW_SECTOR = (225, 315)
AMMERSEE = (47.98, 11.13)
RADAR_RADIUS_KM = 20
RADAR_LEADS = tuple(range(0, 121, 5))
RADAR_TOTAL_BUDGET_S = 24
# RADVOR publishes one directory per product; RE is the precipitation-rate product.
RADVOR_BASE = "https://opendata.dwd.de/weather/radar/radvor/re/"


def is_west_flow(direction):
    """Initial experimental sector: WSW through NW, inclusive."""
    return direction is not None and WEST_FLOW_SECTOR[0] <= float(direction) <= WEST_FLOW_SECTOR[1]


def _latest_wind_row(blob, issued):
    """Read the newest complete DWD 10-min row at/before ``issued`` from a ZIP."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.startswith("produkt"))
    lines = z.read(name).decode("latin-1").splitlines()
    header = [x.strip() for x in lines[0].split(";")]
    pos = {x: i for i, x in enumerate(header)}
    def get(p, key, fallback=None):
        i = pos.get(key, fallback)
        return p[i].strip() if i is not None and i < len(p) else ""
    latest = None
    for line in lines[1:]:
        p = [x.strip() for x in line.split(";")]
        try:
            observed = wd._mess_datum_local(get(p, "MESS_DATUM", 1))
            mean = float(get(p, "FF_10", 3))
            direction = float(get(p, "DD_10", 4))
            gust_s = get(p, "FX_10")
            gust = float(gust_s) if gust_s and float(gust_s) > -999 else mean
        except (TypeError, ValueError):
            continue
        if observed is None or observed > issued or mean <= -999 or direction <= -999:
            continue
        if latest is None or observed > latest[0]:
            latest = (observed, mean, gust, direction)
    return latest


def memmingen_feature(issued):
    """Latest completed station 03244 reading, or an explicit unavailable record."""
    out = {"source": "DWD 03244 Memmingen experimental upstream observation",
           "available": False, "failure_reason": None, "memmingen_mean_kn": None,
           "memmingen_gust_kn": None, "memmingen_direction": None,
           "memmingen_observed_at": None, "memmingen_age_minutes": None,
           "memmingen_west_flow": None}
    base = ("https://opendata.dwd.de/climate_environment/CDC/observations_germany/"
            "climate/10_minutes/wind/now/")
    try:
        listing = wd._get(base, timeout=8).decode("latin-1", "replace")
        names = re.findall(rf'10minutenwerte_wind_{MEMMINGEN}[^" ]*\.zip', listing)
        if not names:
            raise FileNotFoundError("station file absent")
        row = _latest_wind_row(wd._get(base + sorted(set(names))[-1], timeout=8), issued)
        if row is None:
            raise ValueError("no completed valid observation")
        observed, mean, gust, direction = row
        age = (issued - observed).total_seconds() / 60
        if age < 0 or age > MEMMINGEN_MAX_AGE_MINUTES:
            raise ValueError(f"stale observation ({age:.0f} min)")
        out.update({"available": True, "memmingen_mean_kn": round(mean * wd.MS_TO_KN, 1),
                    "memmingen_gust_kn": round(gust * wd.MS_TO_KN, 1),
                    "memmingen_direction": round(direction) % 360,
                    "memmingen_observed_at": observed.isoformat(timespec="minutes"),
                    "memmingen_age_minutes": round(age, 1),
                    "memmingen_west_flow": is_west_flow(direction)})
    except Exception as exc:
        out["failure_reason"] = f"{type(exc).__name__}: {exc}"
    return out


def _radv_filename_parts(listing):
    """Return (cycle, lead, filename) tuples from DWD's RADVOR directory index.

    DWD has changed filename punctuation over time; accepting both ``+005`` and
    ``005`` lead notation avoids baking a presentation detail into the experiment.
    """
    out = []
    for name in set(re.findall(r'href=["\']([^"\']+)', listing, re.I)):
        low = name.lower()
        if "re" not in low or not (low.endswith(".gz") or low.endswith(".bin") or low.endswith(".dat")):
            continue
        # Live RE names use YYMMDDHHMM (e.g. RE2608261235_000.gz); retain the
        # four-digit-year form too for archived/fixture variants.
        stamps = re.findall(r"(20\d{10}|\d{10})", name)
        nums = re.findall(r"(?:\+|_|-|vv)(\d{3})(?:\D|$)", low)
        if stamps and nums:
            out.append((stamps[0], int(nums[-1]), name))
    return out


def _decode_radar_json(raw):
    """Fixture-friendly decoder; DWD binary decoding is intentionally below."""
    try:
        d = json.loads(raw.decode())
    except Exception:
        return None
    return d if isinstance(d, dict) and "grid" in d else None


def _decode_radolan(raw):
    """Decode the standard RADOLAN/RADVOR 16-bit grid without full-grid expansion."""
    import gzip
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    end = raw.find(b"\x03")
    if end < 0:
        raise ValueError("RADOLAN header terminator missing")
    header = raw[:end].decode("ascii", "replace")
    gp = re.search(r"GP\s*(\d+)x\s*(\d+)", header)
    if not gp:
        raise ValueError("RADOLAN grid dimensions missing")
    cols, rows = map(int, gp.groups())
    pr = re.search(r"PR\s*E([+-]\d+)", header)
    scale = 10 ** int(pr.group(1)) if pr else 1.0
    # RADOLAN projection approximation is adequate for an advisory 20 km window;
    # coordinates are only used to sample a tiny bounding box, never to issue rain totals.
    return {"payload": raw[end + 1:end + 1 + cols * rows * 2], "rows": rows, "cols": cols, "scale": scale,
            "origin": "radolan"}


def _signals(decoded):
    """Extract Ammersee point and 20-km max from either binary or JSON test fixture."""
    if decoded.get("origin") != "radolan":
        grid = decoded["grid"]
        # JSON fixtures supply a small local grid and its cell size in km.
        cell = decoded.get("cell_km", 1.0); cy, cx = decoded.get("point", [len(grid)//2, len(grid[0])//2])
        radius = max(1, round(RADAR_RADIUS_KM / cell))
        vals = [grid[y][x] for y in range(max(0,cy-radius), min(len(grid),cy+radius+1))
                for x in range(max(0,cx-radius), min(len(grid[0]),cx+radius+1))
                if (x-cx)**2 + (y-cy)**2 <= radius**2]
        return float(grid[cy][cx]), float(max(vals)) if vals else None
    # RADOLAN stereographic grid, sampled only in memory after the one product
    # file has been decoded.  The stored rows run north-to-south.
    cols, rows, payload, scale = decoded["cols"], decoded["rows"], decoded["payload"], decoded["scale"]
    lat, lon = map(math.radians, AMMERSEE)
    radius_earth_km, lat_ts, lon0 = 6370.04, math.radians(60), math.radians(10)
    m = (1 + math.sin(lat_ts)) / (1 + math.sin(lat))
    x_km = radius_earth_km * m * math.cos(lat) * math.sin(lon - lon0)
    y_km = -radius_earth_km * m * math.cos(lat) * math.cos(lon - lon0)
    # DWD RADOLAN lower-left corner: (-523.4622, -4658.645) km.
    cx, cy = round(x_km + 523.4622), round(rows - (y_km + 4658.645))
    radius = RADAR_RADIUS_KM  # RADOLAN/RADVOR grid spacing is 1 km.
    def val(x, y):
        v = struct.unpack_from(">H", payload, 2 * (y * cols + x))[0] & 0x0FFF
        return None if v >= 4095 else v * scale
    point = val(cx, cy)
    nearby = [val(x, y) for y in range(cy-radius, cy+radius+1) for x in range(cx-radius, cx+radius+1)
              if (x-cx)**2 + (y-cy)**2 <= radius**2 and val(x,y) is not None]
    return point, max(nearby) if nearby else None


def radar_advisory(issued):
    """Fetch one latest complete RADVOR RE cycle and retain all 0–120 min signals."""
    out = {"issue_time": issued.isoformat(timespec="minutes"), "source": "DWD RADVOR RE",
           "available": False, "radar_cycle_time": None, "failure_reason": None, "leads": []}
    started = time.monotonic()
    try:
        listing = wd._get(RADVOR_BASE, timeout=8).decode("latin-1", "replace")
        parts = _radv_filename_parts(listing)
        cycles = {}
        for cycle, lead, name in parts:
            if lead in RADAR_LEADS:
                cycles.setdefault(cycle, {})[lead] = name
        complete = [c for c, files in cycles.items() if all(x in files for x in RADAR_LEADS)]
        if not complete:
            raise FileNotFoundError("no complete 0–120 min RE cycle")
        cycle = max(complete)
        fmt = "%Y%m%d%H%M" if len(cycle) == 12 else "%y%m%d%H%M"
        out["radar_cycle_time"] = dt.datetime.strptime(cycle, fmt).replace(tzinfo=dt.timezone.utc).astimezone(wd.BERLIN).isoformat(timespec="minutes")
        for lead in RADAR_LEADS:
            if time.monotonic() - started >= RADAR_TOTAL_BUDGET_S:
                raise TimeoutError(f"radar advisory budget {RADAR_TOTAL_BUDGET_S}s exhausted")
            raw = wd._get(RADVOR_BASE + cycles[cycle][lead], timeout=8)
            decoded = _decode_radar_json(raw) or _decode_radolan(raw)
            point, radius = _signals(decoded)
            out["leads"].append({"lead_minutes": lead, "point_signal": point, "radius20km_max": radius})
        out["available"] = True
    except Exception as exc:
        out["failure_reason"] = f"{type(exc).__name__}: {exc}"
    return out


def radar_message(record):
    if not record.get("available"):
        return "radar advisory unavailable"
    values = [x.get("radius20km_max") or 0 for x in record.get("leads", [])]
    return "showers approaching / possible near Ammersee" if max(values, default=0) > 0 else "no radar precipitation signal"


def radar_agreement_report(path=None):
    """Compare prior advisory signals with a later RADVOR cycle, not rain gauges.

    This is deliberately named agreement: radar-to-radar confirmation is useful for
    checking the nowcast experiment, but is not ground-truth rainfall verification.
    """
    path = path or __import__("os").path.join(wd.LOG_DIR, "ammersee_radar_advisory.jsonl")
    records = [json.loads(x) for x in open(path) if x.strip()] if __import__("os").path.exists(path) else []
    cycles = []
    for r in records:
        if not r.get("available") or not r.get("radar_cycle_time"):
            continue
        try: cycles.append((dt.datetime.fromisoformat(r["radar_cycle_time"]), r))
        except ValueError: pass
    paired = []
    for cycle, forecast in cycles:
        for lead in forecast.get("leads", []):
            target = cycle + dt.timedelta(minutes=lead["lead_minutes"])
            later = min(cycles, key=lambda x: abs((x[0] - target).total_seconds()), default=None)
            if not later or abs((later[0] - target).total_seconds()) > 300:
                continue
            observed = next((x for x in later[1].get("leads", []) if x.get("lead_minutes") == 0), None)
            if observed:
                paired.append(((lead.get("radius20km_max") or 0) > 0,
                               (observed.get("radius20km_max") or 0) > 0))
    agree = sum(a == b for a, b in paired)
    return {"label": "radar-agreement verification (not ground-truth rainfall)", "n": len(paired),
            "agreement_fraction": round(agree / len(paired), 3) if paired else None}


def selftest():
    assert is_west_flow(225) and is_west_flow(315) and not is_west_flow(224) and not is_west_flow(316)
    raw = json.dumps({"grid": [[0, 0, 0], [0, 1.5, 2], [0, 0, 0]], "point": [1, 1], "cell_km": 10}).encode()
    assert _signals(_decode_radar_json(raw)) == (1.5, 2.0)
    assert radar_message({"available": True, "leads": [{"radius20km_max": 0}]}) == "no radar precipitation signal"
    listing = '<a href="RE2608281155_000.gz"><a href="RE2608281155_120.gz">'
    parts = _radv_filename_parts(listing)
    assert {(lead, name) for _, lead, name in parts} == {(0, "RE2608281155_000.gz"), (120, "RE2608281155_120.gz")}
    # The DWD parser must never select an observation after issuance.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("produkt_wind.txt", "STATIONS_ID;MESS_DATUM;QN;FF_10;DD_10;FX_10\n03244;202608281000;3;4;270;6\n03244;202608281010;3;9;90;10\n")
    issued = dt.datetime(2026, 8, 28, 12, 5, tzinfo=wd.BERLIN)
    row = _latest_wind_row(buf.getvalue(), issued)
    assert row and row[0] <= issued and row[1:] == (4.0, 6.0, 270.0)
    old_get = wd._get
    try:
        wd._get = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline"))
        assert not memmingen_feature(issued)["available"]
        assert not radar_advisory(issued)["available"]
    finally:
        wd._get = old_get
    print("shortlead self-test: PASS sector + radar fixture signals")


if __name__ == "__main__":
    selftest()
