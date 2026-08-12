#!/usr/bin/env python3
"""Issue a rolling 24-hour forecast window for local testing.

The daily pipeline remains untouched while the hourly forecast-of-record migration is
being built. This command writes separate ``logs/<lake>_hourly_forecast.jsonl`` files:
one record contains 24 rows valid from the next full Berlin hour through the following
24 hours. It never overwrites the calendar-day forecast log.

Examples:
  .venv/bin/python hourly_run.py --dry-run
  .venv/bin/python hourly_run.py --at 2026-08-12T04:55+02:00
"""
import argparse, datetime, json, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import forecast as fc
import winddata as wd
import postproc
import verify


def next_hour(now):
    # Round in UTC, then convert back. Adding an hour to a Berlin wall-clock datetime
    # can manufacture the nonexistent 02:00 during the spring DST transition.
    local = now.astimezone(wd.BERLIN)
    utc = local.astimezone(datetime.timezone.utc)
    rounded = utc.replace(minute=0, second=0, microsecond=0)
    if utc.minute or utc.second or utc.microsecond:
        rounded += datetime.timedelta(hours=1)
    return rounded.astimezone(wd.BERLIN)


def select_forecast_of_record(records, valid_time):
    """The latest issued record strictly before ``valid_time`` that contains it.

    This is the hourly equivalent of the daily forecast-of-record rule: a later refresh
    must never revise the forecast used to score an elapsed hour.
    """
    if isinstance(valid_time, str):
        valid_time = datetime.datetime.fromisoformat(valid_time)
    candidates = []
    for rec in records:
        issued = datetime.datetime.fromisoformat(rec["issue_time"])
        for row in rec.get("hourly", []):
            if row.get("valid_time") == valid_time.isoformat() and issued < valid_time:
                candidates.append((issued, row, rec))
    return max(candidates, key=lambda x: x[0]) if candidates else None


