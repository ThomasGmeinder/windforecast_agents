### Learning report — walchensee — learned from 2026-08-11
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 13 hours · 1 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | gradient |  3.1 |  2.7 |  7.5 | -4.4 | -4.8 |    S    S   6
 08 | calm     |  1.5 |  1.4 |  2.9 | -1.4 | -1.5 |    S  SSW  18
 09 | thermal  |  2.6 |  2.5 |  2.8 | -0.2 | -0.3 |   NE  ESE  76
 10 | thermal  |  2.1 |  3.5 |  4.8 | -2.7 | -1.3 |    N  NNE  18
 11 | thermal  | 10.2 |  5.4 | 11.4 | -1.2 | -6.0 |  NNE  NNE  11
 12 | thermal  | 10.7 |  5.6 | 11.1 | -0.4 | -5.5 |    N  NNE   6
 13 | gradient |  9.6 |  5.8 | 10.3 | -0.7 | -4.5 |  NNE  NNE   9
 14 | thermal  |  8.7 |  5.7 | 11.1 | -2.4 | -5.4 |    N    N   0
 15 | thermal  |  9.5 |  5.9 | 10.5 | -1.0 | -4.6 |  NNE    N   6
 16 | thermal  |  9.0 |  5.2 |  9.8 | -0.8 | -4.6 |    N    N  12
 17 | thermal  |  8.8 |  5.4 |  8.7 | +0.1 | -3.3 |    N    N   3
 18 | thermal  |  6.4 |  5.8 |  7.2 | -0.8 | -1.4 |    N    N   4
 19 | thermal  |  6.8 |  6.4 |  9.6 | -2.8 | -3.2 |    N  NNE  13
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.45 kn** vs raw model 3.57 kn
- Mean signed error (bias): -1.44 kn (over-predicting)
- Direction mean abs error: 14°
- Gust ratio (measured/model): 1.41×
- By regime: calm -1.4 kn (1h); gradient -2.55 kn (2h); thermal -1.22 kn (10h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **9/13 hours (69%)**
- Cross-table (scenario→flow sector): calm->foehn ×1; gradient->foehn ×1; gradient->thermal ×1; thermal->foehn ×1; thermal->thermal ×9
- Mismatched hours: 07h gradient→foehn (S); 08h calm→foehn (SSW); 09h thermal→foehn (ESE); 13h gradient→thermal (NNE)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 1.45 kn vs raw-model error 3.57 kn (−2.12 kn).
- Issued forecast UNDER-predicted by 1.44 kn on average → biases nudged up.
- 'gradient' hours (2h) under-predicted by 2.55 kn → those regime buckets shifted most.
- 'thermal' hours (10h) under-predicted by 1.22 kn → those regime buckets shifted most.
- Measured gusts ran 1.41× the model (stronger); gust ratio updated.
- Regime call was right 9/13 hours (69%) vs the measured wind direction.
- ⚠ predicted 'thermal' but measured direction was 'foehn' (1×) — the föhn/thermal ANTI-CORRELATION; re-check the Kochelsee↔Walchensee split.
- Worst hour was 07:00 (gradient): predicted 3.1 kn, measured 7.5 kn (Δ -4.4 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|07   |    +4.80  | -0.16 -> +0.67   | 1.21 -> 1.19    | 5
 calm|08       |    +1.50  | +0.14 -> +0.44   | 0.97 -> 1.00    | 4
 thermal|09    |    +0.30  | -0.29 -> -0.28   | 1.14 -> 1.18    | 3
 thermal|10    |    +1.30  | -0.43 -> -1.06   | 0.72 -> 1.28    | 5
 thermal|11    |    +6.00  | -2.25 -> -2.89   | 2.00 -> 2.00    | 5
 thermal|12    |    +5.50  | -1.75 -> -2.05   | 2.00 -> 2.00    | 5
 gradient|13   |    +4.50  | -0.40 -> -0.49   | 1.71 -> 1.76    | 5
 thermal|14    |    +5.40  | -1.06 -> -1.26   | 1.71 -> 1.83    | 6
 thermal|15    |    +4.60  | +1.17 -> +1.06   | 1.42 -> 1.48    | 5
 thermal|16    |    +4.60  | +1.24 -> +1.38   | 1.48 -> 1.50    | 4
 thermal|17    |    +3.30  | +2.67 -> +2.66   | 1.14 -> 1.14    | 5
 thermal|18    |    +1.40  | +0.52 -> +0.42   | 1.24 -> 1.22    | 2
 thermal|19    |    +3.20  | +0.31 -> +0.03   | 1.12 -> 1.36    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±4 kn guard limits over-correction but cannot prove a physical cause. 36 calibration buckets._
