#!/usr/bin/env python3
"""
analyst.py — Layer-2 LLM analyst for the self-learning loop, on the FREE Gemini API.

Each day it reads the learning evidence (yesterday's forecast-vs-measured errors +
current tunable params) and asks a free-tier Gemini Flash model for a STRUCTURED
diagnosis + a few bounded parameter proposals. The backtest gate (separate) decides
what to apply, so this stays advisory and low-stakes.

WHERE IT RUNS: Gemini is a cloud API called SERVER-SIDE — from the GitHub Actions
runner (or locally), NOT from the static GitHub Pages site (Pages runs no code and
would leak the key). The key lives in an Actions secret / a local 600-perm env file.

Design mirrors CarClaw's cloud_agent.py:
- key from env only (GEMINI_API_KEY); missing -> graceful skip, never crash
- single bounded timeout, no retry loop
- pinned model (GEMINI_MODEL, overridable); structured output via responseSchema
- injectable transport for tests; real path uses urllib (no extra dependency)
"""
import os, sys, json, ssl, socket, urllib.request, urllib.error

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # verified on the free tier
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANALYST_TIMEOUT_S = 30.0
_CA = "/etc/ssl/certs/ca-certificates.crt"

SYSTEM = (
    "You tune a deterministic wind-forecaster for two coupled Bavarian lakes "
    "(Kochelsee ~604 m, Walchensee ~800 m, joined by the Kesselberg). Each day you "
    "receive yesterday's per-hour forecast-vs-measured wind errors, the regime the "
    "forecaster assigned each hour (foehn / thermal / gradient / calm), and its "
    "current tunable parameters. Diagnose the main error patterns and their likely "
    "PHYSICAL cause — thermal onset timing, foehn breakthrough, cold-pool capping "
    "(Kochel-Walchensee Δθ), or terrain-channelled wind direction — then propose AT "
    "MOST 2 small numeric parameter changes that would reduce the error. Every "
    "proposal must name an existing parameter, give a concrete new value, and a "
    "one-line rationale. Be conservative: each change is backtested on held-out days "
    "before it is ever applied, so prefer small, well-justified steps."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "narrative": {"type": "string"},
        "diagnosis": {"type": "array", "items": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "cause": {"type": "string"}},
            "required": ["pattern", "cause"]}},
        "proposals": {"type": "array", "items": {"type": "object", "properties": {
            "param": {"type": "string"}, "proposed": {"type": "number"},
            "rationale": {"type": "string"}}, "required": ["param", "proposed", "rationale"]}},
    },
    "required": ["narrative", "proposals"],
}


def _gemini_transport(system, user, schema, timeout=ANALYST_TIMEOUT_S):
    """One bounded Gemini generateContent call with structured output. Returns the
    parsed JSON object. Reads GEMINI_API_KEY from the env. Raises on any failure."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    ctx = ssl.create_default_context(cafile=_CA) if os.path.exists(_CA) else ssl.create_default_context()
    url = _ENDPOINT.format(model=GEMINI_MODEL) + "?key=" + key
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "responseSchema": schema, "temperature": 0.2},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        d = json.loads(r.read())
    text = d["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def run_analysis(evidence, transport=_gemini_transport):
    """evidence: dict of the day's learning evidence. Returns the analyst result dict,
    or {'skipped': reason} on any failure — the deterministic EWMA loop has already run,
    so the analyst never blocks or crashes the pipeline."""
    if transport is _gemini_transport and not os.environ.get("GEMINI_API_KEY"):
        return {"skipped": "GEMINI_API_KEY not set"}
    user = "Learning evidence (JSON):\n" + json.dumps(evidence, indent=2, ensure_ascii=False)
    try:
        out = transport(SYSTEM, user, RESPONSE_SCHEMA)
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return {"skipped": f"gemini call failed: {e}"}
    except Exception as e:
        return {"skipped": f"analyst error: {type(e).__name__}: {e}"}
    if not isinstance(out, dict) or "proposals" not in out:
        return {"skipped": "malformed analyst output"}
    return out


def probe():
    """Liveness check: one tiny structured call. Returns (ok, detail)."""
    if not os.environ.get("GEMINI_API_KEY"):
        return (False, "GEMINI_API_KEY not set")
    try:
        out = _gemini_transport("Reply concisely.",
                                "Set narrative to 'ok' and proposals to an empty list.",
                                RESPONSE_SCHEMA, timeout=15)
        return (True, f"model={GEMINI_MODEL} narrative={out.get('narrative')!r}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:160]}")


if __name__ == "__main__":
    # synthetic evidence so the round-trip verifies even before real diffs exist
    ev = {"lake": "walchensee", "date": "2026-07-31",
          "current_params": {"COLD_POOL_DTHETA": 1.5, "FOEHN_DP_RIM": 4.0, "THERMAL_CLOUD_MAX": 45},
          "hourly_errors": [{"hour": h, "regime": "thermal", "forecast_kn": 4, "measured_kn": m,
                             "err_kn": 4 - m} for h, m in [(13, 11), (14, 10), (15, 9), (16, 8)]],
          "note": "thermal badly under-forecast 13-16h; cold pool appears to have cleared ~2h early"}
    print(json.dumps(run_analysis(ev), indent=2, ensure_ascii=False))
