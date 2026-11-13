# Command line

Generated from the argument parser (`src/fuzer/cli/__init__.py`); don't edit by hand (`uv run python -m fuzer.reference`).
Every command takes `--project` (the project to use) and `--pack` before the command name.

## `fuzer up`

start everything: data, register, drawings, web UI (the one command you need)

| Argument | What it does |
|---|---|
| `--fresh` | start over: empty the database (and make new fake data) |
| `--port` |  |
| `--demo` (harrier / kestrel / okan) | a shipped seed bundle to serve, with its exports |
| `--no-jobs` | don't start Dagster (schedules: SRE scans, studies, refreshes) |
| `--jobs-port` | port for the Dagster UI |
| `--no-worker` | don't start the agent worker (runs workflow agent steps such as daily report drafts) |

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

## `fuzer sources`

the source catalog (#225): systems, connections, datasets, mappings, runs

| Argument | What it does |
|---|---|
| `action` (list / show / preview / seed / export / import / export-pack / import-pack / conformance / approve / rollback / discard / run / pause / resume / retire / delete / delete-connection / delete-system / edit / add-dataset / add-system / edit-system / add-connection / edit-connection / connections / test / discover / runs / audit / profile / checks / add-check / remove-check / health / matching / rules / set-rule / rank / impact / why / calibrate) | list: datasets in the catalog and tables in the data folder no dataset covers, with their state; show: one dataset: system, connection, mapping versions, recent runs and changes; preview: sample rows, schema drift and what the rows would become; seed: import the built-in mapping files (never overwrites what a person changed); export: write the catalog as YAML (mapping files and systems.yaml), for review and other environments; import: a mapping file becomes a draft version; systems.yaml updates systems and connections; export-pack: SYSTEM: one system as a source pack (its connections, datasets, mappings in use and checks) in one YAML file, for another project or database (--out FILE); import-pack: FILE: install a source pack: its system, connections, datasets, mappings, checks, work types and detectors; safe to repeat (--config CONNECTION.key=value changes a connection's config, e.g. its folder here); conformance: FILE\|SYSTEM: run the source pack conformance suite (installs the pack and loads its fixture on this project; a built-in pack such as sap loads the project's exports; use a scratch database); approve: make a draft mapping version the one in use; rollback: put an older mapping version back in use (as a new version); discard: set a draft mapping version aside without using it; run: load datasets now (all active ones, or the named ones); pause: stop loading a dataset; its records stay; resume: start loading a paused dataset again; retire: stop loading a dataset for good: --retract removes its records, --keep keeps them as history; delete: undo adding a dataset: its records, its mapping (when nothing else uses it) and the dataset go; delete-connection: a connection by id (no dataset may read through it); delete-system: a system with no datasets, and its connections; edit: change a dataset (--set field=value): load_mode, drift_policy, schedule, key, location, ...; add-dataset: add a dataset to a system (--set field=value; --set connection=<name> picks the connection, else the system's first); add-system: add a system of record (--set field=value); edit-system: change a system (--set field=value): owner, timezone, decimal, date_format, rank, ...; add-connection: add a connection to a system: NAME TYPE (--config k=v, --secret k=env:NAME); edit-connection: change a connection by id (--config k=v, --secret k=env:NAME, --set enabled=false); connections: the connections, and whether their secrets resolve; test: test a connection by id: reach, authenticate, list, read a few rows; discover: what a connection can see, and what no dataset covers yet; runs: load history (of one dataset, or all); audit: catalog change history; profile: profile a dataset now (kept); without --now, its saved profiles; checks: a dataset's checks (or all) with their latest results; add-check: DATASET KIND: key_unique \| not_null \| range \| accepted \| row_count \| freshness \| coverage (--column, --set param=value, --blocking); remove-check: a check by id; health: per dataset: freshness, last run, failing checks, profile drift, coverage, open reviews; matching: match statistics per source after each rebuild; rules: survivorship and matching rules, and the systems' rank (the default priority); set-rule: SCOPE NAME: field <field> --set priority=[review,sap] --set review_on_conflict=true, or match <kind> --set auto_accept=0.9 --set review_min=0.6; rank: SYSTEM N: a system's place in the default priority (lower wins); impact: what a mapping change would do (a file, or a version id), before it is approved; why: FUZER_ID FIELD: the records, mapping versions and runs behind a golden value; calibrate: [KEYS] measure the last rebuild's match scores per source against the stewards' decisions and an answer key folder (default: answer_keys in the data folder); --adopt sets each source's thresholds from it (P2.2) |
| `names` | dataset, system, connection id or file names, as the action needs |
| `--data` | the project's data folder (default: $FUZER_DATA_DIR or data) |
| `--rows` | preview, test: sample rows |
| `--set` | field values (YAML scalars) |
| `--config` | connection config |
| `--secret` | connection secret reference |
| `--out` | export: folder to write the files to; export-pack: the file (default: print them) |
| `--full` | run: read every dataset again, even unchanged ones |
| `--retract` | retire: remove its records |
| `--keep` | retire: keep its records as history |
| `--dry-run` | retire: only show what would go |
| `--column` | add-check: the column it looks at |
| `--blocking` | add-check: a failure holds the load |
| `--now` | profile: profile it now |
| `--adopt` | calibrate: set each source's match thresholds from it |
| `--reason` | why (kept in the audit log) |
| `--by` |  |

## `fuzer source`



| Argument | What it does |
|---|---|
| `action` (list / show / preview / seed / export / import / export-pack / import-pack / conformance / approve / rollback / discard / run / pause / resume / retire / delete / delete-connection / delete-system / edit / add-dataset / add-system / edit-system / add-connection / edit-connection / connections / test / discover / runs / audit / profile / checks / add-check / remove-check / health / matching / rules / set-rule / rank / impact / why / calibrate) | list: datasets in the catalog and tables in the data folder no dataset covers, with their state; show: one dataset: system, connection, mapping versions, recent runs and changes; preview: sample rows, schema drift and what the rows would become; seed: import the built-in mapping files (never overwrites what a person changed); export: write the catalog as YAML (mapping files and systems.yaml), for review and other environments; import: a mapping file becomes a draft version; systems.yaml updates systems and connections; export-pack: SYSTEM: one system as a source pack (its connections, datasets, mappings in use and checks) in one YAML file, for another project or database (--out FILE); import-pack: FILE: install a source pack: its system, connections, datasets, mappings, checks, work types and detectors; safe to repeat (--config CONNECTION.key=value changes a connection's config, e.g. its folder here); conformance: FILE\|SYSTEM: run the source pack conformance suite (installs the pack and loads its fixture on this project; a built-in pack such as sap loads the project's exports; use a scratch database); approve: make a draft mapping version the one in use; rollback: put an older mapping version back in use (as a new version); discard: set a draft mapping version aside without using it; run: load datasets now (all active ones, or the named ones); pause: stop loading a dataset; its records stay; resume: start loading a paused dataset again; retire: stop loading a dataset for good: --retract removes its records, --keep keeps them as history; delete: undo adding a dataset: its records, its mapping (when nothing else uses it) and the dataset go; delete-connection: a connection by id (no dataset may read through it); delete-system: a system with no datasets, and its connections; edit: change a dataset (--set field=value): load_mode, drift_policy, schedule, key, location, ...; add-dataset: add a dataset to a system (--set field=value; --set connection=<name> picks the connection, else the system's first); add-system: add a system of record (--set field=value); edit-system: change a system (--set field=value): owner, timezone, decimal, date_format, rank, ...; add-connection: add a connection to a system: NAME TYPE (--config k=v, --secret k=env:NAME); edit-connection: change a connection by id (--config k=v, --secret k=env:NAME, --set enabled=false); connections: the connections, and whether their secrets resolve; test: test a connection by id: reach, authenticate, list, read a few rows; discover: what a connection can see, and what no dataset covers yet; runs: load history (of one dataset, or all); audit: catalog change history; profile: profile a dataset now (kept); without --now, its saved profiles; checks: a dataset's checks (or all) with their latest results; add-check: DATASET KIND: key_unique \| not_null \| range \| accepted \| row_count \| freshness \| coverage (--column, --set param=value, --blocking); remove-check: a check by id; health: per dataset: freshness, last run, failing checks, profile drift, coverage, open reviews; matching: match statistics per source after each rebuild; rules: survivorship and matching rules, and the systems' rank (the default priority); set-rule: SCOPE NAME: field <field> --set priority=[review,sap] --set review_on_conflict=true, or match <kind> --set auto_accept=0.9 --set review_min=0.6; rank: SYSTEM N: a system's place in the default priority (lower wins); impact: what a mapping change would do (a file, or a version id), before it is approved; why: FUZER_ID FIELD: the records, mapping versions and runs behind a golden value; calibrate: [KEYS] measure the last rebuild's match scores per source against the stewards' decisions and an answer key folder (default: answer_keys in the data folder); --adopt sets each source's thresholds from it (P2.2) |
| `names` | dataset, system, connection id or file names, as the action needs |
| `--data` | the project's data folder (default: $FUZER_DATA_DIR or data) |
| `--rows` | preview, test: sample rows |
| `--set` | field values (YAML scalars) |
| `--config` | connection config |
| `--secret` | connection secret reference |
| `--out` | export: folder to write the files to; export-pack: the file (default: print them) |
| `--full` | run: read every dataset again, even unchanged ones |
| `--retract` | retire: remove its records |
| `--keep` | retire: keep its records as history |
| `--dry-run` | retire: only show what would go |
| `--column` | add-check: the column it looks at |
| `--blocking` | add-check: a failure holds the load |
| `--now` | profile: profile it now |
| `--adopt` | calibrate: set each source's match thresholds from it |
| `--reason` | why (kept in the audit log) |
| `--by` |  |

## `fuzer serve`

run the API

| Argument | What it does |
|---|---|
| `--host` |  |
| `--port` |  |

## `fuzer llm-link`

have the configured LLM read operator logs and scanned P&IDs, then rebuild

| Argument | What it does |
|---|---|
| `--source` (logs / pids / docs / curves / all) |  |
| `--limit` | only this many entries (try a small sample first) |
| `--model` | model id (default: the setting llm.model) |
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
| `--record` | save the scores to evals/runs/ as the new baseline |
| `--gate` | fail under an element's floor, or on a drop since the last recorded run of the same model |

## `fuzer mcp`

run the MCP server for AI agents (stdio by default)

| Argument | What it does |
|---|---|
| `--http` | serve streamable HTTP instead of stdio |
| `--host` |  |
| `--port` |  |
| `--as` | answer as this account (its id or user:<id>), seeing the restricted documents its groups see; without it, restricted documents are left out |

## `fuzer jobs`

run the Dagster UI with schedules and sensors

| Argument | What it does |
|---|---|
| `--port` |  |

## `fuzer ask`

ask the operations assistant one question

| Argument | What it does |
|---|---|
| `question` |  |
| `--model` | orchestrator model (default: the setting agents.model); specialists use agents.specialist_model |
| `--effort` (low / medium / high / xhigh / max) | orchestrator effort (default: the setting agents.effort); specialists use agents.specialist_effort |
| `--json` | print the answer, tools used and cost as JSON |
| `--as` | answer as this account (its id or user:<id>), seeing the restricted documents its groups see; without it, restricted documents are left out |

## `fuzer chat`

talk to the operations assistant (follow-up questions keep the context)

| Argument | What it does |
|---|---|
| `--model` | orchestrator model (default: the setting agents.model); specialists use agents.specialist_model |
| `--effort` (low / medium / high / xhigh / max) | orchestrator effort (default: the setting agents.effort); specialists use agents.specialist_effort |
| `--json` | print the answer, tools used and cost as JSON |
| `--as` | answer as this account (its id or user:<id>), seeing the restricted documents its groups see; without it, restricted documents are left out |

## `fuzer work`

work engine (#97): load the starting processes, run the agent worker, fire timers

| Argument | What it does |
|---|---|
| `action` (load / worker / tick) | load: import and publish definitions/*.bpmn; worker: run agent steps with the assistant's specialists; tick: fire due timers and escalate overdue tasks once |

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

## `fuzer audit`

the project's audit record (R.5): export a window, or show what it covers

| Argument | What it does |
|---|---|
| `action` (export / coverage) |  |
| `--since` | start of the window, ISO date or time (default: 30 days before --until) |
| `--until` | end of the window, exclusive (default: now) |
| `--format` (csv / json) | default csv |
| `--out` | write here instead of standard output |
| `--by` | who is exporting (default: $USER); the export is itself recorded |

## `fuzer operating-model`

the operating model as a package (P8.1): export, apply, adopt, deviations, conformance, name a deviation

| Argument | What it does |
|---|---|
| `action` (export / apply / adopt / deviations / conformance / name / unname / applied / standing / fleet) |  |
| `package` | a package folder, or `standard` (export: the folder to write; deviations and conformance: default the model the project's bundle follows, apply: default `standard`) |
| `--part` | with name and unname: roles, work_types, settings, processes or forms |
| `--key` | with name and unname: the role, work type, setting, process or form |
| `--reason` | with name: why this project differs there |
| `--owner` | with name: the role or person who owns the difference |
| `--review-by` | with name: when it is next reviewed (YYYY-MM-DD; default a year) |
| `--days` | with standing and fleet: the days of work to read (P8.2) |
| `--name` | with export: the model's name |
| `--version` | with export: the model's version |
| `--by` | who is applying it (default: $USER) |

## `fuzer model-change`

change control for the standard (P8.3): propose, promote, test-run, review, publish, rollout, rollback, versions, history

| Argument | What it does |
|---|---|
| `action` (propose / promote / test-run / review / publish / rollout / rollback / versions / history) |  |
| `--version` | with test-run, review, publish and rollout: the version |
| `--changes` | with propose: a JSON file of {part: {key: value, or null to remove}} |
| `--part` | with promote: the part of this project's deviation |
| `--key` | with promote: the key of this project's deviation |
| `--summary` | with propose and promote: what it changes and why |
| `--approve` | with review: approve (default: reject) |
| `--note` | with review: why |
| `--target` | with rollout, rollback and history: the project (default: this one) |
| `--reason` | with rollback: why |
| `--by` | who is acting (default: $USER) |

## `fuzer onboarding`

the onboarding journey (P8.4): each step ready, blocked or to do, and what each gap prevents

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
| `--memory` | score the assistant's memory with assistant_memory.json: learn from teaching answers, then ask later questions (Claude; costs money; removes what it stored) |
| `--domain` | with --agents: only the questions in this domain (repeatable), e.g. --domain charts |
| `--out` | with --agents or --packs: also write the full results (answers) to this JSON file |
| `--record` | with --agents, --packs or --ops --full: keep the scores in evals/runs/ for docs/eval-results.md |
| `--ops` | replay history through the operations detectors and score them against the fake plant's injected events (exits 1 if recall fell since the last run of the same period) |
| `--detector` | with --ops: the detector to replay (default: limits) |
| `--full` | with --ops: score everything scheduled (SRE, grouping, diagnosis, daily reports, opportunities) and fail if a score is under its floor or fell since the last run |
| `--live` | with --full: diagnose with the real Claude (costs money) |
| `--packs` | ask each specialist pack's questions for this project's bundle and say which packs pass (P4.2; Claude; costs money; exits 1 if any pack falls short); --specialist picks packs |
| `--specialist` | with --packs: only this pack, by skill path (repeatable) |
| `--loop` | drive every type of work arrive to counted and say which step failed for which type (P1.7; writes cases, so run it on a scratch copy; exits 1 if any type fails) |
| `--sweep` | run each work detector over the bundle's history and score its raises against the events its evals/detections.yaml declares (P3.6a; writes cases, so run it on a scratch copy; exits 1 if a score is under its floor); --detector picks one |
| `--roles` | score the measurement roles proposed for each point against the fake plant's process model (P1.4; exits 1 under 90%% right or if a wrong role was confident) |
| `--since` | with --ops: start (default: 28 days before --until) |
| `--until` | with --ops: end (default: the latest daily report) |

## `fuzer reset`

drop and recreate all tables

## `fuzer init`

initialise this database for a facility from a seed bundle (#376); doesn't ingest or rebuild

| Argument | What it does |
|---|---|
| `--bundle` | the bundle folder (with bundle.yaml) or its bundle.yaml |
| `--upgrade` | replace the stored project config of the same project with the bundle's |
| `--by` | who the stored project is recorded as set by |

## `fuzer projects`

projects (#131): list them, show one, or save one to the database

| Argument | What it does |
|---|---|
| `action` (list / show / save) |  |
| `name` |  |
| `--by` |  |
