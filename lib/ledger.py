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
import os, sys, json
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


def add(lake, param, from_val, proposed, expected_effect, issued_date, review_after_days=3):
    """Record a new open proposal. Idempotent per (lake, param, issued_date): re-adding
    the same key REPLACES the prior entry (a same-day re-run won't duplicate)."""
    entry = {"id": _entry_id(lake, param, issued_date), "lake": lake, "param": param,
             "from": from_val, "proposed": proposed, "expected_effect": expected_effect,
             "issued_date": issued_date, "review_after_days": review_after_days,
             "status": "open", "outcome": None, "resolved_date": None}
    entries = [e for e in _read() if e.get("id") != entry["id"]]
    entries.append(entry)
    _write(entries)
    return entry


def open_entries(lake=None):
    return [e for e in _read() if e.get("status") == "open"
            and (lake is None or e.get("lake") == lake)]


def resolve(entry_id, status, outcome, resolved_date):
    """Mark an entry 'confirmed' or 'retracted' with a measured outcome. No-op if absent."""
    entries = _read()
    for e in entries:
        if e.get("id") == entry_id:
            e.update(status=status, outcome=outcome, resolved_date=resolved_date)
            _write(entries)
            return e
    return None


def all_entries(lake=None):
    return [e for e in _read() if lake is None or e.get("lake") == lake]


if __name__ == "__main__":
    import tempfile
    LEDGER_PATH = os.path.join(tempfile.mkdtemp(), "ledger.jsonl")
    add("walchensee", "THERMAL_CLOUD_MAX", 45, 40, "fewer false thermals", "2026-08-01")
    add("walchensee", "COLD_POOL_DTHETA", 1.5, 1.2, "let thermal through earlier", "2026-08-01")
    assert len(open_entries("walchensee")) == 2
    # idempotent: re-adding the same (lake,param,date) replaces rather than duplicates
    add("walchensee", "THERMAL_CLOUD_MAX", 45, 42, "revised", "2026-08-01")
    ops = open_entries("walchensee")
    assert len(ops) == 2, len(ops)
    assert next(e for e in ops if e["param"] == "THERMAL_CLOUD_MAX")["proposed"] == 42
    # resolve moves it out of 'open' but keeps it in the record
    eid = _entry_id("walchensee", "THERMAL_CLOUD_MAX", "2026-08-01")
    resolve(eid, "confirmed", "CRPS −0.4 kn as expected", "2026-08-04")
    assert len(open_entries("walchensee")) == 1
    assert len(all_entries("walchensee")) == 2
    print("PASS: ledger add / open_entries / resolve + idempotency")
