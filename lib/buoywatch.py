#!/usr/bin/env python3
"""
buoywatch.py — probe the Ammerseeboje every day and announce, loudly, when it comes back.

The buoy (GKD station 16601050, the only buoy in GKD's entire 127-station wind network)
went offline 2026-06-15 with an electronics defect. LfU's own page says the repair will
take "einige Wochen". While it is down Ammersee is trained and graded against a shore
station, which is strictly worse — so the day it returns matters, and nobody should have
to notice it by hand.

THE SWITCH IS ALREADY AUTOMATIC. winddata.measured_source retries the buoy on every single
call and prefers it the moment it reports GKD_MIN_HOURS of data. This module does not
re-implement that decision — re-deriving it here is exactly the kind of second authority
that has bitten this project before. It ASKS measured_source what it chose, compares that
against what was chosen last time, and turns any change into a logged event.

What it produces:
  * a `truth_source` event every day (the heartbeat: which source fed this lake, and
    whether the buoy answered) — idempotent per (lake, date), so a re-run replaces it;
  * a LOUD `truth_source_change` event on any transition, plus a banner in the daily
    report and a line the site renders.

State lives in the event log itself rather than in a separate file: the previous status is
whatever the last truth_source event says. One store, no drift.
"""
import os, sys, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd

# How each source id reads to a human, worst to best, plus a rank so a transition can be
# described as a recovery or a degradation rather than just "it changed".
# Ranked worst to best. The order is the measured one, not a guess: on 1273 held-out hours
# against the buoy, calibrated DWD alone scored MAE 2.788 kn, calibrated BSV alone 2.915,
# and the blend of the two 2.639. The buoy itself is not an estimate at all — it sits on
# the water — so anything with a buoy outranks every shore-station combination.
SOURCE_RANK = {"none": 0, "bsv": 1, "dwd": 2, "blend": 3, "ads": 4,
               "buoy+dwd": 5, "buoy+bsv": 6}
SOURCE_LABEL = {
    "none":     "no measurement at all",
    "bsv":      "BSV Herrsching alone, calibrated (DWD unavailable)",
    "dwd":      "DWD Wielenbach alone, 11 km inland, calibrated (BSV unavailable)",
    "blend":    "BSV Herrsching + DWD Wielenbach blended, both calibrated",
    "ads":      "addicted-sports on-lake station",
    "buoy+dwd": "Ammerseeboje ON THE LAKE (+ DWD for direction/gust)",
    "buoy+bsv": "Ammerseeboje ON THE LAKE (+ BSV for direction/gust)",
}
# Which sources are real on-water measurement rather than a calibrated shore estimate.
MEASURED_ON_WATER = ("buoy+dwd", "buoy+bsv", "ads")


def last_status(lake):
    """The most recent recorded source id for a lake, or None if never recorded.
    Read from the event log — the same store the watcher writes to."""
    path = wd.EVENTS_LOG
    if not os.path.exists(path):
        return None, None
    best = None
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "truth_source" and r.get("lake") == lake:
            if best is None or (r.get("date") or "") >= (best.get("date") or ""):
                best = r
    return (best.get("source_id"), best.get("date")) if best else (None, None)


def check(lake, date, stamp=None):
    """Probe today's source for `lake`, log the heartbeat, and log LOUDLY on a change.
    Returns a result dict; never raises — a watcher must not be able to break the run."""
    prev_id, prev_date = last_status(lake)
    try:
        src = wd.measured_source(lake, date)
        sid, label, n_hours = src["id"], src["label"], len(src.get("data") or {})
        err = None
    except Exception as e:                      # a probe failure is itself information
        sid, label, n_hours, err = "none", f"probe failed: {type(e).__name__}", 0, str(e)

    buoy_up = sid.startswith("buoy")
    changed = prev_id is not None and prev_id != sid
    recovered = changed and SOURCE_RANK.get(sid, 0) > SOURCE_RANK.get(prev_id, 0)

    # The daily record of WHAT WE MEASURED AGAINST. Anyone reading a learning report or a
    # scorecard later needs to know whether that day's "truth" was the buoy on the water or
    # a calibrated estimate from a shore station — the two are not the same claim, and a
    # scorecard is not comparable across a change of source.
    wd.log_event("truth_source", {
        "lake": lake, "date": date, "source_id": sid, "source": label,
        "buoy_up": buoy_up, "on_water": sid in MEASURED_ON_WATER,
        "estimate": sid not in MEASURED_ON_WATER and sid != "none",
        "n_hours": n_hours, "rank": SOURCE_RANK.get(sid, 0),
        "explanation": SOURCE_LABEL.get(sid, sid), "error": err}, stamp=stamp)

    if changed:
        wd.log_event("truth_source_change", {
            "lake": lake, "date": date, "from": prev_id, "to": sid,
            "from_label": SOURCE_LABEL.get(prev_id, prev_id),
            "to_label": SOURCE_LABEL.get(sid, sid),
            "direction": "recovered" if recovered else "degraded",
            "since": prev_date, "source": label}, stamp=stamp)

    return {"lake": lake, "date": date, "source_id": sid, "source": label,
            "buoy_up": buoy_up, "n_hours": n_hours, "changed": changed,
            "recovered": recovered, "previous": prev_id, "previous_date": prev_date,
            "error": err}


