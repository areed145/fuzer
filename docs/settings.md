# Settings

Generated from the settings registry (`src/fuzer/core/settings.py`, #131); don't edit by hand (`uv run python -m fuzer.reference`).
Change them on the Settings page or with `fuzer settings set <key> <value>`; a project's value beats the global one, which beats the default. Secrets hold a reference (`env:NAME`), never the value.

## LLM

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `llm.provider` | choice | `anthropic` | anthropic / openai / azure_openai / ollama | Provider used for reader calls. |
| `llm.base_url` | str | `` |  | OpenAI-compatible endpoint (empty uses the provider default). |
| `llm.api_key` | secret | `env:ANTHROPIC_API_KEY` |  | Environment variable holding the reader provider API key. |
| `llm.model` | str | `claude-opus-5-5` |  | Model for log links, P&IDs, documents, curves, mapping suggestions and daily report drafts. Replaces `FUZER_LLM_MODEL`. |
| `llm.effort` | choice | `medium` | low / medium / high / xhigh / max | Effort for the readers. Replaces `FUZER_LLM_EFFORT`. |

## Agents

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `agents.provider` | choice | `anthropic` | anthropic / openai / azure_openai / ollama | Provider used for assistant and specialist agents. Takes effect on restart. |
| `agents.base_url` | str | `` |  | OpenAI-compatible endpoint (empty uses the provider default). Takes effect on restart. |
| `agents.api_key` | secret | `env:ANTHROPIC_API_KEY` |  | Environment variable holding the agents provider API key. Takes effect on restart. |
| `agents.model` | str | `claude-opus-5-5` |  | Model for the assistant's orchestrator. Takes effect on restart. Replaces `FUZER_AGENT_MODEL`. |
| `agents.effort` | choice | `high` | low / medium / high / xhigh / max | Effort for the assistant's orchestrator. Takes effect on restart. Replaces `FUZER_AGENT_EFFORT`. |
| `agents.specialist_model` | str | `` |  | Model for the specialists (empty: the orchestrator's). Takes effect on restart. Replaces `FUZER_AGENT_SPECIALIST_MODEL`. |
| `agents.specialist_effort` | choice | `medium` | low / medium / high / xhigh / max | Effort for the specialists. Takes effect on restart. Replaces `FUZER_AGENT_SPECIALIST_EFFORT`. |
| `agents.fallbacks` | bool | `True` |  | On a policy decline, let the API re-run the request on a fallback model. Takes effect on restart. Replaces `FUZER_AGENT_FALLBACKS`. |
| `agents.case_cap_usd` | float | `2.0` | ≥ 0 | Agent spend a case may reach (USD) before its next agent step goes to a person, with the spend and the cap on the case (0: no cap). |
| `agents.briefs` | bool | `True` |  | Have the owning specialist brief each new case of a type a pack briefs, when the agent worker has no step to run. |
| `agents.skill_drafts` | bool | `False` |  | Have a skill's specialist draft a change to its playbook once it has fallen short twice since its live version, as work for the skill's owner, when the agent worker has nothing else to do. |
| `agents.brief_cap_usd` | float | `0.5` | ≥ 0 | What one agent brief on a case may cost (USD); a brief that costs more says so on the case (0: no budget). |
| `assistant.retention_days` | int | `0` | ≥ 0 | Days the assistant keeps a conversation after its last message, with its images and answers (0: keep them). |
| `assistant.concurrent_questions` | int | `1` | 1 to 8 | Questions the web assistant can run at once per server process; prevents overlapping model/tool workloads. |
| `assistant.timeout_seconds` | int | `300` | 30 to 900 | Time limit for a web assistant question, including specialists. |

## Schedule

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `daily_report.close_cron` | str | `0 7 * * *` |  | When the day is closed and its report drafted (cron). Takes effect on restart. Replaces `FUZER_DAY_CLOSE_CRON`. |
| `work.lease_seconds` | int | `900` | ≥ 30 | How long an agent holds a workflow task before another may take it. Replaces `FUZER_WORK_LEASE_SECONDS`. |
| `work.agent_fallback_minutes` | int | `30` | ≥ 0 | Minutes an agent step may wait with no agent worker picking it up before it goes to its fallback role's person queue, with the reason on the case (0: wait forever). |
| `alerts.webhook` | secret | `env:FUZER_ALERT_WEBHOOK` |  | Where the URL failed scheduled runs are posted to (Teams, Slack or any JSON webhook) is kept. |
| `notify.smtp` | secret | `env:FUZER_SMTP_URL` |  | Where the mail server people are emailed through is kept, as smtp://user:password@host:port (port 465: TLS; others: STARTTLS when offered). |
| `notify.from` | str | `fuzer@localhost` |  | The address people's notification emails come from. |
| `notify.app_url` | str | `` |  | The application's address (https://fuzer.example.com), so an email or chat message links to the case (empty: the message names the case without a link). |

## Work

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `work.due_soon_minutes` | int | `60` | 0 to 1440 | Tell the person holding a step this many minutes before it is due (0: never). |
| `intake.sap_notifications` | bool | `True` |  | Raise open SAP notifications with no work order as exceptions for the IOC to dispatch, on each register refresh. |
| `closeout.apply_to_exports` | bool | `False` |  | Demo: apply a close-out sent back to a source system (an SAP completion or follow-up) to that system's export file, as the system would, so the next load sees it. |
| `closeout.recheck_days` | int | `30` |  | Days after a real problem is closed out to ask whoever closed it whether the fix held; the same failure on the same asset inside this window reopens the case as a repeat. |
| `readiness.permit_types` | str | `` |  | Work types (exception types, comma-separated) that need a valid permit to work, with its isolations in place, on the job's location before they are dispatched or started in the field; empty by default, since an upset or IOW breach is first a look, not hands-on work. The dispatch card and the phone show permits for every job (P5.5c, P5.11a). |
| `stewardship.bulk_accept_at` | float | `0.0` | 0 to 1 | Offer to accept, in one step, Claude's master data proposals at or above this confidence (0: never; set it once the proposal evals show they hold at that level). |
| `handover.shift_times` | str | `06:00,18:00` |  | Shift changes, as local times in the project's timezone (comma separated). At each one a shift handover is drafted for the ops lead queue (empty: off). |
| `exceptions.join_hours` | int | `48` | 0 to 720 | An exception about an asset (or the unit it is part of) whose open case another source's exception raised within this many hours joins that case, so one event reported by several systems is one case (0: never join). |
| `exceptions.around_before_hours` | int | `4` | 0 to 72 | A case shows the alarms and operator log entries about its event's assets from this many hours before its earliest finding (a trip's vibration climbs before it). |
| `exceptions.around_after_hours` | int | `6` | 0 to 72 | A case shows the alarms and operator log entries about its event's assets until this many hours after its latest finding (the checks and restart are logged after). |
| `exceptions.suppress_planned_work` | bool | `True` |  | A machine's trip, health or upset detection about an asset with planned work or a shutdown scheduled or in progress on it is counted under that window instead of opening a case (never a safety case type). Off: only shutdowns, turnarounds and people's windows count. |
| `exceptions.max_shelve_days` | int | `30` | 1 to 365 | The longest a person can shelve a standing condition off the open list; when its expiry passes it comes back on its own. |
| `providers.triage_role` | str | `role:engineer` |  | Where a detection provider set to triage first sends the case its alert opens, for a person to look at before it goes on (P3.8b). |
| `records.log_tolerance_hours` | int | `12` | 0 to 168 | Records mismatch: hours either side of a downtime record (and during it) an operator log entry about the same plant counts as its log. |
| `records.notification_tolerance_days` | int | `3` | 0 to 60 | Records mismatch: days either side of a downtime record a notification about the same plant counts as its notification. |
| `records.notification_categories` | str | `equipment` |  | Records mismatch: downtime reason categories that call for a maintenance notification (comma separated; the pack's downtime_reasons give each reason its category). |
| `records.lookback_days` | int | `7` | 1 to 90 | Records mismatch: days before the latest log entry whose mismatches are raised for the shift (older ones stay in the calc). |
| `intake.sap_notifications.max_age_days` | int | `60` | 1 to 3650 | Only raise notifications created this many days before the latest data (an older backlog is a planning question). |
| `intake.sap_notifications.due_hours` | str | `1:24,2:72,3:336,4:720` |  | When a dispatched SAP notification is due, by its SAP priority: priority:hours, comma separated, counted from when Fuzer raised it (empty: no due date). |

## Database

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `db.work_mem` | str | `256MB` |  | Postgres work_mem for the rebuild's big queries. Replaces `FUZER_WORK_MEM`. |

## SRE

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `sre.handoff.min_detections` | int | `3` | ≥ 1 | Detections an upset needs before it is handed off (a lone detection is evidence, not an upset worth a person's time). |
| `sre.handoff.max_diagnoses` | int | `10` | 0 to 100 | Upsets the assistant diagnoses per hand-off run. |
| `sre.handoff.max_usd` | float | `5.0` | ≥ 0 | Claude spend cap per hand-off run (USD). |
| `watch.max_usd` | float | `2.0` | ≥ 0 | Model spend cap per agentic watch run, all areas (USD). |
| `watch.max_areas` | int | `12` | 0 to 200 | Process areas the agentic watch reads per run. |
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
| `sources.pi.url` | str | `` |  | PI Web API base URL; seeds a pi_web_api connection in the source catalog (#225). Takes effect on restart. Replaces `FUZER_PI_WEB_API_URL`. |
| `sources.pi.data_server` | str | `` |  | PI Data Archive to read points from (empty: the first). Replaces `FUZER_PI_DATA_SERVER`. |
| `sources.pi.af_server` | str | `` |  | PI AF server (empty: the first). Replaces `FUZER_PI_AF_SERVER`. |
| `sources.pi.af_database` | str | `` |  | PI AF database (empty: the server's first). Replaces `FUZER_PI_AF_DATABASE`. |
| `sources.pi.user` | str | `` |  | PI Web API user for basic auth (empty: no auth). Replaces `FUZER_PI_USER`. |
| `sources.pi.password` | secret | `env:FUZER_PI_PASSWORD` |  | Where the PI Web API password is kept. |
| `sources.pi.verify_tls` | bool | `True` |  | Check the PI Web API's TLS certificate. Replaces `FUZER_PI_VERIFY_TLS`. |
| `sources.adx.url` | str | `` |  | Azure Data Explorer cluster URL; seeds an adx connection in the source catalog (#225). Takes effect on restart. Replaces `FUZER_ADX_URL`. |
| `sources.adx.database` | str | `` |  | ADX database. Replaces `FUZER_ADX_DATABASE`. |
| `sources.adx.catalog` | str | `SeriesCatalog` |  | ADX table listing the series. Replaces `FUZER_ADX_CATALOG`. |
| `sources.adx.token` | secret | `env:FUZER_ADX_TOKEN` |  | Where the ADX bearer token is kept. |

## Access

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `access.session_hours` | int | `8` | 1 to 24 | Absolute signed-in session lifetime in hours. |
| `access.api_concurrency` | int | `4` | 1 to 32 | Maximum simultaneous authenticated API requests per server process; bounds memory pressure on the single-instance demo, not a commercial quota. |

## Detection

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `detectors.on` | bool | `True` |  | Run the detectors; set for one detector as detectors.on@<name> on the Detection page. |
| `detectors.cadence` | choice | `` |  / 5min / 30min / hourly / 6h / daily | How often a scheduled detector runs, set for one detector as detectors.cadence@<name> (empty: the cadence it declares). Window detectors run in the SRE scan. |

## Demo

| Setting | Type | Default | Range | What it is |
|---|---|---|---|---|
| `demo.enabled` | bool | `True` |  | Show the Demo tab and guided onboarding for all approved users. Hiding Demo does not change permissions or disable the underlying tools. |
