# Where the accuracy actually comes from

Measured, not argued. Every number below came out of `lib/verify.py` on replayed real
data, using `lib/simulate.py live` and `lib/backfill.py` to reconstruct history.

## The finding

| lever | effect on MAE | evidence |
|---|---|---|
| **Ground truth being right** | **4.67 → 2.71 kn (+42 %)** | Ammersee fallback calibrated to the on-lake buoy, 11,774 paired hours, validated on held-out later dates |
| **The learned bias correction** | **3.01 → 1.90 kn (+37 %)** | Walchensee, 57 days Oct–Dec 2025, walk-forward replay with the correction on vs off |
| Tuning the six regime thresholds | **~0.006 – 0.029 kn (<0.5 %), CI includes zero** | every candidate, both Alpine-rim lakes, June–July *and* Oct–Dec |

The first two are worth roughly **1–2 knots**. The third is worth roughly **nothing**.

## Why the thresholds don't matter

The correction is a per-`(regime × hour)` regression `corrected = a + b·model`. Moving a
threshold only changes which *bucket* an hour is filed under — and each bucket then learns
its own `a` and `b` from whatever lands in it. So the label barely matters: as long as
similar hours group together, the regression absorbs the error either way.

This was tested where it should have mattered most and still didn't:

- **June–July** (thermal season): föhn fired 5 hours out of ~1,300. Nothing to tune.
- **Oct 15 – Dec 10** (föhn season): föhn fired **219 hours, 16 % of the period** — plenty
  of signal — and `FOEHN_DP_RIM` 4.0→4.5 still moved CRPS by **+0.006 kn** with the
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
   from fixing *what we compare against*, not from tuning. The Kochelsee sectors are still
   inherited from Urfeld and unvalidated; Walchensee/Kochelsee have no equivalent of the
   Ammersee buoy calibration.
3. **Leave the thresholds alone.** They came from published Swiss föhn work; the data says
   they are not the constraint.

## Honest caveats

- Replayed forecasts come from Open-Meteo's archive, which offers no lead-time pinning, so
  they are shorter-lead than a real 05:00 run, and single-source rather than the four-source
  blend. Both hit the compared arms identically, so the *relative* numbers above hold; the
  absolute MAEs are optimistic.
- The 42 % truth-source figure is Ammersee-only, and the calibrated fallback is still an
  estimate (2.71 kn residual) rather than real on-lake measurement.
- Ammersee föhn never fires at all (Δp peaks at 7.5 hPa against a 8.0 threshold), so its
  föhn parameters are untestable there by construction, not by accident.
