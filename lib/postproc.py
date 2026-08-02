#!/usr/bin/env python3
"""
postproc.py — recursive-least-squares (Kalman) bias correction of the model wind.

Replaces the additive per-bucket constant with a per-(regime×hour) linear model
    corrected = a + b · raw
learned online by RLS with a forgetting factor. Because the correction is
`b·raw + a`, it SCALES with what the model already predicted — so it doesn't
double-count the föhn the model already put in `raw`, and it can't blindly add a
fixed offset on a day the model already got the strength right.

A Bayesian prior of (a=0, b=1) — i.e. "trust the model" — with tight variance on
b means: with little data the correction stays ≈ the raw model; as consistent
evidence accrues, a/b move. The forgetting factor gives it long-but-finite memory
(adapts to seasonal drift instead of averaging forever).

State per bucket (JSON-friendly): {n, a, b, P:[p11,p12,p22], gust_ratio, mae_kn}.
"""
PRIOR_A, PRIOR_B = 0.0, 1.0        # start at corrected = raw (trust the model)
PRIOR_VAR_A, PRIOR_VAR_B = 4.0, 0.30  # a may drift ~±2 kn early; b stays near 1 until evidence
FORGET = 0.98                       # RLS forgetting factor: long memory, still tracks drift


def new_state():
    return {"n": 0, "a": PRIOR_A, "b": PRIOR_B,
            "P": [PRIOR_VAR_A, 0.0, PRIOR_VAR_B], "gust_ratio": 1.0, "mae_kn": 0.0}


def update(st, raw, measured):
    """One RLS step with observation (x=[1,raw], y=measured). Mutates & returns st."""
    a, b = st["a"], st["b"]
    p11, p12, p22 = st["P"]
    # Px = P·x, x=[1, raw]
    px0 = p11 + p12 * raw
    px1 = p12 + p22 * raw
    s = FORGET + (px0 + raw * px1)              # λ + xᵀPx
    k0, k1 = px0 / s, px1 / s                   # gain
    e = measured - (a + b * raw)
    a += k0 * e
    b += k1 * e
    # P ← (P − k·(xᵀP)) / λ   (xᵀP = [px0, px1])
    p11 = (p11 - k0 * px0) / FORGET
    p12 = (p12 - k0 * px1) / FORGET
    p22 = (p22 - k1 * px1) / FORGET
    st["a"], st["b"], st["P"] = round(a, 3), round(b, 3), [round(p11, 4), round(p12, 4), round(p22, 4)]
    st["n"] += 1
    resid = abs(measured - (a + b * raw))
    st["mae_kn"] = round(resid if st["n"] == 1 else 0.7 * st["mae_kn"] + 0.3 * resid, 2)
    return st


def apply(st, raw, cap_kn):
    """corrected = a + b·raw, with the TOTAL adjustment clamped to ±cap_kn and ≥0."""
    corr = st.get("a", 0.0) + st.get("b", 1.0) * raw
    corr = max(raw - cap_kn, min(raw + cap_kn, corr))
    return max(0.0, corr)


if __name__ == "__main__":
    # self-test: feed varied raw + a known relationship, check (a,b) converge sensibly
    import itertools
    raws = list(itertools.islice(itertools.cycle([3, 6, 9, 12, 15, 5, 8, 11]), 40))
    for name, fn in [("measured = raw + 5 (additive)", lambda r: r + 5),
                     ("measured = 1.5*raw (multiplicative)", lambda r: 1.5 * r),
                     ("measured = raw (no bias)", lambda r: r),
                     ("measured = 0.7*raw + 2", lambda r: 0.7 * r + 2)]:
        st = new_state()
        for r in raws:
            update(st, r, fn(r))
        print(f"{name:38s} -> a={st['a']:+.2f} b={st['b']:.2f}  "
              f"(apply @raw=10 -> {apply(st,10,8):.1f})")
