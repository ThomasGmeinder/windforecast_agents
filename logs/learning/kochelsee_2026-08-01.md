### Learning report — kochelsee — learned from 2026-08-01
Actual source: addicted-sports on-lake (kochelsee/trimini) · matched 24 hours

**1. Prediction vs measured (per hour)** — Δ = prediction − measured (kn)
```
 Hr | Regime   | Pred | Raw  | Meas | Δiss | Δraw | Pdir Adir Δ°
 ---|----------|------|------|------|------|------|-------------
 00 | gradient |  2.1 |  2.1 | 14.8 | -12.7 | -12.7 |    S  SSW   7
 01 | gradient |  2.6 |  2.6 | 17.3 | -14.7 | -14.7 |  SSW   SW  19
 02 | gradient |  3.2 |  3.2 | 15.6 | -12.4 | -12.4 |  SSE    S  25
 03 | gradient |  3.0 |  3.0 |  1.8 | +1.2 | +1.2 |  NNW  NNW  14
 04 | gradient |  4.2 |  4.2 |  1.2 | +3.0 | +3.0 |   SE    E  59
 05 | gradient |  4.2 |  4.2 |  0.3 | +3.9 | +3.9 |   SE    E  26
 06 | gradient |  4.3 |  4.3 |  0.4 | +3.9 | +3.9 |  ESE  ESE   6
 07 | calm     |  1.4 |  1.4 |  1.4 | +0.0 | +0.0 |  ESE    E  16
 08 | calm     |  0.8 |  0.8 |  5.2 | -4.4 | -4.4 |  NNE   NE  31
 09 | gradient |  2.2 |  2.2 |  5.7 | -3.5 | -3.5 |    W   SW  59
 10 | thermal  |  3.3 |  3.3 |  1.6 | +1.7 | +1.7 |    N    N  16
 11 | gradient |  2.7 |  2.7 |  1.8 | +0.9 | +0.9 |    N    N   7
 12 | gradient |  2.3 |  2.3 |  1.6 | +0.7 | +0.7 |    N    N   9
 13 | gradient |  3.4 |  3.4 |  2.0 | +1.4 | +1.4 |  NNW  NNW   2
 14 | gradient |  3.6 |  3.6 | 11.7 | -8.1 | -8.1 |  WNW    W  13
 15 | gradient |  9.5 |  9.5 |  6.6 | +2.9 | +2.9 |  SSE  SSE   9
 16 | thermal  |  5.6 |  5.6 |  3.4 | +2.2 | +2.2 |    S    S  11
 17 | gradient |  5.0 |  5.0 |  1.6 | +3.4 | +3.4 |  SSW    S  25
 18 | gradient |  3.0 |  3.0 |  4.8 | -1.8 | -1.8 |  SSW    S   9
 19 | gradient |  3.3 |  3.3 |  2.5 | +0.8 | +0.8 |   SW    N 124
 20 | calm     |  0.9 |  0.9 |  1.5 | -0.6 | -0.6 |  SSW    S  20
 21 | gradient |  3.0 |  3.0 |  1.0 | +2.0 | +2.0 |  SSW  SSW  12
 22 | calm     |  1.6 |  1.6 |  0.3 | +1.3 | +1.3 |    S    W 103
 23 | calm     |  0.6 |  0.6 |  0.4 | +0.2 | +0.2 |  SSE  WNW 139
```

**Large misses (|Δ| > 5 kn) — difference, lesson & fix applied**
```
 Hr | Regime   | Pred | Meas |   Δ  
 ---|----------|------|------|------
 00 | gradient |  2.1 | 14.8 | -12.7
 01 | gradient |  2.6 | 17.3 | -14.7
 02 | gradient |  3.2 | 15.6 | -12.4
 14 | gradient |  3.6 | 11.7 |  -8.1
```
- **00:00 (gradient)** — under-predicted — forecast 2.1 kn vs measured 14.8 kn (-12.7 kn). *Lesson:* the model underplays the 'gradient' regime around 00:00. *Fix:* the (regime × hour) bias bucket moved 0.0→12.7 kn, so future 'gradient' forecasts at 00:00 are nudged up ~12.7 kn.
- **01:00 (gradient)** — under-predicted — forecast 2.6 kn vs measured 17.3 kn (-14.7 kn). *Lesson:* the model underplays the 'gradient' regime around 01:00. *Fix:* the (regime × hour) bias bucket moved 0.0→14.7 kn, so future 'gradient' forecasts at 01:00 are nudged up ~14.7 kn.
- **02:00 (gradient)** — under-predicted — forecast 3.2 kn vs measured 15.6 kn (-12.4 kn). *Lesson:* the model underplays the 'gradient' regime around 02:00. *Fix:* the (regime × hour) bias bucket moved 0.0→12.4 kn, so future 'gradient' forecasts at 02:00 are nudged up ~12.4 kn.
- **14:00 (gradient)** — under-predicted — forecast 3.6 kn vs measured 11.7 kn (-8.1 kn). *Lesson:* the model underplays the 'gradient' regime around 14:00. *Fix:* the (regime × hour) bias bucket moved 0.0→8.1 kn, so future 'gradient' forecasts at 14:00 are nudged up ~8.1 kn.

