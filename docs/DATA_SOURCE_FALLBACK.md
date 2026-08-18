# Data-source fallback specification

## Rule

For every forecast field and valid hour, prefer the highest-quality source that has a
usable value for that exact field and hour. A source reaching the end of its horizon
must not leave a table cell blank when a compatible fallback source has data.

This rule is field-level. A source can provide mean wind while another source provides
direction, temperature, weather condition, precipitation, or gusts for the same hour.

## Selection order

1. Use the preferred high-resolution source when it has a valid value.
2. Otherwise use the next compatible available source for that field.
3. If no source has a valid value, show `—`; do not invent, carry forward, or interpolate
   a value.
4. Keep the existing uncertainty/variability guards. Availability does not make an
   unreliable direction trustworthy.

Example: ICON-D2 is preferred for near-term wind direction. When its direction ends
before a four-day forecast window ends, use ICON-EU direction for the remaining hours.
If the direction-spread guard says the direction is variable, display `VAR` rather than
a bearing.

## Forecast-field policy

| Field | Preferred source | Fallback when unavailable |
|---|---|---|
| Mean wind | available-source forecast blend | blend the remaining available forecast sources |
| Gust | available-source forecast blend, then gust guards | remaining available gust sources; retain guards |
| Direction | ICON-D2 10 m direction | ICON-EU 10 m direction |
| Temperature, sky, rain | ICON-D2 | ICON-EU |
| Weather icon | WMO weather code from the selected weather source | compatible WMO code from fallback source |
| Measured wind/gust | lake's selected station source | configured measured-source fallback only |

## Provenance and confidence

- Persist which forecast sources contributed to a row.
- Persist the field values issued at forecast time; never replace an issued forecast with
  later model data.
- Lower confidence when high-resolution or ensemble inputs are unavailable at a longer
  lead time.
- A fallback source may fill a value, but it must not silently claim the coverage or
  uncertainty of the preferred source.

## Verification

Tests must cover a preferred-source horizon ending while a fallback remains available.
They must assert that:

- the fallback field is displayed rather than `—`;
- the issued source/provenance is retained;
- direction guards still withhold variable directions;
- no field is fabricated when all compatible sources are missing.
