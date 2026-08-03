# Kochelsee / Walchensee wind-prediction agent

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
föhn and thermals worst of all. So the engine classifies the **regime** each hour
and corrects toward measured on-lake wind.

Three regimes plus fall-winds, and telling them apart is the whole game:

| Regime | What it is | Effect on the two lakes |
|---|---|---|
| **South föhn** | warm, dry, gusty S down-slope wind when pressure is higher S of the Alps | pours **down the Kesselberg → Kochelsee turns strong**; **suppresses the Walchensee NE thermal** (the anti-correlation). Often an early Kochelsee burst that dies ~09:00 |
| **Thermal ("Walchenseewind")** | NE nozzle wind between Jochberg & Herzogstand, sunny weak-gradient days | Walchensee's reliable summer wind, ~11:00–evening, N–NE; killed by any south föhn |
| **Gradient** | frontal / pressure-driven flow | both lakes feel it; terrain still channels it |
| **Non-föhn fall-winds** | cold-night N-slope drainage off Herzogstand/Heimgarten | up to ~8 Bft into the morning; must not be mislabelled föhn |

**The single most important call** this agent makes is the föhn/thermal split,
because it means *Kochelsee strong / Walchensee thermal dead* — opposite forecasts.

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

Because the surface wind is **channelled by terrain**, the raw model's free-flow
direction is unreliable at the lake — real directions quantise into conduits. This
sector map (direction the wind comes **from**, at Urfeld; confirmed locally) is the
regime discriminator:

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

Sources are **tiered, not averaged** — one tier constrains/corrects the next.

### Forecast (the backbone) — an average of multiple predictions
The forecast VALUE is the **equal-weight mean of several sources**, not one run:
- **ICON-D2 ensemble** mean (20 members) + **ICON-D2 deterministic** + **ICON-EU** +
  the **addicted-sports spot forecast** (`winddata.addicted_forecast`, tuned to the
  local thermal). ICON-D2 access: raw GRIB (`icon_d2_grib_point`, cached) or
  Open-Meteo point. The ensemble **spread** sets the confidence band.

### Föhn diagnosis
- **DWD MOSMIX** cross-Alpine **Δp = Bozen − München** (`foehn_delta_p`): ≥4 hPa
  noticeable, ≥8 hPa reaches the surface. Best föhn direction is **SE**; SW is flagged
  unreliable.
- **Hohenpeißenberg nowcast** (`winddata.hohenpeissenberg_now`, DWD 02290): S/SE wind
  there in the morning is the precondition — föhn is flagged *unconfirmed* until it shows.
- **addicted-sports drivers** (`winddata.addicted_drivers`): `foehn_gradient_hpa`,
  850 hPa wind, `lapse_2m_850`, radiation.

### Measured "ground truth" (for bias correction + learning)
- **On-lake Urfeld anemometer** via the reverse-engineered addicted-sports JSON
  endpoint
  `https://www.addicted-sports.com/forecast/walchensee/urfeld/?json=wind&from=YYYY-MM-DD`
  → hourly `mavg` (measured avg kn), `mmax` (gust), `dir`, webcam images, and the
  site's own `mae`/`guete`. `winddata.addicted_measured_hourly`. Daylight hours
  only. **Far better than any DWD station** — it captures the NE nozzle thermal a
  valley station misses.
- **DWD 10-min obs** (`winddata.dwd_obs_hourly`) as fallback: Garmisch 01550
  (valley proxy) for the southern lakes, Wielenbach 05538 for Ammersee.
- `winddata.actual_hourly(lake, date)` picks on-lake first, DWD as fallback, and
  reports which source it used.

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

1. Pulls ICON-D2 (raw GRIB / Open-Meteo point) for the lake gridpoint.
2. Attaches augmentation features: MOSMIX Δp, addicted föhn drivers, and the Δθ
   stability index.
3. **Classifies the regime** (`classify_regime`):
   - **föhn** if Δp ≥ threshold **and** 850 hPa southerly (120–240°) **and** ≥ ~7 kn;
   - **gradient** if 925 hPa flow ≥ ~12 kn;
   - **thermal** if daytime + low cloud + weak gradient **and not** a cold pool —
     if Δθ ≥ `COLD_POOL_DTHETA` (1.5 K) and model wind is light it is downgraded to
     **"cold-pool capped"** calm;
   - else calm.
