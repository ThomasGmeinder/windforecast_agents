#!/usr/bin/env python3
"""
winddata.py — vetted data-access helpers for the Bavarian lake wind agents.

All network access goes through Python urllib with the system CA bundle so it
validates through the Zscaler TLS-intercepting proxy on this machine (urllib +
system certs is the verified-working path).

Run with the project venv which has cfgrib/eccodes/xarray:
    /home/tgmeinde/wind-agents/.venv/bin/python

Sources, in order of quality:
  1. icon_d2_grib_point()  -> raw DWD ICON-D2 GRIB (BEST: native model field), cached
  2. openmeteo_point()     -> ICON-D2 as a point forecast (fast, no GRIB decode)
  3. openmeteo_ensemble()  -> ICON-D2 ensemble members -> confidence spread
  4. mosmix_pressure()     -> DWD MOSMIX MSL pressure series (for foehn dp)
  5. foehn_delta_p()       -> Bozen - Muenchen pressure difference time series
Plus log_record() to append forecast/actual pairs for later bias-correction.
"""
import ssl, os, re, bz2, io, json, zipfile, datetime, time as _time
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET

try:                                    # the project's one timezone: all series are joined
    from zoneinfo import ZoneInfo       # in Europe/Berlin local time
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:                       # no tzdata: fall back to fixed CEST rather than
    BERLIN = datetime.timezone(datetime.timedelta(hours=2))   # silently mis-joining by 2 h

CA = "/etc/ssl/certs/ca-certificates.crt"
_CTX = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
_UA = {"User-Agent": "bavaria-lake-wind-agent/1.0"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "cache")
LOG_DIR = os.path.join(ROOT, "logs")
for _d in (CACHE_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# Station IDs used across the agents
STA_MUENCHEN = "10865"   # Muenchen-Stadt (MOSMIX)
STA_BOZEN = "16020"      # Bozen / Bolzano (MOSMIX)
STA_INNSBRUCK = "11120"  # Innsbruck (MOSMIX, foehn cross-check)


def _get(url, nbytes=None, timeout=60):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read(nbytes)


# ---------------------------------------------------------------- Open-Meteo
def openmeteo_point(lat, lon, hourly, models="icon_d2", forecast_days=2,
                    base="https://api.open-meteo.com/v1/forecast"):
    """ICON-D2 (or other) as a point forecast. `hourly` is a list of variable
    names, e.g. ['wind_speed_10m','wind_gusts_10m','wind_direction_10m',
    'cloud_cover','shortwave_radiation','wind_speed_850hPa',
    'wind_direction_850hPa','temperature_850hPa','relative_humidity_850hPa']."""
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": ",".join(hourly),
        "models": models, "forecast_days": forecast_days,
        "timezone": "Europe/Berlin", "wind_speed_unit": "kn",
    })
    return json.loads(_get(f"{base}?{q}").decode())


def openmeteo_ensemble(lat, lon, hourly, models="icon_d2", forecast_days=2):
    """ICON-D2 ensemble members -> spread/confidence."""
    return openmeteo_point(lat, lon, hourly, models=models,
                           forecast_days=forecast_days,
                           base="https://ensemble-api.open-meteo.com/v1/ensemble")


# --------------------------------------------------------------- Raw ICON-D2 GRIB (best)
def icon_d2_grib_point(param, fhour, lat, lon, run="00", use_cache=True):
    """Download+decode one ICON-D2 regular-lat-lon single-level field and return
    the value at the nearest gridpoint. `param` e.g. 'u_10m','v_10m','vmax_10m',
    't_2m','pmsl','clct'. `fhour` int forecast hour 0..48. Files roll over ~24h
    on Open Data, so we cache the decoded value locally on first fetch."""
    import xarray as xr
    base = f"https://opendata.dwd.de/weather/nwp/icon-d2/grib/{run}/{param}/"
    html = _get(base).decode("utf-8", "replace")
    pat = re.compile(
        rf'icon-d2_germany_regular-lat-lon_single-level_(\d+)_{int(fhour):03d}_2d_{param}\.grib2\.bz2')
    m = sorted(set(pat.findall(html)))
    if not m:
        raise FileNotFoundError(f"no {param} f{fhour:03d} file in {base}")
    runstamp = m[-1]
    fname = f"icon-d2_germany_regular-lat-lon_single-level_{runstamp}_{int(fhour):03d}_2d_{param}.grib2.bz2"
    cache = os.path.join(CACHE_DIR, fname.replace(".bz2", ""))
    if not (use_cache and os.path.exists(cache)):
        raw = _get(base + fname)
        with open(cache, "wb") as f:
            f.write(bz2.decompress(raw))
    ds = xr.open_dataset(cache, engine="cfgrib", backend_kwargs={"indexpath": ""})
    v = list(ds.data_vars)[0]
    sub = ds[v].sel(latitude=lat, longitude=lon, method="nearest")
    return {"param": param, "fhour": int(fhour), "run": runstamp,
            "value": float(sub.values),
            "gp_lat": float(sub.latitude), "gp_lon": float(sub.longitude)}


