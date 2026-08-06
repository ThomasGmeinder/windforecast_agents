### Learning report — walchensee — learned from 2026-08-05
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 14 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | gradient |  2.0 |  2.0 |  2.6 | -0.6 | -0.6 |   SE  WSW 119
 07 | gradient |  2.5 |  2.5 |  1.3 | +1.2 | +1.2 |    S  SSE   9
 08 | calm     |  1.8 |  1.8 |  1.3 | +0.5 | +0.5 |  SSW  SSW   3
 09 | thermal  |  1.4 |  1.4 |  0.1 | +1.3 | +1.3 |   SW   SW   1
 10 | thermal  |  2.1 |  2.3 |  0.3 | +1.8 | +2.0 |    N   NE  45
 11 | thermal  |  4.4 |  4.7 | 10.0 | -5.6 | -5.3 |  NNE   NE  25
 12 | thermal  |  8.9 |  5.6 | 10.0 | -1.1 | -4.4 |  NNE    N   7
 13 | thermal  |  8.3 |  6.0 |  9.5 | -1.2 | -3.5 |    N    N   3
 14 | thermal  |  8.0 |  6.2 |  9.2 | -1.2 | -3.0 |    N    N   4
 15 | thermal  |  8.0 |  5.6 |  7.8 | +0.2 | -2.2 |    N  NNE  18
 16 | gradient |  5.8 |  4.8 |  8.2 | -2.4 | -3.4 |  NNE  NNE   0
 17 | thermal  |  5.3 |  4.3 |  7.5 | -2.2 | -3.2 |  NNE  NNE   1
 18 | gradient |  5.0 |  4.2 |  5.3 | -0.3 | -1.1 |  NNE    N   8
 19 | gradient |  7.6 |  8.6 |  5.5 | +2.1 | +3.1 |  SSW  NNE 172
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 11 | thermal  |  4.4 | 10.0 |  -5.6
```
- **11:00 (thermal)** — under-predicted — forecast 4.4 kn vs measured 10 kn (-5.6 kn). *Lesson:* the model may underplay the 'thermal' regime around 11:00 — one day is weak evidence. *Fix:* the correction for (thermal×11h) is a regression corrected = -1.1 + 1.98·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **1.55 kn** vs raw model 2.49 kn
- Mean signed error (bias): -0.54 kn (over-predicting)
- Direction mean abs error: 30°
- Gust ratio (measured/model): 1.13×
- By regime: calm +0.5 kn (1h); gradient +0.0 kn (5h); thermal -1.0 kn (8h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **7/13 hours (54%)**
- Confusion (predicted→measured): calm->calm ×1; gradient->calm ×1; gradient->thermal ×3; thermal->calm ×2; thermal->thermal ×6
- Mismatched hours: 07h gradient→calm (SSE); 09h thermal→calm (SW); 10h thermal→calm (NE); 16h gradient→thermal (NNE); 18h gradient→thermal (N); 19h gradient→thermal (NNE)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 1.55 kn vs raw-model error 2.49 kn (−0.94 kn).
- Overall speed bias small (-0.54 kn mean error).
- 'thermal' hours (8h) over-predicted by 1.0 kn → those regime buckets shifted most.
- Regime call was right 7/13 hours (54%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (2×).
- Regime miss: predicted 'gradient', measured 'thermal' (3×).
- Worst hour was 11:00 (thermal): predicted 4.4 kn, measured 10 kn (Δ -5.6 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|06   |    +0.60  | +0.00 -> +0.39   | 1.00 -> 1.06    | 1
 gradient|07   |    -1.20  | +0.00 -> -0.70   | 1.00 -> 0.87    | 1
 calm|08       |    -0.50  | +0.00 -> -0.34   | 1.00 -> 0.95    | 1
 thermal|09    |    -1.30  | +0.00 -> -0.93   | 1.00 -> 0.90    | 1
 thermal|10    |    -2.00  | -0.43 -> -0.73   | 0.95 -> 0.74    | 2
 thermal|11    |    +5.30  | -0.40 -> -1.11   | 0.92 -> 1.98    | 2
 thermal|12    |    +4.40  | -0.26 -> -0.11   | 1.92 -> 1.83    | 3
 thermal|13    |    +3.50  | -1.87 -> -1.89   | 1.89 -> 1.89    | 3
 thermal|14    |    +3.00  | +0.34 -> +0.26   | 1.38 -> 1.42    | 3
 thermal|15    |    +2.20  | +1.38 -> +1.56   | 1.41 -> 1.27    | 3
 gradient|16   |    +3.40  | +1.06 -> +1.13   | 1.44 -> 1.45    | 2
 thermal|17    |    +3.20  | +1.19 -> +1.20   | 1.47 -> 1.47    | 2
 gradient|18   |    +1.10  | +0.91 -> +0.90   | 1.06 -> 1.05    | 3
 gradient|19   |    -3.10  | +5.23 -> +5.96   | 0.28 -> -0.01    | 4
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 23 calibrated buckets._
