### Learning report — kochelsee — learned from 2026-08-06
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 16 hours · 8 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 08 | calm     |  1.8 |  1.2 |  0.1 | +1.7 | +1.1 |   SE  ENE  71
 09 | calm     |  0.7 |  0.7 |  0.8 | -0.1 | -0.1 |    W   NE 135
 10 | calm     |  4.4 |  1.9 |  0.0 | +4.4 | +1.9 |   NW   NW   6
 11 | gradient |  2.6 |  2.6 |  2.3 | +0.3 | +0.3 |    N  WNW  54
 12 | gradient |  2.6 |  2.6 |  1.3 | +1.3 | +1.3 |    N  WNW  68
 13 | gradient |  3.4 |  3.4 |  1.4 | +2.0 | +2.0 |   NW  NNW   6
 14 | gradient |  2.8 |  2.8 |  2.3 | +0.5 | +0.5 |    W  ENE 160
 15 | calm     |  1.6 |  1.6 |  3.0 | -1.4 | -1.4 |  SSE  NNW 171
 16 | calm     |  1.4 |  1.4 |  3.5 | -2.1 | -2.1 |    W  WNW  41
 17 | calm     |  1.5 |  1.5 |  1.0 | +0.5 | +0.5 |    W   SW  33
 18 | gradient |  2.7 |  2.1 |  1.2 | +1.5 | +0.9 |    S   SE  36
 19 | gradient |  1.6 |  2.0 |  0.0 | +1.6 | +2.0 |  SSE  SSE   3
 20 | gradient |  4.7 |  2.4 |  0.3 | +4.4 | +2.1 |  SSE    S  30
 21 | gradient |  2.8 |  3.0 |  0.1 | +2.7 | +2.9 |  SSE    S  28
 22 | calm     |  1.6 |  2.0 |  0.2 | +1.4 | +1.8 |  SSE    S  19
 23 | calm     |  2.0 |  1.4 |  0.7 | +1.3 | +0.7 |  SSE    S   9
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.7 kn** vs raw model 1.35 kn
- Mean signed error (bias): +1.25 kn (under-predicting)
- Direction mean abs error: 54°
- Gust ratio (measured/model): 0.87×
- By regime: calm +0.71 kn (8h); gradient +1.79 kn (8h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **7/16 hours (44%)**
- Confusion (predicted→measured): calm->calm ×6; calm->gradient ×1; calm->thermal ×1; gradient->calm ×6; gradient->gradient ×1; gradient->thermal ×1
- Mismatched hours: 12h gradient→calm (WNW); 13h gradient→calm (NNW); 14h gradient→thermal (ENE); 15h calm→thermal (NNW); 16h calm→gradient (WNW); 18h gradient→calm (SE); 19h gradient→calm (SSE); 20h gradient→calm (S)

**3. Lessons learned**
- The correction HURT yesterday: issued 1.7 kn vs raw 1.35 kn (+0.35 kn) — likely a regime shift vs the days it learned from.
- Issued forecast UNDER-predicted by 1.25 kn on average → biases nudged up.
- 'gradient' hours (8h) under-predicted by 1.79 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 54° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Regime call was right 7/16 hours (44%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'calm' (6×).
- Worst hour was 10:00 (calm): predicted 4.4 kn, measured 0 kn (Δ +4.4 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|08       |    -1.10  | +0.36 -> -0.07   | 1.21 -> 1.22    | 4
 calm|09       |    +0.10  | +0.00 -> +0.08   | 1.00 -> 1.00    | 1
 calm|10       |    -1.90  | +2.23 -> +1.29   | 1.81 -> 1.25    | 3
 gradient|11   |    -0.30  | +0.00 -> -0.17   | 1.00 -> 0.97    | 1
 gradient|12   |    -1.30  | +0.00 -> -0.74   | 1.00 -> 0.85    | 1
 gradient|13   |    -2.00  | +0.00 -> -0.95   | 1.00 -> 0.76    | 1
 gradient|14   |    -0.50  | +0.00 -> -0.27   | 1.00 -> 0.94    | 1
 calm|15       |    +1.40  | +0.00 -> +0.97   | 1.00 -> 1.12    | 1
 calm|16       |    +2.10  | +0.00 -> +1.51   | 1.00 -> 1.16    | 1
 calm|17       |    -0.50  | +0.00 -> -0.35   | 1.00 -> 0.96    | 1
 gradient|18   |    -0.90  | +0.81 -> -0.26   | 1.08 -> 1.26    | 3
 gradient|19   |    -2.00  | +1.21 -> +0.44   | 0.18 -> 0.32    | 4
 gradient|20   |    -2.10  | +4.31 -> +0.11   | 0.63 -> 1.18    | 3
 gradient|21   |    -2.90  | -0.61 -> -1.67   | 1.14 -> 1.28    | 5
 calm|22       |    -1.80  | -0.91 -> -1.10   | 0.89 -> 0.82    | 2
 calm|23       |    -0.70  | +0.55 -> +0.17   | 1.32 -> 1.19    | 3
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 29 calibrated buckets._