4. **Applies the learned regression** `corrected = a + b·model` for that
   (regime × hour-of-day) bucket (`apply_bias` → `postproc`), which **scales with**
   the model so it can't double-count föhn; rows before calibration are flagged
   "raw (no local calib yet)".
5. **Confidence** from ensemble spread + calibration state; föhn capped at "med".
6. Surfaces the drivers in the table Note column (`Δθ±x.x`, `fg±x.x`).

Regime thresholds (`FOEHN_DP_*`, `FOEHN_850_KN`, `GRADIENT_925_KN`,
`THERMAL_CLOUD_MAX`, `SW_SECTOR`, `COLD_POOL_DTHETA`) start from published
(Swiss-calibrated) values and are recalibrated over time by the learning loop.

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
4. **Regime validation** — classifies the *true* regime of each measured hour from
   its **measured direction** (terrain sector) + wind, and reports predicted-vs-
   measured **regime accuracy + confusion matrix**, flagging the föhn/thermal
   **anti-correlation** when it is missed.
5. **Updates the mechanism** — a **recursive-least-squares regression**
   `corrected = a + b·model` per (regime × hour-of-day) (`postproc`), with a prior
   of (a=0, b=1) = "trust the model", a forgetting factor, and a cap; plus a gust
   factor. The report shows the exact **a/b before → after** for every bucket.

The regression **scales with** the model (so it neither double-counts föhn nor blindly
adds a fixed offset), converges to the systematic bias, and is evidence-gated (one day
barely moves the applied correction). Idempotent: each date is learned once per lake. The
forecast reads the just-updated model in the same run, so today benefits
immediately.

**Bias buckets are keyed by the *forecast* regime** (so the correction that gets
applied matches how the forecast is made); the regime-validation section separately
tells us how often the regime *call itself* was right.

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
  *climatology* (all earlier days at that hour). Both computed leak-free — a day is
  scored using only strictly earlier data.
- **Skill score** `SS = 1 − CRPS/CRPS_baseline`; `SS > 0` means we beat that baseline.
  Reported overall, per regime and per hour.

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
- the **learned regression state** (per-`regime×hour` `a`, `b`, `n`), so it reasons about
  the *residual* the linear correction cannot absorb;
- the **CRPS scorecard** and current tunable parameters with their legal bounds;
- **its own past proposals**, each with the measured CRPS *before* vs *after* it was
  issued.

**Output it produces**
1. **Reviews** — for every open hypothesis it must return `confirmed` or `retracted`
   with reasoning; the verdict is written back to the ledger (`logs/ledger.jsonl`). This
   is the memory that makes it accountable rather than a fresh opinion each morning.
2. **Proposals** — at most two small parameter changes, each with a rationale and an
   `expected_effect` it will be judged against next time. Each is recorded as a new open
   hypothesis.

**What happens to a proposal (the gate).** Nothing is applied on the model's say-so.
Each proposal must pass, in order: the parameter is one of the six known tunables → the
value is numeric and inside `PARAM_BOUNDS` → the step is ≤ 25 % of the current value →
and then a **backtest**: every replayable logged day is re-run under the candidate value
(`verify.backtest`, sharing `forecast.replay_hour` with production so a backtest can
never drift from the real path) and it is applied **only if CRPS improves over at least
`verify.N_MIN_BACKTEST_DAYS` (currently 10) replayable days**. A change that passes is written to `config/params_<lake>.json` — per-lake, because
it was only ever verified against that one lake's history — and logged as a `param_change` event with its
evidence. Anything else is refused, with the reason recorded.

**Currently the gate is effectively dormant**: replayable history (days carrying the
captured classification inputs) only began accumulating recently, so proposals are logged
and reviewed but not applied, and the daily report says exactly that
(`held back … insufficient replayable history (n/N days)`). Agency — memory,
self-evaluation, an objective referee — is live now; authority to change the forecaster
switches on only once the evidence exists. Without a `GEMINI_API_KEY` the whole layer
skips cleanly and the deterministic forecast is unaffected.

