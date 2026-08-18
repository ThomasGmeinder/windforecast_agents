# Development workflow for coding agents

This repository produces operational wind forecasts. A small change can affect issued
forecasts, learning state, measurement history, and the public GitHub Pages report. Follow
this workflow before making any change.

## 1. Stop and read before editing

Do not edit code, configuration, workflow files, models, logs, or generated HTML until all
relevant reading is complete.

### Required pre-change checklist

1. Read this document and the root `AGENTS.md`.
2. Inspect repository state:

   ```bash
   git status --short
   git diff
   git log --oneline --decorate -12
   git fetch origin main
   git log --oneline HEAD..origin/main
   ```

3. Identify the authoritative code path for the requested behavior. Read the complete
   related function/module, not only a matching line.
4. Read related specifications in `docs/`, especially:

   - `DATA_SOURCE_FALLBACK.md` for source selection or horizon issues;
   - forecasting/learning methodology documentation for model or scoring changes;
   - systemd and GitHub workflow files for cadence, deployment, or state changes.

5. Inspect relevant recent changes with `git log -p` or `git show` before reintroducing a
   previously fixed bug.
6. Check whether local state is isolated through `WIND_STATE_DIR`. Never assume local logs
   or models are production state.
7. State the intended scope and acceptance checks before editing.

## 2. Establish ownership and invariants

Write down which component owns each fact before changing it.

| Concern | Authority / invariant |
|---|---|
| Issued forecast | Hourly forecast record; never rewrite a forecast used for scoring |
| Measurement | Selected station source for a completed hour only |
| Learning | Each finalized measurement updates at most once |
| Production state | GitHub workflow-owned tracked `logs/` and `models/` |
| Local experimentation | `.local/state/`, never committed or pushed |
| Data fallback | Highest-quality compatible source with an available field |
| Public Pages | Static build artifact; does not read repository files server-side |

If a proposed change violates an invariant, redesign it rather than patching around it.

## 3. Design before implementation

For anything beyond a small wording fix, define:

- affected inputs, persisted fields, and rendered outputs;
- behavior for unavailable data, partial hours, and source-horizon transitions;
- local versus production behavior;
- migration/backfill needs and whether the task is one-off or scheduled;
- verification method and rollback boundary.

For statistical/model changes, define a frozen, out-of-sample comparison before changing
production behavior. New features must be persisted at issue time before they can be
replayed safely.

## 4. Implement narrowly

- Change the smallest complete set of authoritative files.
- Reuse existing source-selection, forecast-of-record, and completed-hour helpers.
- Do not duplicate rules in renderer, workflow, and forecast engine.
- Preserve source provenance and confidence when falling back to another source.
- Never fabricate a missing value, carry it forward, or convert a source gap into a zero.
- Keep user-facing explanations plain; internal terms belong in documentation, not unexplained
  table labels.

## 5. Validate proportionally

At minimum, run the relevant self-tests and a focused behavior check. For forecasting,
learning, rendering, or state changes, also run:

```bash
.venv/bin/python hourly_run.py --selftest
.venv/bin/python lib/verify.py
.venv/bin/python lib/render.py selftest
.venv/bin/python build_site.py
git diff --check
```

For local experiments:

- use `WIND_STATE_DIR="$PWD/.local/state"`;
- snapshot/compare tracked `logs/`, `models/`, and `config/` before and after;
- verify the local preview over HTTP, not only generated files.

For workflow/cadence changes, validate the actual trigger, execution time, and deployed page
separately. A successful code test is not evidence that GitHub Pages has deployed it.

## 6. Review the rendered result

Inspect the exact page or local HTTP endpoint a user will open. Check:

- table values and column alignment;
- future, completed, and `NR` rows;
- time zone labels;
- mobile/horizontal table behavior where relevant;
- stale text, duplicate sections, and legacy reports;
- local versus production state boundaries.

## 7. Commit and push discipline

- Do **not** commit or push unless the user explicitly asks.
- Stage files explicitly; never use `git add .`.
- Keep code/docs commits separate from workflow-generated logs/models where possible.
- Before pushing, fetch/rebase over production hourly commits. Preserve workflow-owned
  generated state; do not overwrite it.
- After pushing, verify `HEAD` equals `origin/main`.

## 8. Report honestly

Report what was changed, what was tested, what is local-only, and what is already deployed.
Never describe a static page as updated until the relevant deployment has completed.
State unresolved limitations directly rather than implying a forecast, source, or experiment
is more certain than the data supports.
