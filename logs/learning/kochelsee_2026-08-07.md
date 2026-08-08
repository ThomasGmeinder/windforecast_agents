### Learning report — kochelsee — learned from 2026-08-07
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 16 hours · 8 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 08 | calm     |  1.0 |  0.8 |  0.4 | +0.6 | +0.4 |   NW   NW  11
 09 | calm     |  1.3 |  1.3 |  1.1 | +0.2 | +0.2 |  NNW    N  14
 10 | calm     |  3.5 |  1.8 |  1.2 | +2.3 | +0.6 |    N    N   4
 11 | gradient |  2.2 |  2.2 |  1.5 | +0.7 | +0.7 |  NNE    N   3
 12 | thermal  |  2.8 |  2.6 |  2.1 | +0.7 | +0.5 |    N    N   6
 13 | gradient |  2.3 |  2.8 |  2.9 | -0.6 | -0.1 |    N    N   6
 14 | gradient |  2.7 |  2.9 |  2.6 | +0.1 | +0.3 |    N    N   6
 15 | gradient |  3.3 |  3.3 |  2.1 | +1.2 | +1.2 |    N    N   6
 16 | gradient |  3.0 |  3.0 |  1.8 | +1.2 | +1.2 |    N    N   0
 17 | gradient |  3.1 |  3.1 |  1.1 | +2.0 | +2.0 |  NNE  NNW  30
 18 | gradient |  3.9 |  3.3 |  0.2 | +3.7 | +3.1 |  NNE    N  19
 19 | gradient |  1.5 |  3.4 |  0.1 | +1.4 | +3.3 |  NNE    N  22
 20 | gradient |  3.1 |  2.6 |  0.3 | +2.8 | +2.3 |   NE  NNE  16
 21 | calm     |  1.8 |  1.8 |  0.3 | +1.5 | +1.5 |    E    N 100
 22 | calm     |  0.7 |  1.6 |  0.0 | +0.7 | +1.6 |   SE    W 154
 23 | calm     |  1.7 |  1.3 |  0.0 | +1.7 | +1.3 |  ESE  WSW 136
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.34 kn** vs raw model 1.27 kn
- Mean signed error (bias): +1.26 kn (under-predicting)
- Direction mean abs error: 33°
- Gust ratio (measured/model): 0.62×
- By regime: calm +1.17 kn (6h); gradient +1.39 kn (9h); thermal +0.7 kn (1h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **7/16 hours (44%)**
- Confusion (predicted→measured): calm->calm ×6; gradient->calm ×6; gradient->thermal ×3; thermal->thermal ×1
- Mismatched hours: 11h gradient→calm (N); 13h gradient→thermal (N); 14h gradient→thermal (N); 15h gradient→thermal (N); 16h gradient→calm (N); 17h gradient→calm (NNW); 18h gradient→calm (N); 19h gradient→calm (N)

**3. Lessons learned**
- Correction was roughly neutral (issued 1.34 kn vs raw 1.27 kn).
- Issued forecast UNDER-predicted by 1.26 kn on average → biases nudged up.
- 'calm' hours (6h) under-predicted by 1.17 kn → those regime buckets shifted most.
- 'gradient' hours (9h) under-predicted by 1.39 kn → those regime buckets shifted most.
- Measured gusts ran 0.62× the model (weaker); gust ratio updated.
- Regime call was right 7/16 hours (44%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'calm' (6×).
- Regime miss: predicted 'gradient', measured 'thermal' (3×).
- Worst hour was 18:00 (gradient): predicted 3.9 kn, measured 0.2 kn (Δ +3.7 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|08       |    -0.40  | -0.07 -> -0.21   | 1.22 -> 1.25    | 5
 calm|09       |    -0.20  | +0.08 -> -0.02   | 1.00 -> 0.97    | 2
 calm|10       |    -0.60  | +1.29 -> +0.93   | 1.25 -> 1.12    | 4
 gradient|11   |    -0.70  | -0.17 -> -0.36   | 0.97 -> 0.96    | 2
 thermal|12    |    -0.50  | +1.96 -> +1.94   | 0.34 -> 0.26    | 4
 gradient|13   |    +0.10  | -0.95 -> -0.20   | 0.76 -> 0.77    | 2
 gradient|14   |    -0.30  | -0.27 -> -0.24   | 0.94 -> 0.95    | 2
 gradient|15   |    -1.20  | +0.00 -> -0.58   | 1.00 -> 0.86    | 1
 gradient|16   |    -1.20  | +0.00 -> -0.62   | 1.00 -> 0.86    | 1
 gradient|17   |    -2.00  | +0.00 -> -1.02   | 1.00 -> 0.76    | 1
 gradient|18   |    -3.10  | -0.26 -> -0.38   | 1.26 -> 0.99    | 4
 gradient|19   |    -3.30  | +0.44 -> +0.33   | 0.32 -> 0.26    | 5
 gradient|20   |    -2.30  | +0.11 -> -1.30   | 1.18 -> 1.36    | 4
 calm|21       |    -1.50  | +0.00 -> -1.01   | 1.00 -> 0.86    | 1
 calm|22       |    -1.60  | -1.10 -> -1.17   | 0.82 -> 0.82    | 3
 calm|23       |    -1.30  | +0.17 -> -0.17   | 1.19 -> 1.11    | 4
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 33 calibrated buckets._
