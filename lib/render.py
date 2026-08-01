#!/usr/bin/env python3
"""
render.py — render the daily wind report as styled HTML.

Three views (all offline; read only the latest logs, no network):
  index_html()            top-level landing page: pick a report
  report_html(group)      one report page per lake group, with a methodology section
Groups: 'kochel-walchensee' (the coupled Alpine-rim pair) and 'ammersee'.

Colors follow the dataviz method (regime = CVD-checked categorical badges,
mean wind = validated blue sequential ramp, confidence = text ink).
"""
import os, sys, json, glob, html, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc

GROUPS = {
    "kochel-walchensee": {
        "title": "Kochelsee & Walchensee",
        "lakes": ["kochelsee", "walchensee"],
        "blurb": "Alpine-rim lakes — föhn vs thermal, coupled through the Kesselberg",
    },
    "ammersee": {
        "title": "Ammersee",
        "lakes": ["ammersee"],
        "blurb": "Pre-Alpine foreland lake — gradient flow + summer thermal",
    },
}

_WIND_RAMP = [(2, "#cde2fb"), (4, "#9ec5f4"), (7, "#6da7ec"), (11, "#3987e5"),
              (16, "#256abf"), (22, "#184f95"), (999, "#0d366b")]


def _wind_cell_style(kn):
    hexc = _WIND_RAMP[-1][1]
    for ub, hx in _WIND_RAMP:
        if kn < ub:
            hexc = hx
            break
    darktext = hexc in ("#cde2fb", "#9ec5f4", "#6da7ec")
    return f"background:{hexc};color:{'#0b0b0b' if darktext else '#fff'}"


def _latest_forecast(lake):
    p = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    if not os.path.exists(p):
        return None
    rec = None
    for line in open(p):
        try:
            rec = json.loads(line)
        except Exception:
            pass
    return rec


def _latest_learning(lake):
    files = sorted(glob.glob(os.path.join(wd.LOG_DIR, "learning", f"{lake}_*.md")))
    return (os.path.basename(files[-1]), open(files[-1]).read()) if files else None


def _generated():
    lrp = os.path.join(wd.LOG_DIR, "latest_report.txt")
    return (datetime.datetime.fromtimestamp(os.path.getmtime(lrp)).strftime("%Y-%m-%d %H:%M")
            if os.path.exists(lrp) else "—")


def _dir_arrow(deg):
    if deg is None:
        return "·"
    return (f'<span class="arr" style="transform:rotate({deg}deg)" '
            f'title="from {fc.compass(deg)}">↓</span>')


def _href(target, static):
    """Link to the index ('' target) or a group page, for dynamic (serve.py) or
    static (GitHub Pages) hosting."""
    if static:
        return f"{target}.html" if target else "index.html"
    return f"/{target}" if target else "/"


def _lake_label(lake):
    rec = _latest_forecast(lake)
    return (rec or {}).get("label", lake.title())


def _measured_rows(lake):
    """Most-recent measured day from the diffs log: (date, source, sorted rows)."""
    p = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
    if not os.path.exists(p):
        return None, None, None
    by_date = {}
    for line in open(p):
        try:
            d = json.loads(line)
        except Exception:
            continue
        by_date.setdefault(d["date"], []).append(d)
    if not by_date:
        return None, None, None
    date = max(by_date)
    rows = sorted(by_date[date], key=lambda r: r["hour"])
    src = next((r.get("source") for r in rows if r.get("source")), "measured")
    return date, src, rows