# --------------------------------------------------------------- DWD MOSMIX
_KML = "{http://www.opengis.net/kml/2.2}"
_DWD = "{https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd}"


def mosmix_pressure(station):
    """Return (timesteps[list of ISO str], pressures[list of hPa or None]) for a
    MOSMIX_L single station. Element PPPP is MSL pressure in Pa -> /100 = hPa."""
    base = f"https://opendata.dwd.de/weather/local_forecasts/mos/MOSMIX_L/single_stations/{station}/kml/"
    html = _get(base).decode("utf-8", "replace")
    kmz = sorted(set(re.findall(rf'MOSMIX_L_\d+_{station}\.kmz', html)))
    if not kmz:
        raise FileNotFoundError(f"no MOSMIX kmz for station {station}")
    data = _get(base + kmz[-1])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        kml = z.read(z.namelist()[0])
    root = ET.fromstring(kml)
    steps = [e.text for e in root.iter(f"{_DWD}TimeStep")]
    pres = None
    for fc in root.iter(f"{_DWD}Forecast"):
        if fc.get(f"{_DWD}elementName") == "PPPP":
            vals = fc.find(f"{_DWD}value").text.split()
            pres = [None if x == "-" else float(x) / 100.0 for x in vals]
            break
    return steps, pres


def _to_local_hour(utc_iso):
    """MOSMIX timesteps are UTC ('2026-08-03T04:00:00.000Z'); every other series here is
    Europe/Berlin local (Open-Meteo is queried with timezone=Europe/Berlin). Return the
    'YYYY-MM-DDTHH' LOCAL key so the two can be joined. Joining the raw UTC string to a
    local one silently shifts the foehn gradient by 2 h in summer / 1 h in winter."""
    try:
        dt = datetime.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(BERLIN).strftime("%Y-%m-%dT%H")


def foehn_delta_p(south=STA_BOZEN, north=STA_MUENCHEN):
    """Cross-Alpine pressure difference dp = p(south) - p(north) time series.
    dp >= ~4 hPa noticeable, >= ~8 hPa reaches the lake surfaces (south foehn).
    `time` is the raw UTC stamp; `hour_local` is the Europe/Berlin 'YYYY-MM-DDTHH' key
    callers must use when joining against the (local-time) forecast series."""
    ts_s, p_s = mosmix_pressure(south)
    ts_n, p_n = mosmix_pressure(north)
    pn = dict(zip(ts_n, p_n))
    out = []
    for t, ps in zip(ts_s, p_s):
        pnv = pn.get(t)
        dp = (ps - pnv) if (ps is not None and pnv is not None) else None
        out.append({"time": t, "hour_local": _to_local_hour(t),
                    "p_south": ps, "p_north": pnv, "dp": dp})
    return out


# --------------------------------------------------------------- logging
# nearest DWD 10-min wind station per lake (see notes: southern lakes only have a
# distant VALLEY station, so their "actual" is a proxy until an on-lake feed is logged)
MS_TO_KN = 1.943844
STA_OBS = {"ammersee": "05538",     # Wielenbach (Demollstr.), 551 m, lake-level ~11 km S
           "kochelsee": "01550",    # Garmisch-Partenkirchen, 719 m valley (distant proxy)
           "walchensee": "01550"}   # Garmisch-Partenkirchen (distant proxy)


