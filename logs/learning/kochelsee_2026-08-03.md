### Learning report — kochelsee — learned from 2026-08-03
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 12 hours · 7 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | calm     |  1.2 |  1.1 |  2.1 | -0.9 | -1.0 |    W   NW  49
 08 | calm     |  2.7 |  1.9 |  2.2 | +0.5 | -0.3 |   SE    S  56
 09 | thermal  |  1.9 |  1.9 |  4.1 | -2.2 | -2.2 |   SW    S  32
 10 | calm     |  1.7 |  1.7 | 10.1 | -8.4 | -8.4 |    W  SSW  65
 11 | thermal  |  1.7 |  1.7 | 12.0 | -10.3 | -10.3 |  WSW    S  65
 12 | thermal  |  1.4 |  1.4 |  5.4 | -4.0 | -4.0 |   NW    S 128
 18 | gradient |  3.8 |  3.6 |  4.2 | -0.4 | -0.6 |  NNE  WNW 101
 19 | gradient |  1.6 |  2.1 |  2.7 | -1.1 | -0.6 |  ENE    W 166
 20 | calm     |  2.0 |  1.8 |  0.5 | +1.5 | +1.3 |    S  SSW  19
 21 | gradient |  3.2 |  2.7 |  3.0 | +0.2 | -0.3 |   SE    S  32
 22 | foehn    |  3.4 |  3.4 |  1.5 | +1.9 | +1.9 |    S  SSW  31
 23 | foehn    |  6.0 |  6.0 | 10.7 | -4.7 | -4.7 |    S  SSW  18
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 10 | calm     |  1.7 | 10.1 |  -8.4
 11 | thermal  |  1.7 | 12.0 | -10.3
```
- **10:00 (calm)** — under-predicted — forecast 1.6525 kn vs measured 10.1 kn (-8.4 kn). *Lesson:* the model may underplay the 'calm' regime around 10:00 — one day is weak evidence. *Fix:* the correction for (calm×10h) is a regression corrected = +5.7 + 1.73·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).
- **11:00 (thermal)** — under-predicted — forecast 1.7112500000000002 kn vs measured 12 kn (-10.3 kn). *Lesson:* the model may underplay the 'thermal' regime around 11:00 — one day is weak evidence. *Fix:* the correction for (thermal×11h) is a regression corrected = +7.0 + 1.90·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **3.01 kn** vs raw model 2.97 kn
- Mean signed error (bias): -2.33 kn (over-predicting)
- Direction mean abs error: 64°
- Gust ratio (measured/model): 2.16×
- By regime: calm -1.83 kn (4h); foehn -1.4 kn (2h); gradient -0.43 kn (3h); thermal -5.5 kn (3h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **4/12 hours (33%)**
- Confusion (predicted→measured): calm->calm ×1; calm->foehn ×2; calm->gradient ×1; foehn->calm ×1; foehn->foehn ×1; gradient->foehn ×1; gradient->gradient ×2; thermal->foehn ×3
- Mismatched hours: 07h calm→gradient (NW); 08h calm→foehn (S); 09h thermal→foehn (S); 10h calm→foehn (SSW); 11h thermal→foehn (S); 12h thermal→foehn (S); 21h gradient→foehn (S); 22h foehn→calm (SSW)

**3. Lessons learned**
- Correction was roughly neutral (issued 3.01 kn vs raw 2.97 kn).
- Issued forecast OVER-predicted by 2.33 kn on average → biases nudged down.
- 'calm' hours (4h) over-predicted by 1.83 kn → those regime buckets shifted most.
- 'foehn' hours (2h) over-predicted by 1.4 kn → those regime buckets shifted most.
- 'thermal' hours (3h) over-predicted by 5.5 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 64° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 2.16× the model (stronger); gust ratio updated.
- Regime call was right 4/12 hours (33%) vs the measured wind direction.
- Regime miss: predicted 'calm', measured 'foehn' (2×).
- ⚠ predicted 'thermal' but measured direction was 'foehn' (3×) — the föhn/thermal ANTI-CORRELATION; re-check the Kochelsee↔Walchensee split.
- Worst hour was 11:00 (thermal): predicted 1.7112500000000002 kn, measured 12 kn (Δ -10.3 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|07       |    +1.00  | +0.00 -> +0.75   | 1.00 -> 1.06    | 1
 calm|08       |    +0.30  | +0.00 -> +0.20   | 1.00 -> 1.03    | 1
 thermal|09    |    +2.20  | +0.00 -> +1.45   | 1.00 -> 1.21    | 1
 calm|10       |    +8.40  | +0.00 -> +5.75   | 1.00 -> 1.73    | 1
 thermal|11    |   +10.30  | +0.00 -> +7.05   | 1.00 -> 1.90    | 1
 thermal|12    |    +4.00  | +0.00 -> +2.87   | 1.00 -> 1.30    | 1
 gradient|18   |    +0.60  | +0.94 -> +0.81   | 1.21 -> 1.08    | 2
 gradient|19   |    +0.60  | -0.39 -> +0.31   | 0.90 -> 0.84    | 2
 calm|20       |    -1.30  | +0.46 -> -0.03   | 1.03 -> 0.76    | 2
 gradient|21   |    +0.30  | -1.04 -> -0.31   | 0.77 -> 0.83    | 2
 foehn|22      |    -1.90  | +0.00 -> -0.90   | 1.00 -> 0.77    | 1
 foehn|23      |    +4.70  | +0.00 -> +1.19   | 1.00 -> 1.54    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 14 calibrated buckets._
