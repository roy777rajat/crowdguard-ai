# Architecture and data flow

## Principles

- Local-first. Runs with no AWS or Meta credentials.
- The forecasting engine is deterministic Python. **No model ever computes or overwrites occupancy or forecasts.**
- Simulated data is labelled `source = "simulated"` end to end and the UI repeats that.
- Every protected backend operation checks the caller's role (and pandal, for managers) on the server.
- Unknown telemetry is stored as `NULL` and shown as "not reported" or "unknown", never invented.

## Components

```
Browser (React)  --/api-->  FastAPI routes  -->  services  -->  repositories  -->  SQLite
                              |   deps.py          monitor        store.py          (db.py)
                              |   (auth + RBAC)    incidents
                              |                    engine/forecast.py   (pure functions)
                              |                    simulator/scenarios.py
                              |                    workflows/runner.py  (stage recorder)
                              |                    observability/llm.py, telemetry.py
                              +--> integrations/meta_adapter.py (stub)
```

## Ingestion workflow (one run per pandal per simulated minute)

1. `receive`: take the simulator observation. If the sensor emitted nothing, fail with `NO_DATA`.
2. `validate`: reject negative, non-finite or non-numeric values (`INVALID_INPUT`).
3. `persist`: write the measurement; mark the camera `receiving_simulated`. Retryable.
4. `forecast`: compute rolling rates, 1/3/5/10 minute baseline forecasts, time-to-threshold, confidence.
5. `evaluate`: apply alarm rules, deduplicate against open incidents, open or update incidents.
6. `explain` (non-critical): ask the model provider to describe the recorded values, then run a groundedness check
   (every number in the text must appear in the facts). A failure here marks the run `partial`, not `failed`.

Each attempt of each stage is stored with start, end, duration, status, error code and a sanitized message
(secrets and file paths are stripped). Runs left `running` by a crash are marked `failed/INTERRUPTED` on startup.

## Alarm rules

`occupancy_warning`, `occupancy_critical`, `predicted_critical`, `exit_bottleneck`, `stale_data`; each can be
disabled in Settings. While data is stale, occupancy rules are suppressed (a stale number is not evidence) and only
`stale_data` can fire. An alarm whose `pandal:rule` key matches an unresolved incident increments its repeat count
(and may raise its severity) instead of creating a duplicate. Notifications go through a `Notifier` interface; the
local implementation sends nothing.

## Forecast model

`forecast(h) = max(0, current + (entry_rate - exit_rate) * h)` with rates averaged over the last three samples.
Assumes constant rates; ignores queueing, entrance limits and behaviour change. Confidence is a **data-quality**
score (freshness times sample coverage) and is shown separately from **risk** (occupancy versus thresholds). The exit
bottleneck is a third, separate signal.

## Roles

| Role | Read | Simulation | Registry write | Incidents | Model calls, system, settings | Audit |
|---|---|---|---|---|---|---|
| admin | all | yes | yes | yes | yes (edit) | yes |
| operator | all | yes | no | yes | read | no |
| manager | own pandals only | no | no | yes (own pandals) | no | no |
| auditor | all | no | no | no | read | yes |

## Data stores (SQLite tables)

pandals, zones, cameras, measurements, forecasts, runs, stages, incidents, incident_events, llm_calls, audit,
users, sessions, settings. Only `db.py` and `repositories/store.py` contain SQL; services use repository objects, so
a DynamoDB or S3 implementation can replace them.

## Observability seams

`TelemetryExporter` (OpenTelemetry-style spans, CloudWatch-style metrics) has only a null implementation. Local
request latency and error counts are kept in an in-memory ring buffer (reset on restart).

## Not yet implemented

Video processing worker, push updates (the UI polls every 2 to 5 seconds), real notification channels, Cognito
validation, trend-based forecasts, multi-process safety (one backend process assumed).
