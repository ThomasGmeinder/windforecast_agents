#!/usr/bin/env python3
"""
ledger.py — the hypothesis ledger: durable memory + accountability for the LLM tuner.

Every parameter proposal the analyst makes is recorded here with what it EXPECTED to
happen and a review date. On later runs the analyst reads its own OPEN entries (with the
MEASURED CRPS outcome since) and must confirm or retract each — so it learns from its own
track record instead of proposing into the void. This is the "memory" that turns the
single-shot advisory call into an agentic loop.

One entry per (lake, param, issued_date). File: logs/ledger.jsonl.
"""
import os, sys, json, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import winddata as wd

LEDGER_PATH = os.path.join(wd.LOG_DIR, "ledger.jsonl")


def _entry_id(lake, param, issued_date):
    return f"{lake}:{param}:{issued_date}"


def _read():
    out = []
    if os.path.exists(LEDGER_PATH):
        for line in open(LEDGER_PATH):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass  # skip a corrupt line rather than lose the whole ledger
    return out


def _write(entries):
    with open(LEDGER_PATH, "w") as f:
        f.write("\n".join(json.dumps(e) for e in entries) + ("\n" if entries else ""))


def add(lake, param, from_val, proposed, expected_effect, issued_date,
        review_after_days=3, applied=False, gate_reason=""):
    """Record a proposal together with WHAT THE GATE DID WITH IT.

    Only a proposal that was actually APPLIED becomes an open hypothesis: there is
    something in the world to observe, so the analyst can later be held to it. A refused
    proposal is stored as status 'not_applied' — grading it on subsequent CRPS would be
    grading the weather, since nothing changed. Idempotent per (lake, param,
    issued_date): re-adding the same key REPLACES the prior entry."""
    entry = {"id": _entry_id(lake, param, issued_date), "lake": lake, "param": param,
             "from": from_val, "proposed": proposed, "expected_effect": expected_effect,
             "issued_date": issued_date, "review_after_days": review_after_days,
             "applied": bool(applied), "gate_reason": gate_reason,
             # a change only starts influencing forecasts the day AFTER it is written
             "effective_date": (_next_day(issued_date) if applied else None),
             "status": ("open" if applied else "not_applied"),
             "outcome": None, "resolved_date": None}
    entries = [e for e in _read() if e.get("id") != entry["id"]]
    entries.append(entry)
    _write(entries)
    return entry


def _next_day(date):
    return (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()


def _due(entry, on_date):
    """Has this hypothesis waited its review period? (REVIEW_AFTER_DAYS was previously
    recorded but never enforced, so hypotheses were judged the same day they were issued.)"""
    if not on_date:
        return True
    try:
        eff = entry.get("effective_date") or entry["issued_date"]
        due = (datetime.date.fromisoformat(eff)
               + datetime.timedelta(days=int(entry.get("review_after_days", 3))))
        return datetime.date.fromisoformat(on_date) >= due
    except Exception:
        return True


def open_entries(lake=None, on_date=None):
    """Open (i.e. actually-applied, unresolved) hypotheses. With `on_date`, only those
    whose review period has elapsed."""
    return [e for e in _read() if e.get("status") == "open"
            and (lake is None or e.get("lake") == lake)
            and _due(e, on_date)]


def recent(lake=None, limit=12):
    """Most recent entries whatever their status — so the analyst can see what it already
    proposed (and why it was refused) without being graded on it."""
    es = [e for e in _read() if lake is None or e.get("lake") == lake]
    return es[-limit:]


def resolve(entry_id, status, outcome, resolved_date, lake=None):
    """Mark an entry 'confirmed' or 'retracted'. When `lake` is given the entry must
    belong to it — an id hallucinated by the model must never close another lake's
    hypothesis. Returns the entry, or None if nothing matched."""
    entries = _read()
    for e in entries:
        if e.get("id") == entry_id and (lake is None or e.get("lake") == lake):
            e.update(status=status, outcome=outcome, resolved_date=resolved_date)
            _write(entries)
            return e
    return None


def all_entries(lake=None):
    return [e for e in _read() if lake is None or e.get("lake") == lake]


if __name__ == "__main__":
    import tempfile
    LEDGER_PATH = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")

    # an APPLIED proposal becomes an open hypothesis; a REFUSED one does not
    add("walchensee", "THERMAL_CLOUD_MAX", 45, 40, "fewer false thermals", "2026-08-01",
        applied=True)
    add("walchensee", "COLD_POOL_DTHETA", 1.5, 1.2, "earlier thermal", "2026-08-01",
        applied=False, gate_reason="insufficient replayable history")
    assert len(open_entries("walchensee")) == 1, open_entries("walchensee")
    assert len(all_entries("walchensee")) == 2
    refused = [e for e in all_entries("walchensee") if e["param"] == "COLD_POOL_DTHETA"][0]
    assert refused["status"] == "not_applied" and refused["effective_date"] is None
    assert "insufficient" in refused["gate_reason"]
    print("PASS: only applied changes become open hypotheses; refused keep their reason")

    # idempotent per (lake, param, issued_date)
    add("walchensee", "THERMAL_CLOUD_MAX", 45, 42, "revised", "2026-08-01", applied=True)
    ops = open_entries("walchensee")
    assert len(ops) == 1 and ops[0]["proposed"] == 42, ops
    print("PASS: idempotent per (lake, param, issued_date)")

    # the review period is ENFORCED, not merely recorded
    eid = _entry_id("walchensee", "THERMAL_CLOUD_MAX", "2026-08-01")
    assert open_entries("walchensee", on_date="2026-08-02") == []      # too soon
    assert len(open_entries("walchensee", on_date="2026-08-06")) == 1  # due
    print("PASS: review_after_days enforced (not judged the day it was issued)")

    # a hallucinated / wrong-lake id must not close someone else's hypothesis
    assert resolve(eid, "confirmed", "x", "2026-08-06", lake="kochelsee") is None
    assert resolve("totally:made:up", "confirmed", "x", "2026-08-06", lake="walchensee") is None
    assert len(open_entries("walchensee")) == 1
    assert resolve(eid, "confirmed", "CRPS -0.4 kn as expected", "2026-08-06",
                   lake="walchensee") is not None
    assert open_entries("walchensee") == [] and len(all_entries("walchensee")) == 2
    print("PASS: resolve is lake-scoped and ignores unknown ids")
    print("ALL SELF-TESTS PASSED")
