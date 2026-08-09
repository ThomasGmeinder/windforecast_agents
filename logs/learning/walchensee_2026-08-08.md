### Learning report — walchensee — learned from 2026-08-08
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 14 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | gradient |  4.1 |  3.3 |  5.1 | -1.0 | -1.8 |    S    S   3
 07 | gradient |  2.3 |  2.9 |  5.8 | -3.5 | -2.9 |    S    S   6
 08 | calm     |  1.0 |  1.3 |  2.8 | -1.8 | -1.5 |    S  SSW  13
 09 | calm     |  1.3 |  1.3 |  1.9 | -0.6 | -0.6 |  NNE  SSW 172
 10 | thermal  |  1.3 |  2.2 |  1.2 | +0.1 | +1.0 |  NNE   NE  28
 11 | thermal  |  6.9 |  4.6 |  9.4 | -2.5 | -4.8 |    N   NE  49
 12 | thermal  |  9.1 |  5.0 | 10.7 | -1.6 | -5.7 |    N   NE  36
 13 | thermal  |  9.0 |  5.7 | 10.9 | -1.9 | -5.2 |    N  NNE  24
 14 | thermal  |  8.7 |  6.0 | 11.1 | -2.4 | -5.1 |    N  NNE  17
 15 | thermal  |  9.5 |  6.2 | 10.9 | -1.4 | -4.7 |    N    N   9
 16 | thermal  |  8.0 |  6.4 | 11.2 | -3.2 | -4.8 |    N    N  10
 17 | thermal  |  9.8 |  6.9 |  9.7 | +0.1 | -2.8 |    N  NNE  10
 18 | thermal  |  6.1 |  6.1 |  8.2 | -2.1 | -2.1 |  NNE   NE   3
 19 | thermal  |  5.1 |  5.1 |  6.1 | -1.0 | -1.0 |   NE   NE   5
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.66 kn** vs raw model 3.14 kn
- Mean signed error (bias): -1.63 kn (over-predicting)
- Direction mean abs error: 28°
- Gust ratio (measured/model): 1.28×
- By regime: calm -1.2 kn (2h); gradient -2.25 kn (2h); thermal -1.59 kn (10h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **10/14 hours (71%)**
- Cross-table (scenario→flow sector): calm->calm ×1; calm->foehn ×1; gradient->foehn ×2; thermal->calm ×1; thermal->thermal ×9
- Mismatched hours: 06h gradient→foehn (S); 07h gradient→foehn (S); 08h calm→foehn (SSW); 10h thermal→calm (NE)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 1.66 kn vs raw-model error 3.14 kn (−1.48 kn).
- Issued forecast OVER-predicted by 1.63 kn on average → biases nudged down.
- 'calm' hours (2h) over-predicted by 1.2 kn → those regime buckets shifted most.
- 'gradient' hours (2h) over-predicted by 2.25 kn → those regime buckets shifted most.
- 'thermal' hours (10h) over-predicted by 1.59 kn → those regime buckets shifted most.
- Measured gusts ran 1.28× the model (stronger); gust ratio updated.
- Regime call was right 10/14 hours (71%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (2×).
- Worst hour was 07:00 (gradient): predicted 2.3 kn, measured 5.8 kn (Δ -3.5 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|06   |    +1.80  | +0.63 -> +0.58   | 1.18 -> 1.27    | 3
 gradient|07   |    +2.90  | -0.54 -> -0.20   | 0.91 -> 1.24    | 3
 calm|08       |    +1.50  | -0.50 -> +0.14   | 0.97 -> 0.97    | 3
 calm|09       |    +0.60  | +0.00 -> +0.44   | 1.00 -> 1.04    | 1
 thermal|10    |    -1.00  | -0.73 -> -0.68   | 0.74 -> 0.76    | 3
 thermal|11    |    +4.80  | -1.11 -> -1.18   | 1.98 -> 2.12    | 3
 thermal|12    |    +5.70  | -0.11 -> -0.26   | 1.83 -> 1.98    | 4
 thermal|13    |    +5.20  | -1.89 -> -2.17   | 1.89 -> 2.06    | 4
 thermal|14    |    +5.10  | +0.26 -> -0.03   | 1.42 -> 1.59    | 4
 thermal|15    |    +4.70  | +1.56 -> +1.17   | 1.27 -> 1.42    | 4
 thermal|16    |    +4.80  | +1.52 -> +1.51   | 1.50 -> 1.51    | 2
 thermal|17    |    +2.80  | +1.20 -> +1.82   | 1.47 -> 1.24    | 3
 thermal|18    |    +2.10  | +0.00 -> +0.52   | 1.00 -> 1.24    | 1
 thermal|19    |    +1.00  | +0.00 -> +0.31   | 1.00 -> 1.12    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 36 calibration buckets._