def _circ_mean_deg(degs):
    import math
    xs = [math.radians(d) for d in degs]
    if not xs:
        return None
    s = sum(math.sin(x) for x in xs)
    c = sum(math.cos(x) for x in xs)
    # Modulo AFTER rounding. The other order lets any mean in [359.5, 360) round up to a
    # bare 360, which is not a bearing — it appeared 7 times in the BSV archive before a
    # range assertion caught it. compass() happened to render it as N, so it was invisible.
    return round(math.degrees(math.atan2(s, c)) + 360) % 360


# --------------------------------------------------------------- GKD Bayern (on-lake buoy)
# Gewässerkundlicher Dienst Bayern. The Ammerseeboje is an official buoy ON the lake and is
# the ONLY buoy in GKD's entire wind network (all 127 stations enumerated; no second one
# exists), which makes it the best possible ground truth for Ammersee — far better than a
# land station kilometres inland. Speed only, hourly. Data © GKD Bayern, CC BY 4.0.
#
# Timestamps are Europe/Berlin, i.e. the same frame as the Open-Meteo forecast series, so
# no conversion is applied. That was VERIFIED empirically rather than assumed (the page
# states no timezone): cross-correlating a live GKD land station against DWD 10-min data
# converted to Berlin peaks at lag 0 on clean-signal days (r=0.86, r=0.90). There is a
# residual ±1 h ambiguity from hour-labelling convention that a longer overlap would settle.
GKD_TABELLE = ("https://www.gkd.bayern.de/de/meteo/{param}/{basin}/{slug}"
               "/messwerte/tabelle?beginn={beg}&ende={end}")
GKD_WIND = {"ammersee": ("isar", "ammerseeboje-16601050", "GKD Ammerseeboje (on-lake)")}
GKD_MIN_HOURS = 3          # below this the day is treated as unusable and we fall back


def gkd_wind_range(basin, slug, beg_ger, end_ger, param="wind", timeout=90):
    """All hourly values in a date range -> [(iso_date, hour, knots), ...].

    The station page renders only the last few hours; the `/messwerte/tabelle` view with an
    explicit range returns the whole span — a full year of hourly values comes back in one
    request, which is what makes building a multi-year climatology cheap and polite.
    Values are m/s with a German decimal comma."""
    url = GKD_TABELLE.format(param=param, basin=basin, slug=slug, beg=beg_ger, end=end_ger)
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _get(url, timeout=timeout).decode("utf-8", "replace")))
    out = []
    for ts, val in re.findall(r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})[^\d\-]{0,20}(-?[\d]+,[\d]+)", txt):
        iso = f"{ts[6:10]}-{ts[3:5]}-{ts[0:2]}"
        out.append((iso, int(ts[11:13]), round(float(val.replace(",", ".")) * MS_TO_KN, 1)))
    return out


def gkd_wind_hourly(basin, slug, yyyy_mm_dd, param="wind"):
    """Hourly mean wind for one GKD station and one LOCAL date -> {hour:int -> knots}."""
    ger = datetime.date.fromisoformat(yyyy_mm_dd).strftime("%d.%m.%Y")
    return {h: kn for iso, h, kn in gkd_wind_range(basin, slug, ger, ger, param, timeout=40)
            if iso == yyyy_mm_dd}


def dwd_obs_all(station):
    """Every hourly mean in the DWD `recent` 10-min archive in ONE download ->
    {(iso_date, hour): {'mean_kn','gust_kn','dir'}}. dwd_obs_hourly re-downloads the zip
    per call, which is fine for one day and hopeless for the ~500 days a calibration
    needs. Same UTC->Europe/Berlin rule as dwd_obs_hourly."""
    base = ("https://opendata.dwd.de/climate_environment/CDC/observations_germany/"
            "climate/10_minutes/wind/recent/")
    html = _get(base).decode("latin-1", "replace")
    fn = re.findall(rf'10minutenwerte_wind_{station}[^"]*\.zip', html)
    if not fn:
        raise FileNotFoundError(f"no recent 10-min wind file for station {station}")
    z = zipfile.ZipFile(io.BytesIO(_get(base + fn[0])))
    name = [n for n in z.namelist() if n.startswith("produkt")][0]
    buck = {}
    for line in z.read(name).decode("latin-1").splitlines()[1:]:
        p = [x.strip() for x in line.split(";")]
        if len(p) < 5:
            continue
        loc = _mess_datum_local(p[1])
        if loc is None:
            continue
        ff, dd = float(p[3]), float(p[4])
        if ff <= -999 or dd <= -999:
            continue
        buck.setdefault((loc.strftime("%Y-%m-%d"), loc.hour), []).append((ff, dd))
    out = {}
    for k, vals in buck.items():
        ff = [v[0] for v in vals]
        out[k] = {"mean_kn": round(sum(ff) / len(ff) * MS_TO_KN, 1),
                  "gust_kn": round(max(ff) * MS_TO_KN, 1),
                  "dir": _circ_mean_deg([v[1] for v in vals])}
    return out


