### Learning report — kochelsee — learned from 2026-08-09
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 11 hours · 6 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 06 | calm     |  0.6 |  0.7 |  0.0 | +0.6 | +0.7 |   NE  NNW  80
 07 | calm     |  2.7 |  1.0 |  0.0 | +2.7 | +1.0 |   NE    N  40
 08 | calm     |  0.9 |  1.0 |  0.0 | +0.9 | +1.0 |    N  NNE  23
 09 | calm     |  0.8 |  0.9 |  0.0 | +0.8 | +0.9 |   NW    S 126
 10 | calm     |  1.8 |  0.8 |  0.0 | +1.8 | +0.8 |   NW    N  37
 11 | thermal  |  3.8 |  0.8 |  0.0 | +3.8 | +0.8 |   NW  ENE 121
 12 | thermal  |  2.2 |  1.2 |  0.0 | +2.2 | +1.2 |  WNW  ENE 144
 20 | gradient |  2.0 |  2.6 |  3.6 | -1.6 | -1.0 |    S   SW  52
 21 | gradient |  3.9 |  4.4 |  2.5 | +1.4 | +1.9 |  WSW  WSW   3
 22 | gradient |  3.2 |  3.0 |  0.6 | +2.6 | +2.4 |  ESE    S  72
 23 | gradient |  2.2 |  2.5 |  9.1 | -6.9 | -6.6 |  SSE  WSW  82
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 23 | gradient |  2.2 |  9.1 |  -6.9
```
- **23:00 (gradient)** — under-predicted — forecast 2.2 kn vs measured 9.1 kn (-6.9 kn). *Lesson:* the model may underplay the 'gradient' scenario around 23:00 — one day is weak evidence. *Fix:* the correction for (gradient×23h) is a regression corrected = +4.8 + -0.02·model — it **scales with** the model's own wind (rather than adding a fixed scenario bonus), refined recursively over days (3 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **2.3 kn** vs raw model 1.66 kn
- Mean signed error (bias): +0.75 kn (under-predicting)
- Direction mean abs error: 71°
- Gust ratio (measured/model): 1.46×
- By regime: calm +1.36 kn (5h); gradient -1.12 kn (4h); thermal +3.0 kn (2h)

**2b. Scenario/flow-sector check** (forecast scenario vs measured direction sector; not physical-regime confirmation)
- Scenario/sector agreement: **5/8 hours (62%)**
- Cross-table (scenario→flow sector): calm->calm ×5; gradient->calm ×1; thermal->calm ×2
- Mismatched hours: 11h thermal→calm (ENE); 12h thermal→calm (ENE); 22h gradient→calm (S)

**3. Lessons learned**
- The correction HURT yesterday: issued 2.3 kn vs raw 1.66 kn (+0.64 kn) — likely a regime shift vs the days it learned from.
- Overall speed bias small (+0.75 kn mean error).
- 'calm' hours (5h) under-predicted by 1.36 kn → those regime buckets shifted most.
- 'gradient' hours (4h) over-predicted by 1.12 kn → those regime buckets shifted most.
- 'thermal' hours (2h) under-predicted by 3.0 kn → those regime buckets shifted most.
- Wind DIRECTION was off by 71° on average (terrain/thermal veer the model misses); direction not yet auto-corrected.
- Measured gusts ran 1.46× the model (stronger); gust ratio updated.
- Regime call was right 5/8 hours (62%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (2×).
- Worst hour was 23:00 (gradient): predicted 2.2 kn, measured 9.1 kn (Δ -6.9 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|06       |    -0.70  | -0.24 -> -0.42   | 0.98 -> 1.01    | 3
 calm|07       |    -1.00  | +1.21 -> +0.48   | 1.52 -> 1.68    | 5
 calm|08       |    -1.00  | -0.27 -> -0.45   | 1.24 -> 1.27    | 7
 calm|09       |    -0.90  | -0.02 -> -0.29   | 0.97 -> 0.97    | 3
 calm|10       |    -0.80  | +0.93 -> +0.19   | 1.12 -> 1.38    | 5
 thermal|11    |    -0.80  | +3.34 -> +1.45   | 0.59 -> 1.20    | 4
 thermal|12    |    -1.20  | +1.95 -> +1.00   | 0.19 -> 0.48    | 6
 gradient|20   |    +1.00  | -1.55 -> -1.05   | 1.38 -> 1.32    | 6
 gradient|21   |    -1.90  | -1.67 -> -1.69   | 1.28 -> 1.22    | 6
 gradient|22   |    -2.40  | +0.24 -> -0.99   | 1.01 -> 1.13    | 3
 gradient|23   |    +6.60  | +0.53 -> +4.82   | 0.63 -> -0.02    | 3
```
_The correction **scales with** the model (b·model) rather than adding a flat scenario bonus; the ±8 kn guard limits over-correction but cannot prove a physical cause. 34 calibration buckets._
