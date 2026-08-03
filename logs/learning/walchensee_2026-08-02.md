### Learning report — walchensee — learned from 2026-08-02
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 14 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | calm     |  1.1 |  1.1 |  2.5 | -1.4 | -1.4 |   SW   NE 180
 07 | calm     |  1.6 |  1.6 |  2.2 | -0.6 | -0.6 |  SSE   SE  27
 08 | calm     |  1.1 |  1.1 |  2.6 | -1.5 | -1.5 |  NNE   NE  27
 09 | calm     |  1.3 |  1.3 |  1.2 | +0.1 | +0.1 |   NW   NW   0
 10 | calm     |  2.0 |  2.0 |  6.4 | -4.4 | -4.4 |  NNE  NNE   8
 11 | thermal  |  4.3 |  4.3 | 12.6 | -8.3 | -8.3 |  NNE  NNE   1
 12 | thermal  |  7.7 |  6.0 | 13.3 | -5.6 | -7.3 |    N    N   5
 13 | thermal  |  6.5 |  6.5 | 13.6 | -7.1 | -7.1 |    N    N   6
 14 | thermal  |  6.6 |  6.6 | 13.4 | -6.8 | -6.8 |    N    N   8
 15 | thermal  |  7.0 |  7.0 | 11.8 | -4.8 | -4.8 |    N    N   7
 16 | thermal  |  7.1 |  6.6 | 11.1 | -4.0 | -4.5 |    N  NNW   5
 17 | gradient |  5.7 |  5.7 | 10.5 | -4.8 | -4.8 |  NNW  NNW   3
 19 | gradient |  5.0 |  2.4 |  1.8 | +3.2 | +0.6 |    S  SSE  14
 20 | calm     |  1.9 |  1.9 |  1.7 | +0.2 | +0.2 |   NW    W  65
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 11 | thermal  |  4.3 | 12.6 |  -8.3
 12 | thermal  |  7.7 | 13.3 |  -5.6
 13 | thermal  |  6.5 | 13.6 |  -7.1
 14 | thermal  |  6.6 | 13.4 |  -6.8
```
- **11:00 (thermal)** — under-predicted — forecast 4.3025 kn vs measured 12.6 kn (-8.3 kn). *Lesson:* the model may underplay the 'thermal' regime around 11:00 — one day is weak evidence. *Fix:* the correction for (thermal×11h) is a regression corrected = +3.2 + 2.02·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).
- **12:00 (thermal)** — under-predicted — forecast 7.7 kn vs measured 13.3 kn (-5.6 kn). *Lesson:* the model may underplay the 'thermal' regime around 12:00 — one day is weak evidence. *Fix:* the correction for (thermal×12h) is a regression corrected = +1.5 + 1.83·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).
- **13:00 (thermal)** — under-predicted — forecast 6.4525 kn vs measured 13.6 kn (-7.1 kn). *Lesson:* the model may underplay the 'thermal' regime around 13:00 — one day is weak evidence. *Fix:* the correction for (thermal×13h) is a regression corrected = +1.6 + 1.78·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).
- **14:00 (thermal)** — under-predicted — forecast 6.62375 kn vs measured 13.4 kn (-6.8 kn). *Lesson:* the model may underplay the 'thermal' regime around 14:00 — one day is weak evidence. *Fix:* the correction for (thermal×14h) is a regression corrected = +1.5 + 1.75·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **3.77 kn** vs raw model 3.74 kn
- Mean signed error (bias): -3.27 kn (over-predicting)
- Direction mean abs error: 25°
- Gust ratio (measured/model): 1.32×
- By regime: calm -1.27 kn (6h); gradient -0.8 kn (2h); thermal -6.1 kn (6h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **8/13 hours (62%)**
- Confusion (predicted→measured): calm->calm ×2; calm->foehn ×1; calm->thermal ×3; gradient->calm ×1; thermal->thermal ×6
- Mismatched hours: 06h calm→thermal (NE); 07h calm→foehn (SE); 08h calm→thermal (NE); 10h calm→thermal (NNE); 19h gradient→calm (SSE)

**3. Lessons learned**
- Correction was roughly neutral (issued 3.77 kn vs raw 3.74 kn).
- Issued forecast OVER-predicted by 3.27 kn on average → biases nudged down.
- 'calm' hours (6h) over-predicted by 1.27 kn → those regime buckets shifted most.
- 'thermal' hours (6h) over-predicted by 6.1 kn → those regime buckets shifted most.
- Measured gusts ran 1.32× the model (stronger); gust ratio updated.
- Regime call was right 8/13 hours (62%) vs the measured wind direction.
- Regime miss: predicted 'calm', measured 'thermal' (3×).
- Worst hour was 11:00 (thermal): predicted 4.3025 kn, measured 12.6 kn (Δ -8.3 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|06       |    +1.40  | +0.00 -> +1.05   | 1.00 -> 1.09    | 1
 calm|07       |    +0.60  | +0.00 -> +0.42   | 1.00 -> 1.05    | 1
 calm|08       |    +1.50  | +0.00 -> +1.12   | 1.00 -> 1.09    | 1
 calm|09       |    -0.10  | +0.00 -> -0.07   | 1.00 -> 0.99    | 1
 calm|10       |    +4.40  | +0.00 -> +2.85   | 1.00 -> 1.43    | 1
 thermal|11    |    +8.30  | +0.00 -> +3.15   | 1.00 -> 2.02    | 1
 thermal|12    |    +7.30  | +1.64 -> +1.45   | 1.60 -> 1.83    | 2
 thermal|13    |    +7.10  | +0.00 -> +1.61   | 1.00 -> 1.78    | 1
 thermal|14    |    +6.80  | +0.00 -> +1.51   | 1.00 -> 1.75    | 1
 thermal|15    |    +4.80  | +0.00 -> +0.98   | 1.00 -> 1.51    | 1
 thermal|16    |    +4.50  | +1.11 -> +1.16   | 1.03 -> 1.47    | 2
 gradient|17   |    +4.80  | +0.00 -> +1.30   | 1.00 -> 1.56    | 1
 gradient|19   |    -0.60  | +6.98 -> +3.53   | 2.26 -> 1.64    | 2
 calm|20       |    -0.20  | +0.00 -> -0.13   | 1.00 -> 0.98    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 24 calibrated buckets._