def _measured_card(lake):
    label = html.escape(_lake_label(lake))
    date, src, rows = _measured_rows(lake)
    if not rows:
        return (f'<section class="card"><h2>{label} <span class="chip meas">measured</span></h2>'
                f'<p class="muted">No measurements shown yet — the measured day appears after the '
                f'next morning run, which compares the previous day\'s forecast to what was '
                f'actually measured on the lake.</p></section>')
    trs = []
    for r in rows:
        kn = r.get("actual_kn") or 0
        reg = r.get("actual_regime", "")
        badge = (f'<span class="badge {reg}">{reg}</span>'
                 if reg and reg != "uncertain" else '<span class="muted">—</span>')
        err = r.get("err_issued_kn")
        note = f'{err:+.1f}' if err is not None else ''
        trs.append(
            f'<tr><td class="hr">{r["hour"]:02d}</td>'
            f'<td class="dir">{_dir_arrow(r.get("dir_actual"))} {fc.compass(r.get("dir_actual"))}</td>'
            f'<td class="wind" style="{_wind_cell_style(kn)}">{kn:.1f}'
            f'<span class="bft">{fc.beaufort(kn)}</span></td>'
            f'<td class="gust">{(r.get("actual_gust_kn") or 0):.0f}</td>'
            f'<td>{badge}</td><td class="note">{note}</td></tr>')
    return f"""
    <section class="card measured">
      <h2>{label} <span class="chip meas">measured · {date}</span></h2>
      <p class="summary">Observed wind from {html.escape(src)}. Regime inferred from the measured
       direction; "vs fc" = measured − that day's forecast (kn). Daylight hours where a station reported.</p>
      <table><thead><tr><th>h</th><th>dir</th><th>mean kn (Bft)</th><th>gust</th>
        <th>regime</th><th>vs&nbsp;fc</th></tr></thead><tbody>{''.join(trs)}</tbody></table>
    </section>"""


def _forecast_card(rec):
    if not rec or not rec.get("hourly"):
        return ""
    label = html.escape(rec.get("label", rec["lake"].title()))
    summ = html.escape(rec.get("summary", ""))
    rows = []
    for r in rec["hourly"]:
        kn = r.get("mean_kn") or 0
        reg = r.get("regime", "gradient")
        note = []
        if r.get("dtheta") is not None and reg in ("thermal", "calm"):
            note.append(f"Δθ{r['dtheta']:+.1f}")
        if reg == "foehn" and r.get("foehn_grad") is not None:
            note.append(f"fg{r['foehn_grad']:+.1f}")
        if not r.get("mean_kn") and reg == "calm":
            note.append("glassy")
        rows.append(
            f'<tr><td class="hr">{r["hour"]:02d}</td>'
            f'<td class="dir">{_dir_arrow(r.get("dir"))} {fc.compass(r.get("dir"))}</td>'
            f'<td class="wind" style="{_wind_cell_style(kn)}">{kn:.1f}'
            f'<span class="bft">{fc.beaufort(kn)}</span></td>'
            f'<td class="gust">{(r.get("gust_kn") or 0):.0f}</td>'
            f'<td><span class="badge {reg}">{reg}</span></td>'
            f'<td class="conf c-{r.get("conf","med")}">{r.get("conf","")}</td>'
            f'<td class="note">{html.escape(" ".join(note))}</td></tr>')
    date = rec.get("date", "")
    return f"""
    <section class="card">
      <h2>{label} <span class="chip fc">forecast · {date}</span></h2>
      <p class="summary">{summ}</p>
      <table>
        <thead><tr><th>h</th><th>dir</th><th>mean kn (Bft)</th><th>gust</th>
          <th>regime</th><th>conf</th><th>note</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>"""


def _analyst_block(lake):
    """The latest advisory LLM-analyst proposal for a lake (from logs/analyst/)."""
    files = sorted(glob.glob(os.path.join(wd.LOG_DIR, "analyst", f"{lake}_*.json")))
    if not files:
        return ""
    try:
        d = json.load(open(files[-1]))
    except Exception:
        return ""
    r = d.get("result", {})
    if not isinstance(r, dict) or r.get("skipped") or not r.get("proposals"):
        return ""
    props = "".join(
        f"<li><code>{html.escape(str(p.get('param')))}</code> → "
        f"<b>{html.escape(str(p.get('proposed')))}</b> — {html.escape(str(p.get('rationale', '')))}</li>"
        for p in r.get("proposals", []))
    return (f'<div class="analyst"><b>🧠 Analyst (advisory · {html.escape(str(d.get("date", "")))}):</b> '
            f'{html.escape(str(r.get("narrative", "")))}'
            + (f'<ul>{props}</ul>' if props else '')
            + '<div class="muted" style="margin-top:4px">Proposals are advisory — the backtest gate '
              'validates any change before it is applied.</div></div>')


