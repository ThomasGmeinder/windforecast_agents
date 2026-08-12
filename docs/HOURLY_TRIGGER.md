# Reliable hourly trigger

GitHub Actions executes the forecast and publishes GitHub Pages. Its native `schedule:`
event is retained as a backup, but it is not a punctual clock: GitHub documents that
scheduled runs can be delayed or dropped under load, and this repository has observed a
scheduled event at a minute different from the configured cron expression.

Use [cron-job.org](https://cron-job.org/en/) as the primary clock. It is free, supports
custom HTTP headers/body, has a test-run button, and keeps recent execution history.
The job only wakes GitHub; it does not serve pages or run forecast code itself.

## One-time GitHub setup

Create a fine-grained personal access token limited to this repository with:

```text
Contents: read and write
Actions: read and write
```

Keep it only in cron-job.org's custom `Authorization` header. Never commit it, put it in
the job URL, or add it to a GitHub Actions secret.

## cron-job.org job

Create a job scheduled at `55 * * * *` in UTC. Configure:

```text
Method: POST
URL: https://api.github.com/repos/ThomasGmeinder/windforecast_agents/dispatches
Headers:
  Accept: application/vnd.github+json
  Authorization: Bearer YOUR_FINE_GRAINED_TOKEN
  X-GitHub-Api-Version: 2022-11-28
Body:
  {"event_type":"hourly_forecast","client_payload":{"source":"cron-job.org"}}
```

Use cron-job.org's **Test run** button once. A successful response is HTTP 204; GitHub
Actions should then show event `repository_dispatch` for `hourly rolling wind forecast`.

## Failure behavior

- cron-job.org retains its own execution result/history and can notify the account owner.
- GitHub records the issued workflow, commits the hourly status history, and deploys the
  last valid table even if the upstream weather fetch fails.
- The GitHub `schedule:` rule remains a backup. Duplicate external/backup triggers are
  harmless to forecast scoring because the forecast-of-record selector freezes the last
  issue before each valid hour; completing the hourly dedupe gate remains required before
  treating duplicate learning as production-safe.
