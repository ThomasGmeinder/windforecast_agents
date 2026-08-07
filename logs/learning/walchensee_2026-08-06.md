### Learning report — walchensee — learned from 2026-08-06
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 15 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | gradient |  2.6 |  2.5 |  4.1 | -1.5 | -1.6 |    S  SSW  17
 07 | gradient |  2.3 |  2.6 |  2.1 | +0.2 | +0.5 |  SSW  SSW   6
 08 | calm     |  1.0 |  1.1 |  0.4 | +0.6 | +0.7 |   NE    E  37
 09 | gradient |  2.5 |  2.5 |  0.5 | +2.0 | +2.0 |  NNE   NE  21
 10 | gradient |  4.1 |  4.1 |  2.6 | +1.5 | +1.5 |  NNE  NNE   3
 11 | gradient |  5.3 |  5.3 |  9.5 | -4.2 | -4.2 |  NNE    N  11
 12 | gradient |  5.5 |  5.5 |  8.9 | -3.4 | -3.4 |  NNE    N   7
 13 | gradient |  5.4 |  5.4 |  9.0 | -3.6 | -3.6 |  NNE    N  14
 14 | gradient |  5.2 |  5.2 |  9.4 | -4.2 | -4.2 |    N  NNE  15
 15 | gradient |  4.5 |  4.5 | 11.1 | -6.6 | -6.6 |    N    N   5
 16 | gradient |  6.3 |  4.3 | 11.2 | -4.9 | -6.9 |  NNE  NNW  53
 17 | gradient |  3.4 |  2.9 |  2.5 | +0.9 | +0.4 |    N  SSE 168
 18 | calm     |  1.7 |  1.8 |  0.5 | +1.2 | +1.3 |    E   SE  56
 19 | calm     |  1.7 |  1.7 |  2.9 | -1.2 | -1.2 |  SSE  SSE   9
 20 | gradient |  2.3 |  2.3 |  2.2 | +0.1 | +0.1 |    S   SW  54
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 15 | gradient |  4.5 | 11.1 |  -6.6
```
- **15:00 (gradient)** — under-predicted — forecast 4.5 kn vs measured 11.1 kn (-6.6 kn). *Lesson:* the model may underplay the 'gradient' regime around 15:00 — one day is weak evidence. *Fix:* the correction for (gradient×15h) is a regression corrected = +2.4 + 1.81·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.41 kn** vs raw model 2.55 kn
- Mean signed error (bias): -1.54 kn (over-predicting)
- Direction mean abs error: 32°
- Gust ratio (measured/model): 1.1×
- By regime: calm +0.2 kn (3h); gradient -1.97 kn (12h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **2/13 hours (15%)**
- Confusion (predicted→measured): calm->calm ×2; calm->foehn ×1; gradient->calm ×1; gradient->foehn ×3; gradient->thermal ×6
- Mismatched hours: 06h gradient→foehn (SSW); 07h gradient→foehn (SSW); 09h gradient→calm (NE); 10h gradient→thermal (NNE); 11h gradient→thermal (N); 12h gradient→thermal (N); 13h gradient→thermal (N); 14h gradient→thermal (NNE)

**3. Lessons learned**
- Correction was roughly neutral (issued 2.41 kn vs raw 2.55 kn).
- Issued forecast OVER-predicted by 1.54 kn on average → biases nudged down.
- 'gradient' hours (12h) over-predicted by 1.97 kn → those regime buckets shifted most.
- Regime call was right 2/13 hours (15%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (3×).
- Regime miss: predicted 'gradient', measured 'thermal' (6×).
- Worst hour was 15:00 (gradient): predicted 4.5 kn, measured 11.1 kn (Δ -6.6 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|06   |    +1.60  | +0.39 -> +0.63   | 1.06 -> 1.18    | 2
 gradient|07   |    -0.50  | -0.70 -> -0.54   | 0.87 -> 0.91    | 2
 calm|08       |    -0.70  | -0.34 -> -0.50   | 0.95 -> 0.97    | 2
 gradient|09   |    -2.00  | +0.00 -> -1.17   | 1.00 -> 0.78    | 1
 gradient|10   |    -1.50  | +0.00 -> -0.60   | 1.00 -> 0.82    | 1
 gradient|11   |    +4.20  | +0.00 -> +1.25   | 1.00 -> 1.50    | 1
 gradient|12   |    +3.40  | +0.00 -> +0.97   | 1.00 -> 1.40    | 1
 gradient|13   |    +3.60  | +0.00 -> +1.05   | 1.00 -> 1.43    | 1
 gradient|14   |    +4.20  | +0.00 -> +1.28   | 1.00 -> 1.50    | 1
 gradient|15   |    +6.60  | +0.00 -> +2.39   | 1.00 -> 1.81    | 1
 gradient|16   |    +6.90  | +1.13 -> +2.77   | 1.45 -> 1.35    | 3
 gradient|17   |    -0.40  | +0.70 -> -0.73   | 1.28 -> 1.44    | 2
 calm|18       |    -1.30  | -0.20 -> -0.58   | 0.97 -> 0.91    | 2
 calm|19       |    +1.20  | +0.00 -> +0.82   | 1.00 -> 1.10    | 1
 gradient|20   |    -0.10  | +0.00 -> -0.06   | 1.00 -> 0.99    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 32 calibrated buckets._
