### Learning report — kochelsee — learned from 2026-08-10
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 11 hours · 7 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | calm     |  3.7 |  1.9 |  3.1 | +0.6 | -1.2 |  ESE   NW 141
 08 | gradient |  2.5 |  2.5 |  1.9 | +0.6 | +0.6 |  SSE  SSE   1
 09 | gradient |  2.2 |  2.2 |  0.4 | +1.8 | +1.8 |  SSE  SSE   8
 10 | gradient |  2.2 |  2.2 |  0.9 | +1.3 | +1.3 |    S  SSE  27
 11 | gradient |  2.4 |  2.7 |  0.6 | +1.8 | +2.1 |    S  SSE  26
 12 | gradient |  2.5 |  2.9 |  0.8 | +1.7 | +2.1 |   SW  SSW  26
 19 | gradient |  1.1 |  2.8 |  0.6 | +0.5 | +2.2 |  SSE  WSW 100
 20 | gradient |  1.9 |  2.3 |  0.1 | +1.8 | +2.2 |  SSE    S  39
 21 | gradient |  1.3 |  2.5 |  0.0 | +1.3 | +2.5 |    S   SW  35
 22 | gradient |  2.2 |  2.9 |  0.0 | +2.2 | +2.9 |  SSE  SSW  35
 23 | gradient |  4.8 |  3.0 | 12.1 | -7.3 | -9.1 |  SSE    S  28
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 23 | gradient |  4.8 | 12.1 |  -7.3
```
- **23:00 (gradient)** — under-predicted — forecast 4.8 kn vs measured 12.1 kn (-7.3 kn). *Lesson:* the model may underplay the 'gradient' scenario around 23:00 — one day is weak evidence. *Fix:* the correction for (gradient×23h) is a regression corrected = +7.1 + -0.17·model — it **scales with** the model's own wind (rather than adding a fixed scenario bonus), refined recursively over days (4 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **1.9 kn** vs raw model 2.55 kn
- Mean signed error (bias): +0.57 kn (under-predicting)
- Direction mean abs error: 42°
- Gust ratio (measured/model): 0.79×
- By regime: calm +0.6 kn (1h); gradient +0.57 kn (10h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **0/11 hours (0%)**
- Cross-table (scenario→flow sector): calm->gradient ×1; gradient->calm ×9; gradient->foehn ×1
- Mismatched hours: 07h calm→gradient (NW); 08h gradient→calm (SSE); 09h gradient→calm (SSE); 10h gradient→calm (SSE); 11h gradient→calm (SSE); 12h gradient→calm (SSW); 19h gradient→calm (WSW); 20h gradient→calm (S)

**3. Lessons learned**
- The learned correction HELPED: issued-forecast error 1.9 kn vs raw-model error 2.55 kn (−0.65 kn).
- Overall speed bias small (+0.57 kn mean error).
- Measured gusts ran 0.79× the model (weaker); gust ratio updated.
- Regime call was right 0/11 hours (0%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'calm' (9×).
- Worst hour was 23:00 (gradient): predicted 4.8 kn, measured 12.1 kn (Δ -7.3 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|07       |    +1.20  | +0.48 -> +0.49   | 1.68 -> 1.60    | 6
 gradient|08   |    -0.60  | +0.00 -> -0.35   | 1.00 -> 0.93    | 1
 gradient|09   |    -1.80  | +0.00 -> -1.12   | 1.00 -> 0.81    | 1
 gradient|10   |    -1.30  | +0.00 -> -0.81   | 1.00 -> 0.87    | 1
 gradient|11   |    -2.10  | -0.36 -> -0.56   | 0.96 -> 0.83    | 3
 gradient|12   |    -2.10  | -0.74 -> -0.95   | 0.85 -> 0.77    | 2
 gradient|19   |    -2.20  | +0.33 -> +0.24   | 0.26 -> 0.26    | 6
 gradient|20   |    -2.20  | -1.05 -> -1.62   | 1.32 -> 1.40    | 7
 gradient|21   |    -2.50  | -1.69 -> -2.26   | 1.22 -> 1.32    | 7
 gradient|22   |    -2.90  | -0.99 -> -1.88   | 1.13 -> 1.23    | 4
 gradient|23   |    +9.10  | +4.82 -> +7.09   | -0.02 -> -0.17    | 4
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 37 calibration buckets._