def _learning_section(lakes):
    blocks = []
    for lake in lakes:
        ab = _analyst_block(lake)
        if ab:
            blocks.append(ab)
        got = _latest_learning(lake)
        if got:
            fn, md = got
            blocks.append(f'<details><summary>{lake.title()} — learning report '
                          f'({html.escape(fn)})</summary><pre>{html.escape(md)}</pre></details>')
    if not blocks:
        blocks = ['<p class="muted">No learning reports yet — the first appears once the '
                  'morning run has a prior day to compare against.</p>']
    return ('<section class="card"><h2>Self-learning (yesterday vs measured)</h2>'
            + "".join(blocks) + "</section>")


def _methodology(group):
    if group == "kochel-walchensee":
        return """
    <section class="card method">
      <h2>Prediction &amp; learning methodology</h2>
      <p>Kochelsee (~604 m) and Walchensee (~800 m) share one wind system through the
      Kesselberg but are reported separately, because under <b>south föhn</b> they behave
      oppositely: föhn pours down the Kesselberg so <b>Kochelsee turns strong</b> while it
      <b>suppresses the Walchensee NE thermal</b>. Getting that split right is the whole game.</p>
      <h3>Regime, classified each hour</h3>
      <ul>
        <li><b>South föhn</b> — cross-Alpine pressure gap Bozen−München (DWD MOSMIX) ≥ 4–8 hPa
            + southerly 850 hPa wind + the addicted-sports föhn-gradient driver.</li>
        <li><b>Thermal (Walchenseewind)</b> — the NE nozzle between Jochberg &amp; Herzogstand;
            needs sun + weak gradient + no föhn, and is <b>capped when a cold-air pool</b>
            sits in the basin (see stability).</li>
        <li><b>Gradient</b> — frontal / pressure-driven flow (925 hPa).</li>
        <li><b>Fall-winds</b> — cold-night drainage off the north slopes, distinguished from föhn.</li>
      </ul>
      <h3>Terrain locks the wind</h3>
      <p>The basin channels surface wind into fixed sectors (direction it comes <i>from</i> at
      Urfeld): <b>N–NE → thermal</b>, <b>S–SE → föhn/Kesselberg</b>, <b>W–NW → gradient</b>,
      E → Jachenau drainage. The raw model's free-flow direction is treated as unreliable; the
      <i>measured</i> direction is used to validate the regime call.</p>
      <h3>Stability — the master switch</h3>
      <p>The Kochelsee−Walchensee air-temperature difference over the ~200 m gap gives a
      potential-temperature index Δθ. Δθ ≈ 0 = neutral / föhn-mixed; <b>Δθ &gt; ~1.5 K = stable
      cold pool → thermal capped</b> (the dead-Kochelsee-morning); Δθ &lt; 0 = unstable → thermal
      likely. Shown per hour in the <i>note</i> column.</p>
      <h3>Data</h3>
      <ul>
        <li>Forecast backbone: <b>ICON-D2</b> (2.2 km) + 20-member ensemble for confidence.</li>
        <li>Föhn: DWD MOSMIX Bozen−München Δp + addicted-sports drivers (föhn gradient, 850 hPa, lapse).</li>
        <li>Measured truth: the <b>on-lake Urfeld anemometer</b> (addicted-sports, daylight hours);
            Kochelsee falls back to DWD Garmisch (a distant valley proxy).</li>
      </ul>
      <h3>How it learns</h3>
      <p>Every morning <b>before</b> forecasting, yesterday's forecast is compared hour-by-hour to
      the measured wind: it logs the diffs, derives plain-language lessons, updates an
      <b>EWMA bias per (regime × hour-of-day)</b>, and <b>validates the predicted regime</b> against
      the measured wind-direction sector (accuracy + confusion, flagging the föhn/thermal
      anti-correlation). Today's forecast uses the just-updated correction.</p>
      <p class="muted">Thresholds start from published (Swiss-calibrated) föhn values and the ~2 K
      dry-adiabatic Δθ pivot, recalibrated over time from local data. Until history accrues, hours
      read "raw (no local calib yet)" and confidence is capped.</p>
    </section>"""
    return """
    <section class="card method">
      <h2>Prediction &amp; learning methodology</h2>
      <p>Ammersee (~533 m) is an open pre-Alpine foreland lake with long N–S fetch. Its wind is
      mostly <b>synoptic gradient flow</b> plus a <b>summer thermal (lake breeze)</b>; south föhn
      is rare and weak this far north.</p>
      <h3>Regime, classified each hour</h3>
      <ul>
        <li><b>Gradient</b> — the dominant driver (925 / 850 hPa flow + pressure gradient).</li>
        <li><b>Thermal</b> — sunny, weak-gradient days; builds late morning, peaks mid-afternoon,
            dies at sunset.</li>
        <li><b>Föhn</b> — flagged only on a strong Bozen−München Δp + southerly 850 hPa signal.</li>
      </ul>
      <h3>Data</h3>
      <ul>
        <li>Forecast: <b>ICON-D2</b> + <b>ICON-EU</b> cross-check + 20-member ensemble.</li>
        <li>Measured truth: the <b>Herrsching sailing-club anemometer</b> (on the water) with
            <b>DWD Wielenbach</b> (lake-level, ~11 km S) as the independent anchor.</li>
      </ul>
      <p class="muted">Caveat: the Windfinder / addicted-sports Herrsching feeds are often the
      <i>same</i> pier sensor (not independent votes) and under-read easterlies; the learning
      currently uses DWD Wielenbach as the measured actual. Ammersee does not use the Kesselberg
      Δθ or föhn-driver features — those are specific to the Alpine-rim pair.</p>
      <h3>How it learns</h3>
      <p>Same mechanism as the other lakes: each morning it compares yesterday's forecast to the
      measured wind and updates an EWMA bias per (regime × hour-of-day) before building today's
      forecast.</p>
    </section>"""