def _mess_datum_local(stamp):
    """DWD MESS_DATUM ('YYYYMMDDHHMM', UTC) -> naive Europe/Berlin datetime, or None."""
    try:
        dt = datetime.datetime.strptime(stamp[:12], "%Y%m%d%H%M")
    except Exception:
        return None
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(BERLIN)


def dwd_obs_hourly(station, yyyymmdd):
    """Hourly-aggregated actual wind for one DWD station and one date (YYYYMMDD),
    from the `recent` 10-min archive (covers through yesterday 23:50). Returns
    {hour:int -> {'mean_kn','gust_kn','dir'}}. Missing values (-999) dropped.
    gust_kn is the max 10-min mean in the hour (a gust *proxy*, not FX_10)."""
    base = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/wind/recent/"
    html = _get(base).decode("latin-1", "replace")
    fn = re.findall(rf'10minutenwerte_wind_{station}[^"]*\.zip', html)
    if not fn:
        raise FileNotFoundError(f"no recent 10-min wind file for station {station}")
    z = zipfile.ZipFile(io.BytesIO(_get(base + fn[0])))
    name = [n for n in z.namelist() if n.startswith("produkt")][0]
    # DWD MESS_DATUM is UTC ("The time stamp is given in UTC" — CDC 10-minute wind
    # description). Every consumer of this dict joins it to Europe/Berlin forecast hours,
    # so bucket on the LOCAL hour and filter on the LOCAL date; keying on the raw stamp
    # shifted every measured value by 2 h in summer / 1 h in winter. Same trap as the
    # foehn dp join and the run_stamp comparison.
    want = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    buckets = {}
    for line in z.read(name).decode("latin-1").splitlines()[1:]:
        p = [x.strip() for x in line.split(";")]
        if len(p) < 5:
            continue
        local = _mess_datum_local(p[1])
        if local is None or local.strftime("%Y-%m-%d") != want:
            continue
        ff, dd = float(p[3]), float(p[4])
        if ff <= -999 or dd <= -999:
            continue
        buckets.setdefault(local.hour, []).append((ff, dd))
    out = {}
    for hh, vals in buckets.items():
        ff = [v[0] for v in vals]
        out[hh] = {"mean_kn": round(sum(ff) / len(ff) * MS_TO_KN, 1),
                   "gust_kn": round(max(ff) * MS_TO_KN, 1),
                   "dir": _circ_mean_deg([v[1] for v in vals])}
    return out


# --------------------------------------------------------------- addicted-sports on-lake measured wind (preferred actuals)
# These are the site's OWN on-water anemometer readings (knots), far more
# representative of the lake than distant DWD valley stations. Measured values are
# typically present for daylight hours only. Endpoint (discovered from the page JS):
#   /forecast/<spot>/?json=wind&from=YYYY-MM-DD  ->  d.mavg (meas avg), d.mmax (meas gust), d.dir
ADS_SPOT = {"walchensee": "walchensee/urfeld", "kochelsee": "kochelsee/trimini"}

# lake coordinates + dry adiabatic lapse rate, for the valley-stability feature
LAKE_LATLON = {"kochelsee": (47.65, 11.35), "walchensee": (47.58, 11.33), "ammersee": (47.98, 11.13)}
DALR = 9.8  # K/km, dry adiabatic lapse rate


