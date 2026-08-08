### Learning report — walchensee — learned from 2026-08-07
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 12 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 08 | gradient |  3.2 |  3.2 |  9.7 | -6.5 | -6.5 |  NNE  NNE   1
 09 | gradient |  3.3 |  4.0 |  7.2 | -3.9 | -3.2 |  NNE  NNE   0
 10 | gradient |  3.9 |  4.4 |  1.4 | +2.5 | +3.0 |  NNE  NNE   5
 11 | gradient |  7.2 |  5.8 |  8.5 | -1.3 | -2.7 |  NNE    N  13
 12 | gradient |  7.4 |  6.2 | 10.8 | -3.4 | -4.6 |  NNE    N  14
 13 | gradient |  7.8 |  6.5 | 11.8 | -4.0 | -5.3 |    N    N   2
 14 | gradient |  7.3 |  5.9 | 11.2 | -3.9 | -5.3 |    N    N   5
 15 | gradient |  6.7 |  4.7 | 10.0 | -3.3 | -5.3 |    N    N   3
 16 | gradient |  9.1 |  4.7 |  8.2 | +0.9 | -3.5 |    N    N   1
 17 | gradient |  4.6 |  3.9 |  5.9 | -1.3 | -2.0 |    N  NNW  19
 18 | gradient |  5.4 |  4.2 |  3.7 | +1.7 | +0.5 |  NNW    N   4
 19 | gradient |  5.9 |  4.1 |  5.7 | +0.2 | -1.6 |    N    N   2
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 08 | gradient |  3.2 |  9.7 |  -6.5
```
- **08:00 (gradient)** — under-predicted — forecast 3.2 kn vs measured 9.7 kn (-6.5 kn). *Lesson:* the model may underplay the 'gradient' regime around 08:00 — one day is weak evidence. *Fix:* the correction for (gradient×08h) is a regression corrected = +3.2 + 1.77·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.74 kn** vs raw model 3.62 kn
- Mean signed error (bias): -1.86 kn (over-predicting)
- Direction mean abs error: 6°
- Gust ratio (measured/model): 1.37×
- By regime: gradient -1.86 kn (12h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **0/12 hours (0%)**
- Confusion (predicted→measured): gradient->calm ×1; gradient->thermal ×11
- Mismatched hours: 08h gradient→thermal (NNE); 09h gradient→thermal (NNE); 10h gradient→calm (NNE); 11h gradient→thermal (N); 12h gradient→thermal (N); 13h gradient→thermal (N); 14h gradient→thermal (N); 15h gradient→thermal (N)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 2.74 kn vs raw-model error 3.62 kn (−0.88 kn).
- Issued forecast OVER-predicted by 1.86 kn on average → biases nudged down.
- 'gradient' hours (12h) over-predicted by 1.86 kn → those regime buckets shifted most.
- Measured gusts ran 1.37× the model (stronger); gust ratio updated.
- Regime call was right 0/12 hours (0%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'thermal' (11×).
- Worst hour was 08:00 (gradient): predicted 3.2 kn, measured 9.7 kn (Δ -6.5 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|08   |    +6.50  | +0.00 -> +3.23   | 1.00 -> 1.77    | 1
 gradient|09   |    +3.20  | -1.17 -> -1.34   | 0.78 -> 1.65    | 2
 gradient|10   |    -3.00  | -0.60 -> -0.80   | 0.82 -> 0.68    | 2
 gradient|11   |    +2.70  | +1.25 -> +1.21   | 1.50 -> 1.38    | 2
 gradient|12   |    +4.60  | +0.97 -> +0.94   | 1.40 -> 1.50    | 2
 gradient|13   |    +5.30  | +1.05 -> +0.89   | 1.43 -> 1.58    | 2
 gradient|14   |    +5.30  | +1.28 -> +1.26   | 1.50 -> 1.60    | 2
 gradient|15   |    +5.30  | +2.39 -> +2.27   | 1.81 -> 1.74    | 2
 gradient|16   |    +3.50  | +2.77 -> +2.61   | 1.35 -> 1.34    | 4
 gradient|17   |    +2.00  | -0.73 -> -0.46   | 1.44 -> 1.45    | 3
 gradient|18   |    -0.50  | +0.90 -> +0.70   | 1.05 -> 1.01    | 4
 gradient|19   |    +1.60  | +5.96 -> +5.91   | -0.01 -> -0.01    | 5
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 33 calibrated buckets._
