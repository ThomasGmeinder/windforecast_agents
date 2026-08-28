#!/usr/bin/env python3
"""Read-only evaluation of the persisted Memmingen short-lead shadow feature."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd
import shortlead


def report(path=None):
    path = path or os.path.join(wd.LOG_DIR, "ammersee_hourly_forecast.jsonl")
    records = [json.loads(x) for x in open(path) if x.strip()] if os.path.exists(path) else []
    bins = ((0, 60, "0–1h"), (60, 120, "1–2h"), (120, 180, "2–3h"))
    groups = {name: {"all": [], "west": [], "nonwest": [], "pairs": []} for _, _, name in bins}
    for rec in records:
        for row in rec.get("hourly", []):
            lead, y, fc = row.get("lead_minutes"), row.get("measured_kn"), row.get("mean_kn")
            if y is None or fc is None or lead is None or not 0 <= lead < 180:
                continue
            key = next((n for lo, hi, n in bins if lo <= lead < hi), None)
            if not key: continue
            err = fc - y; feature = row.get("memmingen_mean_kn")
            groups[key]["all"].append(err)
            # Historical rows predate this experiment. They belong in the baseline error
            # count, but never in either conditional subset.
            west = row.get("memmingen_west_flow")
            if west is not None:
                groups[key]["west" if west else "nonwest"].append(err)
            if feature is not None:
                groups[key]["pairs"].append((feature, y))
    def summary(xs):
        return {"n": len(xs), "mae_kn": round(sum(map(abs, xs))/len(xs), 2) if xs else None,
                "bias_kn": round(sum(xs)/len(xs), 2) if xs else None}
    # This is descriptive correlation, not a correction or significance claim.
    def correlation(pairs):
        if len(pairs) < 2: return None
        x, y = zip(*pairs); mx, my = sum(x)/len(x), sum(y)/len(y)
        den = (sum((v-mx)**2 for v in x) * sum((v-my)**2 for v in y)) ** .5
        return round(sum((a-mx)*(b-my) for a,b in pairs)/den, 3) if den else None
    n_pairs = sum(len(g["pairs"]) for g in groups.values())
    return {"scope": "read-only; 0–3h; Memmingen WSW–NW=225°–315°", "paired_rows": n_pairs,
            "by_lead": {k: {"existing_forecast_errors": summary(g["all"]),
                               "west_flow_errors": summary(g["west"]),
                               "nonwest_flow_errors": summary(g["nonwest"]),
                               "lagged_memmingen_to_ammersee_correlation": correlation(g["pairs"])}
                        for k,g in groups.items()},
            "incremental_skill": "not evaluated: no correction candidate exists",
            "gate": "No correction: require 10 replayable days, 60 pairs, and day-block-bootstrap significant incremental skill."}


if __name__ == "__main__":
    print(json.dumps(shortlead.radar_agreement_report() if "--radar-agreement" in sys.argv else report(), indent=2))
