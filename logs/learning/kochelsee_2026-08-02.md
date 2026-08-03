### Learning report — kochelsee — learned from 2026-08-02
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 1 hours · 20 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 23 | calm     |  1.5 |  1.5 |  3.6 | -2.1 | -2.1 |  ENE   SW 162
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **2.1 kn** vs raw model 2.1 kn
- Mean signed error (bias): -2.1 kn (over-predicting)
- Direction mean abs error: 162°
- Gust ratio (measured/model): 2.89×
- By regime: calm -2.1 kn (1h)

**3. Lessons learned**
- Correction was roughly neutral (issued 2.1 kn vs raw 2.1 kn).
- Issued forecast OVER-predicted by 2.1 kn on average → biases nudged down.
- Wind DIRECTION was off by 162° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 2.89× the model (stronger); gust ratio updated.
- Worst hour was 23:00 (calm): predicted 1.5 kn, measured 3.6 kn (Δ -2.1 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|23       |    +2.10  | -0.16 -> +0.55   | 0.99 -> 1.32    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 6 calibrated buckets._
