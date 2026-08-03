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
import learn   # shared large-miss threshold (LARGE_ERR_KN)
import verify  # shared gate/confidence thresholds (N_MIN_BACKTEST_DAYS, LOW_CONF_DAYS)

GROUPS = {
    "kochel-walchensee": {
        "title": "Walchensee & Kochelsee",
        "lakes": ["walchensee", "kochelsee"],
        "blurb": "Alpine-rim lakes — föhn vs thermal, coupled through the Kesselberg",
    },
    "ammersee": {
        "title": "Ammersee",
        "lakes": ["ammersee"],
        "blurb": "Pre-Alpine foreland lake — gradient flow + summer thermal",
    },
}

# mean-wind "heat" ramp: white → yellow → orange → red (weak → strong wind).
# föhn is recoloured to violet (below) so no legend hue falls inside this warm range.
_WIND_RAMP = [(2, "#fff7e0"), (4, "#ffe89a"), (7, "#ffd24d"), (11, "#ffab3d"),
              (16, "#fb7e2e"), (22, "#ea4a26"), (999, "#c81e1e")]
_WIND_DARKTEXT = {"#fff7e0", "#ffe89a", "#ffd24d", "#ffab3d", "#fb7e2e"}


def _wind_cell_style(kn):
    hexc = _WIND_RAMP[-1][1]
    for ub, hx in _WIND_RAMP:
        if kn < ub:
            hexc = hx
            break
    return f"background:{hexc};color:{'#0b0b0b' if hexc in _WIND_DARKTEXT else '#fff'}"


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
    """Build time in Europe/Berlin, LABELLED. A naive fromtimestamp renders in the build
    machine's zone — UTC on the GitHub runner — next to tables whose hours are all Berlin."""
    lrp = os.path.join(wd.LOG_DIR, "latest_report.txt")
    if not os.path.exists(lrp):
        return "—"
    return datetime.datetime.fromtimestamp(os.path.getmtime(lrp),
                                           wd.BERLIN).strftime("%Y-%m-%d %H:%M %Z")


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


def _bigdiff_card(lake):
    """PROMINENT table: only the hours where |forecast − measured| exceeded the
    threshold — the difference table, filtered by a defined absolute value."""
    label = html.escape(_lake_label(lake))
    date, src, rows = _measured_rows(lake)
    thr = learn.LARGE_ERR_KN
    if not rows:
        return (f'<section class="card"><h2>{label} — big misses</h2>'
                f'<p class="muted">No measured day yet — appears after the next morning run.</p></section>')
    big = [r for r in rows if learn.is_large_miss(r.get("err_issued_kn"))]
    if not big:
        inner = f'<p class="muted">🎯 No hour differed from the forecast by more than {thr:g} kn on {date}.</p>'
    else:
        trs = "".join(
            f'<tr><td class="hr">{r["hour"]:02d}</td>'
            f'<td class="gust">{r.get("issued_kn")}</td>'
            f'<td class="gust" style="{_wind_cell_style(r.get("actual_kn") or 0)}">{r.get("actual_kn")}</td>'
            f'<td class="wind" style="{_wind_cell_style(abs(r["err_issued_kn"]))}">{r["err_issued_kn"]:+.1f}</td>'
            f'<td><span class="badge {r.get("actual_regime","calm")}">{r.get("actual_regime","")}</span></td></tr>'
            for r in big)
        inner = (f'<table><thead><tr><th>h</th><th>forecast</th><th>measured</th>'
                 f'<th>Δ = fc−meas</th><th>measured regime</th></tr></thead><tbody>{trs}</tbody></table>')
    return (f'<section class="card"><h2>{label} '
            f'<span class="chip meas">|Δ| &gt; {thr:g} kn · {date}</span></h2>'
            f'<p class="summary">Hours where the forecast missed the measured wind by more than '
            f'{thr:g} kn (measured: {html.escape(src)}). The morning learning report explains each '
            f'miss and the fix applied.</p>{inner}</section>')