**2. Accuracy summary**
- Mean abs error: issued forecast **3.65 kn** vs raw model 3.65 kn
- Mean signed error (bias): -1.2 kn (over-predicting)
- Direction mean abs error: 32°
- Gust ratio (measured/model): 1.69×
- By regime: calm -0.7 kn (5h); gradient -1.71 kn (17h); thermal +1.95 kn (2h)

**2b. Regime validation** (predicted regime vs measured wind-direction sector)
- Regime accuracy: **5/22 hours (23%)**
- Confusion (predicted→measured): calm->calm ×4; calm->thermal ×1; gradient->calm ×8; gradient->foehn ×4; gradient->gradient ×1; gradient->thermal ×2; thermal->calm ×1; thermal->foehn ×1
- Mismatched hours: 00h gradient→foehn (SSW); 02h gradient→foehn (S); 03h gradient→calm (NNW); 04h gradient→calm (E); 05h gradient→calm (E); 06h gradient→calm (ESE); 08h calm→thermal (NE); 10h thermal→calm (N)

**3. Lessons learned**
- Correction was roughly neutral (issued 3.65 kn vs raw 3.65 kn).
- Issued forecast OVER-predicted by 1.2 kn on average → biases nudged down.
- 'gradient' hours (17h) over-predicted by 1.71 kn → those regime buckets shifted most.
- 'thermal' hours (2h) under-predicted by 1.95 kn → those regime buckets shifted most.
- Measured gusts ran 1.69× the model (stronger); gust ratio updated.
- Regime call was right 5/22 hours (23%) vs the measured wind direction.
- Regime miss: predicted 'gradient', measured 'foehn' (4×).
- Regime miss: predicted 'gradient', measured 'calm' (8×).
- Regime miss: predicted 'gradient', measured 'thermal' (2×).
- ⚠ predicted 'thermal' but measured direction was 'foehn' (1×) — the föhn/thermal ANTI-CORRELATION; re-check the Kochelsee↔Walchensee split.
- Worst hour was 01:00 (gradient): predicted 2.6 kn, measured 17.3 kn (Δ -14.7 kn).

**4. How the prediction mechanism was updated** (EWMA α=0.3, per regime×hour)
```
 bucket        | model_err | bias: before -> after | gustR: before -> after | n
 --------------|-----------|-----------------------|------------------------|--
 gradient|00   |   +12.70  |  +0.00 -> +12.70      |  1.00 ->  4.55          | 1
 gradient|01   |   +14.70  |  +0.00 -> +14.70      |  1.00 ->  4.27          | 1
 gradient|02   |   +12.40  |  +0.00 -> +12.40      |  1.00 ->  5.76          | 1
 gradient|03   |    -1.20  |  +0.00 ->  -1.20      |  1.00 ->  2.55          | 1
 gradient|04   |    -3.00  |  +0.00 ->  -3.00      |  1.00 ->  0.57          | 1
 gradient|05   |    -3.90  |  +0.00 ->  -3.90      |  1.00 ->  0.42          | 1
 gradient|06   |    -3.90  |  +0.00 ->  -3.90      |  1.00 ->  0.72          | 1
 calm|07       |    +0.00  |  +0.00 ->  +0.00      |  1.00 ->  0.78          | 1
 calm|08       |    +4.40  |  +0.00 ->  +4.40      |  1.00 ->  2.79          | 1
 gradient|09   |    +3.50  |  +0.00 ->  +3.50      |  1.00 ->  3.45          | 1
 thermal|10    |    -1.70  |  +0.00 ->  -1.70      |  1.00 ->  0.82          | 1
 gradient|11   |    -0.90  |  +0.00 ->  -0.90      |  1.00 ->  0.55          | 1
 gradient|12   |    -0.70  |  +0.00 ->  -0.70      |  1.00 ->  0.80          | 1
 gradient|13   |    -1.40  |  +0.00 ->  -1.40      |  1.00 ->  1.07          | 1
 gradient|14   |    +8.10  |  +0.00 ->  +8.10      |  1.00 ->  2.32          | 1
 gradient|15   |    -2.90  |  +0.00 ->  -2.90      |  1.00 ->  0.91          | 1
 thermal|16    |    -2.20  |  +0.00 ->  -2.20      |  1.00 ->  0.51          | 1
 gradient|17   |    -3.40  |  +0.00 ->  -3.40      |  1.00 ->  0.68          | 1
 gradient|18   |    +1.80  |  +0.00 ->  +1.80      |  1.00 ->  1.76          | 1
 gradient|19   |    -0.80  |  +0.00 ->  -0.80      |  1.00 ->  1.67          | 1
 calm|20       |    +0.60  |  +0.00 ->  +0.60      |  1.00 ->  0.40          | 1
 gradient|21   |    -2.00  |  +0.00 ->  -2.00      |  1.00 ->  0.24          | 1
 calm|22       |    -1.30  |  +0.00 ->  -1.30      |  1.00 ->  0.34          | 1
 calm|23       |    -0.20  |  +0.00 ->  -0.20      |  1.00 ->  2.53          | 1
```
_Result: today's forecast adds these per-(regime×hour) biases to the raw model. Model now holds 24 calibrated buckets._