def addicted_measured_hourly(spot, yyyy_mm_dd):
    """On-lake MEASURED hourly wind from addicted-sports (knots) for one date.
    Returns {hour:int -> {'mean_kn','gust_kn','dir'}}; only reported (daylight) hours."""
    url = f"https://www.addicted-sports.com/forecast/{spot}/?json=wind&from={yyyy_mm_dd}"
    d = json.loads(_get(url))
    t = d.get("time", []); mavg = d.get("mavg", []); mmax = d.get("mmax", []); dr = d.get("dir", [])
    dd, mm = yyyy_mm_dd[8:10], yyyy_mm_dd[5:7]
    out = {}
    for i, label in enumerate(t):
        m = re.search(r'(\d{2})\.(\d{2})\.\s+(\d{2}):', label or "")
        if not m or m.group(1) != dd or m.group(2) != mm:
            continue
        mv = mavg[i] if i < len(mavg) else None
        mx = mmax[i] if i < len(mmax) else None
        if mv is None and mx is None:
            continue
        mean = round(mv, 1) if mv is not None else round(mx, 1)
        out[int(m.group(3))] = {"mean_kn": mean,
                                "gust_kn": round(mx, 1) if mx is not None else mean,
                                "dir": (dr[i] if i < len(dr) else None)}
    return out


def addicted_forecast(spot, yyyy_mm_dd):
    """addicted-sports' OWN spot-tuned forecast (knots) for one date — hourly avg + gust.
    A strong extra 'member' for the blend since it is tuned to the local thermal/föhn."""
    url = f"https://www.addicted-sports.com/forecast/{spot}/?json=wind&from={yyyy_mm_dd}"
    d = json.loads(_get(url))
    t, avg, boe = d.get("time", []), d.get("avg", []), d.get("boe", [])
    dd, mm = yyyy_mm_dd[8:10], yyyy_mm_dd[5:7]
    out = {}
    for i, label in enumerate(t):
        m = re.search(r'(\d{2})\.(\d{2})\.\s+(\d{2}):', label or "")
        if not m or m.group(1) != dd or m.group(2) != mm:
            continue
        a = avg[i] if i < len(avg) else None
        if a is None:
            continue
        out[int(m.group(3))] = {"avg_kn": a, "boe_kn": (boe[i] if i < len(boe) else None)}
    return out


def hohenpeissenberg_now():
    """Latest Hohenpeißenberg (DWD 02290) 10-min wind — the classic föhn nowcast: S/SE
    wind here in the morning is the precondition for Kochelsee/Walchensee föhn.
    Returns {'time','dir','kn','southerly'} or None."""
    base = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/10_minutes/wind/now/"
    try:
        html = _get(base).decode("latin-1", "replace")
        fn = re.findall(r'10minutenwerte_wind_02290[^"]*\.zip', html)
        if not fn:
            return None
        z = zipfile.ZipFile(io.BytesIO(_get(base + fn[0])))
        name = [n for n in z.namelist() if n.startswith("produkt")][0]
        last = None
        for line in z.read(name).decode("latin-1").splitlines()[1:]:
            p = [x.strip() for x in line.split(";")]
            if len(p) < 5:
                continue
            ff, dd = float(p[3]), float(p[4])
            if ff <= -999 or dd <= -999:
                continue
            last = (p[1], ff, dd)
        if not last:
            return None
        t, ff, dd = last
        return {"time": t, "dir": round(dd), "kn": round(ff * MS_TO_KN, 1),
                "southerly": 120 <= dd <= 210}   # SE–S = reliable föhn sector (SW excluded)
    except Exception:
        return None


# Lakes with an on-lake private weather station (module name -> handled in measured_source).
# Ammersee only: BSV Herrsching, a sailing club's Davis on the east shore. It measures
# speed, GUSTS and DIRECTION at 15-minute resolution with ~4 years of queryable history.
BSV_LAKES = ("ammersee",)


