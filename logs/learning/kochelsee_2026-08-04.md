### Learning report — kochelsee — learned from 2026-08-04
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 11 hours · 6 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | calm     |  1.3 |  1.3 |  1.2 | +0.1 | +0.1 |  SSE   SW  51
 07 | calm     |  1.8 |  1.6 |  9.9 | -8.1 | -8.3 |    N  NNW  15
 08 | calm     |  1.5 |  1.5 |  3.5 | -2.0 | -2.0 |  NNW   SW 106
 09 | thermal  |  1.8 |  1.2 |  1.2 | +0.6 | +0.0 |    W  NNE 105
 10 | calm     |  3.5 |  1.3 |  0.4 | +3.1 | +0.9 |  SSE    S   2
 11 | calm     |  0.8 |  0.8 |  1.3 | -0.5 | -0.5 |   NW   NW   6
 12 | thermal  |  3.1 |  1.9 |  2.1 | +1.0 | -0.2 |   NW   NW   1
 20 | gradient |  4.7 |  4.7 | 10.6 | -5.9 | -5.9 |    S  SSW  26
 21 | gradient |  4.2 |  4.9 |  7.1 | -2.9 | -2.2 |  SSE    S  18
 22 | gradient |  3.5 |  3.5 |  4.1 | -0.6 | -0.6 |  SSE  SSW  32
 23 | gradient |  3.5 |  3.5 |  4.7 | -1.2 | -1.2 |  ESE  SSW  81
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 07 | calm     |  1.8 |  9.9 |  -8.1
 20 | gradient |  4.7 | 10.6 |  -5.9
```
- **07:00 (calm)** — under-predicted — forecast 1.8 kn vs measured 9.9 kn (-8.1 kn). *Lesson:* the model may underplay the 'calm' regime around 07:00 — one day is weak evidence. *Fix:* the correction for (calm×07h) is a regression corrected = +3.2 + 1.85·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).
- **20:00 (gradient)** — under-predicted — forecast 4.67375 kn vs measured 10.6 kn (-5.9 kn). *Lesson:* the model may underplay the 'gradient' regime around 20:00 — one day is weak evidence. *Fix:* the correction for (gradient×20h) is a regression corrected = +2.0 + 1.72·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.36 kn** vs raw model 1.99 kn
- Mean signed error (bias): -1.49 kn (over-predicting)
- Direction mean abs error: 40°
- Gust ratio (measured/model): 2.14×
- By regime: calm -1.48 kn (5h); gradient -2.65 kn (4h); thermal +0.8 kn (2h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **3/9 hours (33%)**
- Confusion (predicted→measured): calm->calm ×3; gradient->foehn ×4; thermal->calm ×1; thermal->gradient ×1
- Mismatched hours: 09h thermal→calm (NNE); 12h thermal→gradient (NW); 20h gradient→foehn (SSW); 21h gradient→foehn (S); 22h gradient→foehn (SSW); 23h gradient→foehn (SSW)

**3. Lessons learned**
- The correction HURT yesterday: issued 2.36 kn vs raw 1.99 kn (+0.37 kn) — likely a regime shift vs the days it learned from.
- Issued forecast OVER-predicted by 1.49 kn on average → biases nudged down.
- 'calm' hours (5h) over-predicted by 1.48 kn → those regime buckets shifted most.
- 'gradient' hours (4h) over-predicted by 2.65 kn → those regime buckets shifted most.
- Measured gusts ran 2.14× the model (stronger); gust ratio updated.
- Regime call was right 3/9 hours (33%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (4×).
- Worst hour was 07:00 (calm): predicted 1.8 kn, measured 9.9 kn (Δ -8.1 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|06       |    -0.10  | +0.00 -> -0.07   | 1.00 -> 0.99    | 1
 calm|07       |    +8.30  | +0.75 -> +3.15   | 1.06 -> 1.85    | 2
 calm|08       |    +2.00  | +0.20 -> +1.00   | 1.03 -> 1.02    | 2
 thermal|09    |    +0.00  | +1.45 -> +0.58   | 1.21 -> 1.28    | 2
 calm|10       |    -0.90  | +5.75 -> +2.23   | 1.73 -> 1.81    | 2
 calm|11       |    +0.50  | +0.00 -> +0.39   | 1.00 -> 1.02    | 1
 thermal|12    |    +0.20  | +2.87 -> +1.93   | 1.30 -> 0.95    | 2
 gradient|20   |    +5.90  | +0.00 -> +2.03   | 1.00 -> 1.72    | 1
 gradient|21   |    +2.20  | -0.31 -> -1.30   | 0.83 -> 1.45    | 3
 gradient|22   |    +0.60  | +0.00 -> +0.28   | 1.00 -> 1.07    | 1
 gradient|23   |    +1.20  | +0.00 -> +0.56   | 1.00 -> 1.15    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 19 calibrated buckets._