def banner(res):
    """One loud line for the daily report. Empty string when nothing changed and the
    buoy is still down — the steady state should not shout every morning."""
    if res.get("changed"):
        arrow = "RECOVERED" if res["recovered"] else "DEGRADED"
        return (f"*** MEASUREMENT SOURCE {arrow} — {res['lake']}: "
                f"{SOURCE_LABEL.get(res['previous'], res['previous'])} -> "
                f"{SOURCE_LABEL.get(res['source_id'], res['source_id'])} ***")
    if res.get("buoy_up"):
        return f"    {res['lake']}: buoy healthy ({res['n_hours']} h)"
    return ""


def run_all(date=None, stamp=None, verbose=True):
    date = date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    out = []
    for lake in ("ammersee",):          # only Ammersee has a buoy to watch
        r = check(lake, date, stamp)
        out.append(r)
        if verbose:
            b = banner(r)
            print(b if b else
                  f"    {lake}: buoy still down — using {SOURCE_LABEL.get(r['source_id'], r['source_id'])}"
                  f" ({r['n_hours']} h)")
    return out


def _selftest():
    """Transition detection must key off what measured_source actually returned, and must
    describe a move toward the buoy as a recovery rather than merely 'changed'."""
    # The order is the MEASURED one (1273 held-out hours vs the buoy): blend 2.639 kn beats
    # dwd 2.788 beats bsv 2.915, and anything on the water beats every shore estimate.
    # An earlier version of this test asserted bsv > dwd, encoding a belief that came from
    # a 109-hour sample and was wrong. Keep the numbers in the comment so the next person
    # to reorder this has to argue with evidence.
    assert (SOURCE_RANK["buoy+bsv"] > SOURCE_RANK["ads"] > SOURCE_RANK["blend"]
            > SOURCE_RANK["dwd"] > SOURCE_RANK["bsv"] > SOURCE_RANK["none"]), \
        "ranking must order the sources worst-to-best or 'recovered' is meaningless"
    assert all(s in SOURCE_RANK for s in MEASURED_ON_WATER)
    assert min(SOURCE_RANK[s] for s in MEASURED_ON_WATER) > SOURCE_RANK["blend"], \
        "a real on-water measurement must outrank every calibrated shore estimate"
    for k in SOURCE_RANK:
        assert k in SOURCE_LABEL, f"{k} would be reported to a human as a bare token"
    r = {"changed": True, "recovered": True, "lake": "ammersee",
         "previous": "bsv", "source_id": "buoy+bsv"}
    b = banner(r)
    assert "RECOVERED" in b and "Ammerseeboje" in b, b
    r2 = {"changed": False, "buoy_up": False}
    assert banner(r2) == "", "steady state must not shout every morning"
    assert "healthy" in banner({"changed": False, "buoy_up": True, "lake": "x", "n_hours": 24})
    print("  PASS buoywatch: ranking ordered, every id explainable, quiet in steady state")
    print("ALL SELF-TESTS PASSED")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selftest()
    else:
        d = sys.argv[1] if len(sys.argv) > 1 else None
        print("=== Ammerseeboje watch ===")
        for r in run_all(d):
            print(f"  source_id={r['source_id']}  buoy_up={r['buoy_up']}  "
                  f"hours={r['n_hours']}  changed={r['changed']}")
            print(f"  {r['source']}")