---

## 6. Daily automation (`daily_run.py` + systemd)

`daily_run.py`:
1. STEP 1 — learn from yesterday (writes the detailed reports above), then run the
   self-tuning loop (review past hypotheses → propose → backtest-gated apply);
2. STEP 2 — build today's tables from the just-updated model, print them, write
   `logs/tables/<lake>_<date>.txt` and `logs/latest_report.txt`, and log the
   forecast (with raw values, features, predictive deciles and the classification
   inputs needed for replay) for tomorrow to learn from;
3. STEP 3 — verify: score the logged forecasts with CRPS against persistence and
   climatology and log a `verification` event.

Idempotent (one forecast record per date).

**Where it actually runs:** the pipeline runs **in GitHub Actions**, not on the laptop.
`.github/workflows/daily.yml` is on a `7 3 * * *` **UTC** cron (≈05:07 Berlin in summer,
04:07 in winter — GitHub cron has no timezone), and it also runs the self-tests before the
pipeline, then commits `models/`, `logs/` and `config/` back to `main` and publishes Pages.

The local **systemd user timer** `wind-agents-daily.timer` fires at **05:00 local** and
does *not* run `daily_run.py` — it only dispatches the cloud workflow
(`gh workflow run daily.yml`), because GitHub's own cron can lag by many minutes and this
makes the morning publish punctual. `Persistent=true`, so a dispatch missed while the
laptop slept fires on next wake. Linger is **not** enabled, so the timer only runs while
you are logged in; the cloud cron is the backstop that guarantees a daily run regardless.

```
systemctl --user list-timers wind-agents-daily.timer
journalctl --user -u wind-agents-daily.service -n 40
gh run list -R ThomasGmeinder/windforecast_agents -L 5     # what the cloud actually did
```

---

## 6b. Web report

The report is published as a static site to **GitHub Pages** (rebuilt each morning
by the daily workflow), split into a top-level index and one dedicated page per lake
group:

- **Live: https://thomasgmeinder.github.io/windforecast_agents/** — landing page with
  two clickable tiles (live peak teasers): **Kochelsee & Walchensee** and **Ammersee**.
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
├── lib/
│   ├── winddata.py               data access (forecast, obs, föhn, drivers, Δθ)
│   ├── forecast.py               engine (ensemble-mean blend, regime, terrain, output)
│   ├── postproc.py               RLS regression correction (corrected = a + b·model)
│   ├── learn.py                  self-learning + detailed morning audit
│   └── render.py                 HTML rendering of the report
├── models/<lake>_bias.json       learned regression (a,b) per regime×hour (+ processed dates)
├── logs/
│   ├── <lake>_forecast.jsonl     issued forecasts (1/day, with raw + features)
│   ├── <lake>_diffs.jsonl        per-hour prediction-vs-measured diffs
│   ├── learning/<lake>_<date>.md detailed morning learning report
│   ├── tables/<lake>_<date>.txt  the hourly table
│   └── latest_report.txt         full morning output (learning + forecasts)
├── cache/                        decoded ICON-D2 GRIB
└── .claude/agents/kochel-walchensee-wind.md   the LLM agent definition
```

---

## 8. Running it

```bash
# environment (Zscaler: curl is sandbox-blocked; helpers use urllib + system CA)
export SSL_CERT_FILE=REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# full daily cycle (learn yesterday → forecast today)
.venv/bin/python daily_run.py

# a single lake's table for a date
.venv/bin/python lib/forecast.py walchensee 2026-08-02

# force a learning update for one past day
.venv/bin/python lib/learn.py walchensee 2026-08-01
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
- **`foehn_gradient_hpa` is logged and displayed but not yet a hard regime trigger**
  (MOSMIX Δp remains primary) until the learning calibrates it.
- **Kochelsee actuals**: the on-lake `kochelsee/trimini` feed provides genuine measured
  wind (`mavg`/`mmax`/`dir`) and is used as truth; DWD Garmisch (a distant valley proxy)
  is only a fallback if that feed is unavailable. Walchensee/Urfeld is on-lake truth too.
- **Kochelsee terrain sectors** are inherited from Urfeld (provisional).
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
