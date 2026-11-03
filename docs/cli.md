# Command line

Generated from the argument parser (`src/fuzer/cli/__init__.py`); don't edit by hand (`uv run python -m fuzer.reference`).
Every command takes `--project` (the project to use) and `--pack` before the command name.

## `fuzer up`

start everything: data, register, drawings, web UI (the one command you need)

| Argument | What it does |
|---|---|
| `--fresh` | start over: empty the database (and make new fake data) |
| `--port` |  |
| `--demo` | a fake plant to run: okan (the Okan field), offshore or onshore |

## `fuzer ingest`

load source exports into Postgres

| Argument | What it does |
|---|---|
| `--data` | exports folder (default: $FUZER_DATA_DIR or data) |
| `--full` | read every dataset again, even unchanged ones |

## `fuzer rebuild`

rerun resolve / golden / graph

## `fuzer suggest-mappings`

draft mappings for tables that have none (review queue)

| Argument | What it does |
|---|---|
| `--data` | folder of new tables (default: $FUZER_DATA_DIR or data) |
| `--table` | only this table (repeatable); default: every unmapped table |
| `--claude` | ask Claude about columns rules can't name (costs money) |

## `fuzer source`

sources (#132): list the tables in the data folder, or preview one before it loads

| Argument | What it does |
|---|---|
| `action` (list / preview) |  |
| `table` | preview: the table (dataset) name |
| `--data` | exports folder (default: $FUZER_DATA_DIR or data) |
| `--rows` | preview: sample rows |

## `fuzer serve`

run the API

| Argument | What it does |
|---|---|
| `--host` |  |
| `--port` |  |

## `fuzer llm-link`

have Claude read operator logs and scanned P&IDs, then rebuild

| Argument | What it does |
|---|---|
| `--source` (logs / pids / docs / curves / all) |  |
| `--limit` | only this many entries (try a small sample first) |
| `--model` | Claude model id (default: the setting llm.model) |
| `--effort` (low / medium / high / xhigh / max) |  |
| `--per-request` | log entries per request |
| `--workers` | parallel requests |
| `--batch` | use the Batches API: half price, results within ~1 hour |
| `--force` | re-read entries that already have a result |
| `--dry-run` | show how much would be sent, call nothing |
| `--no-rebuild` |  |

## `fuzer pid-score`

score image reading against the fake data's answer key

| Argument | What it does |
|---|---|
| `--truth` | answer key (default: pid_truth.json in the exports folder) |

## `fuzer mcp`

run the MCP server for AI agents (stdio by default)

| Argument | What it does |
|---|---|
| `--http` | serve streamable HTTP instead of stdio |
| `--host` |  |
| `--port` |  |

## `fuzer jobs`

run the Dagster UI with schedules and sensors

| Argument | What it does |
|---|---|
| `--port` |  |

## `fuzer ask`

ask the operations assistant one question (Claude agents)

| Argument | What it does |
|---|---|
| `question` |  |
| `--model` | orchestrator model (default: the setting agents.model); specialists use agents.specialist_model |
| `--effort` (low / medium / high / xhigh / max) | orchestrator effort (default: the setting agents.effort); specialists use agents.specialist_effort |
| `--json` | print the answer, tools used and cost as JSON |

## `fuzer chat`

talk to the operations assistant (follow-up questions keep the context)

| Argument | What it does |
|---|---|
| `--model` | orchestrator model (default: the setting agents.model); specialists use agents.specialist_model |
| `--effort` (low / medium / high / xhigh / max) | orchestrator effort (default: the setting agents.effort); specialists use agents.specialist_effort |
| `--json` | print the answer, tools used and cost as JSON |

## `fuzer work`

work engine (#97): load the starting processes, run the agent worker, fire timers

| Argument | What it does |
|---|---|
| `action` (load / worker / tick) | load: import and publish definitions/*.bpmn; worker: run agent steps with the assistant's specialists (Claude; costs money); tick: fire due timers and escalate overdue tasks once |

## `fuzer limits`

operating limits (#101): learn normal ranges, check limits, scan for excursions

| Argument | What it does |
|---|---|
| `action` (learn / issues / scan) | learn: normal ranges per point and operating mode from history; issues: limits that look wrong or are missing; scan: record excursions and open work for IOW breaches past their time |
| `--since` | start (default: learn 90 days, scan 6 hours before --until) |
| `--until` | end (default: the latest daily report, else today) |
| `--step` | minutes between values (default: learn 60, scan 10) |

## `fuzer sre`

agentic SRE (#96): detect upsets in the process data since the last run

| Argument | What it does |
|---|---|
| `--since` | start (default: where the last run ended, less an hour) |
| `--until` | end (default: now, or the end of a fake plant's data) |
| `--handoff` | then prioritise, diagnose (Claude, capped) and hand off the new upsets to the workflow |
| `--max-diagnoses` | Claude diagnoses per run (0: none) |

## `fuzer settings`

settings (#131): list, get, set, reset, or show a setting's history

| Argument | What it does |
|---|---|
| `action` (list / get / set / reset / history) |  |
| `key` |  |
| `value` |  |
| `--group` | with list: only this group (SRE, Claude, ...) |
| `--project` | set or reset for this project (default: all projects) |
| `--by` | who is changing it (default: $USER) |
| `--reason` | why, kept in the history |

## `fuzer board`

the work board (#99): refresh it, or show the top cards

| Argument | What it does |
|---|---|
| `action` (refresh / show) |  |
| `--limit` |  |

## `fuzer report`

daily operations report (#98): close a day and draft it

| Argument | What it does |
|---|---|
| `--day` | the production day (default: the latest DAY001) |
| `--claude` | have Claude draft it from the facts (costs money) |

## `fuzer bench`

time the main read queries against the current database

## `fuzer eval`

score search, document links, section failure modes and log reading

| Argument | What it does |
|---|---|
| `--keys` | folder of answer-key JSON files (default: answer_keys in the exports folder) |
| `--agents` | also ask the operations assistant the questions in assistant.json (Claude; costs money) |
| `--domain` | with --agents: only the questions in this domain (repeatable), e.g. --domain charts |
| `--out` | with --agents: also write the full results (answers) to this JSON file |
| `--ops` | replay history through the operations detectors and score them against the fake plant's injected events (exits 1 if recall fell since the last run of the same period) |
| `--detector` | with --ops: the detector to replay (default: limits) |
| `--full` | with --ops: score everything scheduled (SRE, grouping, diagnosis, daily reports, opportunities) and fail if a score is under its floor or fell since the last run |
| `--live` | with --full: diagnose with the real Claude (costs money) |
| `--since` | with --ops: start (default: 28 days before --until) |
| `--until` | with --ops: end (default: the latest daily report) |

## `fuzer reset`

drop and recreate all tables

## `fuzer projects`

projects (#131): list them, show one, or save one to the database

| Argument | What it does |
|---|---|
| `action` (list / show / save) |  |
| `name` |  |
| `--by` |  |
