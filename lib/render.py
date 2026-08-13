#!/usr/bin/env python3
"""
render.py — render the daily wind report as styled HTML.

Three views (all offline; read only the latest logs, no network):
  index_html()            top-level landing page: pick a report
  report_html(group)      one report page per lake group, with a methodology section
Groups: 'kochel-walchensee' (the coupled Alpine-rim pair) and 'ammersee'.

Colors follow the dataviz method (scenario = CVD-checked colour-code column,
mean wind = validated blue sequential ramp, confidence = text ink).
"""
import os, sys, json, glob, html, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import forecast as fc
import learn   # shared large-miss threshold (LARGE_ERR_KN)
import verify  # shared gate/confidence thresholds (N_MIN_BACKTEST_DAYS, LOW_CONF_DAYS)
import postproc  # shared gust-ratio plausibility band (GUST_RATIO_LO/HI)

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
    def legacy_rows(date):
        """Real daily forecast rows used only to fill a visual transition gap.

        The hourly issuer began part-way through the day. Its missing earlier hours must
        not render as empty, but the daily record is never eligible for hourly learning
        or lead-time scoring. ``legacy_calendar_backfill`` makes that boundary explicit.
        """
        path = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
        candidates = []
        if os.path.exists(path):
            for line in open(path):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("date") == date and r.get("hourly"):
                    candidates.append(r)
        if not candidates:
            return {}
        rec = min(candidates, key=lambda r: verify._rank(r.get("run_stamp")))
        return {r["hour"]: {**r, "legacy_calendar_backfill": True,
                            "issue_time": rec.get("run_stamp"), "lead_minutes": None}
                for r in rec["hourly"]}

    hp = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
    if os.path.exists(hp):
        try:
            records = [json.loads(x) for x in open(hp) if x.strip()]
            r = records[-1]
            if r.get("hourly"):
                start, end = r.get("valid_start", ""), r.get("valid_end", "")
                day = start[:10]
                now = datetime.datetime.now(wd.BERLIN)
                rows = []
                legacy = legacy_rows(day)
                for hour in range(24):
                    vt = f"{day}T{hour:02d}:00:00+02:00"
                    valid = datetime.datetime.fromisoformat(vt)
                    candidates = []
                    for rec in records:
                        issued = datetime.datetime.fromisoformat(rec["issue_time"])
                        for row in rec.get("hourly", []):
                            if row.get("valid_time") == vt and issued <= now:
                                candidates.append((issued, row))
                    # Past: last forecast available before the valid hour. Future: newest
                    # currently issued update for that hour.
                    allowed = [x for x in candidates if x[0] < valid] if valid < now else candidates
                    if not allowed and valid < now:
                        # Prefer the reconciled legacy copy persisted by hourly_run.py.
                        # It carries measured/delta values; the daily log alone does not.
                        allowed = [x for x in candidates if x[1].get("legacy_calendar_backfill")]
                    if allowed:
                        issued, row = max(allowed, key=lambda x: x[0])
                        rows.append({**row, "issue_time": issued.isoformat(timespec="minutes")})
                    elif hour in legacy:
                        # Display-only transition bridge. The row receives the persisted
                        # measurement/delta below, but cannot enter hourly score/learning.
                        rows.append(legacy[hour])
                return {"lake": lake, "label": fc.LAKES.get(lake, (0, 0, lake.title()))[2],
                        "date": start[:10], "run_stamp": r.get("issue_time"),
                        "summary": f"rolling window {start[11:16]} → {end[11:16]} next day",
                        "hourly": rows, "rolling": True}
        except Exception:
            pass
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
    """Most recent hourly issuance in Europe/Berlin.

    ``latest_report.txt`` belongs to the retained daily audit and therefore becomes stale
    between morning runs.  The status record is written by every hourly workflow run,
    including a failed one, and is the timestamp users need when judging the table.
    """
    status = os.path.join(wd.LOG_DIR, "hourly_status.json")
    if os.path.exists(status):
        try:
            stamp = json.load(open(status)).get("time")
            if stamp:
                return datetime.datetime.fromisoformat(stamp).astimezone(wd.BERLIN).strftime("%Y-%m-%d %H:%M %Z")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    lrp = os.path.join(wd.LOG_DIR, "latest_report.txt")
    if not os.path.exists(lrp):
        return "—"
    return datetime.datetime.fromtimestamp(os.path.getmtime(lrp),
                                           wd.BERLIN).strftime("%Y-%m-%d %H:%M %Z")


def _hourly_status_section():
    p = os.path.join(wd.LOG_DIR, "hourly_status.jsonl")
    rows = []
    if os.path.exists(p):
        for line in open(p):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    rows = rows[-24:]
    if not rows:
        return '<section class="card"><h2>Hourly update status</h2><p class="muted">No hourly update has been recorded yet.</p></section>'
    last = rows[-1]
    state = 'success' if last.get('ok') else 'failure'
    label = 'last update succeeded' if last.get('ok') else 'last update failed — showing last valid forecast'
    def hour(x):
        # Older workflow failures wrote a UTC-based fallback hour.  A status card must
        # identify the Berlin calendar row that was being issued, so derive failures
        # from their timestamp rather than perpetuating that stored-label bug.
        if not x.get("ok"):
            try:
                local = datetime.datetime.fromisoformat(x["time"]).astimezone(wd.BERLIN)
                valid = local.replace(minute=0, second=0, microsecond=0)
                if local > valid:
                    valid += datetime.timedelta(hours=1)
                return f'{valid.hour:02d}'
            except (KeyError, TypeError, ValueError):
                pass
        if x.get("hour") is not None:
            return f'{int(x["hour"]):02d}'
        try:
            return f'{(datetime.datetime.fromisoformat(x["time"]) + datetime.timedelta(hours=1)).hour:02d}'
        except Exception:
            return '—'
    table = ''.join(f'<tr><td class="hr">{hour(x)}</td><td>{html.escape(str(x.get("time", "—")))}</td><td>{html.escape(str(x.get("trigger", "legacy")))}</td><td>{html.escape(str(x.get("source", "legacy")))}</td><td class="{("ok" if x.get("ok") else "bad")}">{("success" if x.get("ok") else "failed")}</td><td>{html.escape(str(x.get("message", "")))}</td></tr>' for x in reversed(rows))
    return f'''<section class="card hourly-status {state}"><h2>Hourly update status</h2>
      <p class="summary"><b>{label}</b> · {html.escape(str(last.get("time", "—")))}</p>
      <details><summary>Update results — last 24 runs</summary><table><thead><tr><th>H</th><th>time (UTC)</th><th>trigger</th><th>source</th><th>result</th><th>detail</th></tr></thead><tbody>{table}</tbody></table></details></section>'''


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


def _scenario_label(value):
    return {"foehn": "föhn-favourable", "thermal": "thermal-favourable",
            "gradient": "strong-gradient", "calm": "calm/capped"}.get(value, value or "—")


