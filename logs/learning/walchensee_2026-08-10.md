### Learning report — walchensee — learned from 2026-08-10
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 12 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 08 | gradient |  6.2 |  3.5 |  3.7 | +2.5 | -0.2 |  SSW  SSW  17
 09 | gradient |  3.3 |  2.9 |  1.7 | +1.6 | +1.2 |  SSW    S  19
 10 | gradient |  1.2 |  2.2 |  0.4 | +0.8 | +1.8 |  SSW  SSW  10
 11 | calm     |  2.6 |  1.9 |  1.3 | +1.3 | +0.6 |   SW    S  63
 12 | gradient |  4.6 |  2.7 |  6.6 | -2.0 | -3.9 |   NW  SSE 144
 13 | gradient |  7.4 |  4.2 |  5.2 | +2.2 | -1.0 |    N    N   4
 14 | thermal  |  6.5 |  4.1 |  4.4 | +2.1 | -0.3 |  WNW    N  72
 15 | gradient | 10.6 |  5.6 |  3.8 | +6.8 | +1.8 |  NNW  NNE  49
 16 | gradient | 10.8 |  6.1 |  5.5 | +5.3 | +0.6 |  NNW  NNE  54
 17 | gradient |  9.0 |  6.5 |  4.1 | +4.9 | +2.4 |  NNW  NNE  43
 18 | gradient |  6.2 |  5.5 |  5.3 | +0.9 | +0.2 |   NW   NW   1
 19 | gradient |  5.9 |  3.6 |  3.2 | +2.7 | +0.4 |  WNW    W  24
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 15 | gradient | 10.6 |  3.8 |  +6.8
 16 | gradient | 10.8 |  5.5 |  +5.3
```
- **15:00 (gradient)** — over-predicted — forecast 10.6 kn vs measured 3.8 kn (+6.8 kn). *Lesson:* the model may overplay the 'gradient' scenario around 15:00 — one day is weak evidence. *Fix:* the correction for (gradient×15h) is a regression corrected = +3.9 + 0.79·model — it **scales with** the model's own wind (rather than adding a fixed scenario bonus), refined recursively over days (4 obs; full weight after 3).
- **16:00 (gradient)** — over-predicted — forecast 10.8 kn vs measured 5.5 kn (+5.3 kn). *Lesson:* the model may overplay the 'gradient' scenario around 16:00 — one day is weak evidence. *Fix:* the correction for (gradient×16h) is a regression corrected = +4.5 + 0.74·model — it **scales with** the model's own wind (rather than adding a fixed scenario bonus), refined recursively over days (5 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.76 kn** vs raw model 1.2 kn
- Mean signed error (bias): +2.43 kn (under-predicting)
- Direction mean abs error: 42°
- Gust ratio (measured/model): 0.85×
- By regime: calm +1.3 kn (1h); gradient +2.57 kn (10h); thermal +2.1 kn (1h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **4/11 hours (36%)**
- Cross-table (scenario→flow sector): calm->calm ×1; gradient->calm ×2; gradient->foehn ×1; gradient->gradient ×2; gradient->thermal ×4; thermal->thermal ×1
- Mismatched hours: 09h gradient→calm (S); 10h gradient→calm (SSW); 12h gradient→foehn (SSE); 13h gradient→thermal (N); 15h gradient→thermal (NNE); 16h gradient→thermal (NNE); 17h gradient→thermal (NNE)

**3. Lessons learned**
- The correction HURT yesterday: issued 2.76 kn vs raw 1.2 kn (+1.56 kn) — likely a regime shift vs the days it learned from.
- Issued forecast UNDER-predicted by 2.43 kn on average → biases nudged up.
- 'gradient' hours (10h) under-predicted by 2.57 kn → those regime buckets shifted most.
- Measured gusts ran 0.85× the model (weaker); gust ratio updated.
- Regime call was right 4/11 hours (36%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'calm' (2×).
- Regime miss: predicted 'gradient', measured 'thermal' (4×).
- Worst hour was 15:00 (gradient): predicted 10.6 kn, measured 3.8 kn (Δ +6.8 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|08   |    +0.20  | +1.27 -> +1.32   | 1.81 -> 1.36    | 3
 gradient|09   |    -1.20  | -1.34 -> -1.89   | 1.65 -> 1.65    | 3
 gradient|10   |    -1.80  | -0.80 -> -1.01   | 0.68 -> 0.72    | 3
 calm|11       |    -0.60  | +1.72 -> +0.71   | 1.24 -> 1.10    | 2
 gradient|12   |    +3.90  | +0.41 -> +1.77   | 1.56 -> 1.36    | 4
 gradient|13   |    +1.00  | +0.59 -> -0.40   | 1.61 -> 1.71    | 4
 thermal|14    |    +0.30  | -0.03 -> -1.06   | 1.59 -> 1.71    | 5
 gradient|15   |    -1.80  | +2.33 -> +3.88   | 1.47 -> 0.79    | 4
 gradient|16   |    -0.60  | +2.61 -> +4.50   | 1.34 -> 0.74    | 5
 gradient|17   |    -2.40  | -0.46 -> +1.28   | 1.45 -> 0.79    | 4
 gradient|18   |    -0.20  | +1.00 -> +1.40   | 0.96 -> 0.82    | 6
 gradient|19   |    -0.40  | +5.91 -> +5.28   | -0.01 -> 0.03    | 6
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 36 calibration buckets._
