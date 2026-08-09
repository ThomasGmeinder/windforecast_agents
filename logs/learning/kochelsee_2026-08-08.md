### Learning report — kochelsee — learned from 2026-08-08
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 12 hours · 6 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | calm     |  1.2 |  1.3 |  0.8 | +0.4 | +0.5 |  ENE    N  67
 07 | calm     |  3.4 |  1.0 |  0.9 | +2.5 | +0.1 |    N    N   2
 08 | calm     |  1.4 |  1.3 |  1.0 | +0.4 | +0.3 |    N  NNE  19
 09 | thermal  |  1.5 |  1.0 |  0.8 | +0.7 | +0.2 |  NNW    N  37
 10 | thermal  |  1.2 |  1.5 |  1.1 | +0.1 | +0.4 |  NNW   NE  55
 11 | thermal  |  4.6 |  2.1 |  2.0 | +2.6 | +0.1 |    N  NNE  31
 12 | thermal  |  2.6 |  2.7 |  1.9 | +0.7 | +0.8 |    N  NNE  15
 13 | thermal  |  3.8 |  3.8 |  1.8 | +2.0 | +2.0 |  NNE  NNE   4
 20 | gradient |  2.7 |  3.0 |  2.0 | +0.7 | +1.0 |    E  ENE  31
 21 | calm     |  1.4 |  1.9 |  0.4 | +1.0 | +1.5 |   SE  WSW 111
 22 | calm     |  0.2 |  1.6 |  0.0 | +0.2 | +1.6 |  ESE    W 148
 23 | calm     |  1.4 |  1.4 |  0.0 | +1.4 | +1.4 |    E  NNW 106
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.06 kn** vs raw model 0.83 kn
- Mean signed error (bias): +1.06 kn (under-predicting)
- Direction mean abs error: 52°
- Gust ratio (measured/model): 0.54×
- By regime: calm +0.98 kn (6h); gradient +0.7 kn (1h); thermal +1.22 kn (5h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **7/12 hours (57%)**
- Cross-table (scenario→flow sector): calm->calm ×6; gradient->thermal ×1; thermal->calm ×4; thermal->thermal ×1
- Mismatched hours: 09h thermal→calm (N); 10h thermal→calm (NE); 12h thermal→calm (NNE); 13h thermal→calm (NNE); 20h gradient→thermal (ENE)

**3. Lessons learned**
- The correction HURT yesterday: issued 1.06 kn vs raw 0.83 kn (+0.23 kn) — likely a regime shift vs the days it learned from.
- Issued forecast UNDER-predicted by 1.06 kn on average → biases nudged up.
- 'thermal' hours (5h) under-predicted by 1.22 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 52° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 0.54× the model (weaker); gust ratio updated.
- Regime call was right 7/12 hours (57%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (4×).
- Worst hour was 11:00 (thermal): predicted 4.6 kn, measured 2 kn (Δ +2.6 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|06       |    -0.50  | -0.07 -> -0.24   | 0.99 -> 0.98    | 2
 calm|07       |    -0.10  | +2.05 -> +1.21   | 1.34 -> 1.52    | 4
 calm|08       |    -0.30  | -0.21 -> -0.27   | 1.25 -> 1.24    | 6
 thermal|09    |    -0.20  | +0.13 -> -0.08   | 1.34 -> 1.38    | 4
 thermal|10    |    -0.40  | -0.88 -> -0.66   | 0.92 -> 0.96    | 2
 thermal|11    |    -0.10  | +4.08 -> +3.34   | 0.85 -> 0.59    | 3
 thermal|12    |    -0.80  | +1.94 -> +1.95   | 0.26 -> 0.19    | 5
 thermal|13    |    -2.00  | +0.00 -> -0.86   | 1.00 -> 0.76    | 1
 gradient|20   |    -1.00  | -1.30 -> -1.55   | 1.36 -> 1.38    | 5
 calm|21       |    -1.50  | -1.01 -> -1.09   | 0.86 -> 0.85    | 2
 calm|22       |    -1.60  | -1.17 -> -1.21   | 0.82 -> 0.82    | 4
 calm|23       |    -1.40  | -0.17 -> -0.35   | 1.11 -> 1.04    | 5
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 34 calibration buckets._
