### Learning report — walchensee — learned from 2026-08-01
Actual source: addicted-sports on-lake (walchensee/urfeld) · matched 2 hours · 11 hour(s) skipped (already elapsed when issued)

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 18 | calm     |  1.7 |  1.7 |  1.4 | +0.3 | +0.3 |   SE   SW  81
 19 | gradient |  2.4 |  2.4 | 14.1 | -11.7 | -11.7 |   SW  SSW   5
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 19 | gradient |  2.4 | 14.1 | -11.7
```
- **19:00 (gradient)** — under-predicted — forecast 2.4 kn vs measured 14.1 kn (-11.7 kn). *Lesson:* the model may underplay the 'gradient' regime around 19:00 — one day is weak evidence. *Fix:* the correction for (gradient×19h) is a regression corrected = +7.0 + 2.26·model — it **scales with** the model's own wind (so it can't double-count or blindly add a fixed amount), refined recursively over days (1 obs; full weight after 3).

**2. Accuracy summary**
- Mean abs error: issued forecast **6.0 kn** vs raw model 6.0 kn
- Mean signed error (bias): -5.7 kn (over-predicting)
- Direction mean abs error: 43°
- Gust ratio (measured/model): 3.36×
- By regime: calm +0.3 kn (1h); gradient -11.7 kn (1h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **1/1 hours (100%)**
- Confusion (predicted→measured): calm->calm ×1

**3. Lessons learned**
- Correction was roughly neutral (issued 6.0 kn vs raw 6.0 kn).
- Issued forecast OVER-predicted by 5.7 kn on average → biases nudged down.
- Measured gusts ran 3.36× the model (stronger); gust ratio updated.
- Regime call was right 1/1 hours (100%) vs the measured wind direction.
- Worst hour was 19:00 (gradient): predicted 2.4 kn, measured 14.1 kn (Δ -11.7 kn).

**4. How the prediction mechanism was updated** (RLS regression `corrected = a + b·model`, per regime×hour)
```
 bucket        | model_err |   a: before->after |   b: before->after | n
 --------------|-----------|--------------------|--------------------|--
 calm|18       |    -0.30  | +0.00 -> -0.20   | 1.00 -> 0.97    | 1
 gradient|19   |   +11.70  | +0.00 -> +6.98   | 1.00 -> 2.26    | 1
```
_The correction **scales with** the model (b·model) rather than adding a flat offset, so it neither double-counts nor over-adds. 2 calibrated buckets._