def measured_source(lake, yyyy_mm_dd):
    """THE authority for which measured source a lake/date uses, and its data.

    Returns {'id', 'label', 'data'}, where id is a stable machine token:
        ads   | buoy | buoy+bsv | buoy+dwd | bsv | dwd | none

    Preference order for Ammersee, best first:
      1. the GKD Ammerseeboje — actually ON the water. Speed only, so direction and gusts
         come from the best available companion (BSV, else DWD).
      2. BSV Herrsching, calibrated — while the buoy is down. r=0.486 against the buoy.
      3. DWD Wielenbach, calibrated — last resort only. r=0.171: on 2026-06-05 its highest
         reading of the day landed on the lake's CALMEST hour. It is kept solely so the
         pipeline still produces something if both better sources are unreachable.

    The buoy is retried EVERY day and wins automatically the moment it reports again, so
    recovery needs no intervention; buoywatch.py turns that into a logged, loud event.
    Both actual_hourly and the watcher call this one function — the choice is never
    re-derived anywhere else."""
    if lake in ADS_SPOT:
        try:
            data = addicted_measured_hourly(ADS_SPOT[lake], yyyy_mm_dd)
            if len(data) >= 3:
                return {"id": "ads", "data": data,
                        "label": f"addicted-sports on-lake ({ADS_SPOT[lake]})"}
        except Exception:
            pass  # fall back below

    # on-lake private station (speed + gust + direction), calibrated onto lake-equivalent
    bsv_h, bsv_label = {}, None
    if lake in BSV_LAKES:
        try:
            import bsv as _bsv
            import obs_calib as _oc
            raw = _bsv.hourly(yyyy_mm_dd)
            if len(raw) >= _bsv.MIN_HOURS_DAY:
                m = _oc.load(lake, "bsv")
                bsv_h = {}
                for h, v in raw.items():
                    cm, cg, ok = _oc.correct(lake, h, v["mean_kn"], v["gust_kn"], "bsv")
                    bsv_h[h] = {"mean_kn": cm, "gust_kn": cg, "dir": v["dir"]}
                bsv_label = _bsv.STATION + (
                    f" calibrated to on-lake (+{m['validation']['improvement']*100:.0f}%,"
                    f" {m['n_pairs']} paired hrs)" if m else " (uncalibrated)")
        except Exception:
            bsv_h, bsv_label = {}, None

    st = STA_OBS[lake]
    dwd, dwd_src = None, f"DWD station {st}"

    if lake in GKD_WIND:
        # PREFERRED: the on-lake buoy. It reports SPEED only, so direction and the gust
        # proxy are taken from the DWD station for the same hours — speed is both the most
        # important field and the one a land station distorts most. The buoy is used only
        # for hours it actually covers on THIS date; a defect or a lifted buoy simply means
        # too few hours and we fall through to DWD entirely.
        basin, slug, label = GKD_WIND[lake]
        try:
            buoy = gkd_wind_hourly(basin, slug, yyyy_mm_dd)
        except Exception:
            buoy = {}
        if len(buoy) >= GKD_MIN_HOURS:
            # The buoy is back (or never left): it wins on SPEED, unconditionally.
            # Direction and gusts it does not measure, so they come from the best companion
            # available — BSV first (r=0.486, real gust sensor), DWD only if BSV is down.
            if bsv_h:
                aux_src, aux, sid = "BSV Herrsching", bsv_h, "buoy+bsv"
            else:
                try:
                    dwd = dwd_obs_hourly(st, yyyy_mm_dd.replace("-", ""))
                except Exception:
                    dwd = {}
                aux_src, aux, sid = dwd_src, (dwd or {}), "buoy+dwd"
            data = {}
            for h, kn in buoy.items():
                a = aux.get(h, {})
                data[h] = {"mean_kn": kn,
                           "gust_kn": a.get("gust_kn", kn),   # no gust sensor on the buoy
                           "dir": a.get("dir")}
            extra = f" + {aux_src} for dir/gust" if aux else " (no direction available)"
            return {"id": sid, "data": data, "label": f"{label}{extra}"}

    if dwd is None:
        try:
            dwd = dwd_obs_hourly(st, yyyy_mm_dd.replace("-", ""))
        except Exception:
            dwd = {}
    note = "lake-level, ~11 km inland" if lake == "ammersee" else "valley proxy"

    # The fallback station under-reads the lake badly (Wielenbach measured a mean 3.2 kn
    # where the buoy had 8.2 kn). Where a VALIDATED calibration exists, map the reading
    # onto lake-equivalent wind so we do not train and grade against a truth that is wrong
    # by more than the forecast error. Import is local: obs_calib imports forecast, which
    # would otherwise be a cycle at module load.
    try:
        import obs_calib
    except Exception:
        obs_calib = None
    dwd_cal = {}
    if obs_calib is not None and obs_calib.load(lake):
        for h, v in (dwd or {}).items():
            cm, cg, ok = obs_calib.correct(lake, h, v.get("mean_kn"), v.get("gust_kn"))
            if ok:
                dwd_cal[h] = {**v, "mean_kn": cm, "gust_kn": cg}

    # BLEND. Neither shore station wins on its own — measured on 1273 held-out hours
    # against the buoy, calibrated DWD scored MAE 2.788 kn and calibrated BSV 2.915, but
    # the MEAN OF THE TWO scored 2.639, better than either. They sit on opposite sides of
    # the lake, so a good deal of their error is independent local noise that averaging
    # cancels. (An earlier claim here that BSV tracked three times better than DWD came
    # from a 109-hour sample and did not survive the full 4,746-hour test: r=0.651 vs
    # 0.660, i.e. the same. Small samples lie.)
    # Speed is the blend; direction and gusts come from BSV, which has a real sensor at
    # the lake — the buoy has none and DWD's is 11 km inland. That part is a physical
    # argument, not a measured one: the buoy provides no direction or gust to validate against.
    if bsv_h and dwd_cal:
        both = sorted(set(bsv_h) & set(dwd_cal))
        if both:
            data = {}
            for h in both:
                b, d = bsv_h[h], dwd_cal[h]
                data[h] = {"mean_kn": round((b["mean_kn"] + d["mean_kn"]) / 2, 1),
                           "gust_kn": b.get("gust_kn") or d.get("gust_kn"),
                           "dir": b.get("dir") if b.get("dir") is not None else d.get("dir")}
            for h, v in bsv_h.items():
                data.setdefault(h, v)          # hours only one source covers still count
            for h, v in dwd_cal.items():
                data.setdefault(h, v)
            return {"id": "blend", "data": data,
                    "label": ("blend of BSV Herrsching + DWD Wielenbach, both calibrated "
                              f"to on-lake ({len(both)}/{len(data)} h blended; measured "
                              "2.64 kn MAE vs 2.79 best single source)")}

    if bsv_h:
        return {"id": "bsv", "data": bsv_h, "label": bsv_label + " — DWD unavailable"}
    if dwd_cal:
        m = obs_calib.load(lake)
        gain = m["validation"]["improvement"] * 100
        return {"id": "dwd", "data": dwd_cal,
                "label": (f"{dwd_src} ({note}) calibrated to on-lake "
                          f"(+{gain:.0f}% vs raw, {m['n_pairs']} paired hrs) — BSV unavailable")}
    return {"id": "dwd" if dwd else "none", "data": dwd or {}, "label": f"{dwd_src} ({note})"}