def build_window(lake, issued):
    start = next_hour(issued)
    end = start + datetime.timedelta(hours=24)
    rows = []
    # fc.build_table currently filters a calendar date after fetching a three-day model
    # response. A window crossing midnight needs two calls, so cache the identical model
    # and MOSMIX fetches during this one issuance.
    cache = {}
    old_point, old_ens, old_dp = wd.openmeteo_point, wd.openmeteo_ensemble, wd.foehn_delta_p
    def cached(fn, name):
        def inner(*args, **kwargs):
            key = (name, repr(args), repr(sorted(kwargs.items())))
            if key not in cache:
                cache[key] = fn(*args, **kwargs)
            return cache[key]
        return inner
    wd.openmeteo_point = cached(old_point, "point")
    wd.openmeteo_ensemble = cached(old_ens, "ensemble")
    wd.foehn_delta_p = cached(old_dp, "dp")
    try:
        for day in sorted({start.date(), (end - datetime.timedelta(hours=1)).date()}):
            table = fc.build_table(lake, day.isoformat(), run_stamp=issued.isoformat(timespec="minutes"))
            for row in table["rows"]:
                valid = datetime.datetime.combine(day, datetime.time(row["hour"]), wd.BERLIN)
                if start <= valid < end:
                    rows.append({**fc.logged_row(row), "valid_time": valid.isoformat(),
                                 "lead_minutes": int((valid - issued).total_seconds() // 60),
                                 "blend_kn": row.get("blend_kn"),
                                 "blend_range_kn": row.get("blend_range_kn")})
    finally:
        wd.openmeteo_point, wd.openmeteo_ensemble, wd.foehn_delta_p = old_point, old_ens, old_dp
    rows.sort(key=lambda r: r["valid_time"])
    assert len(rows) == 24, f"{lake}: expected 24 rows, got {len(rows)}"
    return {"lake": lake, "kind": "hourly_forecast", "issue_time": issued.isoformat(timespec="minutes"),
            "valid_start": start.isoformat(), "valid_end": end.isoformat(), "hourly": rows}


def write(record):
    path = os.path.join(wd.LOG_DIR, f"{record['lake']}_hourly_forecast.jsonl")
    old = []
    if os.path.exists(path):
        old = [x.rstrip("\n") for x in open(path) if x.strip()]
    key = record["issue_time"]
    old = [x for x in old if json.loads(x).get("issue_time") != key]
    old.append(json.dumps(record))
    with open(path, "w") as f:
        f.write("\n".join(old) + "\n")


def reconcile_measurements(lake, now=None, actual_provider=wd.actual_hourly):
    """Attach observations to the forecast-of-record for every elapsed valid hour."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
    if not os.path.exists(path):
        return 0
    records = [json.loads(x) for x in open(path) if x.strip()]
    by_date, sources = {}, {}
    now = (now or datetime.datetime.now(wd.BERLIN)).astimezone(wd.BERLIN)
    valid_times = sorted({r["valid_time"] for rec in records for r in rec.get("hourly", [])
                          if datetime.datetime.fromisoformat(r["valid_time"]) < now})
    changed = learned = 0
    bias = fc.load_bias(lake)
    for vt in valid_times:
        selected = select_forecast_of_record(records, vt)
        if selected is None:
            continue
        _issued, row, _rec = selected
        if row.get("measured_kn") is not None:
            continue
        d = vt[:10]
        if d not in by_date:
            by_date[d], sources[d] = actual_provider(lake, d)
        hour = int(vt[11:13])
        obs = by_date[d].get(hour)
        if obs is None:
            continue
        row["measured_kn"] = obs["mean_kn"]
        row["measured_gust_kn"] = obs.get("gust_kn")
        row["measured_source"] = sources[d]
        row["fc_minus_measured_kn"] = round(row["mean_kn"] - obs["mean_kn"], 1)
        # Exactly once per selected forecast-of-record row. The hourly record carries the
        # marker, so a later reconciliation cannot train the same observation twice.
        if not row.get("legacy_calendar_backfill") and not row.get("learned_hourly"):
            st = bias.setdefault("buckets", {}).setdefault(fc._bucket_key(row.get("regime", "gradient"), hour),
                                                           postproc.new_state())
            postproc.update(st, row["raw_kn"], obs["mean_kn"])
            postproc.update_gust(st, row.get("raw_gust_kn") or row["raw_kn"], obs.get("gust_kn"))
            row["learned_hourly"] = True
            learned += 1
        changed += 1
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(x) for x in records) + "\n")
    if learned:
        path = fc.bias_path(lake); tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(bias, f, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    return changed, learned


def verification_rows(lake):
    """Frozen hourly forecast-of-record rows with an attached measurement."""
    path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
    if not os.path.exists(path):
        return []
    records = [json.loads(x) for x in open(path) if x.strip()]
    out = []
    seen = set()
    for rec in records:
        for row in rec.get("hourly", []):
            vt = row.get("valid_time")
            if not vt or vt in seen or row.get("measured_kn") is None:
                continue
            got = select_forecast_of_record(records, vt)
            if got and got[1] is row:
                seen.add(vt)
                out.append({"valid_time": vt, "lead_minutes": row.get("lead_minutes"),
                            "forecast_kn": row["mean_kn"], "measured_kn": row["measured_kn"],
                            "delta_kn": row["fc_minus_measured_kn"]})
    return sorted(out, key=lambda r: r["valid_time"])


def scorecard(lake):
    rows = verification_rows(lake)
    def summary(xs):
        if not xs: return {"n": 0, "mae": None, "bias": None, "crps": None}
        return {"n": len(xs), "mae": round(sum(abs(x["delta_kn"]) for x in xs) / len(xs), 2),
                "bias": round(sum(x["delta_kn"] for x in xs) / len(xs), 2),
                "crps": round(sum(verify.crps_ensemble([x["forecast_kn"]], x["measured_kn"]) for x in xs) / len(xs), 2)}
    bins = [(0,60,"0–1h"),(60,180,"1–3h"),(180,360,"3–6h"),(360,720,"6–12h"),(720,1441,"12–24h")]
    return {"overall": summary(rows), "by_lead": {name: summary([r for r in rows if lo <= (r.get("lead_minutes") or 0) < hi]) for lo,hi,name in bins}}


def write_scorecard(lake, issued):
    path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_verification.jsonl")
    rec = {"issued_time": issued.isoformat(timespec="minutes"), **scorecard(lake)}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def purge_test_history(before):
    """Remove explicitly identified pre-production hourly test records and rebuild bias.

    This migration is intentionally explicit: production code never guesses whether a
    record is synthetic. ``before`` is an aware issue timestamp supplied by the operator.
    """
    cutoff = datetime.datetime.fromisoformat(before)
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must include a timezone offset")
    result = {}
    for lake in fc.LAKES:
        path = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
        records = [json.loads(x) for x in open(path) if x.strip()] if os.path.exists(path) else []
        kept = [r for r in records if datetime.datetime.fromisoformat(r["issue_time"]) >= cutoff]
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write("\n".join(json.dumps(r) for r in kept) + ("\n" if kept else ""))
        # Rebuild from legacy daily history, then replay only retained as-issued hourly
        # observations. This removes test-row influence from the shared RLS state.
        bias = verify.rebuild_bias(lake, fc.params_for(lake))
        bpath, tmp = fc.bias_path(lake), fc.bias_path(lake) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(bias, f, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, bpath)
        for rec in kept:
            for row in rec.get("hourly", []):
                row.pop("learned_hourly", None)
        if kept:
            with open(path, "w") as f:
                f.write("\n".join(json.dumps(r) for r in kept) + "\n")
            reconcile_measurements(lake, now=datetime.datetime.now(wd.BERLIN))
        result[lake] = {"removed": len(records) - len(kept), "kept": len(kept)}
    return result


def fill_bootstrap_from_daily_log(lake):
    """Fill pre-05:00 rows from the real legacy calendar forecast log.

    This is only a migration bridge until the prior 23:55 hourly record exists. Rows
    retain ``legacy_calendar_backfill`` so they are never mistaken for hourly-issued
    forecasts in later verification.
    """
    hp = os.path.join(wd.LOG_DIR, f"{lake}_hourly_forecast.jsonl")
    dp = os.path.join(wd.LOG_DIR, f"{lake}_forecast.jsonl")
    if not (os.path.exists(hp) and os.path.exists(dp)):
        return 0
    records = [json.loads(x) for x in open(hp) if x.strip()]
    rec = records[-1]; day = rec["valid_start"][:10]
    candidates = [json.loads(x) for x in open(dp) if x.strip()]
    candidates = [x for x in candidates if x.get("date") == day and x.get("hourly")]
    if not candidates:
        return 0
    legacy = min(candidates, key=lambda x: x.get("run_stamp") or "")
    have = {r["valid_time"] for r in rec["hourly"]}
    added = 0
    for row in legacy["hourly"]:
        if row["hour"] >= 5:
            continue
        valid = f"{day}T{row['hour']:02d}:00:00+02:00"
        if valid not in have:
            rec["hourly"].append({**row, "valid_time": valid, "legacy_calendar_backfill": True})
            added += 1
    rec["hourly"].sort(key=lambda r: r["valid_time"])
    records[-1] = rec
    with open(hp, "w") as f:
        f.write("\n".join(json.dumps(x) for x in records) + "\n")
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="issue timestamp, ISO-8601; defaults to now in Berlin")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--purge-test-before", help="explicit aware issue-time cutoff for test-record purge")
    args = ap.parse_args()
    if args.selftest:
        a = datetime.datetime.fromisoformat("2026-08-12T04:55+02:00")
        b = datetime.datetime.fromisoformat("2026-08-12T05:00+02:00")
        assert next_hour(a).isoformat() == "2026-08-12T05:00:00+02:00"
        assert next_hour(b).isoformat() == "2026-08-12T05:00:00+02:00"
        late = datetime.datetime.fromisoformat("2026-08-12T06:46+02:00")
        assert next_hour(late).isoformat() == "2026-08-12T07:00:00+02:00"
        spring = datetime.datetime(2026, 3, 29, 1, 55, tzinfo=wd.BERLIN)
        assert next_hour(spring).isoformat() == "2026-03-29T03:00:00+02:00"
        autumn = datetime.datetime(2026, 10, 25, 1, 55, tzinfo=wd.BERLIN)
        assert next_hour(autumn).isoformat() == "2026-10-25T02:00:00+02:00"
        # The 00:00 row is supplied by 23:55, and a later 00:55 refresh must not
        # replace it. The 01:00 row correctly uses the 00:55 record.
        r0 = {"issue_time": "2026-08-11T23:55+02:00", "hourly": [
            {"valid_time": "2026-08-12T00:00:00+02:00", "mean_kn": 4.0, "raw_kn": 4.0, "raw_gust_kn": 5.0, "regime": "gradient"},
            {"valid_time": "2026-08-12T01:00:00+02:00", "mean_kn": 5.0, "raw_kn": 5.0, "raw_gust_kn": 6.0, "regime": "gradient"}]}
        r1 = {"issue_time": "2026-08-12T00:55+02:00", "hourly": [
            {"valid_time": "2026-08-12T01:00:00+02:00", "mean_kn": 6.0, "raw_kn": 6.0, "raw_gust_kn": 7.0, "regime": "gradient"}]}
        got0 = select_forecast_of_record([r0, r1], "2026-08-12T00:00:00+02:00")
        got1 = select_forecast_of_record([r0, r1], "2026-08-12T01:00:00+02:00")
        assert got0 and got0[1]["mean_kn"] == 4.0
        assert got1 and got1[1]["mean_kn"] == 6.0
        # A frozen measured row remains tied to the 23:55 record even after refreshes.
        r0["hourly"][0].update({"measured_kn": 5.0, "fc_minus_measured_kn": -1.0})
        # Inline equivalent of verification extraction: one eligible 00:00 row, no rewrite.
        assert select_forecast_of_record([r0, r1], "2026-08-12T00:00:00+02:00")[1]["fc_minus_measured_kn"] == -1.0
        # End-to-end reconciliation: only the selected record receives the observation,
        # and rerunning it does not learn or mutate the same row twice.
        tmp = tempfile.mkdtemp(); old_log, old_models = wd.LOG_DIR, fc.MODELS_DIR
        wd.LOG_DIR = tmp; fc.MODELS_DIR = tmp
        try:
            with open(os.path.join(tmp, "walchensee_hourly_forecast.jsonl"), "w") as f:
                clean0 = json.loads(json.dumps(r0)); clean1 = json.loads(json.dumps(r1))
                clean0["hourly"][0].pop("measured_kn", None); clean0["hourly"][0].pop("fc_minus_measured_kn", None)
                f.write(json.dumps(clean0) + "\n" + json.dumps(clean1) + "\n")
            fake = lambda _lake, _date: ({0: {"mean_kn": 5.0, "gust_kn": 6.0}}, "test")
            now2 = datetime.datetime.fromisoformat("2026-08-12T01:30+02:00")
            n1, l1 = reconcile_measurements("walchensee", now2, fake)
            n2, l2 = reconcile_measurements("walchensee", now2, fake)
            assert (n1, l1) == (1, 1) and (n2, l2) == (0, 0), ((n1,l1),(n2,l2))
        finally:
            wd.LOG_DIR, fc.MODELS_DIR = old_log, old_models
        print("hourly_run self-test: PASS anchor 04:55→05:00, 05:00→05:00, 06:46→07:00")
        return
    if args.purge_test_before:
        print(json.dumps(purge_test_history(args.purge_test_before), indent=2))
        return
    issued = (datetime.datetime.fromisoformat(args.at) if args.at else datetime.datetime.now(wd.BERLIN))
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=wd.BERLIN)
    # Reconcile observations from the prior issued window first. This is the normal
    # production path; it never changes an issued forecast, only attaches a measurement
    # and its frozen forecast-minus-measurement delta once data exists.
    if not args.dry_run:
        for lake in fc.LAKES:
            n, learned = reconcile_measurements(lake)
            sc = scorecard(lake)["overall"]
            write_scorecard(lake, issued)
            print(f"{lake}: reconciled {n} measured row(s); learned {learned}; "
                  f"hourly score n={sc['n']} MAE={sc['mae']} CRPS={sc['crps']}")
    # Build every lake before writing any of them: an API timeout must not leave a
    # half-issued cross-lake state. Short bounded retries handle transient TLS/API errors.
    built = {}
    for lake in fc.LAKES:
        last = None
        for attempt in range(3):
            try:
                built[lake] = build_window(lake, issued)
                break
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        if lake not in built:
            raise RuntimeError(f"{lake}: hourly issuance failed after 3 attempts: {last}")
    for lake, rec in built.items():
        if not args.dry_run:
            write(rec)
        print(f"{lake}: issued {rec['issue_time']} | {rec['valid_start']} → {rec['valid_end']} "
              f"| {len(rec['hourly'])} rows" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
