### Learning report — kochelsee — learned from 2026-08-01
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 6 hours · 18 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 18 | gradient |  3.0 |  3.0 |  4.8 | -1.8 | -1.8 |  SSW    S   9
 19 | gradient |  3.3 |  3.3 |  2.5 | +0.8 | +0.8 |   SW    N 124
 20 | calm     |  0.9 |  0.9 |  1.5 | -0.6 | -0.6 |  SSW    S  20
 21 | gradient |  3.0 |  3.0 |  1.0 | +2.0 | +2.0 |  SSW  SSW  12
 22 | calm     |  1.6 |  1.6 |  0.3 | +1.3 | +1.3 |    S    W 103
 23 | calm     |  0.6 |  0.6 |  0.4 | +0.2 | +0.2 |  SSE  WSW  81
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.12 kn** vs raw model 1.12 kn
- Mean signed error (bias): +0.32 kn (under-predicting)
- Direction mean abs error: 58°
- Gust ratio (measured/model): 1.16×
- By regime: calm +0.3 kn (3h); gradient +0.33 kn (3h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **3/6 hours (50%)**
- Confusion (predicted→measured): calm->calm ×3; gradient->calm ×1; gradient->foehn ×1; gradient->thermal ×1
- Mismatched hours: 18h gradient→foehn (S); 19h gradient→thermal (N); 21h gradient→calm (SSW)

**3. Lessons learned**
- Correction was roughly neutral (issued 1.12 kn vs raw 1.12 kn).
- Overall speed bias small (+0.32 kn mean error).
- Wind DIRECTION was off by 58° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 1.16× the model (stronger); gust ratio updated.
- Regime call was right 3/6 hours (50%) vs the measured wind direction.
- Worst hour was 21:00 (gradient): predicted 3.0 kn, measured 1 kn (Δ +2.0 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|18   |    +1.80  | +0.00 -> +0.94   | 1.00 -> 1.21    | 1
 gradient|19   |    -0.80  | +0.00 -> -0.39   | 1.00 -> 0.90    | 1
 calm|20       |    +0.60  | +0.00 -> +0.46   | 1.00 -> 1.03    | 1
 gradient|21   |    -2.00  | +0.00 -> -1.04   | 1.00 -> 0.77    | 1
 calm|22       |    -1.30  | +0.00 -> -0.91   | 1.00 -> 0.89    | 1
 calm|23       |    -0.20  | +0.00 -> -0.16   | 1.00 -> 0.99    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 6 calibrated buckets._