def actual_hourly(lake, yyyy_mm_dd):
    """Preferred measured actuals for a lake/date -> (data, source_label).
    Thin wrapper over measured_source, which owns the choice."""
    s = measured_source(lake, yyyy_mm_dd)
    return s["data"], s["label"]


# --------------------------------------------------------------- foehn/thermal cause drivers + valley stability
def addicted_drivers(spot, yyyy_mm_dd):
    """Hourly CAUSE drivers from addicted-sports for one date. Keys per hour:
    foehn_gradient_hpa (cross-Alpine foehn pressure gradient), wind_speed_850hPa,
    wind_direction_850hPa, lapse_2m_850 (stratification), shortwave_radiation,
    thermik_gradient_hpa. Returns {hour:int -> {...}}."""
    url = f"https://www.addicted-sports.com/forecast/{spot}/?json=wind&from={yyyy_mm_dd}"
    d = json.loads(_get(url))
    t = d.get("time", []); drv = d.get("drivers", {})
    keys = ["foehn_gradient_hpa", "wind_speed_850hPa", "wind_direction_850hPa",
            "lapse_2m_850", "shortwave_radiation", "thermik_gradient_hpa"]
    dd, mm = yyyy_mm_dd[8:10], yyyy_mm_dd[5:7]
    out = {}
    for i, label in enumerate(t):
        m = re.search(r'(\d{2})\.(\d{2})\.\s+(\d{2}):', label or "")
        if not m or m.group(1) != dd or m.group(2) != mm:
            continue
        out[int(m.group(3))] = {k: (drv.get(k) or [None] * len(t))[i] for k in keys}
    return out


