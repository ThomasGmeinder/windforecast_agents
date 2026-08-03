### Learning report — kochelsee — learned from 2026-08-02
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 21 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 00 | calm     |  1.7 |  1.7 |  0.9 | +0.8 | +0.8 |   SE    S  41
 01 | calm     |  1.4 |  1.4 |  0.9 | +0.5 | +0.5 |  NNE    N  24
 02 | calm     |  1.4 |  1.4 |  0.4 | +1.0 | +1.0 |  ENE  NNE  34
 03 | calm     |  1.3 |  1.3 |  0.4 | +0.9 | +0.9 |  ENE  NNE  34
 04 | calm     |  1.0 |  1.0 |  0.1 | +0.9 | +0.9 |   NE   NE   0
 05 | calm     |  0.6 |  0.6 |  0.1 | +0.5 | +0.5 |   NE  NNE  23
 06 | calm     |  0.9 |  0.9 |  0.4 | +0.5 | +0.5 |   SE   SE   0
 07 | calm     |  1.1 |  1.1 |  1.3 | -0.2 | -0.2 |    N    N  10
 08 | calm     |  2.7 |  1.4 |  0.4 | +2.3 | +1.0 |    N    N  14
 09 | calm     |  1.4 |  1.4 |  0.6 | +0.8 | +0.8 |  NNW    N  21
 10 | thermal  |  1.4 |  1.8 |  1.0 | +0.4 | +0.8 |    N  NNE  23
 11 | calm     |  2.0 |  2.0 |  1.6 | +0.4 | +0.4 |    N   NE  30
 12 | gradient |  3.0 |  3.2 |  2.2 | +0.8 | +1.0 |    N    N   6
 13 | thermal  |  3.9 |  3.9 |  1.8 | +2.1 | +2.1 |    N  NNE   8
 17 | gradient |  3.5 |  4.5 | 15.4 | -11.9 | -10.9 |  NNE  NNE   1
 18 | gradient | 11.7 | 10.6 |  6.4 | +5.3 | +4.2 |    S    S   4
 19 | gradient |  7.6 |  8.0 |  4.2 | +3.4 | +3.8 |    S    S   3
 20 | gradient |  6.9 |  6.9 |  2.5 | +4.4 | +4.4 |    S    S  10
 21 | gradient |  2.4 |  2.9 |  6.5 | -4.1 | -3.6 |   SW    S  48
 22 | gradient |  2.3 |  2.3 |  2.3 | +0.0 | +0.0 |    W    S  84
 23 | calm     |  1.5 |  1.5 |  3.6 | -2.1 | -2.1 |  ENE   SW 162
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 17 | gradient |  3.5 | 15.4 | -11.9
 18 | gradient | 11.7 |  6.4 |  +5.3
```
- **17:00 (gradient)** — under-predicted — forecast 3.5 kn vs measured 15.4 kn (-11.9 kn). *Lesson:* the model may underplay the 'gradient' regime around 17:00 — one day is weak evidence. *Fix:* the correction for (gradient×17h) is a regression corrected = +3.2 + 1.04·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).
- **18:00 (gradient)** — over-predicted — forecast 11.7 kn vs measured 6.4 kn (+5.3 kn). *Lesson:* the model may overplay the 'gradient' regime around 18:00 — one day is weak evidence. *Fix:* the correction for (gradient×18h) is a regression corrected = +2.5 + 0.42·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (2 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.06 kn** vs raw model 1.92 kn
- Mean signed error (bias): +0.32 kn (under-predicting)
- Direction mean abs error: 28°
- Gust ratio (measured/model): 1.2×
- By regime: calm +0.52 kn (12h); gradient -0.3 kn (7h); thermal +1.25 kn (2h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **11/20 hours (55%)**
- Confusion (predicted→measured): calm->calm ×11; gradient->foehn ×5; gradient->thermal ×2; thermal->calm ×2
- Mismatched hours: 10h thermal→calm (NNE); 12h gradient→thermal (N); 13h thermal→calm (NNE); 17h gradient→thermal (NNE); 18h gradient→foehn (S); 19h gradient→foehn (S); 20h gradient→foehn (S); 21h gradient→foehn (S)

**3. Lessons learned**
- Correction was roughly neutral (issued 2.06 kn vs raw 1.92 kn).
- Overall speed bias small (+0.32 kn mean error).
- 'thermal' hours (2h) under-predicted by 1.25 kn → those regime buckets shifted most.
- Measured gusts ran 1.2× the model (stronger); gust ratio updated.
- Regime call was right 11/20 hours (55%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (2×).
- Regime miss: predicted 'gradient', measured 'thermal' (2×).
- Regime miss: predicted 'gradient', measured 'foehn' (5×).
- Worst hour was 17:00 (gradient): predicted 3.5 kn, measured 15.4 kn (Δ -11.9 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|00       |    -0.80  | +0.00 -> -0.55   | 1.00 -> 0.93    | 1
 calm|01       |    -0.50  | +0.00 -> -0.36   | 1.00 -> 0.96    | 1
 calm|02       |    -1.00  | +0.00 -> -0.72   | 1.00 -> 0.93    | 1
 calm|03       |    -0.90  | +0.00 -> -0.66   | 1.00 -> 0.94    | 1
 calm|04       |    -0.90  | +0.00 -> -0.68   | 1.00 -> 0.95    | 1
 calm|05       |    -0.50  | +0.00 -> -0.39   | 1.00 -> 0.98    | 1
 calm|06       |    -0.50  | +0.00 -> -0.38   | 1.00 -> 0.97    | 1
 calm|07       |    +0.20  | +0.00 -> +0.09   | 1.00 -> 1.00    | 2
 calm|08       |    -1.00  | +3.40 -> +1.82   | 1.20 -> 0.67    | 2
 calm|09       |    -0.80  | +0.00 -> -0.57   | 1.00 -> 0.94    | 1
 thermal|10    |    -0.80  | -0.82 -> -0.57   | 0.80 -> 0.76    | 2
 calm|11       |    -0.40  | +0.00 -> -0.26   | 1.00 -> 0.96    | 1
 gradient|12   |    -1.00  | -0.43 -> -0.46   | 0.93 -> 0.88    | 2
 thermal|13    |    -2.10  | +0.00 -> -0.88   | 1.00 -> 0.74    | 1
 gradient|17   |   +10.90  | -1.09 -> +3.23   | 0.59 -> 1.04    | 2
 gradient|18   |    -4.20  | +0.94 -> +2.46   | 1.21 -> 0.42    | 2
 gradient|19   |    -3.80  | -0.39 -> +0.29   | 0.90 -> 0.53    | 2
 gradient|20   |    -4.40  | +0.00 -> -0.91   | 1.00 -> 0.53    | 1
 gradient|21   |    +3.60  | -1.04 -> +0.61   | 0.77 -> 1.05    | 2
 gradient|22   |    +0.00  | +0.00 -> +0.00   | 1.00 -> 1.00    | 1
 calm|23       |    +2.10  | -0.16 -> +0.55   | 0.99 -> 1.32    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 36 calibrated buckets._
