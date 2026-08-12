# Hourly forecast data audit

This file defines what may be used to evaluate the hourly forecast-of-record system.
It prevents a later model refresh, reconstructed blend, or changed truth source from being
mistaken for the forecast actually available before a valid hour.

## Current inventory — 12 August 2026

| lake | daily forecast records | aware issue stamps | scored hourly pairs | limitation |
|---|---:|---:|---:|---|
| Ammersee | 88 | 85 | 1,255 | truth changes among raw/calibrated DWD, buoy, and blend; blend components not persisted |
| Kochelsee | 22 | 19 | 119 | short August-only archive; blend components not persisted |
| Walchensee | 26 | 23 | 123 | short August-only archive; 14 rows lack a measurement source |

All historical daily forecast records contain 24 calendar-hour rows. They do not contain
the individual as-issued `eps`, deterministic ICON-D2, ICON-EU, and spot forecast values;
only the blended raw value survives. They are therefore **not** suitable for proving the
skill of a future hourly source-blend system.

## Record classes

1. **Hourly as-issued** — required for hourly production scorecards. Must contain an aware
   `issue_time`, a `valid_time`, the source/model values used, the forecast-of-record flag,
   and the measurement provenance.
2. **Partial daily legacy** — old daily record with an issue stamp. Only rows after the
   issue time may be used for limited legacy comparison. Never fill missing pre-issue hours.
3. **Reconstruction/test** — generated from current or archive model data for a past issue
   time. Useful to test code paths only; never include in public skill or learning.
4. **Invalid** — missing/unparseable issue time, missing measurement provenance, or a row
   whose forecast was issued after its valid time. Exclude from learning and scoring.

## Production acceptance rules

An hourly row may be learned from and scored only when:

```text
issue_time < valid_time
forecast-of-record is frozen
measurement is present and source-labelled
measurement quality is accepted
row has not been learned before
```

Hourly skill must be reported separately by lead bin: 0–1 h, 1–3 h, 3–6 h, 6–12 h,
and 12–24 h. A bin with insufficient rows or source-consistent coverage must say so.