def _data_sources(group):
    common = ("All sources are fetched server-side with Python <code>urllib</code> and the system "
              "CA bundle, which validates through the corporate Zscaler TLS-intercepting proxy. "
              "ICON-D2 GRIB is decoded locally with <code>cfgrib</code>/<code>eccodes</code> in the "
              "project venv and cached; DWD Open Data keeps only a ~24 h rolling window, so files are "
              "archived on fetch (DWD data © Deutscher Wetterdienst, CC BY 4.0).")
    if group == "kochel-walchensee":
        pred = [
            ("ICON-D2", "forecast backbone (2.2 km, hourly, 48 h, 8 runs/day)",
             "raw GRIB2 (bz2) from <code>opendata.dwd.de/weather/nwp/icon-d2/grib/</code>, decoded &amp; "
             "cached; plus the same model as a point from Open-Meteo "
             "<code>api.open-meteo.com/v1/forecast?…&amp;models=icon_d2</code> (incl. 850/925 hPa)"),
            ("ICON-D2 ensemble", "confidence (20 members → P10/P50/P90, gust probability)",
             "Open-Meteo <code>ensemble-api.open-meteo.com/v1/ensemble?…&amp;models=icon_d2</code>"),
            ("DWD MOSMIX", "föhn trigger — cross-Alpine Δp (Bozen − München)",
             "KML/KMZ from <code>opendata.dwd.de/…/MOSMIX_L/single_stations/{16020,10865}/kml/</code>, "
             "parsed for the <code>PPPP</code> pressure series"),
            ("addicted-sports drivers", "föhn/thermal cause (foehn gradient, 850 hPa, lapse, radiation)",
             "the <code>drivers</code> block of the same JSON feed "
             "<code>addicted-sports.com/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code>"),
            ("Open-Meteo T2m", "Kochel−Walchensee Δθ stability index",
             "<code>api.open-meteo.com/v1/forecast?hourly=temperature_2m</code> at both lake points"),
        ]
        meas = [
            ("addicted-sports Urfeld", "on-lake measured wind (Walchensee truth)",
             "<code>mavg</code>/<code>mmax</code>/<code>dir</code> from the same JSON feed "
             "<code>…/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code> (daylight hours)"),
            ("DWD 10-min obs", "Kochelsee measured wind (valley proxy: Garmisch 01550)",
             "zipped 10-min FF/DD from "
             "<code>opendata.dwd.de/climate_environment/CDC/…/10_minutes/wind/recent/</code>"),
        ]
    else:
        pred = [
            ("ICON-D2", "forecast backbone (2.2 km, hourly, 48 h)",
             "raw GRIB2 from <code>opendata.dwd.de/weather/nwp/icon-d2/grib/</code> (decoded &amp; cached) "
             "+ Open-Meteo point <code>…?models=icon_d2</code>"),
            ("ICON-EU", "independent cross-check + horizon beyond 48 h",
             "Open-Meteo <code>api.open-meteo.com/v1/forecast?…&amp;models=icon_eu</code>"),
            ("ICON-D2 ensemble", "confidence (20 members)",
             "Open-Meteo <code>ensemble-api.open-meteo.com/v1/ensemble</code>"),
        ]
        meas = [
            ("DWD 10-min obs", "measured actual — Wielenbach 05538 (lake-level, ~11 km S)",
             "zipped 10-min FF/DD from "
             "<code>opendata.dwd.de/climate_environment/CDC/…/10_minutes/wind/recent/</code>"),
            ("Herrsching anemometer", "on-water reference (human-readable; not yet the learning actual)",
             "addicted-sports / Windfinder Herrsching station pages"),
        ]

    def _tbl(rows):
        body = "".join(f"<tr><td>{s}</td><td>{role}</td><td>{acc}</td></tr>" for s, role, acc in rows)
        return (f'<table class="srctbl"><thead><tr><th>source</th><th>role</th>'
                f'<th>access</th></tr></thead><tbody>{body}</tbody></table>')

    return f"""
    <section class="card method">
      <h2>Data sources &amp; how they're accessed</h2>
      <p>{common}</p>
      <h3><span class="chip fc">forecast</span> Prediction inputs — today</h3>
      {_tbl(pred)}
      <h3><span class="chip meas">measured</span> Measured inputs — yesterday (verification &amp; learning)</h3>
      {_tbl(meas)}
    </section>"""