def _measured_card(lake):
    label = html.escape(_lake_label(lake))
    date, src, rows = _measured_rows(lake)
    if not rows:
        return ""  # the big-miss card already shows the "no measured day yet" note
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
            f'<td class="gust" style="{_wind_cell_style(r.get("actual_gust_kn") or 0)}">'
            f'{(r.get("actual_gust_kn") or 0):.0f}</td>'
            f'<td>{badge}</td><td class="note">{note}</td></tr>')
    return f"""
    <section class="card measured">
      <details><summary>{label} — all measured hours · {date}</summary>
      <p class="summary">Observed wind from {html.escape(src)}. Regime inferred from the measured
       direction; "vs fc" = that day's forecast − measured (kn), same sign convention as the big-miss table. Daylight hours where a station reported.</p>
      <table><thead><tr><th>h</th><th>dir</th><th>mean kn (Bft)</th><th>gust</th>
        <th>regime</th><th>vs&nbsp;fc</th></tr></thead><tbody>{''.join(trs)}</tbody></table>
      </details>
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
        if r.get("foehn_note"):
            note.append(r["foehn_note"])
        if not r.get("mean_kn") and reg == "calm":
            note.append("glassy")
        rows.append(
            f'<tr><td class="hr">{r["hour"]:02d}</td>'
            f'<td class="dir">{_dir_arrow(r.get("dir"))} {fc.compass(r.get("dir"))}</td>'
            f'<td class="wind" style="{_wind_cell_style(kn)}">{kn:.1f}'
            f'<span class="bft">{fc.beaufort(kn)}</span></td>'
            f'<td class="gust" style="{_wind_cell_style(r.get("gust_kn") or 0)}">'
            f'{(r.get("gust_kn") or 0):.0f}</td>'
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
    """The latest self-tuning cycle for a lake: what it reviewed, proposed, and whether
    the backtest gate let anything through (from logs/analyst/)."""
    files = sorted(glob.glob(os.path.join(wd.LOG_DIR, "analyst", f"{lake}_*.json")))
    if not files:
        return ""
    try:
        d = json.load(open(files[-1]))
    except Exception:
        return ""
    r = d.get("result", {})
    if not isinstance(r, dict) or r.get("skipped"):
        return ""
    if not (r.get("proposals") or r.get("reviewed") or r.get("applied")):
        return ""
    esc = lambda x: html.escape(str(x))
    parts = []
    for v in r.get("reviewed", []):
        verd = v.get("verdict", "")
        parts.append(f'<li>↺ <b>{esc(verd)}</b> its earlier hypothesis '
                     f'<code>{esc(v.get("id", ""))}</code></li>')
    for p in r.get("proposals", []):
        parts.append(f'<li><code>{esc(p.get("param"))}</code> → <b>{esc(p.get("proposed"))}</b>'
                     f' — {esc(p.get("rationale", ""))}</li>')
    for a in r.get("applied", []):
        parts.append(f'<li>✔ <b>applied</b> <code>{esc(a.get("param"))}</code>: '
                     f'{esc(a.get("from"))} → {esc(a.get("to"))} ({esc(a.get("reason"))})</li>')
    for x in r.get("refused", []):
        parts.append(f'<li>✗ held back <code>{esc(x.get("param"))}</code>='
                     f'{esc(x.get("proposed"))} — {esc(x.get("reason"))}</li>')
    n_app = len(r.get("applied", []))
    nmin = verify.N_MIN_BACKTEST_DAYS
    foot = (f"A change is applied only if replaying past days under it measurably lowers "
            f"CRPS on at least {nmin} replayable days; otherwise it stays a logged proposal."
            if n_app else
            f"Nothing was applied: every proposal must first lower CRPS on at least {nmin} "
            f"replayable days of backtest. Until that history accrues, proposals are "
            f"recorded and reviewed but the forecaster is left unchanged.")
    return (f'<div class="analyst"><b>🧠 Self-tuning loop · {esc(d.get("date", ""))}:</b> '
            f'{esc(r.get("narrative", ""))}'
            + (f'<ul>{"".join(parts)}</ul>' if parts else '')
            + f'<div class="muted" style="margin-top:4px">{foot}</div></div>')


def _verification_block(lake):
    """The latest objective scorecard: CRPS vs persistence & climatology."""
    path = os.path.join(wd.LOG_DIR, "events.jsonl")
    if not os.path.exists(path):
        return ""
    rec = None
    for line in open(path):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("kind") == "verification" and e.get("lake") == lake:
            rec = e
    if not rec or not rec.get("crps"):
        return ""
    f = lambda x: "n/a" if x is None else f"{x:.2f}"
    ss = lambda x: "n/a" if x is None else f"{x:+.2f}"
    warn = ("" if (rec.get("n_days") or 0) >= verify.LOW_CONF_DAYS else
            f' <b>low confidence</b> — only {rec.get("n_days")} day(s) scored')
    return (f'<div class="analyst"><b>📊 Verification · {html.escape(str(rec.get("date","")))}:</b> '
            f'CRPS <b>{f(rec.get("crps"))} kn</b> (MAE {f(rec.get("mae"))}, '
            f'RMSE {f(rec.get("rmse"))}, bias {f(rec.get("bias"))}) over '
            f'{rec.get("n_pairs")} hours.{warn}'
            f'<div class="muted" style="margin-top:4px">Skill vs persistence '
            f'{ss(rec.get("ss_pers"))} · vs climatology {ss(rec.get("ss_clim"))} '
            f'(&gt;0 beats the baseline). CRPS grades the whole predicted spread, not just '
            f'the mean; for a single-number forecast it equals the absolute error.</div></div>')


def _learning_section(lakes):
    blocks = []
    for lake in lakes:
        vb = _verification_block(lake)
        if vb:
            blocks.append(vb)
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
    nmin = verify.N_MIN_BACKTEST_DAYS
    # thresholds are tuner-writable and PER-LAKE. Reading only lakes[0] published one
    # lake's tuned numbers as if they governed both, which is exactly what the per-lake
    # split exists to prevent. Show a single value only while the lakes agree.
    _lk = GROUPS[group]["lakes"]
    _ps = {l: fc.params_for(l) for l in _lk}
    def P_(key, unit=""):
        vals = {l: _ps[l][key] for l in _lk}
        if len(set(vals.values())) == 1:
            return f"{vals[_lk[0]]:g}{unit}"
        return " / ".join(f"{l.title()} {v:g}{unit}" for l, v in vals.items())
    P = {k: P_(k) for k in fc.TUNABLE}
    if group == "kochel-walchensee":
        return f"""
    <section class="card method">
      <h2>How it's predicted</h2>
      <p>Kochelsee and Walchensee share one wind system but are reported separately: under
      <b>south föhn</b> the Kesselberg fall-wind makes <b>Kochelsee strong</b> while it kills the
      <b>Walchensee NE thermal</b> — getting that split right is the job.</p>
      <h3>Prediction algorithm (per hour)</h3>
      <ol>
        <li><b>Blend the models.</b> Value = mean of ICON-D2 ensemble + ICON-D2 deterministic +
            ICON-EU + addicted-sports' spot forecast; ensemble spread → confidence.</li>
        <li><b>Diagnose drivers.</b> Cross-Alpine Δp (Bozen−München), 850 hPa wind, föhn-gradient,
            radiation, and the Kochel−Walchensee <b>Δθ</b> stability index.</li>
        <li><b>Classify the regime</b> — terrain then fixes direction (N–NE thermal · S–SE föhn ·
            W–NW gradient): <b>föhn</b> (Δp ≥ {P['FOEHN_DP_RIM']} hPa + SE–S 850 wind; confirmed by morning S/SE at
            Hohenpeißenberg) → <b>gradient</b> (925 hPa ≥ {P['GRADIENT_925_KN']} kn) → <b>thermal</b> (sun + weak gradient
            + no cold pool, Δθ &lt; {P['COLD_POOL_DTHETA']} K) → <b>calm</b>.</li>
        <li><b>Correct.</b> A learned regression <b>corrected = a + b·model</b> that scales with the
            model (no föhn double-count), evidence-gated and capped.</li>
      </ol>
      <p class="muted"><b>Föhn caveat:</b> its strength/timing isn't reliably predictable (often blows
      only till ~09:00); flagged "unconfirmed" until Hohenpeißenberg shows S/SE.</p>
      <h3>How it learns</h3>
      <p>Each morning it compares yesterday's forecast to the measured wind (on-lake Urfeld for
      Walchensee, on-lake Trimini for Kochelsee), updates the per-(regime×hour) regression, and
      validates the regime against the measured direction. Until history builds, hours read
      "raw (no local calib yet)".</p>
      <h3>How it's checked, and how it tunes itself</h3>
      <p>Every run is scored out of sample with <b>CRPS</b> (knots, lower better — the
      probabilistic version of mean absolute error: it grades the whole predicted spread, and for a
      single-number forecast equals the absolute error) against two baselines,
      <b>persistence</b> and <b>climatology</b>. On top of that an LLM tuner reviews
      <i>its own</i> earlier proposals against the measured CRPS, confirms or retracts each, and may
      propose small threshold changes — but a change is only written to the forecaster if replaying
      past days under it measurably lowers CRPS on at least {nmin} replayable days. Until that
      history exists, proposals are recorded and shown, and nothing is applied.</p>
    </section>"""
    return f"""
    <section class="card method">
      <h2>How it's predicted</h2>
      <p>Ammersee (~533 m) is an open foreland lake: wind is mostly <b>synoptic gradient</b> plus a
      <b>summer thermal (lake breeze)</b>; south föhn is rare here.</p>
      <h3>Prediction algorithm (per hour)</h3>
      <ol>
        <li><b>Blend the models.</b> Value = mean of ICON-D2 ensemble + ICON-D2 deterministic +
            ICON-EU at the Herrsching point; ensemble spread → confidence.</li>
        <li><b>Classify the regime:</b> <b>gradient</b> (strong 925/850 hPa flow) → <b>thermal</b>
            (sunny, weak-gradient afternoons) → <b>föhn</b> (only on strong Δp + southerly 850, rare)
            → <b>calm</b>.</li>
        <li><b>Correct.</b> A learned regression <b>corrected = a + b·model</b> (scales with the
            model), evidence-gated and capped.</li>
      </ol>
      <p class="muted">Measured truth: DWD Wielenbach (lake-level, ~11 km); the Herrsching on-water
      station is the reference but its Windfinder/addicted feeds are often the same sensor. No
      Kesselberg Δθ / föhn drivers here (Alpine-rim only).</p>
      <h3>How it's checked, and how it tunes itself</h3>
      <p>Every run is scored out of sample with <b>CRPS</b> (knots, lower better — the probabilistic
      version of mean absolute error) against <b>persistence</b> and <b>climatology</b> baselines. An
      LLM tuner reviews its own earlier proposals against the measured CRPS and may suggest small
      threshold changes, but a change reaches the forecaster only if a backtest over at least
      {nmin} replayable days shows it lowers CRPS.</p>
      <h3>How it learns</h3>
      <p>Each morning it compares yesterday's forecast to the measured wind and updates the
      per-(regime×hour) regression before today's forecast.</p>
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
            ("addicted-sports spot forecast", "blend member — each lake's own on-spot forecast",
             "the <code>avg</code>/<code>boe</code> series, per lake: "
             "<code>…/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code> for Walchensee, "
             "<code>…/forecast/kochelsee/trimini/?json=wind&amp;from=DATE</code> for Kochelsee"),
            ("addicted-sports drivers", "föhn/thermal cause (foehn gradient, 850 hPa, lapse, radiation)",
             "the <code>drivers</code> block of the same per-lake feed "
             "(<code>…/walchensee/urfeld/…</code> for Walchensee, "
             "<code>…/kochelsee/trimini/…</code> for Kochelsee)"),
            ("Open-Meteo T2m", "Kochel−Walchensee Δθ stability index",
             "<code>api.open-meteo.com/v1/forecast?hourly=temperature_2m</code> at both lake points"),
        ]
        meas = [
            ("addicted-sports Urfeld", "on-lake measured wind (Walchensee truth)",
             "<code>mavg</code>/<code>mmax</code>/<code>dir</code> from the same JSON feed "
             "<code>…/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code> (daylight hours)"),
            ("addicted-sports Trimini", "on-lake measured wind (Kochelsee truth)",
             "<code>mavg</code>/<code>mmax</code>/<code>dir</code> from "
             "<code>…/forecast/kochelsee/trimini/?json=wind&amp;from=DATE</code> — the station at the "
             "Kristall Therme Trimini on Kochelsee's south shore (~629 m; a genuinely separate sensor "
             "from Urfeld, verified hour-by-hour), daylight hours; DWD Garmisch 01550 only as a "
             "fallback if the on-lake feed is unavailable"),
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
            ("GKD Ammerseeboje", "measured actual — official buoy ON the lake (preferred)",
             "hourly means from <code>gkd.bayern.de/de/meteo/wind/isar/ammerseeboje-16601050"
             "/messwerte/tabelle</code> (© GKD Bayern, CC BY 4.0). Speed only — direction and "
             "gust come from DWD for the same hours. Offline since 15.06.2026 (electronics "
             "defect), so the DWD fallback below is currently in use"),
            ("DWD 10-min obs", "measured actual — fallback: Wielenbach 05538 (lake-level, ~11 km inland)",
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
 --g:#2a78d6;--t:#008300;--f:#7c3aed;--c:#898781;}
@media(prefers-color-scheme:dark){:root{--surface:#1a1a19;--plane:#0d0d0d;--ink:#fff;
 --ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--link:#3987e5;
 --g:#3987e5;--t:#008300;--f:#8b5cf6;}}
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
.gust{width:48px;border-radius:4px;text-align:center;font-weight:500}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;color:#fff}
.badge.gradient{background:var(--g)}.badge.thermal{background:var(--t)}
.badge.foehn{background:var(--f)}.badge.calm{background:var(--c)}
.conf{width:44px;font-size:12px;color:var(--muted)}
.conf.c-high{color:var(--ink)} .conf.c-low{opacity:.7}
.note{color:var(--ink2);font-size:12px}
.method p,.method li{color:var(--ink2);font-size:13.5px} .method b{color:var(--ink)}
.method ul,.method ol{margin:4px 0 4px 20px;padding:0}
.method ol li{margin:3px 0}
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
/* one lake's big-miss table + its measured dropdown read as a single unit: tighter gap
   inside the pair (8px) than between lakes (the 18px main grid gap) */
.lakegroup{display:grid;gap:8px}
.lakegroup .card.measured{margin:0}
.lakegroup>.card.measured>details>summary{color:var(--ink2)}
.analyst{margin:8px 0;padding:10px 12px;border-left:3px solid var(--g);background:var(--plane);
 border-radius:8px;font-size:13px;color:var(--ink2)} .analyst b{color:var(--ink)}
.analyst ul{margin:6px 0 0 18px;padding:0}
.legendrow{display:flex;gap:24px;flex-wrap:wrap;align-items:center;margin-top:6px}
.legend{display:flex;gap:14px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:5px}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.tscale{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted)}
.tsbar{width:120px;height:11px;border-radius:3px;border:1px solid var(--ring)}
.tsend{font-size:11px;color:var(--muted)}
.reghint{margin-top:8px;font-size:12px;line-height:1.5;color:var(--ink2);max-width:1000px}
.reghint b{color:var(--ink)}
footer{padding:16px 24px;color:var(--muted);font-size:12px;max-width:1200px;margin:0 auto}
"""


def _legend():
    grad = ",".join(hx for _, hx in _WIND_RAMP)  # same ramp used for the cells
    cmin = 0                       # calm end of the scale (kn)
    cmax = _WIND_RAMP[-2][0]       # strong end = where the top (red) band starts (kn)
    return f"""<div class="legendrow">
    <div class="legend">
      <span><i class="sw" style="background:var(--t)"></i>thermal</span>
      <span><i class="sw" style="background:var(--f)"></i>föhn</span>
      <span><i class="sw" style="background:var(--g)"></i>gradient</span>
      <span><i class="sw" style="background:var(--c)"></i>calm</span>
    </div>
    <div class="tscale"><span>mean &amp; gust (kn):</span>
      <span class="tsend">calm {cmin}</span>
      <span class="tsbar" style="background:linear-gradient(to right,{grad})"></span>
      <span class="tsend">{cmax}+ strong</span>
    </div>
  </div>
  <div class="reghint"><b>Regime</b> — which wind dominates that hour:
    <b>thermal</b> sun-driven lake/valley breeze · <b>föhn</b> warm, gusty south fall-wind ·
    <b>gradient</b> frontal / pressure-driven flow · <b>calm</b> little or no wind.</div>"""


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
    # Pair each lake's big-miss table with ITS OWN "all measured hours" dropdown, wrapped
    # so the two read as one unit (tighter internal gap than between lakes). Previously
    # both diff tables came first and both dropdowns after, so the Walchensee dropdown sat
    # under the Kochelsee table.
    meascards = "".join(f'<div class="lakegroup">{_bigdiff_card(l)}{_measured_card(l)}</div>'
                        for l in g["lakes"])
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
  <div class="sec"><span class="chip meas">measured</span> Yesterday: forecast vs measured — big misses</div>
  {meascards}
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
