# Improvement plan — wind prediction & self-learning

Goal: move from a hand-rule regime switch + additive-constant bias to a properly
verified, predictor-conditioned **probabilistic** post-processing system, and fix
the dynamical underplay of terrain-driven wind — without pretending small data can
feed big models.

## Guiding principles
1. **Verify before believing.** Every change is scored **out-of-sample** with **CRPS**
   (+ MAE/RMSE, reliability, PIT) and must beat **persistence** and **climatology**.
2. **Match method to data volume.** Adaptive filter now → MOS/EMOS at weeks–months →
   ML at a year+. No false sophistication on a handful of days.
3. **LLM proposes, deterministic backtest disposes.** Numbers stay reproducible;
   parameters live in a versioned `config/params.json`; the analyst never writes
   production numbers directly.
4. **Separate concerns:** dynamical forecast (+ downscaling) → one probabilistic
   post-processor that *learns driver weights* → verification. The regime rules
   become interpretable **features/priors**, not a hard winner-take-all gate.

---

## Phase 0 — Verification harness & baselines  *(do first; it referees everything)*
**Why:** we currently only track MAE; we can't tell if any change actually helps, and
we have no baseline. Nothing else should ship before this exists.

**Tasks**
- `lib/verify.py`: rolling-origin / leave-one-day-out evaluation from the logged
  forecasts + measured obs.
- Metrics: MAE, RMSE, mean bias, **CRPS** (probabilistic); **reliability diagram**,
  **PIT histogram**, per-regime and per-hour skill.
- Baselines: **persistence** (last/҆previous-day measured) and **climatology**
  (per hour-of-day and month mean/quantiles).
- **Skill scores** vs baselines (MAE-SS, CRPS-SS); a change is only "better" if SS > 0.
- A **verification page** on the site: skill over time vs baselines.

**Acceptance:** can score any model version out-of-sample and rank it against
persistence/climatology. **Honest expectation:** with today's data, climatology likely
still wins — the page will say so.
**Data prerequisite:** works from day one.

---

## Phase 1 — Adaptive Kalman regression  *(replace the additive fudge; correct for low data)*
**Why:** the additive per-(regime×hour) offset with ~2-day memory doesn't scale with
the forecast, doesn't weight drivers, and forgets trends. The literature's low-data
method is an **adaptive Kalman-filtered regression**.

**Tasks**
- `lib/postproc.py`: per-hour Kalman filter over regression state `(a_h, b_h)` so
  `corrected = a_h + b_h · raw`; recursively updated each day, tunable process/observation
  noise (memory), capped.
- Retire the additive-constant path in `learn.py` (keep the diff/large-miss reporting).
- Wire through the Phase-0 harness: must beat raw ICON-D2 **and** persistence.

**Acceptance:** out-of-sample MAE/CRPS improvement over raw + persistence on the harness.
**Data prerequisite:** usable now (adaptive, short window).

---

## Phase 2 — Better inputs  *(dynamical + data quality; usable now)*
**Why:** statistics can't recreate flow the 2.2 km model never resolved, and bad obs
poison learning.

**2a. Observations & QC**
- On-lake feeds for **Kochelsee & Ammersee** (Holfuy / Windguru / pioupiou / SUKI /
  addicted-Kochelsee) so all three learn against real lake wind.
- `lib/obs_qc.py`: reject flatlines, dropouts, calm-wind direction noise; flag
  single-sensor exposure bias (Urfeld ≠ Sachenbach).

**2b. Terrain downscaling** *(the dynamical root-cause fix)*
- Diagnostic **mass-consistent wind model (WindNinja-style) over a fine DEM**, seeded
  by ICON-D2 → recover Kesselberg channeling & the Jochberg–Herzogstand nozzle.
- Add downscaled wind as a predictor into the post-processor.

**2c. Poor-man's ensemble**
- Use ICON-D2-**EPS mean + spread** as predictors (not just the deterministic member);
  add a **time-lagged ensemble** (last 2–3 runs) and a second model (ICON-EU / AROME
  where covered).

**Acceptance:** each addition improves CRPS on the harness; obs QC reduces learning noise.
**Data prerequisite:** none — usable now.

---

## Phase 3 — Probabilistic post-processing (EMOS)  *(the main statistical upgrade)*
**Why:** wind is non-negative and skewed; we should predict a **calibrated distribution**
and learn the **relative weight of each driver** from measurements.

**Tasks**
- **EMOS / non-homogeneous regression**: truncated-normal (or gamma), parameters linked
  to predictors — raw/downscaled wind, EPS spread, **Δθ, föhn_gradient, radiation,
  925/850-hPa, hour, month** — **fit by CRPS**.
- **Soft, locally-calibrated regime:** logistic regression `P(föhn | Δp, ridge-normal
  850, Δθ, …)` trained on *observed* föhn (measured S-direction + warming/RH-drop);
  feed regime probabilities as features instead of a hard switch.
- **Direction** via circular stats (von Mises); **gusts** via a gust-factor model
  conditioned on stability (not a flat ratio).
- **Seasonality/trend:** rolling multi-month window + month/day-length predictors →
  detects e.g. "föhn runs hotter in autumn."
- Turns the site's P10/P50/P90 into *calibrated* bands.

**Acceptance:** beats Phase 1 and climatology on CRPS + reliability out-of-sample.
**Data prerequisite:** ~**4–8 weeks** of matched forecast/obs pairs.

---

## Phase 4 — ML post-processing + autonomous tuning  *(data-gated)*
**Why:** nonlinear driver interactions and boundary-layer transitions are learnable
once enough data exists (cf. DWD wind-gust studies).

**Tasks**
- **Gradient-boosted EMOS** and/or **distributional regression network (DRN)** over the
  full predictor set; captures nonlinear + seasonal structure.
- **LLM analyst → backtest gate → config**: analyst proposes *structural* changes (new
  predictors, regime redefinition, thresholds); the deterministic backtest accepts only
  out-of-sample improvers; accepted deltas auto-commit to `config/params.json` with
  provenance. Structural changes stay human-reviewed.

**Acceptance:** beats Phase 3 on CRPS; every auto-applied change has a logged
backtest gain.
**Data prerequisite:** ~**months to a year** (thousands of samples).

---

## Cross-cutting infrastructure
- **`config/params.json`** — all thresholds/method choices versioned; enables A/B and
  clean rollback (git = provenance).
- **Backtest gate in CI** — no post-processing change ships without an out-of-sample win.
- **Analyst mode ramp:** advisory → auto-apply-if-gate-passes (config only) → structural
  proposals via PR.

## Data-volume gates (honest)
| Phase | Needs | Start |
|---|---|---|
| 0 verification + baselines | any | now |
| 1 Kalman regression | days | now |
| 2 inputs (downscale/ensemble/obs+QC) | none | now |
| 3 EMOS probabilistic | ~4–8 weeks | when history exists |
| 4 ML + autotune | months–1 yr | later |

## Sequencing
`0 (referee) → 1 (honest low-data correction) → 2 (better inputs, parallel) →
3 (EMOS, when data allows) → 4 (ML + autonomy)`.
Phase 0 first, always. Phase 2 can run in parallel with 1. Do **not** jump to 3/4
before the data gate — the harness will show it's worse than climatology if you do.

## Top-3 by leverage
1. **Phase 0** — verification vs persistence/climatology with CRPS (the referee).
2. **Phase 3** — predictor-conditioned probabilistic post-processing (learns driver
   weights + trends; the real fix for the additive-fudge complaint).
3. **Phase 2b** — terrain downscaling (fixes what statistics can't: unresolved channeling).