def _css():
    return """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
 --grid:#e1e0d9;--ring:rgba(11,11,11,.10);--link:#2a78d6;
 --g:#2a78d6;--t:#008300;--f:#e34948;--c:#898781;}
@media(prefers-color-scheme:dark){:root{--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--link:#3987e5;
 --g:#3987e5;--t:#008300;--f:#e66767;}}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
 font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--link);text-decoration:none} a:hover{text-decoration:underline}
header{padding:20px 24px;border-bottom:1px solid var(--grid)}
h1{margin:0 0 4px;font-size:20px}
.sub{color:var(--ink2);font-size:13px}
.nav{font-size:13px;margin-bottom:8px}
main{display:grid;gap:18px;padding:20px 24px;max-width:1200px;margin:0 auto}
.card{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px 18px}
h2{margin:0 0 6px;font-size:17px} h3{margin:14px 0 4px;font-size:14px}
.summary{margin:0 0 12px;color:var(--ink2);font-size:13.5px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;
 color:var(--muted);font-weight:600;padding:4px 8px;border-bottom:1px solid var(--grid)}
td{padding:3px 8px;border-bottom:1px solid var(--grid);font-size:13.5px}
.hr{color:var(--ink2);width:34px} .dir{white-space:nowrap;color:var(--ink2);width:74px}
.arr{display:inline-block}
.wind{font-weight:600;border-radius:4px;width:96px;text-align:left}
.wind .bft{opacity:.8;font-weight:400;font-size:11px;margin-left:6px}
.gust{color:var(--ink2);width:48px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;color:#fff}
.badge.gradient{background:var(--g)}.badge.thermal{background:var(--t)}
.badge.foehn{background:var(--f)}.badge.calm{background:var(--c)}
.conf{width:44px;font-size:12px;color:var(--muted)}
.conf.c-high{color:var(--ink)} .conf.c-low{opacity:.7}
.note{color:var(--ink2);font-size:12px}
.method p,.method li{color:var(--ink2);font-size:13.5px} .method b{color:var(--ink)}
.method ul{margin:4px 0 4px 18px;padding:0}
.srctbl{margin-top:8px} .srctbl th{width:auto}
.srctbl td{vertical-align:top;width:auto;color:var(--ink2);font-size:13px}
.srctbl td:first-child{white-space:nowrap;font-weight:600;color:var(--ink)}
code{font:11.5px/1.4 ui-monospace,Menlo,Consolas,monospace;background:var(--plane);
 padding:1px 5px;border-radius:4px;word-break:break-word;color:var(--ink)}
details{margin:8px 0} summary{cursor:pointer;font-weight:600;font-size:13.5px}
pre{white-space:pre-wrap;font:12px/1.4 ui-monospace,Menlo,Consolas,monospace;
 background:var(--plane);padding:12px;border-radius:8px;overflow-x:auto}
.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.tile{display:block;background:var(--surface);border:1px solid var(--ring);border-radius:12px;
 padding:20px;color:inherit}
.tile:hover{border-color:var(--link);text-decoration:none}
.tile h2{margin:0 0 4px} .tile .blurb{color:var(--ink2);font-size:13.5px;margin:0 0 10px}
.tile .teaser{font-size:12.5px;color:var(--muted)} .tile .teaser div{margin:2px 0}
.sec{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
 color:var(--ink2);margin:6px 0 -6px;display:flex;align-items:center;gap:8px}
.chip{font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;
 border:1px solid var(--ring);vertical-align:middle;text-transform:none;letter-spacing:0}
.chip.fc{color:#fff;background:var(--g);border-color:transparent}
.chip.meas{color:var(--ink2);background:var(--plane)}
.card.measured{border-left:3px solid var(--muted)}
.analyst{margin:8px 0;padding:10px 12px;border-left:3px solid var(--g);background:var(--plane);
 border-radius:8px;font-size:13px;color:var(--ink2)} .analyst b{color:var(--ink)}
.analyst ul{margin:6px 0 0 18px;padding:0}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
footer{padding:16px 24px;color:var(--muted);font-size:12px;max-width:1200px;margin:0 auto}
"""


