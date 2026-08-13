# Kochelsee / Walchensee wind-prediction agent

## 🌬️ [**→ Live forecast: thomasgmeinder.github.io/windforecast_agents**](https://thomasgmeinder.github.io/windforecast_agents/)

**[Kochelsee & Walchensee](https://thomasgmeinder.github.io/windforecast_agents/kochel-walchensee.html)**
 · **[Ammersee](https://thomasgmeinder.github.io/windforecast_agents/ammersee.html)**
— rebuilt and republished every morning at ~05:00 Berlin by the daily GitHub Actions run.
Each page shows today's hourly forecast, yesterday's measured wind beside it, and the
self-learning report that connects the two.

> **Hourly-cadence migration status.** The committed production loop is still the daily
> forecast/learn/verify cycle described below. The hourly issuer (`hourly_run.py`) now
> writes timestamped 24-hour records, reconciles measurements, and can make a guarded
> hourly RLS update. Its CRPS verification, lead-time scorecard, tuner input, and Pages
> deployment are still transitioning from the daily path. Do not compare its short leads
> with the legacy daily scorecard as though they measured the same forecast problem.

---

Hourly, next-day surface-wind forecasting for two windsurf lakes at the northern
Alpine rim in Bavaria — **Kochelsee** (~604 m) and **Walchensee** (~800 m) — with a
deterministic forecast engine, a self-learning bias correction that updates every
morning from measured wind, and an on-demand LLM agent that narrates the result.

Kochelsee and Walchensee are **analysed together** (their winds are physically
coupled through the Kesselberg) but **always reported separately**, because under
föhn they behave in opposite ways (see below). A sibling agent handles Ammersee.

---

## 1. The meteorology (why this is hard)

Raw numerical-weather-prediction (NWP) wind is a *first guess, not truth*: the best
freely-available high-res model here (ICON-D2, 2.2 km) sits right at the edge of
resolving valley/thermal winds and systematically **under-plays** them, and handles
föhn and thermals worst of all. The engine therefore assigns a rule-based forecast
**scenario** each hour and corrects the model blend toward measured on-lake wind.

These physical patterns motivate the scenarios, but a scenario label is not proof that
the physical cause occurred:

| Regime | What it is | Effect on the two lakes |
|---|---|---|
| **South föhn** | warm, dry, gusty S down-slope wind when pressure is higher S of the Alps | pours **down the Kesselberg → Kochelsee turns strong**; **suppresses the Walchensee NE thermal** (the anti-correlation). Often an early Kochelsee burst that dies ~09:00 |
| **Thermal ("Walchenseewind")** | NE nozzle wind between Jochberg & Herzogstand, sunny weak-gradient days | Walchensee's reliable summer wind, ~11:00–evening, N–NE; killed by any south föhn |
| **Gradient** | frontal / pressure-driven flow | both lakes feel it; terrain still channels it |
| **Non-föhn fall-winds** | cold-night N-slope drainage off Herzogstand/Heimgarten | up to ~8 Bft into the morning; must not be mislabelled föhn |

Föhn is physically expected to make *Kochelsee strong / Walchensee thermal weak*. The
current code does not impose that opposite response explicitly: lake-specific inputs and
separately learned corrections must represent it. In existing replay tests, changing the
scenario thresholds had little effect on mean-wind accuracy.

---

## 2. Topography findings (terrain locks the wind)

From the topographic map (Kochel am See district):

- **Kochelsee ≈ 604 m**, at the very edge where terrain drops to the flat foreland
  (~590–670 m) → it is the **cold-air drainage collection basin**, and where föhn
  first breaks through to the flatland.
- **Walchensee ≈ 800 m**, an **enclosed basin** ringed by 1500–1800 m peaks
  (Herzogstand/Heimgarten NW, Jochberg N, Karwendel foothills S) with cold deep
  water → strong diurnal decoupling = the reliable thermal engine.
- **Elevation gap ≈ 200 m** → the dry-adiabatic temperature offset is ~2.0 K
  (basis of the stability index in §4).
- **Kesselberg** (B11) is the funnel between the lakes; **Jachenau** opens east.

Because the surface wind is **channelled by terrain**, measured direction is summarized
with this flow-sector map. It is a post-event diagnostic, not an independent physical-
regime observation: in particular S–SE flow may be föhn, gradient flow, or fall-wind.

| Wind from | Conduit | Regime |
|---|---|---|
| **N–NE** (≈340–70°) | Jochberg↔Herzogstand nozzle | **thermal** |
| **S–SE** (≈120–210°) | down the Kesselberg | **föhn / fall-wind** |
| **W–NW** (≈250–335°) | Herzogstand-ridge spillover | **gradient** |
| **E** (≈70–120°) | Jachenau valley | drainage / gradient |
| SW (≈210–250°) | — | transition / uncertain |

Kochelsee currently **inherits** Urfeld's sectors (PROVISIONAL — its thermal is
weaker and its föhn comes over the pass more from the SW; to be calibrated).

---

## 3. Data sources and the role of each

Forecast sources are blended; measurement sources are selected in a quality hierarchy.

### Forecast (the backbone) — an average of multiple predictions
The forecast VALUE is the **equal-weight mean of several sources**, not one run:
- **ICON-D2 ensemble** mean (20 members) + **ICON-D2 deterministic** + **ICON-EU** +
  the **addicted-sports spot forecast** (`winddata.addicted_forecast`, tuned to the
  local thermal). The live pipeline uses Open-Meteo point/ensemble APIs. A raw-GRIB
  reader exists for diagnostics but is not called by `build_table()`. The ensemble
  **speed spread** sets the confidence band.

### Föhn diagnosis
- **DWD MOSMIX** cross-Alpine **Δp = Bozen − München** (`foehn_delta_p`): ≥4 hPa
  noticeable, ≥8 hPa reaches the surface. Best föhn direction is **SE**; SW is flagged
  unreliable.
- **Hohenpeißenberg nowcast** (`winddata.hohenpeissenberg_now`, DWD 02290): a confidence
  cross-check. It can mark a föhn-favourable scenario unconfirmed, but does not change the
  scenario or wind speed.
- **addicted-sports drivers** (`winddata.addicted_drivers`): `foehn_gradient_hpa`,
  `lapse_2m_850`, and radiation are logged/displayed diagnostics, not regression
  predictors. The active 850 hPa wind used by the scenario rule comes from ICON-D2.

### Measured "ground truth" (for bias correction + learning)
- **On-lake Urfeld anemometer** via the reverse-engineered addicted-sports JSON
  endpoint
  `https://www.addicted-sports.com/forecast/walchensee/urfeld/?json=wind&from=YYYY-MM-DD`
  → hourly `mavg` (measured avg kn), `mmax` (gust), `dir`, webcam images, and the
  site's own `mae`/`guete`. `winddata.addicted_measured_hourly`. Daylight hours
  only. **Far better than any DWD station** — it captures the NE nozzle thermal a
  valley station misses. The operator documents its anemometer on a buoy roughly in the
  middle of the lake, about 1.6 m above the water: highly representative of surface wind
  at that buoy, but not a standard 10 m meteorological reference. No public sensor model,
  calibration record, or traceable instrument-accuracy specification was found.
- **Kochelsee / Trimini station** via the same endpoint → `mavg`, `mmax`, and direction.
  The operator documents it at the lake edge on the Kristall Trimini grounds, about 4 m
  above the water. It is the best available local operational truth for that south-shore
  spot, but not a lake-wide or central-water reference: shore/terrain exposure can
  matter, and no public sensor model, calibration record, or traceable accuracy
  specification was found. When it provides no wind speed, the hourly table shows
  **NR** and no forecast-minus-measurement value.
- **Ammerseeboje** (`winddata.gkd_wind_hourly`, GKD Bayern station 16601050) — an
  official buoy **on Ammersee**, hourly, archive back to 2014, CC BY 4.0. It is the ONLY
  buoy in GKD's entire 127-station wind network, so there is no second one to fall back
  to. Speed only, so direction and the gust proxy come from DWD for the same hours.
  Used **by default** for Ammersee, with a validity check (≥3 hours for the requested
  date); if the buoy is down the code falls through to DWD automatically. It is offline
  as of 15.06.2026 with a reported electronics defect, so the fallback is live today.
  Timestamps are Europe/Berlin — verified by cross-correlation against DWD, not assumed.
- **BSV Herrsching** (`lib/bsv.py`) — a sailing club's Davis Vantage on the **east shore of
  Ammersee**, published through a PWS Dashboard fed by WeatherLink Cloud. Its graph page
  embeds the whole series inline at **15-minute resolution** and accepts an explicit
  year/month/day, so any past day is a single request — no polling, no sampler. History
  reaches back to roughly mid-2022. Unlike the buoy it measures **gusts and direction**, not
  just speed. Days are cached and never re-fetched; requests are rate-limited, because it is
  a small club's own server.
- **DWD 10-min obs** (`winddata.dwd_obs_hourly`): Garmisch 01550 (valley proxy) for the
  southern lakes, Wielenbach 05538 for Ammersee (lake-level but ~11 km inland). DWD's own
  station list confirms Wielenbach at 11.0 km is genuinely their **closest** wind station to
  Ammersee — the next is Hohenpeißenberg, a 977 m summit 22 km away.
- **Ammersee truth is a BLEND of the two shore stations while the buoy is down.** Measured on
  1,273 held-out hours against the buoy, both calibrated:

  | source | out-of-sample MAE vs the buoy |
  |---|---|
  | BSV Herrsching, calibrated | 2.915 kn |
  | DWD Wielenbach, calibrated | 2.788 kn |
  | **mean of the two** | **2.639 kn** |

  Neither shore station wins alone; averaging does, because they sit on opposite sides of
  the lake and much of their error is independent local noise. Speed is the blend;
  **direction and gusts come from BSV**, which has a real sensor at the lake — though note
  that part is a physical argument, not a measured one, because the buoy provides no
  direction or gust to validate against.

  **A correction worth recording.** An earlier version of this README claimed Wielenbach
  reads "just 53 % of the on-lake wind" and that BSV tracked three times better
  (r=0.486 vs 0.171). Both figures came from samples of ~110 hours and **neither survives the
  full test**: over 4,746 paired hours the two stations track the lake almost identically
  (r=0.651 vs 0.660). Small samples lie, and this one lied convincingly enough to nearly get
  a working station deleted.
- **Fallback calibration** (`lib/obs_calib.py`, `models/ammersee_fallback_calib.json`):
  while the buoy is down, the Wielenbach reading is mapped onto lake-equivalent wind by a
  per-hour-of-day linear fit `lake ≈ a_h + b_h·station`, learned from **11,774 paired
  hours** and validated on held-out later dates: **MAE 4.67 → 2.71 kn (+42 %)**. The fit
  is mostly an *offset* (global `+5.06 + 1.06·x`) — above a baseline the two track ~1:1,
  but the lake carries wind the sheltered station never sees, which is why naive ratio
  scaling gained only 6 %. The correction is applied ONLY if that out-of-sample check
  passed (`apply` flag in the model file), never to buoy data, and the source string
  always says when a value was corrected. Rebuild:
  `python lib/obs_calib.py build ammersee`.

  **This made the scorecard look worse, which is the point.** Graded against the honest
  lake-equivalent truth Ammersee's CRPS went 1.46 → 4.19 kn and a **−3.97 kn
  under-forecast bias** became visible that the low inland truth had been hiding. An
  independent consistency check supports the correction: the on-lake climatology now fits
  the truth 36 % better (CRPS 2.45 → 1.57) than it did against raw inland values.
- **`winddata.measured_source(lake, date)` is the single authority** for which source a
  lake/date uses, returning a stable machine id. Order, best first:
  `buoy+bsv` / `buoy+dwd` → `ads` → `blend` → `dwd` → `bsv` → `none`.
  `actual_hourly` is a thin wrapper over it. **The buoy is retried on every single call and
  wins outright the moment it reports again**, so recovery needs no intervention.
- **`lib/buoywatch.py` makes that visible.** It runs as STEP 0 of the daily job and does
  *not* re-implement the choice — it asks `measured_source` what it picked, compares against
  the last recorded status, and logs:
  - `truth_source` — a daily heartbeat: source id, whether the buoy answered, whether the
    day is a real on-water measurement or a calibrated estimate, hours covered, and a
    plain-English explanation;
  - `truth_source_change` — **loud**, on any transition, naming both sides and whether it
    was a recovery or a degradation.

  This matters beyond tidiness: **a scorecard is not comparable across a change of source.**
  Ammersee's truth has already changed hands mid-archive, and every day now records which
  truth produced it.

### Stability (the thermal/föhn master switch)
- **Kochel–Walchensee Δθ** (`winddata.stability_dtheta`): the two-lake
  potential-temperature difference,
  `Δθ = (T_Walchensee − T_Kochelsee) + 9.8·Δz/1000`, from Open-Meteo T2m.
  Δθ≈0 neutral / föhn-mixed; **Δθ > ~1.5 K = stable cold-air pool → thermal capped**
  (the dead-Kochelsee-morning signature); Δθ < 0 = unstable → thermal favoured.

---

## 4. The forecast engine (`lib/forecast.py`) — single source of truth

`build_table(lake, date)` produces the hourly table; both the automated 6 a.m. job
and the LLM agent call it, so numbers never disagree. Per hour it:

1. Pulls ICON-D2 from Open-Meteo for the lake gridpoint.
2. Attaches augmentation features: MOSMIX Δp, addicted föhn drivers, and the Δθ
   stability index.
3. **Assigns a forecast scenario** (`classify_regime`, first matching rule):
   - **föhn-favourable** if Δp ≥ threshold **and** 850 hPa southerly (120–240°) **and** ≥ ~7 kn;
   - **strong-gradient** if 925 hPa flow ≥ ~12 kn;
   - **thermal-favourable** if daytime + low cloud + weak gradient **and not** a cold pool —
     if Δθ ≥ `COLD_POOL_DTHETA` (1.5 K) and model wind is light it is downgraded to
     **"cold-pool capped"** calm;
   - else calm.
4. **Applies the learned regression** `corrected = a + b·model` for that
   (scenario × hour-of-day) bucket (`apply_bias` → `postproc`). Föhn indicators select
   the bucket; they are not continuous regression predictors and the model does not
   estimate how many knots föhn caused. Scaling avoids a fixed scenario bonus but cannot
   guarantee that over-correction is impossible; rows before calibration are flagged
   "raw (no local calib yet)". The **mean** adjustment is bounded additively by
   `BIAS_CAP_KN` (±4 kn) — dimensionally right for an additive correction. The tighter cap
   was selected after recent small-bucket over-corrections: it improves the available
   leak-free replay for both Alpine lakes and prevents a learned local adjustment from
   adding a 5–8 kn jump to the raw model.
5. **Guards the gust**, which is a *multiplicative* correction (`raw × gust_ratio`) and so
   needs different bounds than the mean:
   - **plausibility** — a stored ratio outside `[0.6, 1.8]` is *refused* and the raw model
     gust published instead. The band is Ammersee's own empirical range across 23
     buoy-verified buckets, not a guess. A refusal is shown as a note, never silently.
   - **ceiling** — the published gust is bounded by `1.5 ×` the hour's own ICON-D2-EPS gust
     members (already downloaded, previously discarded), hard-limited at Beaufort 12. This
     is seasonally self-adjusting, which a constant cannot be.

   *Why both:* the ratio check catches a bad correction; only the ceiling catches a
   pathological **raw** value with a blameless ratio — the worst on record is a 54.8 kn raw
   blend gust at Ammersee, a lake whose highest measured gust in 1,552 hours is 20.8 kn.
6. **Confidence** from ensemble spread + calibration state; föhn capped at "med".
7. **Withholds the direction** as `VAR` when ensemble *speed* spread crosses the same threshold
   `_confidence` uses for its "low" band. At the evening thermal reversal the wind passes
   through near-zero and the modelled bearing swings freely: on 2026-08-05 at 19:00
   Walchensee read 201° and Kochelsee 284° — 8 km apart, same model run, ~5 kn spread on a
   ~5 kn wind — and both settled to ~172° by 21:00. `dir_label` is the single authority, so
   the text table, the HTML and the headline cannot disagree.
8. Surfaces the drivers in the table Note column (`Δθ±x.x`, `fg±x.x`).

Scenario thresholds (`FOEHN_DP_*`, `FOEHN_850_KN`, `GRADIENT_925_KN`,
`THERMAL_CLOUD_MAX`, `COLD_POOL_DTHETA`) start from published/provisional values. Daily
learning does not change them; the optional tuner may propose bounded changes that must
pass a paired walk-forward MAE gate. `SW_SECTOR` is fixed, not tuner-writable.

Output is knots with Beaufort in brackets, e.g. `9.2 (3)`, and gusts in knots.

---

## 5. The self-learning mechanism (`lib/learn.py`)

Every morning, **before** the new forecast, for each lake:

1. **Compares** yesterday's logged forecast (issued *and* raw model) to yesterday's
   **measured** wind, hour by hour.
2. **Logs the diffs** — machine-readable `logs/<lake>_diffs.jsonl` and a
   human-readable report `logs/learning/<lake>_<date>.md`.
3. **Explains** — an accuracy summary (MAE issued vs raw, signed bias, per-regime,
   direction error, gust ratio), plus auto-derived plain-language **lessons**.
4. **Flow-sector check** — maps measured direction into a terrain sector and compares it
   with the forecast scenario. This does not confirm a physical regime and cannot by
   itself distinguish föhn from drainage/fall-wind.
5. **Updates the mechanism** — a **recursive-least-squares regression**
   `corrected = a + b·model` per (scenario × hour-of-day) (`postproc`), with a prior
   of (a=0, b=1) = "trust the model", a forgetting factor, and a cap; plus a gust
   factor. The report shows the exact **a/b before → after** for every bucket.

The two comparisons have different jobs: the published residual
`issued forecast − measured wind` is retained for the scorecard and the ±5 kn
large-miss explanation; the regression learns from `raw model` and `measured wind`:
`measured ≈ a + b·raw model`. It does **not** fit yesterday's already-corrected
issued value, so it cannot repeatedly correct its own previous adjustment.

The regression **scales with** the model rather than adding a fixed scenario bonus,
converges toward systematic bias, and is evidence-ramped (one day
barely moves the applied correction). Idempotent: each date is learned once per lake. The
forecast reads the just-updated model in the same run, so today benefits
immediately.

To keep one burst or timing mismatch from corrupting a sparse bucket, each RLS update
clips its innovation to ±4 kn and constrains the wind-speed scaling coefficient to
`0 ≤ b ≤ 2`. The resulting learner is a robust online approximation to weighted
least squares, rather than the exact unconstrained least-squares minimum. Repeated
evidence can still move the correction; an isolated 6–10 kn residual cannot create a
negative or extreme slope.

Only the first forecast issued for a date is eligible, and hours that had already elapsed
when it was issued are recorded but neither scored nor learned from.

**Bias buckets are keyed by the forecast scenario.** The direction-sector section is a
diagnostic comparison, not a statement that the physical cause was observed.

---

## 5b. Verification — the referee (`lib/verify.py`)

Layer 1 (above) learns; this layer decides **whether any of it actually helps**. Every
morning the logged forecasts are scored out of sample against the measured wind:

- **CRPS** (Continuous Ranked Probability Score, in knots, lower is better) — the
  probabilistic generalization of MAE. For a single-number forecast CRPS *equals* the
  absolute error; for a distribution it also grades whether the spread was honest, so
  over-confidence is penalised and well-placed uncertainty is rewarded. To make that
  meaningful each hour stores a predictive distribution: the ICON-D2 ensemble deciles
  (`q_kn`, P10–P90) recentred on the issued blended value, plus `spread_kn`.
- **Baselines**: *persistence* (yesterday's measured wind at the same hour) and
  *climatology*. For Ammersee the climatology is a real observational archive —
  **93,080 hourly on-lake readings from the Ammerseeboje, 2014–2026**, aggregated to a
  per-(month × hour-of-day) distribution (`lib/climatology.py`,
  `models/ammersee_climatology.json`). It is available from day one, whereas the
  from-logs climatology needs ≥3 prior days at the same hour and is therefore absent
  early on; the other two lakes still use the from-logs version. Rebuild with
  `python lib/climatology.py build ammersee` (~13 requests; the archive is static while
  the buoy is offline). Both baselines are leak-free — a day is scored using only
  strictly earlier data, and the archive climatology **refuses** any date inside its own
  coverage window.
- **Skill score** `SS = 1 − CRPS/CRPS_baseline`; `SS > 0` means we beat that baseline.
  Reported overall, per forecast scenario and per hour.
- **Lead time**: hours that had already elapsed when the forecast was issued are not
  scored or learned from — a 05:00 run does not "predict" 00:00–04:00. The forecast *of
  record* for a date is the EARLIEST one logged, so a better-informed same-day re-run
  cannot replace it. Their measurements are still recorded (the baselines need them).

`python lib/verify.py` runs the self-tests (three independent CRPS implementations agree,
plus a discrimination test that a good forecaster outranks a biased one);
`python lib/verify.py <lake>|all` prints the scorecard. **Honest expectation:** with only
a few days logged the scorecard says `LOW CONFIDENCE` and climatology may well win — the
page will say so rather than flatter the model.

---

## 5c. The self-tuning loop (`lib/tuner.py`) — what the LLM actually does

The LLM is **not** the forecaster and not the learner; it is a *tuner* that is measured
and gated. One call (`tuner.run`) performs a full cycle:

**Input it perceives** (previously: yesterday's errors only)
- a **multi-day error window** (14 days of per-hour forecast-vs-measured errors);
- the **learned regression state** (per-`scenario×hour` `a`, `b`, `n`), so it reasons about
  the *residual* the linear correction cannot absorb;
- the **CRPS scorecard** and current tunable parameters with their legal bounds;
- **its own past proposals**, each with the measured CRPS *before* vs *after* it was
  issued.

**Output it produces**
1. **Reviews** — for every open hypothesis it must return `confirmed` or `retracted`
   with reasoning; the verdict is written back to the ledger (`logs/ledger.jsonl`). This
   is the memory that makes it accountable rather than a fresh opinion each morning.
2. **Proposals** — at most two small parameter changes, each with a rationale and an
   `expected_effect` it will be judged against next time. A proposal that passes the gate becomes an open
   hypothesis; one that is refused is recorded with the gate's reason instead, so the
   analyst is never graded on a change that never took effect.

**What happens to a proposal (the gate).** Nothing is applied on the model's say-so.
Each proposal must pass, in order: the parameter is one of the six known tunables → the
value is numeric and inside `PARAM_BOUNDS` → the step is ≤ 25 % of the current value →
and then a **paired MAE backtest**: every replayable logged day is re-run under the candidate value
(`verify.backtest`, sharing `forecast.replay_hour` with production so a backtest can
never drift from the real path) and it is applied only if the improvement is **statistically significant**: the
per-hour errors of the two arms are compared **pairwise** (same hours, so the weather
cancels), and a block bootstrap over whole DAYS — hours within a day are not independent —
must put the entire 95 % confidence interval below zero, with a mean gain of at least
`MIN_EFFECT_KN`. It also needs `N_MIN_BACKTEST_DAYS` (10) days **and**
`N_MIN_BACKTEST_PAIRS` (60) scored hours. A merely positive point estimate is not
evidence and is refused. A change that passes is written to `config/params_<lake>.json` — per-lake, because
it was only ever verified against that one lake's history — and logged as a `param_change` event with its
evidence. Before activation, calibration is rebuilt from replayable history under the new
scenario labels so production does not cold-start. Anything else is refused, with the
reason recorded.

**Measured leverage (`docs/LEVERAGE.md`).** Replaying real history shows the six tunable
thresholds are worth **<0.5 % of MAE with confidence intervals straddling zero**, even in
föhn season with 219 föhn hours — because the per-(regime×hour) regression absorbs the
error whichever bucket an hour lands in. By contrast the bias correction itself is worth
**+37 %** and getting the ground truth right was worth **+42 %**. The gate refusing every
proposal is therefore not excessive caution; it is an accurate report that these knobs do
not move the forecast. Pointing the tuner at blend weights or the correction's own
constants would give it something real to optimise.

**Currently the gate is effectively dormant**: replayable history (days carrying the
captured classification inputs) only began accumulating recently, so proposals are logged
and reviewed but not applied, and the daily report says exactly that
(`held back … insufficient replayable history (n/N days)`). Agency — memory,
self-evaluation, an objective referee — is live now; authority to change the forecaster
switches on only once the evidence exists. Without a `GEMINI_API_KEY` the whole layer
skips cleanly and the deterministic forecast is unaffected.

---

## 6. Hourly automation (`hourly_run.py` + systemd)

### Hourly trigger

The public hourly path is dispatched by cron-job.org through GitHub
`repository_dispatch`. The hourly workflow deliberately has no GitHub `schedule:`
trigger: GitHub documents scheduled workflows as best-effort, whereas the external
trigger calls the dispatch API at `:55` Europe/Berlin. A manual workflow dispatch is
available for recovery/testing. For a local mirror, install the bundled user units:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/wind-agents-hourly.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wind-agents-hourly.timer
systemctl --user list-timers wind-agents-hourly.timer
```

The timer runs `hourly_run.py` at `:55` Europe/Berlin. It issues the next valid hourly
window locally; it does not dispatch or deploy GitHub Pages.

Each hourly run first reconciles every elapsed forecast-of-record row for which the
measurement source has reported a value. It records forecast-minus-measurement, makes at
most one guarded RLS update per row, scores the hourly record, then issues the next
24-hour window. GitHub Actions commits the resulting hourly logs/model state and deploys
the static Pages report.

`daily_run.py` and `.github/workflows/daily.yml` are retained only as a legacy daily
verification audit. They do not own the production forecast, hourly learner, model state,
or Pages deployment, and their old reports are not displayed on the public forecast pages.

---

## 6b. Web report

The report is published as a static site to **GitHub Pages** (rebuilt after every successful
hourly run), split into a top-level index and one dedicated page per lake
group:

- **Live: https://thomasgmeinder.github.io/windforecast_agents/** — landing page, headed
  **"Forecast overview for &lt;date&gt;"** (the date is read off the records being rendered,
  never off the clock, so it cannot claim today above yesterday's numbers), with two
  clickable tiles (live peak teasers): **Kochelsee & Walchensee** and **Ammersee**.
- **`measurements.html`** — a browsable archive of what was actually **measured**, one day
  at a time, with a lake and day picker. Linked from every forecast page. Every day names
  the **source** that produced it, because Ammersee's truth has changed hands over the
  archive and an error figure is not comparable across that boundary. Rows that had already
  elapsed when the forecast was issued are greyed: recorded, but never learned from or
  scored. The whole archive is embedded once and switched client-side, so it is a single
  static file with no server.
- **`kochel-walchensee.html`** and **`ammersee.html`** — each page separates
  **Predicted — today** (the forecast card(s), blue "forecast" chip) from
  **Observed — yesterday** (a distinct "measured" card per lake, grey left-border
  accent, showing the actual measured wind, its source, the regime inferred from the
  measured direction, and a *vs fc* = forecast − measured column). Below those: a
  **Prediction & learning methodology** section, a **Data sources & how they're
  accessed** section — split into **Prediction inputs (today)** and **Measured inputs
  (yesterday)** with source · role · access-endpoint per lake group — the self-learning
  reports (collapsible), and back/cross nav.
  The measured card reads from `logs/<lake>_diffs.jsonl` and populates after the
  first morning learning run.
- `lib/render.py` builds pages from the latest logs (offline, no API calls):
  **wind cells colour-coded** by the validated blue sequential ramp (light→dark =
  weak→strong, Beaufort inline), rotated direction arrows, **regime badges**
  (blue=gradient, green=thermal, red=föhn, grey=calm — CVD-checked), confidence,
  Δθ/föhn-gradient notes. Light/dark via `prefers-color-scheme`.
- `build_site.py` renders the static pages the Pages workflow publishes.
- **Local preview (optional):** `serve.py` serves the same pages at
  `http://localhost:8092/` for development — run directly, or via the systemd user
  service `wind-agents-web.service`. Not used by the public deployment.

```
systemctl --user status wind-agents-web.service
```

## 7. Layout

```
wind-agents/
├── README.md                     ← this file
├── .venv/                        cfgrib + eccodes + xarray (no apt needed)
├── daily_run.py                  05:00 entrypoint (learn → forecast → log)
├── serve.py                      web server for the HTML report (port 8092)
├── build_site.py                 renders site/ for GitHub Pages
├── lib/
│   ├── winddata.py               data access + measured_source() = the truth-source authority
│   ├── forecast.py               engine (blend, regime, correction, gust guards, output)
│   ├── postproc.py               RLS regression (a + b·model) + guarded gust ratio
│   ├── learn.py                  self-learning, morning audit, gust-ratio rebuild
│   ├── verify.py                 the referee — CRPS vs persistence & climatology
│   ├── tuner.py                  the agentic loop: perceive → reflect → propose → act
│   ├── ledger.py                 hypothesis ledger (proposals + verdicts)
│   ├── climatology.py            per-(month×hour) archive climatology baseline
│   ├── obs_calib.py              calibrate a shore station onto lake-equivalent wind
│   ├── bsv.py                    BSV Herrsching on-lake station (15-min, gusts + direction)
│   ├── buoywatch.py              daily buoy probe + loud source-change events
│   ├── backfill.py               reconstruct past forecasts from the Open-Meteo archive
│   ├── simulate.py               end-to-end loop simulation (offline + live)
│   ├── analyst.py                the LLM call
│   └── render.py                 HTML rendering (forecast pages + measured archive)
├── models/<lake>_bias.json       learned regression (a,b) per regime×hour (+ processed dates)
│   ├── ammersee_bsv_calib.json       BSV → lake-equivalent, validated out of sample
│   ├── ammersee_fallback_calib.json  DWD → lake-equivalent, validated out of sample
│   └── ammersee_climatology.json     12 years of buoy readings, per month × hour
├── config/params[_<lake>].json   tunable regime thresholds (written only by the gated tuner)
├── logs/
│   ├── <lake>_forecast.jsonl     issued forecasts (1/day, with raw + features + guards)
│   ├── <lake>_diffs.jsonl        per-hour prediction-vs-measured diffs — the measured archive
│   ├── events.jsonl              notable events: truth_source, param_change, verification, …
│   ├── ledger.jsonl              open + resolved tuner hypotheses
│   ├── learning/<lake>_<date>.md detailed morning learning report
│   ├── tables/<lake>_<date>.txt  the hourly table
│   └── latest_report.txt         full morning output (learning + forecasts)
├── docs/LEVERAGE.md              where the accuracy actually comes from (measured)
├── cache/                        decoded ICON-D2 GRIB + cached BSV days (gitignored)
└── .claude/agents/kochel-walchensee-wind.md   the LLM agent definition
```

---

## 8. Running it

```bash
# environment (Zscaler: curl is sandbox-blocked; helpers use urllib + system CA)
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# full daily cycle (learn yesterday → forecast today)
.venv/bin/python daily_run.py

# a single lake's table for a date
.venv/bin/python lib/forecast.py walchensee 2026-08-02

# force a learning update for one past day
.venv/bin/python lib/learn.py walchensee 2026-08-01

# which measured source is in force, and is the buoy back?
.venv/bin/python lib/buoywatch.py

# BSV: one day's measurements, or warm the cache for a range (rate-limited)
.venv/bin/python lib/bsv.py show 2026-08-07
.venv/bin/python lib/bsv.py backfill 2025-09-01 2026-08-07

# rebuild a calibration (network; validated out of sample before it is applied)
.venv/bin/python lib/obs_calib.py build ammersee bsv
.venv/bin/python lib/obs_calib.py show  ammersee bsv

# re-derive every gust ratio from logged history (dry run unless --apply)
.venv/bin/python lib/learn.py rebuild-gusts walchensee
```

Self-tests (all of these run in CI):

```bash
.venv/bin/python lib/postproc.py          # regression + gust-ratio guards
.venv/bin/python lib/forecast.py selftest # headline, gust guards, direction, persistence
.venv/bin/python lib/verify.py            # CRPS correctness + the backtest gate
.venv/bin/python lib/tuner.py selftest    # propose → gate → apply → review
.venv/bin/python lib/render.py selftest   # page headings + the measured archive
.venv/bin/python lib/buoywatch.py selftest
.venv/bin/python lib/simulate.py offline  # the whole loop, hermetic
```

The LLM agent (`kochel-walchensee-wind`) is project-scoped: run it from within this
directory. It calls `build_table()` for the numbers, then adds narrative and
caveats.

---

## 9. Data-access notes (this machine)

- **`curl` is sandbox-blocked**; all fetching uses Python `urllib` with the system
  CA bundle `/etc/ssl/certs/ca-certificates.crt`, which verifies cleanly through the
  Zscaler proxy.
- The GRIB stack (`cfgrib`, `eccodes` via bundled `eccodeslib`) installed into
  `.venv` with **no apt/sudo**.
- DWD Open Data keeps only a ~24 h rolling window per model run → GRIB is cached on
  fetch. Licence CC BY 4.0.

---

## 10. Honest limitations

- **Bias correction starts empty**; rows read "raw (no local calib yet)" until
  history accrues. It improves a little each morning.
- **Δθ uses model (Open-Meteo) T2m** — captures the diurnal stability swing well but
  smooths absolute values; `COLD_POOL_DTHETA` is a provisional pivot for the
  learning loop to recalibrate. The model also flattens the true summit (Herzogstand
  1731 m → ~1456 m grid cell).
- **`foehn_gradient_hpa` is logged and displayed but is not an active predictor.** MOSMIX
  Δp and ICON-D2 850 hPa wind drive the current föhn-favourable rule.
- **Kochelsee actuals**: the on-lake `kochelsee/trimini` feed provides genuine measured
  wind (`mavg`/`mmax`/`dir`) and is used as truth; DWD Garmisch (a distant valley proxy)
  is only a fallback if that feed is unavailable. Walchensee/Urfeld is on-lake truth too.
- **Kochelsee terrain sectors** are inherited from Urfeld (provisional).
- **Ammersee's truth is currently an estimate, not a measurement.** Both shore stations are
  sheltered and their range is *compressed*: on 2026-06-05 the buoy spanned 1.4–17.9 kn
  across the day while BSV spanned 2.4–6.2. Calibration can stretch the average back out but
  cannot recover a 17.9 kn morning from a 5.3 kn reading — that information is not in the
  signal. The blend is the best available truth while the buoy is down; it is not a
  substitute for it, and `truth_source` records which is which, day by day.
- **BSV direction and gusts are unvalidated.** The buoy measures speed only, so there is no
  ground truth to check them against. Preferring them over DWD's is a physical argument
  (a real sensor at the lake beats one 11 km inland), not a measured one.
- **The measured archive depends on a small club's web server.** If it is unreachable the
  pipeline degrades to DWD alone and says so in the daily report — but there is no
  agreement in place, and it could disappear.
- Residual error even after correction is ~1.0 m/s (valley) to ~1.5 m/s (ridge),
  worst in stable/thermal regimes — the forecast states confidence honestly and does
  not imply spurious precision.
- The addicted-sports JSON endpoint is undocumented; `actual_hourly` falls back to
  DWD automatically and every report names the source actually used.

---

## 11. Roadmap

- Have the learning **use** the logged features: recalibrate `COLD_POOL_DTHETA` and
  a `foehn_gradient_hpa` threshold from what actually happened, then let them drive
  classification.
- Give **Kochelsee its own terrain sectors** and an on-lake measured feed.
- Add the **observed** (not forecast) cross-Alpine Δp (Bozen−Innsbruck) as an
  independent föhn cross-check.
- Fold the measured **lake water temperature** (thermal-engine cold source) into the
  stability feature.
- **Ask BSV for WeatherLink API access.** The station is a Davis on WeatherLink Cloud, whose
  v2 API exposes proper historical data. Scraping an embedded chart array works but is
  fragile, and a conversation with the club would make it durable.
- **Point the tuner at levers that move.** `docs/LEVERAGE.md` measures the six regime
  thresholds at <0.5 % of MAE with confidence intervals straddling zero, while the blend
  weights and the correction's own constants (`FORGET`, `BIAS_CAP_KN`, `N_MIN_OBS`) are
  untuned and demonstrably worth more.
- **Weight the Ammersee blend** rather than taking a plain mean — e.g. per hour, or by which
  shore the wind direction favours. The plain mean already beats both single sources; a
  smarter combination has not been tested.
