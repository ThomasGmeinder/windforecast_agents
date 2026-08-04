### Learning report — walchensee — learned from 2026-08-03
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 13 hours · 1 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 07 | foehn    |  4.0 |  4.0 |  3.8 | +0.2 | +0.2 |  SSE   SE  30
 08 | foehn    |  3.7 |  3.7 |  2.3 | +1.4 | +1.4 |  SSE  SSE  13
 09 | foehn    |  3.3 |  3.3 |  2.7 | +0.6 | +0.6 |    S  SSE   8
 10 | foehn    |  3.0 |  3.0 |  2.3 | +0.7 | +0.7 |    S    S  16
 11 | thermal  |  4.5 |  2.6 |  1.9 | +2.6 | +0.7 |   SW    S  39
 12 | thermal  |  4.6 |  2.3 |  0.6 | +4.0 | +1.7 |  WSW    S  63
 13 | thermal  |  4.2 |  2.9 |  0.5 | +3.7 | +2.4 |    N   SE 126
 14 | thermal  |  5.4 |  3.9 |  5.2 | +0.2 | -1.3 |    N    N   5
 15 | thermal  |  5.4 |  4.3 |  7.9 | -2.5 | -3.6 |  NNW   NW  22
 16 | thermal  |  6.5 |  4.4 |  8.5 | -2.0 | -4.1 |    N    N   7
 17 | thermal  |  5.2 |  5.2 |  9.1 | -3.9 | -3.9 |   NE    N  40
 18 | gradient |  4.6 |  4.6 |  4.8 | -0.2 | -0.2 |  ENE  WSW 179
 19 | gradient |  6.3 |  2.8 |  1.4 | +4.9 | +1.4 |   SW   SW  11
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
- none: every matched hour was within 5 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **2.07 kn** vs raw model 1.71 kn
- Mean signed error (bias): +0.75 kn (under-predicting)
- Direction mean abs error: 43°
- Gust ratio (measured/model): 0.97×
- By regime: foehn +0.72 kn (4h); gradient +2.35 kn (2h); thermal +0.3 kn (7h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **7/12 hours (57%)**
- Confusion (predicted→measured): foehn->foehn ×4; gradient->calm ×1; thermal->calm ×3; thermal->gradient ×1; thermal->thermal ×3
- Mismatched hours: 11h thermal→calm (S); 12h thermal→calm (S); 13h thermal→calm (SE); 15h thermal→gradient (NW); 19h gradient→calm (SW)

**3. Lessons learned**
- The correction HURT yesterday: issued 2.07 kn vs raw 1.71 kn (+0.36 kn) — likely a regime shift vs the days it learned from.
- Overall speed bias small (+0.75 kn mean error).
- 'gradient' hours (2h) under-predicted by 2.35 kn → those regime buckets shifted most.
- Regime call was right 7/12 hours (57%) vs the measured wind direction.
- Regime miss: predicted 'thermal', measured 'calm' (3×).
- Worst hour was 19:00 (gradient): predicted 6.3 kn, measured 1.4 kn (Δ +4.9 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 foehn|07      |    -0.20  | +0.00 -> -0.08   | 1.00 -> 0.97    | 1
 foehn|08      |    -1.40  | +0.00 -> -0.62   | 1.00 -> 0.83    | 1
 foehn|09      |    -0.60  | +0.00 -> -0.29   | 1.00 -> 0.93    | 1
 foehn|10      |    -0.70  | +0.00 -> -0.36   | 1.00 -> 0.92    | 1
 thermal|11    |    -0.70  | +0.00 -> -0.40   | 1.00 -> 0.92    | 1
 thermal|12    |    -1.70  | +0.00 -> -1.03   | 1.00 -> 0.82    | 1
 thermal|13    |    -2.40  | +0.00 -> -1.28   | 1.00 -> 0.72    | 1
 thermal|14    |    +1.30  | +0.00 -> +0.55   | 1.00 -> 1.16    | 1
 thermal|15    |    +3.60  | +0.00 -> +1.37   | 1.00 -> 1.44    | 1
 thermal|16    |    +4.10  | +0.00 -> +1.52   | 1.00 -> 1.50    | 1
 thermal|17    |    +3.90  | +0.00 -> +1.19   | 1.00 -> 1.47    | 1
 gradient|18   |    +0.20  | +0.00 -> +0.07   | 1.00 -> 1.02    | 1
 gradient|19   |    -1.40  | +6.98 -> +4.42   | 2.26 -> 1.05    | 2
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 14 calibrated buckets._