def _flow_label(value):
    return {"foehn": "S–SE flow", "thermal": "N–NE flow",
            "gradient": "gradient-sector flow", "calm": "calm",
            "uncertain": "uncertain"}.get(value, value or "uncertain")


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
    """PROMINENT table: eligible forecast hours where |forecast − measured| exceeded
    the threshold. Hours already elapsed at issue time are observations, not forecasts,
    and must never be promoted as a forecast miss."""
    label = html.escape(_lake_label(lake))
    date, src, rows = _measured_rows(lake)
    thr = learn.LARGE_ERR_KN
    if not rows:
        return (f'<section class="card"><h2>{label} — big misses</h2>'
                f'<p class="muted">No measured day yet — appears after the next morning run.</p></section>')
    big = [r for r in rows if not r.get("leaked") and learn.is_large_miss(r.get("err_issued_kn"))]
    if not big:
        inner = (f'<p class="muted">🎯 No eligible forecast hour differed from the forecast by more than '
                 f'{thr:g} kn on {date}. Hours that had already elapsed when the forecast was issued '
                 f'are observations only and are not scored as misses.</p>')
    else:
        trs = "".join(
            f'<tr><td class="hr">{r["hour"]:02d}</td>'
            f'<td class="gust">{(r.get("issued_kn") or 0):{fc.KN_FMT}}</td>'
            f'<td class="gust" style="{_wind_cell_style(r.get("actual_kn") or 0)}">'
            f'{(r.get("actual_kn") or 0):{fc.KN_FMT}}</td>'
            f'<td class="wind" style="{_wind_cell_style(abs(r["err_issued_kn"]))}">{r["err_issued_kn"]:+.1f}</td>'
            f'<td><span class="badge flow">{html.escape(_flow_label(r.get("observed_flow", r.get("actual_regime", ""))))}</span></td></tr>'
            for r in big)
        inner = (f'<table><thead><tr><th>h</th><th>forecast</th><th>measured</th>'
                 f'<th>Δ = fc−meas</th><th>observed flow</th></tr></thead><tbody>{trs}</tbody></table>')
    return (f'<section class="card"><h2>{label} '
            f'<span class="chip meas">|Δ| &gt; {thr:g} kn · {date}</span></h2>'
            f'<p class="summary">Eligible hours where the forecast missed the measured wind by more than '
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
        reg = r.get("observed_flow", r.get("actual_regime", ""))
        badge = (f'<span class="badge flow">{html.escape(_flow_label(reg))}</span>'
                 if reg and reg != "uncertain" else '<span class="muted">—</span>')
        leaked = bool(r.get("leaked"))
        err = r.get("err_issued_kn")
        note = ("not forecastable at issue time" if leaked else
                (f'{err:+.1f}' if err is not None else ''))
        trs.append(
            ('<tr class="elapsed" title="hour had already elapsed when forecast was issued">'
             if leaked else '<tr>') +
            f'<td class="hr">{r["hour"]:02d}</td>'
            f'<td class="dir">{_dir_arrow(r.get("dir_actual"))} {fc.compass(r.get("dir_actual"))}</td>'
            f'<td class="wind" style="{_wind_cell_style(kn)}">{kn:{fc.KN_FMT}}'
            f'<span class="bft">{fc.beaufort(kn)}</span></td>'
            f'<td class="gust" style="{_wind_cell_style(r.get("actual_gust_kn") or 0)}">'
            f'{(r.get("actual_gust_kn") or 0):{fc.KN_FMT}}</td>'
            f'<td>{badge}</td><td class="note">{note}</td></tr>')
    return f"""
    <section class="card measured">
      <details><summary>{label} — all measured hours · {date}</summary>
      <p class="summary">Observed wind from {html.escape(src)}. Flow sector inferred from measured direction; it does not confirm the physical cause; "vs fc" = that day's forecast − measured (kn). Grey rows had already elapsed when the forecast was issued: recorded as observations, but not forecastable, scored, or learned from.</p>
      <table><thead><tr><th>h</th><th>dir</th><th>mean kn (Bft)</th><th>gust</th>
        <th>observed flow</th><th>vs&nbsp;fc</th></tr></thead><tbody>{''.join(trs)}</tbody></table>
      </details>
    </section>"""


def _forecast_card(rec):
    if not rec or not rec.get("hourly"):
        return ""
    label = html.escape(rec.get("label", rec["lake"].title()))
    summ = html.escape(rec.get("summary", ""))
    # Measurements arrive after the forecast and are persisted in the diffs log. Joining
    # them here gives one operational table: future rows show dashes, while a rendered
    # historical/current row can show the recorded measurement and signed error.
    observed = {}
    p = os.path.join(wd.LOG_DIR, f"{rec['lake']}_diffs.jsonl")
    if os.path.exists(p):
        for line in open(p):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("date") == rec.get("date") and d.get("actual_kn") is not None:
                observed[d.get("hour")] = d
    # The hourly migration can start after midnight, while daily diffs are only written
    # the following day. For display-only legacy bridge rows, query the same measured
    # source directly so current-day H00–H13 never become blank merely because a daily
    # report has not been produced yet. These values are deliberately not written back
    # into hourly learning/verification state here.
    live_obs, live_source = {}, None
    if rec.get("rolling"):
        try:
            live_obs, live_source = wd.actual_hourly(rec["lake"], rec["date"])
        except Exception:
            live_obs, live_source = {}, None
    forecast_rows = rec["hourly"]
    if rec.get("rolling"):
        # Render the familiar current calendar day, not the raw 05:00→05:00 issuance
        # horizon. Once the hourly service has run through a midnight, 00:00–04:00 come
        # from the prior 23:55 record. During bootstrap they remain explicit gaps.
        by_hour = {int(r["valid_time"][11:13]): r for r in rec["hourly"]
                   if r.get("valid_time", "").startswith(rec["date"])}
        # Daily bridge rows intentionally have no valid_time: include them only to
        # populate the current display during the hourly migration.
        by_hour.update({r["hour"]: r for r in rec["hourly"] if r.get("legacy_calendar_backfill")})
        forecast_rows = [by_hour.get(h, {"hour": h, "missing": True}) for h in range(24)]
    rows = []
    for r in forecast_rows:
        if r.get("missing"):
            rows.append(f'<tr class="elapsed"><td class="hr">{r["hour"]:02d}</td><td class="note">—</td>'
                        '<td class="dir">—</td><td class="wind">—</td><td class="measured">—</td>'
                        '<td class="delta">—</td><td class="gust">—</td><td class="scenario-code">—</td>'
                        '<td class="note">no prior hourly forecast</td><td class="conf">—</td>'
                        '<td class="note">hourly service bootstrap</td></tr>')
            continue
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
        # a gust that a guard bounded must say so — the old +/-8 kn clamp fired on 8.7% of
        # Walchensee hours and left no trace anywhere, so a clamped value read as a forecast
        note += [fc.GUST_FLAG_NOTE[f] for f in (r.get("gust_flags") or [])
                 if f in fc.GUST_FLAG_NOTE]
        obs = observed.get(r["hour"])
        own_measured = r.get("measured_kn")
        # Measurement feeds can publish a completed hour after the last :55 issuer ran.
        # Use the same live source for any elapsed display row, so H22 does not stay
        # blank until the next scheduled reconciliation. This is display-only; state is
        # still persisted/reconciled by hourly_run.py on the next issuance.
        if own_measured is None:
            live = live_obs.get(r["hour"])
            if live is not None:
                own_measured = live.get("mean_kn")
                r = {**r, "_display_live_delta": round(r["mean_kn"] - own_measured, 1),
                     "_display_live_source": live_source}
        past = bool(r.get("valid_time") and datetime.datetime.fromisoformat(r["valid_time"]) < datetime.datetime.now(wd.BERLIN))
        unavailable = past and rec.get("rolling") and r["hour"] not in live_obs and obs is None
        measured = (f'{own_measured:{fc.KN_FMT}}' if own_measured is not None else
                    (("NR" if unavailable else "—") if obs is None else f'{obs["actual_kn"]:{fc.KN_FMT}}'))
        own_delta = r.get("fc_minus_measured_kn", r.get("_display_live_delta"))
        delta = (f'{own_delta:+.1f}' if own_delta is not None else
                 ("—" if obs is None else
                  (f'{obs.get("err_issued_kn", 0):+.1f}' if r.get("legacy_calendar_backfill")
                   else ("not forecastable" if obs.get("leaked") else f'{obs.get("err_issued_kn", 0):+.1f}'))))
        scenario = html.escape(_scenario_label(reg))
        issue = r.get("issue_time") or rec.get("run_stamp")
        lead = r.get("lead_minutes")
        issued = "—" if not issue else (issue[11:16] + (f' · {lead // 60}h{lead % 60:02d}' if lead is not None else ''))
        if r.get("legacy_calendar_backfill"):
            note.append("legacy daily row; display-only" + ("; current source" if r.get("_display_live_source") else ""))
        rows.append(
            f'<tr><td class="hr">{r["hour"]:02d}</td><td class="note">{issued}</td>'
            # No arrow when the direction is flagged variable — a rotated arrow reads as a
            # firm bearing even next to the word "VAR". fc.dir_label is the one authority.
            f'<td class="dir">{_dir_arrow(None if r.get("dir_variable") else r.get("dir"))} '
            f'{fc.dir_label(r)}</td>'
            f'<td class="wind" style="{_wind_cell_style(kn)}">{kn:{fc.KN_FMT}}'
            f'<span class="bft">{fc.beaufort(kn)}</span></td>'
            f'<td class="measured">{measured}</td><td class="delta">{delta}</td>'
            f'<td class="gust" style="{_wind_cell_style(r.get("gust_kn") or 0)}">'
            f'{(r.get("gust_kn") or 0):{fc.KN_FMT}}</td>'
            f'<td class="scenario-code"><i class="sw {reg}" title="{scenario}" '
            f'aria-label="{scenario}"></i></td>'
            f'<td class="note">{"not recorded" if "calib_n" not in r else ("raw" if not r.get("calib_n") else "n=" + str(r.get("calib_n")))}</td>'
            f'<td class="conf c-{r.get("conf","med")}">{r.get("conf","")}</td>'
            f'<td class="note">{html.escape(" ".join(note))}</td></tr>')
    date = rec.get("date", "")
    return f"""
    <section class="card">
      <h2>{label} <span class="chip fc">forecast · {date}</span></h2>
      <p class="summary">{summ}</p>
      <table>
        <thead><tr><th>h</th><th>issued / lead</th><th>dir</th><th>forecast kn (Bft)</th><th>measured</th><th>Δ fc−meas</th><th>gust</th>
          <th>scenario</th><th>support</th><th>conf</th><th>note</th></tr></thead>
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
            f"mean absolute error on at least {nmin} replayable days; otherwise it stays a logged proposal."
            if n_app else
            f"Nothing was applied: every proposal must first lower mean absolute error on at least {nmin} "
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


def _hourly_learning_section(lakes):
    blocks = []
    for lake in lakes:
        path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_measurements.jsonl")
        rows = []
        if os.path.exists(path):
            for line in open(path):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
        present = sum(r.get("measurement_status") == "present" for r in rows)
        nr = sum(r.get("measurement_status") == "NR" for r in rows)
        sc = verify.evaluate_hourly(lake)
        update = None
        lp = os.path.join(wd.LOG_DIR, f"{lake}_hourly_learning.jsonl")
        if os.path.exists(lp):
            for line in open(lp):
                try: update = json.loads(line)
                except Exception: pass
        latest = (f'Latest run ({html.escape(str(update.get("time", "—")))}): '
                  f'{update.get("new_measurements", 0)} new station reading(s) incorporated; '
                  f'{update.get("learning_updates", 0)} learning update(s) applied.' if update else
                  'The next hourly run will record its learning result here.')
        blocks.append(f'<div class="analyst"><b>⏱ {_lake_label(lake)} — latest hourly learning result.</b> '
                      f'{latest} {present} completed hour(s) currently have a station measurement; '
                      f'{nr} hour(s) were not reported. '
                      f'<div class="muted" style="margin-top:4px">'
                      f'Current hourly MAE: {("n/a" if sc.get("mae") is None else f"{sc["mae"]:.2f} kn")} '
                      f'over {sc.get("n_pairs", 0)} measured hour(s).</div></div>')
    return '<section class="card"><h2>Hourly reconciliation &amp; learning</h2>' + ''.join(blocks) + '</section>'


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
    caveat = ('<p class="muted">Scenario names in archived automated reports may use the '
              'older “regime” wording. They describe rule-selected buckets or measured '
              'direction sectors, not confirmed physical causes.</p>')
    return ('<section class="card"><h2>Legacy daily learning &amp; verification</h2>' + caveat
            + "".join(blocks) + "</section>")


def _methodology(group, static=False):
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
      <p>Kochelsee and Walchensee share one wind system but are reported separately. South föhn is physically expected to strengthen Kochelsee and suppress Walchensee’s NE thermal; the current system does not impose that response explicitly, but relies on lake-specific model inputs and learned corrections.</p>
      <h3>Prediction algorithm (per hour)</h3>
      <p class="muted"><b>Formula key:</b> <code>h</code> = forecast hour; <code>rₕ</code> = raw blended wind for that hour;
      <code>ŷₕ</code> = full locally corrected mean; <code>publishedₕ</code> = displayed forecast;
      <code>a</code> = additive local bias; <code>b</code> = local scaling; <code>n</code> = eligible prior
      observations in this exact lake×scenario×hour bucket.</p>
      <ol>
        <li><b>Blend the models.</b> Each hourly raw value is the equal-weight average of
            <code>(ICON-D2 ensemble + ICON-D2 deterministic + ICON-EU + addicted-sports spot forecast) ÷ 4</code>.
            If a source is unavailable, it averages the sources that are available. Ensemble spread
            becomes the uncertainty band.</li>
        <li><b>Read indicators.</b> Cross-Alpine Δp and 850 hPa wind actively select the föhn-favourable scenario. Cloud, 925 hPa wind and Δθ affect other scenarios. Föhn-gradient, lapse rate and radiation are displayed diagnostics, not regression predictors.</li>
        <li><b>Assign a forecast scenario.</b> First matching rule: <b>föhn-favourable</b> (Δp ≥ {P['FOEHN_DP_RIM']} hPa plus southerly 850 hPa wind) → <b>strong-gradient</b> (925 hPa ≥ {P['GRADIENT_925_KN']} kn) → <b>thermal-favourable</b> (daytime, limited cloud and no capped cold pool) → <b>calm/capped</b>. The scenario chooses a separate local model for this lake and hour; it does not confirm the physical cause or alter forecast direction.</li>
        <li><b>Correct the hourly mean.</b> In the selected <code>(lake, scenario, hour)</code> bucket,
            the full correction is <code>ŷₕ = a + b·rₕ</code>, first bounded to within
            ±{fc.BIAS_CAP_KN:g} kn of the raw value. It is then ramped in as
            <code>publishedₕ = rₕ + min(1, n/3)·(ŷₕ − rₕ)</code>. Thus one observation moves the forecast only a third
            of the way (<code>n=1</code>), two move it two thirds, and <code>n≥3</code> can apply the full bounded correction.</li>
        <li><b>Why ramp it?</b> <code>n</code> counts only earlier eligible examples of this exact scenario at
            this exact hour—at most one per day—so rare buckets can have fewer than three. The ramp
            prevents one unusual day from applying the whole adjustment.</li>
        <li><b>Guard the gust.</b> The gust correction is a multiplier, so an additive bound is the
            wrong tool for it: a learned ratio outside {postproc.GUST_RATIO_LO}–{postproc.GUST_RATIO_HI}
            is <b>refused</b> and the raw model gust published instead, and the published gust is
            capped at {fc.GUST_ENS_CEIL_MULT:g}× the hour's own ensemble gust members. Whenever a
            guard fires the row says so rather than passing a bounded number off as a learned one.</li>
        <li><b>Withhold the direction</b> as <b>VAR</b> when the ensemble disagrees by more than
            {fc.SPREAD_LOW_KN:g} kn. At the evening thermal reversal the wind passes through
            near-zero and the modelled bearing swings freely — these two lakes read 201° and 284°
            for the same hour on 2026-08-05, 8 km apart, and both settled to ~172° by 21:00.</li>
      </ol>
      <p class="muted"><b>Föhn caveat:</b> “föhn-favourable” means forecast pressure and upper-air thresholds were crossed. Hohenpeißenberg is a confidence cross-check, not a classification condition; measured S–SE flow alone cannot distinguish föhn from drainage or fall-wind.</p>
      <h3>How it learns</h3>
      <p>In the legacy daily path, every eligible measured hour updates only that hour’s
      <code>(lake, scenario, hour)</code> bucket. It fits <code>measuredₕ ≈ a + b·rₕ</code> with
      recursive least squares: <code>error = measuredₕ − (a + b·rₕ)</code>; the new <code>a</code>
      and <code>b</code> move toward that error with forgetting factor λ = {postproc.FORGET:g}. The
      initial model is <code>a = 0, b = 1</code> (“trust the raw model”), so local evidence builds
      gradually and older evidence fades. Standard RLS would target the weighted squared-error sum
      <code>Σᵢ₌₁ⁿ λ^(n−i)·[measuredᵢ − (a + b·rᵢ)]²</code>. This implementation is deliberately safer:
      it first clips each new error to ±{postproc.INNOVATION_CAP_KN:g} kn, updates <code>a</code> and <code>b</code>
      recursively, then projects <code>b</code> into [{postproc.SLOPE_LO:g}, {postproc.SLOPE_HI:g}]. It is therefore
      a robust online approximation, not the exact unconstrained minimum of that sum.</p>
      <p>The published difference <code>forecastₕ − measuredₕ</code> has a different job: it feeds
      the scorecard and the ±{learn.LARGE_ERR_KN:g} kn large-miss report. The regression trains on
      <i>raw model → measurement</i>, never an already-corrected forecast. Only the first-issued
      forecast and hours still in the future when it was issued can learn. Gusts use a separate
      smoothed ratio <code>g ← clip[0.6,1.8](0.7g + 0.3·measured gust/raw gust)</code>, guarded to
      {postproc.GUST_RATIO_LO}–{postproc.GUST_RATIO_HI}, where <code>g</code> is the bucket’s current gust multiplier.
      Measured direction is a flow-sector check,
      not physical-regime validation.</p>
      <h3>How it's checked, and how it tunes itself</h3>
      <p>Every issued hour is scored out of sample with <b>CRPS</b> (knots, lower better) against two baselines,
      <b>persistence</b> and <b>climatology</b>. On top of that an LLM tuner reviews
      <i>its own</i> earlier proposals against the measured CRPS, confirms or retracts each, and may
      propose small threshold changes — but a change is only written to the forecaster if replaying
      past days under it measurably lowers mean absolute error on at least {nmin} replayable days. Until that
      history exists, proposals are recorded and shown, and nothing is applied. For hourly ensemble
      members <code>xᵢ</code>, measurement <code>y</code>, and <code>m</code> members,
      <code>CRPS = (1/m)Σᵢ|xᵢ−y| − (1/2m²)ΣᵢΣⱼ|xᵢ−xⱼ|</code>, where <code>i</code> and <code>j</code>
      each run over those members. It evaluates the published hourly
      distribution—its centre and spread—not the RLS update; for a single-number forecast it equals
      <code>|forecastₕ − measuredₕ|</code>.</p>
    </section>"""
    return f"""
    <section class="card method">
      <h2>How it's predicted</h2>
      <p>Ammersee (~533 m) is an open foreland lake: wind is mostly <b>synoptic gradient</b> plus a
      <b>summer thermal (lake breeze)</b>; south föhn is rare here.</p>
      <h3>Prediction algorithm (per hour)</h3>
      <p class="muted"><b>Formula key:</b> <code>h</code> = forecast hour; <code>rₕ</code> = raw blended wind for that hour;
      <code>ŷₕ</code> = full locally corrected mean; <code>publishedₕ</code> = displayed forecast;
      <code>a</code> = additive local bias; <code>b</code> = local scaling; <code>n</code> = eligible prior
      observations in this exact lake×scenario×hour bucket.</p>
      <ol>
        <li><b>Blend the models.</b> Each hourly raw value is the equal-weight average of
            <code>(ICON-D2 ensemble + ICON-D2 deterministic + ICON-EU) ÷ 3</code> at the Herrsching point.
            If a source is unavailable, it averages the sources that are available. Ensemble spread
            becomes the uncertainty band.</li>
        <li><b>Assign a forecast scenario:</b> <b>föhn-favourable</b> (strong Δp plus southerly 850 hPa wind, rare) → <b>strong-gradient</b> (strong 925 hPa flow) → <b>thermal-favourable</b> (daytime with limited cloud) → <b>calm/capped</b>. The scenario selects a local correction; it is not a confirmed physical regime.</li>
        <li><b>Correct the hourly mean.</b> In the selected <code>(lake, scenario, hour)</code> bucket,
            the full correction is <code>ŷₕ = a + b·rₕ</code>, first bounded to within
            ±{fc.BIAS_CAP_KN:g} kn of the raw value. It is then ramped in as
            <code>publishedₕ = rₕ + min(1, n/3)·(ŷₕ − rₕ)</code>. Thus one observation moves the forecast only a third
            of the way (<code>n=1</code>), two move it two thirds, and <code>n≥3</code> can apply the full bounded correction.</li>
        <li><b>Why ramp it?</b> <code>n</code> counts only earlier eligible examples of this exact scenario at
            this exact hour—at most one per day—so rare buckets can have fewer than three. The ramp
            prevents one unusual day from applying the whole adjustment.</li>
        <li><b>Guard the gust.</b> The gust correction is a multiplier, so it gets different
            bounds than the mean: a learned ratio outside {postproc.GUST_RATIO_LO}–{postproc.GUST_RATIO_HI}
            is <b>refused</b> and the raw model gust published instead, and the result is capped at
            {fc.GUST_ENS_CEIL_MULT:g}× the hour's own ensemble gust members. Whenever a guard fires
            the row says so.</li>
        <li><b>Withhold the direction</b> as <b>VAR</b> when the ensemble disagrees more than
            {fc.SPREAD_LOW_KN:g} kn — at a wind reversal the bearing swings freely and a crisp
            compass point would claim precision the forecast does not have.</li>
      </ol>
      <p class="muted">No Kesselberg Δθ / föhn drivers here (Alpine-rim only).</p>
      <h3>How it's checked, and how it tunes itself</h3>
      <p>Every issued hour is scored out of sample with <b>CRPS</b> (knots, lower better) against
      <b>persistence</b> and <b>climatology</b> baselines. For hourly ensemble members <code>xᵢ</code>
      measurement <code>y</code>, and <code>m</code> members,
      <code>CRPS = (1/m)Σᵢ|xᵢ−y| − (1/2m²)ΣᵢΣⱼ|xᵢ−xⱼ|</code>, where <code>i</code> and <code>j</code>
      each run over those members. It evaluates the published hourly distribution—its centre and spread—not the RLS update; for
      a single-number forecast it equals <code>|forecastₕ − measuredₕ|</code>. An
      LLM tuner reviews its earlier proposals against subsequent forecast scores and may suggest small
      threshold changes, but a change reaches the forecaster only if a backtest over at least {nmin} replayable days shows it lowers mean absolute error.</p>
      <h3>How it learns</h3>
      <p>In the legacy daily path, every eligible measured hour updates only that hour’s
      <code>(lake, scenario, hour)</code> bucket. It fits <code>measuredₕ ≈ a + b·rₕ</code> with
      recursive least squares: <code>error = measuredₕ − (a + b·rₕ)</code>; the new <code>a</code>
      and <code>b</code> move toward that error with forgetting factor λ = {postproc.FORGET:g}. The
      initial model is <code>a = 0, b = 1</code> (“trust the raw model”), so local evidence builds
      gradually and older evidence fades. Standard RLS would target the weighted squared-error sum
      <code>Σᵢ₌₁ⁿ λ^(n−i)·[measuredᵢ − (a + b·rᵢ)]²</code>. This implementation is deliberately safer:
      it first clips each new error to ±{postproc.INNOVATION_CAP_KN:g} kn, updates <code>a</code> and <code>b</code>
      recursively, then projects <code>b</code> into [{postproc.SLOPE_LO:g}, {postproc.SLOPE_HI:g}]. It is therefore
      a robust online approximation, not the exact unconstrained minimum of that sum.</p>
      <p>The published difference <code>forecastₕ − measuredₕ</code> has a different job: it feeds
      the scorecard and the ±{learn.LARGE_ERR_KN:g} kn large-miss report. The regression trains on
      <i>raw model → measurement</i>, never an already-corrected forecast. Only the first-issued
      forecast and hours still in the future when it was issued can learn. Gusts use a separate
      smoothed ratio <code>g ← clip[0.6,1.8](0.7g + 0.3·measured gust/raw gust)</code>, guarded to
      {postproc.GUST_RATIO_LO}–{postproc.GUST_RATIO_HI}, where <code>g</code> is the bucket’s current gust multiplier.</p>
      <h3>Which truth it learned from</h3>
      <p>This matters more than it sounds: Ammersee's measured truth has <b>changed hands</b>
      — the on-water buoy until 15.06.2026, a calibrated shore blend since — and an error figure
      is <b>not comparable across that boundary</b>. Every day therefore records the source that
      produced it, and any change is logged loudly. The
      <a href="{_href('measurements', static)}">measured archive</a> shows the source for each day
      alongside the numbers.</p>
    </section>"""


def _data_sources(group):
    common = ("All sources are fetched server-side with Python <code>urllib</code> and the system "
              "CA bundle, which validates through the corporate Zscaler TLS-intercepting proxy. "
              "The live forecast uses Open-Meteo point and ensemble APIs. A separate raw-GRIB "
              "reader exists for diagnostics/backups but is not called by the daily pipeline "
              "(DWD data © Deutscher Wetterdienst, CC BY 4.0).")
    if group == "kochel-walchensee":
        pred = [
            ("ICON-D2", "forecast backbone (2.2 km, hourly, 48 h, 8 runs/day)",
             "Open-Meteo point forecast "
             "<code>api.open-meteo.com/v1/forecast?…&amp;models=icon_d2</code> (incl. 850/925 hPa)"),
            ("ICON-D2 ensemble", "confidence (20 members → P10/P50/P90); its gust members also "
             "set the ceiling a corrected gust may not exceed",
             "Open-Meteo <code>ensemble-api.open-meteo.com/v1/ensemble?…&amp;models=icon_d2</code>"),
            ("ICON-EU", "independent second model in the blend + horizon beyond 48 h",
             "Open-Meteo <code>api.open-meteo.com/v1/forecast?…&amp;models=icon_eu</code>"),
            ("DWD MOSMIX", "föhn trigger — cross-Alpine Δp (Bozen − München)",
             "KML/KMZ from <code>opendata.dwd.de/…/MOSMIX_L/single_stations/{16020,10865}/kml/</code>, "
             "parsed for the <code>PPPP</code> pressure series"),
            ("addicted-sports spot forecast", "blend member — each lake's own on-spot forecast",
             "the <code>avg</code>/<code>boe</code> series, per lake: "
             "<code>…/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code> for Walchensee, "
             "<code>…/forecast/kochelsee/trimini/?json=wind&amp;from=DATE</code> for Kochelsee"),
            ("addicted-sports drivers", "displayed diagnostics (föhn gradient, lapse, radiation); "
             "not regression predictors",
             "the <code>drivers</code> block of the same per-lake feed "
             "(<code>…/walchensee/urfeld/…</code> for Walchensee, "
             "<code>…/kochelsee/trimini/…</code> for Kochelsee)"),
            ("Open-Meteo T2m", "Kochel−Walchensee Δθ stability index",
             "<code>api.open-meteo.com/v1/forecast?hourly=temperature_2m</code> at both lake points"),
        ]
        meas = [
            ("addicted-sports Urfeld", "central-water surface-wind reference (Walchensee)",
             "<code>mavg</code>/<code>mmax</code>/<code>dir</code> from the same JSON feed "
             "<code>…/forecast/walchensee/urfeld/?json=wind&amp;from=DATE</code>. The operator places "
             "the anemometer on a buoy near the lake centre at ~1.6 m above water: very representative "
             "of surface wind there, but not a 10 m reference. Sensor model/calibration accuracy is not published."),
            ("addicted-sports Trimini", "lake-edge local wind (Kochelsee south shore)",
             "<code>mavg</code>/<code>mmax</code>/<code>dir</code> from "
             "<code>…/forecast/kochelsee/trimini/?json=wind&amp;from=DATE</code> — the station at the "
             "Kristall Therme Trimini grounds (~629 m, ~4 m above water). It is the best available local "
             "spot reference but shore/terrain exposure means it is not lake-wide; sensor model/calibration "
             "accuracy is not published. Speed gaps render as NR rather than a fabricated delta."),
        ]
    else:
        pred = [
            ("ICON-D2", "forecast backbone (2.2 km, hourly, 48 h)",
             "Open-Meteo point <code>…?models=icon_d2</code>"),
            ("ICON-EU", "independent cross-check + horizon beyond 48 h",
             "Open-Meteo <code>api.open-meteo.com/v1/forecast?…&amp;models=icon_eu</code>"),
            ("ICON-D2 ensemble", "confidence (20 members)",
             "Open-Meteo <code>ensemble-api.open-meteo.com/v1/ensemble</code>"),
        ]
        meas = [
            ("GKD Ammerseeboje", "measured actual — official buoy ON the water (preferred; "
             "wins automatically the moment it reports again)",
             "hourly means from <code>gkd.bayern.de/de/meteo/wind/isar/ammerseeboje-16601050"
             "/messwerte/tabelle</code> (© GKD Bayern, CC BY 4.0). Speed only — direction and "
             "gust are taken from BSV for the same hours. <b>Offline since 15.06.2026</b> "
             "(electronics defect; LfU says the repair will take weeks), so the blend below is "
             "in use. It is re-probed every morning and the change is logged loudly"),
            ("BSV Herrsching + DWD Wielenbach", "measured actual — <b>blend of both, each "
             "calibrated to lake-equivalent</b>, while the buoy is down",
             "Neither shore station is good enough alone: on 1273 held-out hours against the "
             "buoy, calibrated DWD scored 2.79 kn mean absolute error and calibrated BSV 2.92 — "
             "but the <b>mean of the two scored 2.64</b>, better than either. They sit on "
             "opposite shores, so much of their error is independent noise that averaging "
             "cancels. Speed is the blend; <b>direction and gusts come from BSV</b>, which has a "
             "real sensor at the lake"),
            ("BSV Herrsching", "the on-lake half — a sailing club's Davis Vantage Pro2 on the "
             "east shore (speed, gusts AND direction)",
             "15-minute series embedded in <code>wetter.bsv-ammersee.de/PWS_graph_xx.php"
             "?type=wind&amp;period=day1&amp;y=&amp;m=&amp;d=</code>; any past day is one "
             "request. Days are cached and mirrored into a committed archive so the history "
             "survives even if that server goes away"),
            ("DWD 10-min obs", "the inland half — Wielenbach 05538 (lake-level, ~11 km inland); "
             "DWD's own closest wind station to Ammersee",
             "zipped 10-min FF/DD from "
             "<code>opendata.dwd.de/climate_environment/CDC/…/10_minutes/wind/recent/</code>"),
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
      <h3><span class="chip meas">measured</span> Measured inputs — reconciled when reported</h3>
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
/* Observed flow is a diagnostic derived from measured direction, not the forecast
   scenario. Keeping it neutral prevents S–SE flow from looking like the violet
   föhn-favourable forecast scenario. */
.badge.flow{background:transparent;border:1px solid var(--muted);color:var(--ink2)}
.elapsed td{opacity:.48}
.hourly-status.success{border-left:3px solid var(--t)} .hourly-status.failure{border-left:3px solid var(--f)}
.ok{color:var(--t);font-weight:600}.bad{color:var(--f);font-weight:600}
.scenario-code{width:38px;text-align:center}
.sw.gradient{background:var(--g)}.sw.thermal{background:var(--t)}
.sw.foehn{background:var(--f)}.sw.calm{background:var(--c)}
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
      <span><i class="sw" style="background:var(--t)"></i>thermal-favourable</span>
      <span><i class="sw" style="background:var(--f)"></i>föhn-favourable</span>
      <span><i class="sw" style="background:var(--g)"></i>strong-gradient</span>
      <span><i class="sw" style="background:var(--c)"></i>calm/capped</span>
    </div>
    <div class="tscale"><span>mean &amp; gust (kn):</span>
      <span class="tsend">calm {cmin}</span>
      <span class="tsbar" style="background:linear-gradient(to right,{grad})"></span>
      <span class="tsend">{cmax}+ strong</span>
    </div>
  </div>
  <div class="reghint"><b>Scenario colour code</b> — a rule-based weather pattern that selects the local correction; it is not a confirmed physical regime:
    <b>thermal-favourable</b> sun-driven lake/valley breeze · <b>föhn-favourable</b> warm, gusty south fall-wind ·
    <b>gradient</b> frontal / pressure-driven flow · <b>calm</b> little or no wind.</div>
  <div class="reghint"><b>Reading the table</b> — forecast is the wind issued for that hour; measured and
    <b>Δ fc−meas</b> appear once an observation is available. Positive Δ means the forecast was too strong;
    negative Δ means it was too weak. <b>NR</b> means the selected measurement source did not report that
    elapsed hour; future hours show —.</div>"""


def _overview_date(recs):
    """The date the overview heading states, taken from the RECORDS BEING SHOWN rather
    than from the clock.

    datetime.date.today() would be a second authority for the same fact: the first morning
    the daily job fails to run, a clock-derived heading would announce today's date above
    yesterday's numbers. Reading it off the records means the heading can only ever be
    wrong if the numbers under it are wrong too. If the lakes somehow disagree (one lake's
    run failed), say so as a range instead of silently picking one."""
    dates = sorted({r.get("date") for r in recs if r and r.get("date")})
    if not dates:
        return None
    return dates[0] if len(dates) == 1 else f"{dates[0]} – {dates[-1]}"


def index_html(static=False):
    tiles, shown = [], []
    for key, g in GROUPS.items():
        teaser = []
        for lake in g["lakes"]:
            rec = _latest_forecast(lake)
            if rec:
                shown.append(rec)
                teaser.append(f'<div><b>{html.escape(rec.get("label", lake.title()))}:</b> '
                              f'{html.escape(rec.get("summary",""))}</div>')
        tiles.append(f'<a class="tile" href="{_href(key, static)}"><h2>{html.escape(g["title"])} →</h2>'
                     f'<p class="blurb">{html.escape(g["blurb"])}</p>'
                     f'<div class="teaser">{"".join(teaser)}</div></a>')
    day = _overview_date(shown)
    heading = (f'<h2>Forecast overview for {html.escape(day)}</h2>' if day else
               '<h2>Forecast overview — no forecast on record yet</h2>')
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bavarian lake wind</title><style>{_css()}</style></head><body>
<header><h1>Bavarian lake wind forecasts</h1>
<div class="sub">generated {_generated()} · updates hourly · hourly learning</div></header>
<main>{heading}<div class="tiles">{''.join(tiles)}</div></main>
<footer>Choose a report. Each has its own hourly forecast, self-learning history, and the
prediction methodology for those lakes.</footer></body></html>"""


def report_html(group, static=False):
    g = GROUPS[group]
    other = [k for k in GROUPS if k != group]
    nav = (f'<div class="nav"><a href="{_href("", static)}">← all lakes</a>'
           + "".join(f' &nbsp;·&nbsp; <a href="{_href(k, static)}">{html.escape(GROUPS[k]["title"])}</a>'
                     for k in other)
           + f' &nbsp;·&nbsp; <a href="{_href("measurements", static)}">📊 measured archive</a>'
           + "</div>")
    fcards = "".join(_forecast_card(_latest_forecast(l)) for l in g["lakes"])
    hourly = any((_latest_forecast(l) or {}).get("rolling") for l in g["lakes"])
    cadence = "updates hourly · timestamped 24-hour windows" if hourly else "updates ~05:00 daily"
    # Pair each lake's big-miss table with ITS OWN "all measured hours" dropdown, wrapped
    # so the two read as one unit (tighter internal gap than between lakes). Previously
    # both diff tables came first and both dropdowns after, so the Walchensee dropdown sat
    # under the Kochelsee table.
    date = next((r["date"] for r in (_latest_forecast(l) for l in g["lakes"]) if r), "—")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(g['title'])} wind — {date}</title><style>{_css()}</style></head><body>
<header>{nav}
  <h1>{html.escape(g['title'])} — {date}</h1>
  <div class="sub">generated {_generated()} · {cadence} · knots (Beaufort) · gusts in kn</div>
  {_legend()}
</header>
<main>
  {('<div class="analyst"><b>How hourly forecasting works.</b> Shortly before each hour begins, the system issues a fresh 24-hour forecast and keeps today’s 00–23 table up to date. Its starting point is a blend of ICON-D2, ICON-EU and ensemble weather-model data, plus a local spot forecast where available; the “How it’s predicted” and input sections below explain the calculation and sources. Every run also reads the station, compares each newly available completed hour with its forecast, and uses that reading once to improve future forecasts.</div>' if hourly else '')}
  <div class="sec"><span class="chip fc">forecast</span> Predicted — today ({date})</div>
  {fcards or '<p class="muted">No hourly forecast logged yet — run hourly_run.py.</p>'}
  {_methodology(group, static)}
  {_data_sources(group)}
  {_hourly_learning_section(g["lakes"])}
  {_hourly_status_section()}
</main>
<footer>Raw model wind is a first guess, corrected toward measured wind and improved as hourly
self-learning pairs accumulate; "raw (no local calib yet)" hours are uncalibrated. Residual error ~1–1.5 kn+,
worst in thermal/föhn.</footer></body></html>"""


def _measurements_data():
    """Measured archive from hourly reconciliation, with legacy diffs as fallback.

    Shape: {lake: {date: {"src": str, "rows": [[hr,meas,gust,dir,fc,err]]}}}.
    The hourly archive wins for a date because it contains the frozen hourly
    forecast-of-record rather than a calendar-day daily forecast.
    """
    out = {}
    for lake in ("ammersee", "kochelsee", "walchensee"):
        days = {}
        p = os.path.join(wd.LOG_DIR, f"{lake}_diffs.jsonl")
        if os.path.exists(p):
            for line in open(p):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("actual_kn") is None:
                    continue
                d = days.setdefault(r["date"], {"src": r.get("source") or "source not recorded", "rows": []})
                d["rows"].append([r["hour"], r.get("actual_kn"), r.get("actual_gust_kn"),
                                  r.get("dir_actual"), r.get("issued_kn"), r.get("err_issued_kn"),
                                  bool(r.get("leaked"))])
        hp = os.path.join(wd.LOG_DIR, f"{lake}_hourly_measurements.jsonl")
        hourly = {}
        if os.path.exists(hp):
            for line in open(hp):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("measurement_status") != "present":
                    continue
                date, hour = r["valid_time"][:10], int(r["valid_time"][11:13])
                d = hourly.setdefault(date, {"src": r.get("measurement_source") or "source not recorded", "rows": []})
                d["rows"].append([hour, r.get("measured_kn"), r.get("measured_gust_kn"), None,
                                  r.get("forecast_kn"), r.get("fc_minus_measured_kn"), False])
        days.update(hourly)
        for d in days.values():
            d["rows"].sort(key=lambda x: x[0])
        if days:
            out[lake] = days
    return out


def measurements_html(static=False):
    """A browsable archive of MEASURED wind, one day at a time.

    Static hosting, so the day picker is client-side: every day is embedded once and the
    table is rebuilt in the browser. That keeps it to a single file that works on GitHub
    Pages with no server, and the whole archive is a few hundred KB.

    Each day names the SOURCE that produced it. That matters more than it looks: Ammersee's
    truth has changed hands over this archive — the buoy until 2026-06-15, then a calibrated
    shore estimate — and an error figure is not comparable across that boundary. The page
    says which truth produced each day rather than presenting them as one series."""
    data = _measurements_data()
    lakes = [l for l in ("ammersee", "kochelsee", "walchensee") if l in data]
    default_lake = lakes[0] if lakes else ""
    payload = json.dumps(data, separators=(",", ":"))
    opts = "".join(f'<option value="{l}">{html.escape(_lake_label(l))}</option>' for l in lakes)
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Measured wind archive</title><style>{_css()}
.pick{{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:0 0 12px}}
.pick select{{font:inherit;padding:5px 8px;border:1px solid var(--grid);
 background:var(--plane);color:var(--ink);border-radius:6px}}
.srcline{{font-size:12.5px;color:var(--ink2);margin:2px 0 10px}}
.srcline b{{color:var(--ink)}}
.leaked{{opacity:.55}}
</style></head><body>
<header><h1>Measured wind — archive</h1>
<div class="sub">what was actually measured, hour by hour · generated {_generated()}</div></header>
<main>
<section class="card">
  <div class="pick">
    <label>Lake <select id="lake">{opts}</select></label>
    <label>Day <select id="day"></select></label>
    <span id="count" class="muted"></span>
  </div>
  <div id="src" class="srcline"></div>
  <table id="tbl"><thead><tr><th>Hour</th><th>Dir</th><th>Measured</th><th>Gust</th>
  <th>Forecast</th><th>&Delta; fc&minus;meas</th></tr></thead><tbody></tbody></table>
  <p class="blurb" style="margin-top:10px">
    &Delta; is forecast minus measured, so positive means the forecast was too strong.
    Greyed rows had already elapsed when the forecast was issued, so they were recorded but
    never learned from or scored.</p>
</section>
<p><a href="{_href('', static)}">&larr; back to the overview</a>
{"".join(f' &nbsp;·&nbsp; <a href="{_href(k, static)}">{html.escape(GROUPS[k]["title"])}</a>' for k in GROUPS)}</p>
</main>
<script>
const DATA = {payload};
const lakeSel = document.getElementById('lake'), daySel = document.getElementById('day');
function fillDays() {{
  const days = Object.keys(DATA[lakeSel.value] || {{}}).sort().reverse();
  daySel.innerHTML = days.map(d => `<option value="${{d}}">${{d}}</option>`).join('');
  draw();
}}
function draw() {{
  const day = (DATA[lakeSel.value] || {{}})[daySel.value];
  const tb = document.querySelector('#tbl tbody');
  document.getElementById('src').innerHTML = day
    ? 'Measured by: <b>' + day.src.replace(/[<>&]/g, '') + '</b>' : '';
  document.getElementById('count').textContent = day ? day.rows.length + ' hours' : 'no data';
  tb.innerHTML = (day ? day.rows : []).map(r => {{
    const [hr, meas, gust, dir, fc, err, leaked] = r;
    const f = v => (v === null || v === undefined) ? '&middot;' : (+v).toFixed(1);
    return `<tr class="${{leaked ? 'leaked' : ''}}"><td class="hr">${{String(hr).padStart(2,'0')}}</td>`
      + `<td class="dir">${{dir === null || dir === undefined ? '&middot;' : Math.round(dir) + '&deg;'}}</td>`
      + `<td class="wind">${{f(meas)}}</td><td class="gust">${{f(gust)}}</td>`
      + `<td>${{f(fc)}}</td><td>${{err === null || err === undefined ? '&middot;' : (err > 0 ? '+' : '') + (+err).toFixed(1)}}</td></tr>`;
  }}).join('');
}}
lakeSel.addEventListener('change', fillDays);
daySel.addEventListener('change', draw);
fillDays();
</script>
</body></html>"""


def _selftest():
    """The overview heading must describe the records it sits above, never the clock."""
    assert _overview_date([{"date": "2026-08-05"}, {"date": "2026-08-05"}]) == "2026-08-05"
    # a stale record must drag the heading back with it, not be papered over
    assert _overview_date([{"date": "2026-08-05"}, {"date": "2026-08-04"}]) == "2026-08-04 – 2026-08-05"
    assert _overview_date([]) is None and _overview_date([None, {}]) is None
    h = index_html()
    day = _overview_date([_latest_forecast(l) for l in ("ammersee", "kochelsee", "walchensee")])
    if day:
        assert f"Forecast overview for {day}" in h, "heading missing from the rendered index"
        assert h.index("Forecast overview") < h.index('class="tiles"'), \
            "heading must come BEFORE the prediction overview"
    print(f"  PASS index heading: 'Forecast overview for {day}', placed above the tiles")

    # --- measured archive page ---
    data = _measurements_data()
    m = measurements_html(static=True)
    assert "const DATA = {" in m, "the day archive shipped with no data payload"
    for lake in data:
        assert f'value="{lake}"' in m, f"{lake} has measurements but no picker entry"
    # every day must name the source that produced it: Ammersee's truth changed hands
    # mid-archive (buoy -> calibrated shore estimate) and the two are not comparable
    unsourced = 0
    for lake, days in data.items():
        for d, v in days.items():
            assert v.get("src"), f"{lake} {d} would render with a blank source field"
            unsourced += v["src"] == "source not recorded"
    # the forecast pages must actually link to it, or it is unreachable
    for g in GROUPS:
        assert "measurements" in report_html(g, static=True), \
            f"{g} page does not link to the measured archive"
    # --- the prose must describe the code, not a previous version of it ---
    # This section drifted badly once: the Ammersee page still said the measured truth was
    # "DWD Wielenbach" and called BSV a reference "not yet the learning actual", months after
    # both had stopped being true. Prose has no compiler, so pin the load-bearing claims.
    for g in GROUPS:
        page = report_html(g, static=True)
        for token, why in (
                (f"{fc.SPREAD_LOW_KN:g} kn", "the direction-withholding threshold"),
                (f"{postproc.GUST_RATIO_LO}", "the gust-ratio plausibility floor"),
                (f"{postproc.GUST_RATIO_HI}", "the gust-ratio plausibility ceiling"),
                (f"{fc.GUST_ENS_CEIL_MULT:g}×", "the ensemble gust ceiling multiplier"),
                ("VAR", "the withheld-direction marker")):
            assert token in page, f"{g} page never mentions {why} ({token})"
        for stale in ("measured regime", "terrain then fixes direction", "lowers CRPS"):
            assert stale not in page, f"stale methodology claim on {g}: {stale!r}"
        assert "scenario" in page.lower(), f"{g} page does not explain forecast scenarios"
        assert "not a confirmed physical regime" in page.lower(), \
            f"{g} page omits scenario-causality caveat"
    amm = report_html("ammersee", static=True)
    assert "blend" in amm.lower(), "the Ammersee page must say its truth is a blend"
    assert "Ammerseeboje" in amm, "the Ammersee page must name the preferred on-water source"
    # claims that were true once and are now false must not come back
    for stale in ("not yet the learning actual", "the DWD fallback below is currently in use"):
        assert stale not in amm, f"stale claim resurfaced on the Ammersee page: {stale!r}"
    print("  PASS page prose: guard thresholds, VAR, blend and buoy all stated from the "
          "code's own constants; retired claims blocked")
    n = sum(len(v["rows"]) for days in data.values() for v in days.values())
    print(f"  PASS measured archive: {len(data)} lake(s), "
          f"{sum(len(d) for d in data.values())} days, {n} hours, "
          f"{unsourced} day(s) predate source recording (shown as such, not hidden)")
    print("ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "index"
    if which == "selftest":
        _selftest()
    else:
        print(index_html() if which == "index" else report_html(which))
