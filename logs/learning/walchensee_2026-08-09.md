### Learning report — walchensee — learned from 2026-08-09
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 14 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | gradient |  5.3 |  3.7 |  4.2 | +1.1 | -0.5 |    S    S   7
 07 | gradient |  4.1 |  3.5 |  3.9 | +0.2 | -0.4 |    S  WSW  60
 08 | gradient |  4.2 |  2.5 |  3.5 | +0.7 | -1.0 |    S    S   8
 09 | thermal  |  1.5 |  1.9 |  3.0 | -1.5 | -1.1 |  SSW    S   6
 10 | thermal  |  0.5 |  1.5 |  1.2 | -0.7 | +0.3 |   SW    S  50
 11 | thermal  |  2.9 |  1.9 |  0.6 | +2.3 | +1.3 |   NW  SSE 162
 12 | gradient |  5.7 |  3.8 |  5.9 | -0.2 | -2.1 |  NNE    E  82
 13 | gradient |  6.3 |  4.1 |  6.9 | -0.6 | -2.8 |  NNE  ENE  48
 14 | gradient |  7.7 |  4.9 |  6.6 | +1.1 | -1.7 |  NNE  ENE  47
 15 | gradient |  9.0 |  5.0 |  7.4 | +1.6 | -2.4 |  NNE  ENE  50
 16 | thermal  |  8.1 |  5.3 |  8.3 | -0.2 | -3.0 |  NNE  ENE  56
 17 | thermal  |  6.7 |  3.9 |  8.2 | -1.5 | -4.3 |  NNE   NE  19
 18 | gradient |  3.9 |  3.2 |  4.5 | -0.6 | -1.3 |   NE    E  45
 19 | calm     |  1.8 |  1.5 |  4.0 | -2.2 | -2.5 |  SSE  ESE  36
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **1.04 kn** vs raw model 1.76 kn
- Mean signed error (bias): -0.04 kn (over-predicting)
- Direction mean abs error: 48°
- Gust ratio (measured/model): 1.0×
- By regime: calm -2.2 kn (1h); gradient +0.41 kn (8h); thermal -0.32 kn (5h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **6/13 hours (46%)**
- Cross-table (scenario→flow sector): calm->gradient ×1; gradient->foehn ×2; gradient->gradient ×5; thermal->calm ×2; thermal->foehn ×1; thermal->gradient ×1; thermal->thermal ×1
- Mismatched hours: 06h gradient→foehn (S); 08h gradient→foehn (S); 09h thermal→foehn (S); 10h thermal→calm (S); 11h thermal→calm (SSE); 16h thermal→gradient (ENE); 19h calm→gradient (ESE)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 1.04 kn vs raw-model error 1.76 kn (−0.72 kn).
- Overall speed bias small (-0.04 kn mean error).
- Wind DIRECTION was off by 48° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Regime call was right 6/13 hours (46%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (2×).
- ⚠ predicted 'thermal' but measured direction was 'foehn' (1×) — the föhn/thermal ANTI-CORRELATION; re-check the Kochelsee↔Walchensee split.
- Regime miss: predicted 'thermal', measured 'calm' (2×).
- Worst hour was 11:00 (thermal): predicted 2.9 kn, measured 0.6 kn (Δ +2.3 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 gradient|06   |    +0.50  | +0.58 -> +0.76   | 1.27 -> 1.11    | 4
 gradient|07   |    +0.40  | -0.20 -> -0.16   | 1.24 -> 1.21    | 4
 gradient|08   |    +1.00  | +3.23 -> +1.27   | 1.77 -> 1.81    | 2
 thermal|09    |    +1.10  | -0.93 -> -0.29   | 0.90 -> 1.14    | 2
 thermal|10    |    -0.30  | -0.68 -> -0.43   | 0.76 -> 0.72    | 4
 thermal|11    |    -1.30  | -1.18 -> -2.58   | 2.12 -> 2.38    | 4
 gradient|12   |    +2.10  | +0.94 -> +0.41   | 1.50 -> 1.56    | 3
 gradient|13   |    +2.80  | +0.89 -> +0.59   | 1.58 -> 1.61    | 3
 gradient|14   |    +1.70  | +1.26 -> +0.43   | 1.60 -> 1.61    | 3
 gradient|15   |    +2.40  | +2.27 -> +2.33   | 1.74 -> 1.47    | 3
 thermal|16    |    +3.00  | +1.51 -> +1.30   | 1.51 -> 1.48    | 3
 thermal|17    |    +4.30  | +1.82 -> +2.67   | 1.24 -> 1.14    | 4
 gradient|18   |    +1.30  | +0.70 -> +1.00   | 1.01 -> 0.96    | 5
 calm|19       |    +2.50  | +0.82 -> +1.46   | 1.10 -> 1.14    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 36 calibration buckets._
