### Learning report — walchensee — learned from 2026-08-01
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 13 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | gradient |  2.4 |  2.4 |  3.6 | -1.2 | -1.2 |    S  SSW  25
 08 | gradient |  2.2 |  2.2 |  4.6 | -2.4 | -2.4 |   SE   SE  11
 09 | thermal  |  0.7 |  0.7 |  4.2 | -3.5 | -3.5 |   SE    W 124
 10 | thermal  |  0.4 |  0.4 |  1.0 | -0.6 | -0.6 |    S    E  90
 11 | calm     |  1.2 |  1.2 |  3.1 | -1.9 | -1.9 |   NE  ENE  33
 12 | thermal  |  4.9 |  4.9 |  9.9 | -5.0 | -5.0 |    N    N   2
 13 | gradient |  5.2 |  5.2 |  9.7 | -4.5 | -4.5 |    N    N   4
 14 | gradient |  7.6 |  7.6 |  7.5 | +0.1 | +0.1 |    S    S   1
 15 | gradient |  4.1 |  4.1 |  1.7 | +2.4 | +2.4 |    S    S   7
 16 | thermal  |  0.4 |  0.4 |  1.8 | -1.4 | -1.4 |  NNW    S 144
 17 | calm     |  1.4 |  1.4 |  0.6 | +0.8 | +0.8 |  NNW  SSW 141
 18 | calm     |  1.7 |  1.7 |  1.4 | +0.3 | +0.3 |   SE   SW  81
 19 | gradient |  2.4 |  2.4 | 14.1 | -11.7 | -11.7 |   SW  SSW   5
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 12 | thermal  |  4.9 |  9.9 |  -5.0
 19 | gradient |  2.4 | 14.1 | -11.7
```
- **12:00 (thermal)** — under-predicted — forecast 4.9 kn vs measured 9.9 kn (-5.0 kn). *Lesson:* the model may underplay the 'thermal' regime around 12:00 — one day is weak evidence. *Fix:* the correction for (thermal×12h) is a regression corrected = +1.6 + 1.60·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).
- **19:00 (gradient)** — under-predicted — forecast 2.4 kn vs measured 14.1 kn (-11.7 kn). *Lesson:* the model may underplay the 'gradient' regime around 19:00 — one day is weak evidence. *Fix:* the correction for (gradient×19h) is a regression corrected = +7.0 + 2.26·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.75 kn** vs raw model 2.75 kn
- Mean signed error (bias): -2.2 kn (over-predicting)
- Direction mean abs error: 51°
- Gust ratio (measured/model): 1.66×
- By regime: calm -0.27 kn (3h); gradient -2.88 kn (6h); thermal -2.62 kn (4h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **3/12 hours (25%)**
- Confusion (predicted→measured): calm->calm ×2; calm->gradient ×1; gradient->calm ×1; gradient->foehn ×3; gradient->thermal ×1; thermal->calm ×2; thermal->gradient ×1; thermal->thermal ×1
- Mismatched hours: 07h gradient→foehn (SSW); 08h gradient→foehn (SE); 09h thermal→gradient (W); 10h thermal→calm (E); 11h calm→gradient (ENE); 13h gradient→thermal (N); 14h gradient→foehn (S); 15h gradient→calm (S)

**3. Lessons learned**
- Correction was roughly neutral (issued 2.75 kn vs raw 2.75 kn).
- Issued forecast OVER-predicted by 2.2 kn on average → biases nudged down.
- 'gradient' hours (6h) over-predicted by 2.88 kn → those regime buckets shifted most.
- 'thermal' hours (4h) over-predicted by 2.62 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 51° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 1.66× the model (stronger); gust ratio updated.
- Regime call was right 3/12 hours (25%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (3×).
- Regime miss: predicted 'thermal', measured 'calm' (2×).
- Worst hour was 19:00 (gradient): predicted 2.4 kn, measured 14.1 kn (Δ -11.7 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|07   |    +1.20  | +0.00 -> +0.72   | 1.00 -> 1.13    | 1
 gradient|08   |    +2.40  | +0.00 -> +1.49   | 1.00 -> 1.25    | 1
 thermal|09    |    +3.50  | +0.00 -> +2.73   | 1.00 -> 1.14    | 1
 thermal|10    |    +0.60  | +0.00 -> +0.48   | 1.00 -> 1.01    | 1
 calm|11       |    +1.90  | +0.00 -> +1.40   | 1.00 -> 1.13    | 1
 thermal|12    |    +5.00  | +0.00 -> +1.64   | 1.00 -> 1.60    | 1
 gradient|13   |    +4.50  | +0.00 -> +1.38   | 1.00 -> 1.54    | 1
 gradient|14   |    -0.10  | +0.00 -> -0.02   | 1.00 -> 0.99    | 1
 gradient|15   |    -2.40  | +0.00 -> -0.96   | 1.00 -> 0.70    | 1
 thermal|16    |    +1.40  | +0.00 -> +1.11   | 1.00 -> 1.03    | 1
 calm|17       |    -0.80  | +0.00 -> -0.57   | 1.00 -> 0.94    | 1
 calm|18       |    -0.30  | +0.00 -> -0.20   | 1.00 -> 0.97    | 1
 gradient|19   |   +11.70  | +0.00 -> +6.98   | 1.00 -> 2.26    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 13 calibrated buckets._
