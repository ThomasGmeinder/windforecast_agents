# Where the accuracy actually comes from

Measured, not argued. Every number below came from real data, not from reasoning about the
system:

- the **forecast-accuracy** figures from `lib/verify.py` on replayed history, using
  `lib/simulate.py live` and `lib/backfill.py` to reconstruct past days;
- the **truth-source** figures from pairing each candidate station against the GKD
  Ammerseeboje archive (`lib/obs_calib.py`, `lib/bsv.py`), scored on held-out later dates.

Read ["How big a sample has to be before it means anything"](#how-big-a-sample-has-to-be-before-it-means-anything)
before trusting a comparison in this file that you have re-run on a short window. Measuring
is necessary and was not, on its own, sufficient.

## The finding

| lever | effect on MAE | evidence |
|---|---|---|
| **Ground truth being right** | **4.67 → 2.71 kn (+42 %)** | Ammersee fallback calibrated to the on-lake buoy, 11,774 paired hours, validated on held-out later dates |
| **The learned bias correction** | **3.01 → 1.90 kn (+37 %)** | Walchensee, 57 days Oct–Dec 2025, walk-forward replay with the correction on vs off |
| **Blending two truth sources** | **2.79 → 2.64 kn (+5.3 %)** | Ammersee, 1,273 held-out hours where buoy + BSV + DWD all exist; mean of the two calibrated shore stations vs the better one alone |
| Tuning the six regime thresholds | **~0.006 – 0.029 kn (<0.5 %), CI includes zero** | every candidate, both Alpine-rim lakes, June–July *and* Oct–Dec |

The first two are worth roughly **1–2 knots**. The third is a real but modest further gain
on the same lever — truth. The fourth is worth roughly **nothing**.

Note the first and third rows are measured on *different* test sets and do not compose:
+42 % is the DWD calibration scored on its own held-out split of 11,774 pairs, while +5.3 %
is the blend scored on the 1,273 hours where all three sources overlap. Both are honest;
adding them is not.

## How big a sample has to be before it means anything

This document says "measured, not argued". Here is a case where something *was* measured,
convincingly, and was still wrong — worth more than any single number above.

Looking for a replacement truth source while the buoy is down, BSV Herrsching and DWD
Wielenbach were compared against the buoy over **five days, 109 paired hours**:

| sample | BSV raw | DWD raw | apparent conclusion |
|---|---|---|---|
| 5 days, **n = 109** | r = 0.486 | r = 0.196 | BSV tracks **2.5× better**; Wielenbach is useless |
| full overlap, **n = 4,746** | r = **0.651** | r = **0.660** | they are **the same**, DWD marginally ahead |

The small sample did not merely exaggerate the effect. **It inverted the ranking.** It also
came with a vivid supporting anecdote — on 2026-06-05 Wielenbach's highest reading of the
day (6.0 kn at 16:00) fell on the lake's calmest hour (1.4 kn), while at 06:00 the lake blew
17.9 kn and it reported 1.9 — which made the wrong conclusion feel obvious. That day was
real. It was also one day.

Acting on it would have deleted a working source. What saved it was re-running the
comparison on the full overlap before changing anything, which is also what surfaced the
blend as the actually-better answer.

Practical floor, from this and the threshold work: **a few hundred paired hours is not
enough to rank two noisy sources.** The backtest gate already encodes this for parameters
(`N_MIN_BACKTEST_DAYS`, `N_MIN_BACKTEST_PAIRS`, plus a block bootstrap over whole days,
because hours within a day are not independent). Source comparisons had no such gate and
were done by hand. They should be held to the same standard.

## Not every win shows up in MAE

Two changes in this project improved the product without moving the accuracy numbers at
all, and a leverage document that only tracks MAE would score them at zero:

- **A published 67.9 kn gust** on a lake whose highest measured gust in 1,552 hours is
  20.8 kn. It came from a `gust_ratio` of 3.18 learned from three observations and applied
  at full weight, uncapped. Its contribution to MAE is nil — gusts are not in the CRPS
  scorecard — but it is the single most visibly wrong number the site ever showed.
- **A crisp compass bearing during the evening reversal**, when the two Alpine lakes
  disagreed by 83° for the same hour with ensemble spread as large as the wind itself.

Both are *correctness*, not accuracy. The lesson for prioritisation: the scorecard measures
what it measures, and "the forecast is not obviously absurd" is not one of those things.

## Why the thresholds don't matter

The correction is a per-`(regime × hour)` regression `corrected = a + b·model`. Moving a
threshold only changes which *bucket* an hour is filed under — and each bucket then learns
its own `a` and `b` from whatever lands in it. So the label barely matters: as long as
similar hours group together, the regression absorbs the error either way.

This was tested where it should have mattered most and still didn't:

- **June–July** (thermal season): föhn fired 5 hours out of ~1,300. Nothing to tune.
- **Oct 15 – Dec 10** (föhn season): föhn fired **219 hours, 16 % of the period** — plenty
  of rule-positive hours for a sensitivity test — and `FOEHN_DP_RIM` 4.0→4.5 moved
  paired walk-forward MAE by only **+0.006 kn**, with the
  confidence interval straddling zero. `FOEHN_850_KN` 7→8: +0.029 kn, also straddling.
- **March–April**: föhn 1.4 % of hours. Untestable again.

So the gate refusing every proposal is **not** the gate being too strict. It is the gate
correctly reporting that these knobs have no measurable leverage.

## What this means for the self-tuning loop

The loop works — `lib/simulate.py offline` drives it end to end and it applies, opens a
hypothesis, withholds it from early review and then reviews it. But pointed at the current
`TUNABLE` set it will approximately never fire, because there is nothing there to win.

Options, in order of expected value:

1. **Point the tuner at levers that move.** The blend is currently equal-weight across
   ICON-D2 EPS / deterministic / ICON-EU / addicted-sports; per-regime source weights are a
   real degree of freedom. So are `postproc.FORGET`, `BIAS_CAP_KN` and `N_MIN_OBS`, which
   govern the correction that is demonstrably worth 37 %.
2. **Spend the effort on truth and inputs instead.** The single biggest measured win came
   from fixing *what we compare against*, not from tuning — and truth has since paid out a
   second time, via the blend (+5.3 %). The Kochelsee sectors are still inherited from Urfeld
   and unvalidated; Walchensee/Kochelsee have no equivalent of the Ammersee buoy calibration,
   and no second source to blend with either. Both are on-lake feeds from the same operator,
   so a Walchensee/Kochelsee blend would not have the independent-error property that makes
   the Ammersee one work.
3. **Leave the thresholds alone.** They came from published Swiss föhn work; the data says
   they are not the constraint.

## Honest caveats

- Replayed forecasts come from Open-Meteo's archive, which offers no lead-time pinning, so
  they are shorter-lead than a real 05:00 run, and single-source rather than the four-source
  blend. Both hit the compared arms identically, so the *relative* numbers above hold; the
  absolute MAEs are optimistic.
- The 42 % truth-source figure is Ammersee-only, and the calibrated fallback is still an
  **estimate** (2.71 kn residual) rather than real on-lake measurement. The blend lowers that
  residual to 2.64 kn but does not change its nature: both inputs are sheltered shore
  stations whose range is *compressed*. On 2026-06-05 the buoy spanned 1.4–17.9 kn across the
  day while BSV spanned 2.4–6.2. Calibration restores the average; it cannot recover a
  17.9 kn morning from a 5.3 kn reading, because that information is not in the signal.
  **Ammersee's numbers will get better, discontinuously, when the buoy is repaired** — and
  the `truth_source` event log records which days used which truth, because an error figure
  is not comparable across that boundary.
- The blend's direction and gusts are taken from BSV rather than DWD on the grounds that a
  real sensor at the lake beats one 11 km inland. **That is a physical argument, not a
  measured one** — the buoy reports neither, so there is nothing to validate against.
- Ammersee föhn never fires at all (Δp peaks at 7.5 hPa against a 8.0 threshold), so its
  föhn parameters are untestable there by construction, not by accident.
- The +37 % bias-correction figure is for the **mean wind** (the RLS regression). The
  multiplicative gust half of the same correction was separately broken until 2026-08 and
  was never part of that measurement.