def stability_dtheta(yyyy_mm_dd, low="kochelsee", high="walchensee"):
    """Kesselberg valley stability from the two-lake temperature difference.
      dtheta = (T_high - T_low) + DALR*(z_high - z_low)/1000     [potential-temp diff]
      dtheta ~ 0  neutral / foehn-mixed
      dtheta > 0  stable cold-air pool in the lower basin -> thermal capped, foehn capped
      dtheta < 0  super-adiabatic -> strong surface heating -> thermal likely
    Uses Open-Meteo T2m at both lake points (accounts for each cell's elevation).
    Returns {hour:int -> {'t_low','t_high','dt_low_minus_high','dtheta'}}."""
    (la1, lo1), (la2, lo2) = LAKE_LATLON[low], LAKE_LATLON[high]
    dlo = openmeteo_point(la1, lo1, ["temperature_2m"], forecast_days=3)
    dhi = openmeteo_point(la2, lo2, ["temperature_2m"], forecast_days=3)
    zlo, zhi = dlo.get("elevation", 598.0), dhi.get("elevation", 800.0)
    Tlo, Thi, tt = dlo["hourly"]["temperature_2m"], dhi["hourly"]["temperature_2m"], dlo["hourly"]["time"]
    off = DALR * (zhi - zlo) / 1000.0
    out = {}
    for i, ts in enumerate(tt):
        if not ts.startswith(yyyy_mm_dd) or Tlo[i] is None or Thi[i] is None:
            continue
        out[int(ts[11:13])] = {"t_low": Tlo[i], "t_high": Thi[i],
                               "dt_low_minus_high": round(Tlo[i] - Thi[i], 1),
                               "dtheta": round((Thi[i] - Tlo[i]) + off, 2)}
    return out


def log_record(lake, kind, payload, path=None):
    """Append one JSON line to logs/<lake>_forecasts.jsonl. kind='forecast' or
    'actual'. Enables later forecast-vs-actual bias-correction. Uses mtime, not
    Date.now(), only when caller supplies a timestamp in payload."""
    path = path or os.path.join(LOG_DIR, f"{lake}_{kind}.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps({"lake": lake, "kind": kind, **payload}) + "\n")
    return path


EVENTS_LOG = os.path.join(LOG_DIR, "events.jsonl")


def log_event(kind, payload, stamp=None):
    """Record one notable event in the single events log (logs/events.jsonl).
    Idempotent per (kind, lake, date, param): re-logging the same event (e.g. a same-day
    workflow re-run) REPLACES the prior record instead of duplicating it, mirroring the
    forecast log. `param` is included so two different parameter changes on one day are
    kept as separate audit records. kind ∈ {'blend_disagreement','analyst','diff_table','verification','param_change'}; `stamp`
    is an ISO time supplied by the caller (no Date.now() dependency, so it stays
    deterministic). Single authority for the app's notable-event stream."""
    rec = {"kind": kind, **payload}
    if stamp is not None:
        rec["stamp"] = stamp
    # `param` is part of the identity: two DIFFERENT parameters changed for the same lake
    # on the same day are distinct events, and must not overwrite each other's audit trail.
    key = (kind, payload.get("lake"), payload.get("date"), payload.get("param"))
    kept = []
    if os.path.exists(EVENTS_LOG):
        for line in open(EVENTS_LOG):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                kept.append(line)  # preserve anything unparseable rather than lose it
                continue
            if (r.get("kind"), r.get("lake"), r.get("date"), r.get("param")) != key:
                kept.append(line)
    kept.append(json.dumps(rec))
    with open(EVENTS_LOG, "w") as f:
        f.write("\n".join(kept) + "\n")
    return EVENTS_LOG


if __name__ == "__main__":
    print("self-test:")
    d = openmeteo_point(47.65, 11.35, ["wind_speed_10m", "wind_gusts_10m"])
    print("  openmeteo_point:", d["hourly"]["time"][0],
          d["hourly"]["wind_speed_10m"][12], "kn @ idx12")
    g = icon_d2_grib_point("vmax_10m", 24, 47.58, 11.33)
    print("  icon_d2_grib_point vmax_10m f024:", round(g["value"], 2), "m/s",
          f"@({g['gp_lat']:.3f},{g['gp_lon']:.3f}) run {g['run']}")
    dp = foehn_delta_p()
    got = [r for r in dp if r["dp"] is not None][:3]
    print("  foehn_delta_p first 3:", [(r["time"][:13], round(r["dp"], 1)) for r in got])
