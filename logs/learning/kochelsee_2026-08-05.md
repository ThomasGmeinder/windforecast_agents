### Learning report — kochelsee — learned from 2026-08-05
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 12 hours · 6 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | gradient |  2.5 |  2.5 |  0.4 | +2.1 | +2.1 |   SE    E  38
 07 | calm     |  4.8 |  1.7 |  0.6 | +4.2 | +1.1 |   SE    S  48
 08 | calm     |  1.3 |  0.6 |  0.3 | +1.0 | +0.3 |  SSW  SSW   4
 09 | thermal  |  1.7 |  1.1 |  0.8 | +0.9 | +0.3 |  NNW  SSW 135
 10 | thermal  |  1.3 |  1.3 |  0.1 | +1.2 | +1.2 |  NNE    N  25
 11 | thermal  |  4.7 |  2.1 |  0.5 | +4.2 | +1.6 |   NW   NE  75
 12 | thermal  |  3.9 |  2.7 |  1.0 | +2.9 | +1.7 |  NNW    N  14
 19 | gradient |  4.4 |  4.8 |  0.6 | +3.8 | +4.2 |  WNW    N  71
 20 | gradient |  9.8 |  7.4 |  6.7 | +3.1 | +0.7 |  SSE  SSE  17
 21 | gradient |  7.2 |  5.8 |  4.9 | +2.3 | +0.9 |    S  WSW  74
 22 | gradient |  4.4 |  4.2 |  4.2 | +0.2 | +0.0 |    S  SSW  24
 23 | gradient |  4.7 |  4.4 |  1.5 | +3.2 | +2.9 |    S  WSW  58
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **2.43 kn** vs raw model 1.42 kn
- Mean signed error (bias): +2.43 kn (under-predicting)
- Direction mean abs error: 49°
- Gust ratio (measured/model): 1.01×
- By regime: calm +2.6 kn (2h); gradient +2.45 kn (6h); thermal +2.3 kn (4h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **2/11 hours (18%)**
- Confusion (predicted→measured): calm->calm ×2; gradient->calm ×3; gradient->foehn ×2; thermal->calm ×4
- Mismatched hours: 06h gradient→calm (E); 09h thermal→calm (SSW); 10h thermal→calm (N); 11h thermal→calm (NE); 12h thermal→calm (N); 19h gradient→calm (N); 20h gradient→foehn (SSE); 22h gradient→foehn (SSW)

**3. Lessons learned**
- The correction HURT yesterday: issued 2.43 kn vs raw 1.42 kn (+1.01 kn) — likely a regime shift vs the days it learned from.
- Issued forecast UNDER-predicted by 2.43 kn on average → biases nudged up.
- 'calm' hours (2h) under-predicted by 2.6 kn → those regime buckets shifted most.
- 'gradient' hours (6h) under-predicted by 2.45 kn → those regime buckets shifted most.
- 'thermal' hours (4h) under-predicted by 2.3 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 49° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Regime call was right 2/11 hours (18%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'calm' (3×).
- Regime miss: predicted 'thermal', measured 'calm' (4×).
- Regime miss: predicted 'gradient', measured 'foehn' (2×).
- Worst hour was 07:00 (calm): predicted 4.8 kn, measured 0.6 kn (Δ +4.2 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|06   |    -2.10  | +0.00 -> -1.23   | 1.00 -> 0.77    | 1
 calm|07       |    -1.10  | +3.15 -> +2.05   | 1.85 -> 1.34    | 3
 calm|08       |    -0.30  | +1.00 -> +0.36   | 1.02 -> 1.21    | 3
 thermal|09    |    -0.30  | +0.58 -> +0.13   | 1.28 -> 1.34    | 3
 thermal|10    |    -1.20  | +0.00 -> -0.88   | 1.00 -> 0.92    | 1
 thermal|11    |    -1.60  | +7.05 -> +4.08   | 1.90 -> 0.85    | 2
 thermal|12    |    -1.70  | +1.93 -> +1.96   | 0.95 -> 0.34    | 3
 gradient|19   |    -4.20  | +0.31 -> +1.21   | 0.84 -> 0.18    | 3
 gradient|20   |    -0.70  | +2.03 -> +4.31   | 1.72 -> 0.63    | 2
 gradient|21   |    -0.90  | -1.30 -> -0.61   | 1.45 -> 1.14    | 4
 gradient|22   |    +0.00  | +0.28 -> +0.24   | 1.07 -> 1.01    | 2
 gradient|23   |    -2.90  | +0.56 -> +0.53   | 1.15 -> 0.63    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 21 calibrated buckets._
