### Learning report — walchensee — learned from 2026-08-04
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 14 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | foehn    |  4.1 |  4.1 |  4.3 | -0.2 | -0.2 |  SSE  ESE  51
 07 | foehn    |  4.0 |  4.0 |  3.5 | +0.5 | +0.5 |    S  ESE  52
 08 | foehn    |  2.7 |  3.1 |  2.3 | +0.4 | +0.8 |    S  SSE  17
 09 | foehn    |  2.2 |  2.3 |  1.8 | +0.4 | +0.5 |    S    S   3
 10 | thermal  |  1.4 |  1.4 |  0.8 | +0.6 | +0.6 |   NE   SE  90
 11 | calm     |  1.9 |  1.9 |  4.5 | -2.6 | -2.6 |  NNE  SSW 163
 12 | thermal  |  2.6 |  3.2 |  9.3 | -6.7 | -6.1 |    N   NE  27
 13 | thermal  |  3.7 |  4.5 |  9.2 | -5.5 | -4.7 |    N  NNE  18
 14 | thermal  |  5.9 |  5.4 |  8.4 | -2.5 | -3.0 |    N    N   0
 15 | thermal  |  6.5 |  5.3 |  8.7 | -2.2 | -3.4 |    N    N   5
 16 | gradient |  5.6 |  5.6 |  9.4 | -3.8 | -3.8 |  NNW    N  13
 17 | gradient |  5.4 |  5.4 |  7.8 | -2.4 | -2.4 |  NNW    N  16
 18 | gradient |  4.0 |  3.9 |  6.2 | -2.2 | -2.3 |  WNW  NNW  45
 19 | gradient |  7.1 |  4.0 |  4.2 | +2.9 | -0.2 |  SSW    N 144
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 12 | thermal  |  2.6 |  9.3 |  -6.7
 13 | thermal  |  3.7 |  9.2 |  -5.5
```
- **12:00 (thermal)** — under-predicted — forecast 2.6 kn vs measured 9.3 kn (-6.7 kn). *Lesson:* the model may underplay the 'thermal' regime around 12:00 — one day is weak evidence. *Fix:* the correction for (thermal×12h) is a regression corrected = -0.3 + 1.92·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).
- **13:00 (thermal)** — under-predicted — forecast 3.7 kn vs measured 9.2 kn (-5.5 kn). *Lesson:* the model may underplay the 'thermal' regime around 13:00 — one day is weak evidence. *Fix:* the correction for (thermal×13h) is a regression corrected = -1.9 + 1.89·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.35 kn** vs raw model 2.22 kn
- Mean signed error (bias): -1.66 kn (over-predicting)
- Direction mean abs error: 46°
- Gust ratio (measured/model): 1.1×
- By regime: calm -2.6 kn (1h); foehn +0.28 kn (4h); gradient -1.37 kn (4h); thermal -3.26 kn (5h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **5/13 hours (38%)**
- Confusion (predicted→measured): foehn->calm ×1; foehn->foehn ×1; foehn->gradient ×2; gradient->thermal ×4; thermal->calm ×1; thermal->thermal ×4
- Mismatched hours: 06h foehn→gradient (ESE); 07h foehn→gradient (ESE); 09h foehn→calm (S); 10h thermal→calm (SE); 16h gradient→thermal (N); 17h gradient→thermal (N); 18h gradient→thermal (NNW); 19h gradient→thermal (N)

**3. Lessons learned**
- Correction was roughly neutral (issued 2.35 kn vs raw 2.22 kn).
- Issued forecast OVER-predicted by 1.66 kn on average → biases nudged down.
- 'gradient' hours (4h) over-predicted by 1.37 kn → those regime buckets shifted most.
- 'thermal' hours (5h) over-predicted by 3.26 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 46° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Regime call was right 5/13 hours (38%) vs the measured wind direction.
- Regime miss: predicted 'foehn', measured 'gradient' (2×).
- Regime miss: predicted 'gradient', measured 'thermal' (4×).
- Worst hour was 12:00 (thermal): predicted 2.6 kn, measured 9.3 kn (Δ -6.7 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 foehn|06      |    +0.20  | +0.00 -> +0.08   | 1.00 -> 1.02    | 1
 foehn|07      |    -0.50  | -0.08 -> -0.15   | 0.97 -> 0.95    | 2
 foehn|08      |    -0.80  | -0.62 -> -0.47   | 0.83 -> 0.83    | 2
 foehn|09      |    -0.50  | -0.29 -> -0.31   | 0.93 -> 0.93    | 2
 thermal|10    |    -0.60  | +0.00 -> -0.43   | 1.00 -> 0.95    | 1
 calm|11       |    +2.60  | +0.00 -> +1.72   | 1.00 -> 1.24    | 1
 thermal|12    |    +6.10  | -1.03 -> -0.26   | 0.82 -> 1.92    | 2
 thermal|13    |    +4.70  | -1.28 -> -1.87   | 0.72 -> 1.89    | 2
 thermal|14    |    +3.00  | +0.55 -> +0.34   | 1.16 -> 1.38    | 2
 thermal|15    |    +3.40  | +1.37 -> +1.38   | 1.44 -> 1.41    | 2
 gradient|16   |    +3.80  | +0.00 -> +1.06   | 1.00 -> 1.44    | 1
 gradient|17   |    +2.40  | +0.00 -> +0.70   | 1.00 -> 1.28    | 1
 gradient|18   |    +2.30  | +0.07 -> +0.91   | 1.02 -> 1.06    | 2
 gradient|19   |    +0.20  | +4.42 -> +5.23   | 1.05 -> 0.28    | 3
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 19 calibrated buckets._
