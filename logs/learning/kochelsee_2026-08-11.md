### Learning report — kochelsee — learned from 2026-08-11
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 11 hours · 7 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | calm     |  1.7 |  0.8 |  0.3 | +1.4 | +0.5 |  SSE   SW  63
 08 | calm     |  0.3 |  0.6 |  1.1 | -0.8 | -0.5 |  ESE    W 162
 09 | calm     |  1.5 |  1.9 |  0.3 | +1.2 | +1.6 |    N    N   8
 10 | calm     |  2.8 |  1.9 |  1.0 | +1.8 | +0.9 |    N    N   2
 11 | thermal  |  3.5 |  1.7 |  1.7 | +1.8 | +0.0 |    N    N  11
 12 | thermal  |  1.6 |  1.3 |  1.8 | -0.2 | -0.5 |  NNW  NNW   9
 13 | thermal  |  1.5 |  1.9 |  1.6 | -0.1 | +0.3 |    N   NW  46
 20 | gradient |  2.8 |  3.1 |  0.0 | +2.8 | +3.1 |   NE   NE   6
 21 | calm     |  1.0 |  2.0 |  0.0 | +1.0 | +2.0 |  ENE  ENE  14
 22 | calm     |  0.0 |  0.6 |  0.0 | +0.0 | +0.6 |    N  NNW  31
 23 | calm     |  0.5 |  0.8 |  0.0 | +0.5 | +0.8 |    S  WNW 105
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.05 kn** vs raw model 0.98 kn
- Mean signed error (bias): +0.85 kn (under-predicting)
- Direction mean abs error: 42°
- Gust ratio (measured/model): 0.54×
- By regime: calm +0.73 kn (7h); gradient +2.8 kn (1h); thermal +0.5 kn (3h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **7/11 hours (64%)**
- Cross-table (scenario→flow sector): calm->calm ×7; gradient->calm ×1; thermal->calm ×3
- Mismatched hours: 11h thermal→calm (N); 12h thermal→calm (NNW); 13h thermal→calm (NW); 20h gradient→calm (NE)

**3. Lessons learned**
- Correction was roughly neutral (issued 1.05 kn vs raw 0.98 kn).
- Overall speed bias small (+0.85 kn mean error).
- Measured gusts ran 0.54× the model (weaker); gust ratio updated.
- Regime call was right 7/11 hours (64%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (3×).
- Worst hour was 20:00 (gradient): predicted 2.8 kn, measured 0 kn (Δ +2.8 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|07       |    -0.50  | +0.22 -> -0.07   | 1.40 -> 1.51    | 7
 calm|08       |    +0.50  | -0.45 -> -0.26   | 1.27 -> 1.19    | 8
 calm|09       |    -1.60  | -0.29 -> -0.34   | 0.97 -> 0.74    | 4
 calm|10       |    -0.90  | -0.23 -> -0.26   | 1.12 -> 1.04    | 6
 thermal|11    |    +0.00  | +0.32 -> +0.22   | 1.16 -> 1.15    | 5
 thermal|12    |    +0.50  | +1.00 -> +1.06   | 0.48 -> 0.46    | 7
 thermal|13    |    -0.30  | -0.86 -> -0.15   | 0.76 -> 0.66    | 2
 gradient|20   |    -3.10  | -2.04 -> -2.50   | 1.54 -> 1.57    | 8
 calm|21       |    -2.00  | -1.09 -> -1.22   | 0.85 -> 0.81    | 3
 calm|22       |    -0.60  | -1.22 -> -0.88   | 0.85 -> 0.76    | 3
 calm|23       |    -0.80  | -0.41 -> -0.56   | 1.06 -> 1.11    | 5
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±4 kn guard limits over-correction but cannot prove a physical cause. 37 calibration buckets._
