# Settings

Generated from the settings registry (`src/fuzer/core/settings.py`, #131); don't edit by hand (`uv run python -m fuzer.reference`).
Change them on the Settings page or with `fuzer settings set <key> <value>`; a project's value beats the global one, which beats the default. Secrets hold a reference (`env:NAME`), never the value.

## Claude

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `llm.model` | str | `claude-opus-5-5` |  | Claude model for the readers: log links, P&IDs, documents, curves, mapping suggestions, daily report drafts. Replaces `FUZER_LLM_MODEL`. |
| `llm.effort` | choice | `medium` | low / medium / high / xhigh / max | Effort for the readers. Replaces `FUZER_LLM_EFFORT`. |
| `agents.model` | str | `claude-opus-5-5` |  | Claude model for the assistant's orchestrator. Replaces `FUZER_AGENT_MODEL`. |
| `agents.effort` | choice | `high` | low / medium / high / xhigh / max | Effort for the assistant's orchestrator. Replaces `FUZER_AGENT_EFFORT`. |
| `agents.specialist_model` | str | `` |  | Claude model for the specialists (empty: the orchestrator's). Replaces `FUZER_AGENT_SPECIALIST_MODEL`. |
| `agents.specialist_effort` | choice | `medium` | low / medium / high / xhigh / max | Effort for the specialists. Replaces `FUZER_AGENT_SPECIALIST_EFFORT`. |
| `agents.fallbacks` | bool | `True` |  | On a policy decline, let the API re-run the request on a fallback model. Replaces `FUZER_AGENT_FALLBACKS`. |
| `assistant.retention_days` | int | `0` | ≥ 0 | Days the assistant keeps a conversation after its last message, with its images and answers (0: keep them). |

## Schedule

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `daily_report.close_cron` | str | `0 7 * * *` |  | When the day is closed and its report drafted (cron). Takes effect on restart. Replaces `FUZER_DAY_CLOSE_CRON`. |
| `work.lease_seconds` | int | `900` | ≥ 30 | How long an agent holds a workflow task before another may take it. Replaces `FUZER_WORK_LEASE_SECONDS`. |
| `alerts.webhook` | secret | `env:FUZER_ALERT_WEBHOOK` |  | Where the URL failed scheduled runs are posted to (Teams, Slack or any JSON webhook) is kept. |

## Database

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `db.work_mem` | str | `256MB` |  | Postgres work_mem for the rebuild's big queries. Replaces `FUZER_WORK_MEM`. |

## SRE

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `sre.handoff.min_detections` | int | `3` | ≥ 1 | Detections an upset needs before it is handed off (a lone detection is evidence, not an upset worth a person's time). |
| `sre.handoff.max_diagnoses` | int | `10` | 0 to 100 | Upsets the troubleshooter diagnoses per hand-off run. |
| `sre.handoff.max_usd` | float | `5.0` | ≥ 0 | Claude spend cap per hand-off run (USD). |
| `sre.step_minutes` | int | `10` | 1 to 60 | Minutes between the values the detectors read. |
| `sre.lookback_hours` | int | `24` | 1 to 168 | Hours before the window that a step is judged against. |
| `sre.learned_minutes` | int | `60` | ≥ 0 | Minutes a value must stay outside a learned normal range (one no source set) to count. |
| `sre.spe_minutes` | int | `120` | ≥ 10 | Minutes a multivariate anomaly lasts before it is raised. |
| `sre.merge_minutes` | int | `30` | ≥ 0 | The same finding again within this many minutes carries on the one before. |
| `sre.drift_rise` | float | `0.05` | 0 to 1 | Share of its starting value a drift must climb before it is raised. |
| `sre.group_gap_hours` | float | `2.0` | 0.1 to 48 | A detection joins an upset whose latest detection started within this many hours. |
| `sre.hops` | int | `4` | 1 to 10 | Units this many links apart in the plant model count as connected (upsets and correlation studies). |

## Signals

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `signals.flat_hours` | float | `2.0` | 0.5 to 48 | Hours with no change at all, while the unit runs, before a reading is frozen. |
| `signals.stuck_hours` | float | `6.0` | 0.5 to 72 | Hours a reading moves far less than usual (under stuck_ratio x), while the unit runs, before it is stuck. |
| `signals.stuck_ratio` | float | `0.1` | 0.01 to 0.9 | A stuck reading's step-to-step spread is under this share of its usual. |
| `signals.noisy_hours` | float | `1.0` | 0.25 to 48 | Hours a reading moves far more than usual (over noisy_ratio x) before it is noisy. |
| `signals.noisy_ratio` | float | `4.0` | 1.5 to 50 | A noisy reading's step-to-step spread is over this many times its usual. |
| `signals.range_minutes` | int | `30` | 10 to 1440 | Minutes at or past the end of its instrument range before a reading is out of range. |
| `signals.redundant_hours` | float | `2.0` | 0.5 to 72 | Hours two redundant transmitters stay further apart than usual before one is drifting. |
| `signals.baseline_days` | int | `7` | 1 to 90 | Days before the window that set a reading's usual behaviour (instrument_health). |
| `signals.merge_minutes` | int | `240` | 0 to 1440 | The same instrument finding again within this many minutes is one finding. |
| `signals.step_z` | float | `6.0` | 2 to 50 | A step: the median moves this many times its usual window-to-window move. |
| `signals.var_ratio` | float | `4.0` | 1.5 to 50 | A variance change: the jitter either side differs by this factor. |
| `signals.drift_z` | float | `4.0` | 2 to 50 | A drift: the fitted trend moves this many times the spread around it. |

## Alarms

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `alarms.flood_per_10min` | int | `10` | 2 to 100 | More alarms than this in 10 minutes is a flood (EEMUA 191). |
| `alarms.target_per_hour` | float | `6.0` | 0.5 to 60 | Alarms per hour an operator can handle; more is overloaded. |
| `alarms.standing_hours` | float | `24.0` | 1 to 720 | Hours an alarm stays on before it is standing. |
| `alarms.chatter` | int | `3` | 2 to 50 | Alarms on one point, each within a minute of the last clearing, that make it chattering. |
| `alarms.failed_start_minutes` | int | `30` | 1 to 1440 | A start followed by a stop within this many minutes is a failed start. |

## Opportunities

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `opportunities.gas_lift_breakeven` | float | `0.5` | 0 to 10 | Sm3/d of oil per kSm3/d of lift gas below which more gas isn't worth it. |
| `opportunities.stale_days` | int | `90` | 7 to 730 | Days without a well test before the test is stale. |
| `opportunities.material` | float | `0.5` | 0.05 to 10 | A decided opportunity comes back when its gain moves by this share. |

## Reports

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `daily_report.mover_pct` | float | `10.0` | 1 to 100 | A well test this many percent from the well's previous one is a mover in the daily report. |

## Studies

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `studies.max_points` | int | `2000` | 10 to 100000 | Points an interactive correlation or clustering study reads at most. |
| `studies.max_values` | int | `15000000` | ≥ 10000 | Values (points x steps) a study reads at once; bigger ones run in the background. |
| `studies.workers` | int | `8` | 1 to 64 | Threads reading a study's points in parallel. |

## Sources

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `sources.pi.url` | str | `` |  | PI Web API base URL (empty: the point list and AF tree come from files). Takes effect on restart. Replaces `FUZER_PI_WEB_API_URL`. |
| `sources.pi.data_server` | str | `` |  | PI Data Archive to read points from (empty: the first). Replaces `FUZER_PI_DATA_SERVER`. |
| `sources.pi.af_server` | str | `` |  | PI AF server (empty: the first). Replaces `FUZER_PI_AF_SERVER`. |
| `sources.pi.af_database` | str | `` |  | PI AF database (empty: the server's first). Replaces `FUZER_PI_AF_DATABASE`. |
| `sources.pi.user` | str | `` |  | PI Web API user for basic auth (empty: no auth). Replaces `FUZER_PI_USER`. |
| `sources.pi.password` | secret | `env:FUZER_PI_PASSWORD` |  | Where the PI Web API password is kept. |
| `sources.pi.verify_tls` | bool | `True` |  | Check the PI Web API's TLS certificate. Replaces `FUZER_PI_VERIFY_TLS`. |
| `sources.adx.url` | str | `` |  | Azure Data Explorer cluster URL (empty: no ADX series catalog). Takes effect on restart. Replaces `FUZER_ADX_URL`. |
| `sources.adx.database` | str | `` |  | ADX database. Replaces `FUZER_ADX_DATABASE`. |
| `sources.adx.catalog` | str | `SeriesCatalog` |  | ADX table listing the series. Replaces `FUZER_ADX_CATALOG`. |
| `sources.adx.token` | secret | `env:FUZER_ADX_TOKEN` |  | Where the ADX bearer token is kept. |