def _legend():
    return """<div class="legend">
    <span><i class="sw" style="background:var(--t)"></i>thermal</span>
    <span><i class="sw" style="background:var(--f)"></i>föhn</span>
    <span><i class="sw" style="background:var(--g)"></i>gradient</span>
    <span><i class="sw" style="background:var(--c)"></i>calm</span></div>"""


def index_html(static=False):
    tiles = []
    for key, g in GROUPS.items():
        teaser = []
        for lake in g["lakes"]:
            rec = _latest_forecast(lake)
            if rec:
                teaser.append(f'<div><b>{html.escape(rec.get("label", lake.title()))}:</b> '
                              f'{html.escape(rec.get("summary",""))}</div>')
        tiles.append(f'<a class="tile" href="{_href(key, static)}"><h2>{html.escape(g["title"])} →</h2>'
                     f'<p class="blurb">{html.escape(g["blurb"])}</p>'
                     f'<div class="teaser">{"".join(teaser)}</div></a>')
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bavarian lake wind</title><style>{_css()}</style></head><body>
<header><h1>Bavarian lake wind forecasts</h1>
<div class="sub">generated {_generated()} · updates ~05:00 daily · self-learning</div></header>
<main><div class="tiles">{''.join(tiles)}</div></main>
<footer>Choose a report. Each has its own hourly forecast, self-learning history, and the
prediction methodology for those lakes.</footer></body></html>"""


def report_html(group, static=False):
    g = GROUPS[group]
    other = [k for k in GROUPS if k != group]
    nav = (f'<div class="nav"><a href="{_href("", static)}">← all lakes</a>'
           + "".join(f' &nbsp;·&nbsp; <a href="{_href(k, static)}">{html.escape(GROUPS[k]["title"])}</a>'
                     for k in other) + "</div>")
    fcards = "".join(_forecast_card(_latest_forecast(l)) for l in g["lakes"])
    mcards = "".join(_measured_card(l) for l in g["lakes"])
    date = next((r["date"] for r in (_latest_forecast(l) for l in g["lakes"]) if r), "—")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(g['title'])} wind — {date}</title><style>{_css()}</style></head><body>
<header>{nav}
  <h1>{html.escape(g['title'])} — {date}</h1>
  <div class="sub">generated {_generated()} · updates ~05:00 daily · knots (Beaufort) · gusts in kn</div>
  {_legend()}
</header>
<main>
  <div class="sec"><span class="chip fc">forecast</span> Predicted — today ({date})</div>
  {fcards or '<p class="muted">No forecast logged yet — run daily_run.py.</p>'}
  <div class="sec"><span class="chip meas">measured</span> Observed — yesterday</div>
  {mcards}
  {_methodology(group)}
  {_data_sources(group)}
  {_learning_section(g["lakes"])}
</main>
<footer>Raw model wind is a first guess, corrected toward measured wind and improved daily by the
self-learning loop; "raw (no local calib yet)" hours are uncalibrated. Residual error ~1–1.5 kn+,
worst in thermal/föhn.</footer></body></html>"""


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "index"
    print(index_html() if which == "index" else report_html(which))
