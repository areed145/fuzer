# Database schema

Generated from each module's `schema.sql`; don't edit by hand (`uv run python -m fuzer.reference`).
One Postgres database; `fuzer` creates or updates it on start (idempotent). Each module owns the tables in its own `schema.sql`, applied in the order below.

## Core

[`src/fuzer/core/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/core/schema.sql)

### `schema_version`

Which schema this database is at (the hash of every module's schema.sql): init_schema re-runs them only when one changed, since their migrations take exclusive locks on busy tables.

| Column | Type | Note |
|---|---|---|
| `id` | int |  |
| `hash` | text |  |
| `applied_at` | timestamptz |  |

### `access_account`

Sign-in identities are independent of workflow routing roles.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `tenant_id` | text |  |
| `object_id` | text |  |
| `display_name` | text |  |
| `enabled` | boolean |  |
| `owner` | boolean |  |
| `created_at` | timestamptz |  |

### `access_role`

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `description` | text |  |
| `permissions` | text[] |  |

### `access_membership`

| Column | Type | Note |
|---|---|---|
| `account_id` | text |  |
| `role` | text |  |

### `access_session`

| Column | Type | Note |
|---|---|---|
| `token_hash` | text |  |
| `account_id` | text |  |
| `csrf` | text |  |
| `expires_at` | timestamptz |  |

### `access_project_grant`

Application roles in a project other than the database's first one (whose roles are access_membership). Owners span every project; anyone else sees a project only through a grant here.

| Column | Type | Note |
|---|---|---|
| `account_id` | text |  |
| `project` | text |  |
| `role` | text |  |

### `access_area_grant`

Area grants (R.7, D7): narrow what an account may act on in one project to areas of its register (area or facility fuzer ids). No rows: the whole project. Reading is never narrowed.

| Column | Type | Note |
|---|---|---|
| `account_id` | text |  |
| `project` | text |  |
| `area` | text |  |

### `access_document_group`

Restricted documents (R.5, #29): a document class (its doc_type) an owner restricts to a group in one project, and the groups each account is in there. A class with no row is open to everyone who can read the project.

| Column | Type | Note |
|---|---|---|
| `project` | text |  |
| `doc_type` | text |  |
| `grp` | text |  |

### `access_group_grant`

| Column | Type | Note |
|---|---|---|
| `account_id` | text |  |
| `project` | text |  |
| `grp` | text |  |

### `access_login`

| Column | Type | Note |
|---|---|---|
| `token_hash` | text |  |
| `flow` | jsonb |  |
| `expires_at` | timestamptz |  |

### `access_event`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `actor` | text |  |
| `action` | text |  |
| `data` | jsonb |  |
| `at` | timestamptz |  |

### `setting`

Settings (#131): values people set, per project ('project:<name>') or for all ('global'); the registry in core/settings.py holds each setting's type, default and range. Every change is kept.

| Column | Type | Note |
|---|---|---|
| `scope` | text |  |
| `key` | text |  |
| `value` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `setting_history`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `scope` | text |  |
| `key` | text |  |
| `old` | jsonb |  |
| `new` | jsonb |  |
| `by` | text |  |
| `reason` | text |  |
| `at` | timestamptz |  |

### `project`

Projects (#131): what one name selects (pack, data folder, mappings, time-series provider, timezone); a shipped seed bundle of the same name is the fallback when there is no row.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `config` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `ontology_kind`

The data model as data (#241): entity kinds and link types. core/ontology.yaml seeds the core rows; a person's edit (edited) is kept over a later change to the file. from_kinds / to_kinds empty means any kind.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `label` | text |  |
| `description` | text |  |
| `"group"` | text |  |
| `colour` | text |  |
| `label_field` | text |  |
| `status` | text |  |
| `edited` | boolean |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `ontology_relation`

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `label` | text |  |
| `description` | text |  |
| `view` | text |  |
| `dated` | boolean |  |
| `from_kinds` | text[] |  |
| `to_kinds` | text[] |  |
| `status` | text |  |
| `edited` | boolean |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `ontology_audit`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `at` | timestamptz |  |
| `by` | text |  |
| `object` | text | kind \| relation |
| `name` | text |  |
| `action` | text | create \| update \| delete \| rename |
| `old` | jsonb |  |
| `new` | jsonb |  |
| `reason` | text |  |

### `event_outbox`

The event outbox (core/events.py): what happened, written in the transaction that made the change, then handed to subscribers by the work tick. A handler that fails leaves its event pending, with the error, to retry.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `topic` | text | exception.raised, exception.cleared, ... |
| `ref` | text, | what it is about: exception:<id>, work_item:<id>, ... |
| `payload` | jsonb |  |
| `created_at` | timestamptz |  |
| `delivered_at` | timestamptz |  |
| `attempts` | integer |  |
| `last_error` | text |  |

### `gate_policy`

Human gates as policy (P5.10, #619; core/gates.py). The fixed gates are code and never configurable; a confidence gate's thresholds (per asset criticality) and the safety case types are data here, over the shipped defaults.

| Column | Type | Note |
|---|---|---|
| `kind` | text |  |
| `settings` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `gate_decision`

Every automatic or agent decision a gate saw: its confidence, the threshold applied and whether the gate fired (stopped it for a person), with the reason a person reads on the case.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text |  |
| `actor` | text |  |
| `ref` | text |  |
| `item_id` | bigint |  |
| `confidence` | double precision |  |
| `threshold` | double precision |  |
| `criticality` | text |  |
| `could_not_check` | text[] |  |
| `fired` | boolean |  |
| `reason` | text |  |
| `at` | timestamptz |  |

## Work engine

[`src/fuzer/workflow/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow/schema.sql)

### `work_definition`

Work engine (#97): BPMN definitions with versions, running processes, their steps (tokens), the work items people and agents see, and an append-only history of everything that happened to them.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `process_key` | text | the bpmn:process id |
| `version` | int |  |
| `name` | text |  |
| `xml` | text | the whole .bpmn file |
| `state` | text | draft \| checked \| published \| superseded |
| `problems` | jsonb |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `published_by` | text |  |
| `published_at` | timestamptz |  |

### `work_form`

form-js task forms, by the key a userTask names in fuzer:form, with versions. A task shows the latest.

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `version` | int |  |
| `schema` | jsonb | the form-js schema (plus `fuzer.bulk`: bulk actions) |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

### `work_person`

People by name and the roles they hold (everyone sees everything; Entra groups later).

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `roles` | text[] |  |

### `work_person_cover`

Overarching people (#497): specialists and monitoring staff whose work cuts across fields. Shared by every project in the instance; their roles count in each project they cover (`projects` empty: every project), on top of any roles that project's own roster gives them. Routing roles only: whether they may open a project is access's call.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `roles` | text[] |  |
| `projects` | text[] |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `work_role`

Roles are data (workflow/roles.py): person roles hold steps people do, agent roles steps agents do. A role's default SLA applies to steps that set none; when a step's SLA passes it moves to the step's escalateTo, else the role's escalate_to (chained up to three hops, each with that role's SLA). Assignment: queue (whoever claims it), round_robin or least_loaded among the role's members who aren't away.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `kind` | text |  |
| `description` | text |  |
| `default_sla` | text, | ISO 8601 duration |
| `escalate_to` | text |  |
| `assignment` | text |  |
| `last_assigned` | text, | round_robin: who got the last one |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `work_item`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `type` | text |  |
| `title` | text |  |
| `subjects` | text[] | fuzer ids the work is about |
| `state` | text | open \| done \| cancelled, or a free state on items without a process |
| `outcome` | text, | the end event reached |
| `step` | text, | the open steps' names |
| `priority` | double precision, | #100 score |
| `assignee` | text, | a person, when one has it |
| `role` | text, | the role or agent whose queue it is in |
| `due` | timestamptz |  |
| `created_by` | text |  |
| `source` | text |  |
| `payload` | jsonb |  |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |
| `closed_at` | timestamptz |  |

### `work_draft`

What the assistant proposes: it never writes to a queue. A draft belongs to whoever asked and becomes at most one work item, when that person confirms it.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `title` | text |  |
| `role` | text |  |
| `summary` | text |  |
| `evidence` | text[] |  |
| `subjects` | text[] |  |
| `by_user` | text |  |
| `item_id` | bigint |  |
| `confirmed_by` | text |  |
| `confirmed_at` | timestamptz |  |
| `created_at` | timestamptz |  |

### `work_process`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `definition_id` | bigint |  |
| `process_key` | text |  |
| `item_id` | bigint |  |
| `parent_token` | bigint, | the call activity step that started it |
| `correlation_key` | text |  |
| `state` | text | running \| completed \| terminated \| cancelled |
| `outcome` | text |  |
| `data` | jsonb |  |
| `started_by` | text |  |
| `started_at` | timestamptz |  |
| `ended_at` | timestamptz |  |

### `work_token`

One row per step a process reached. Rows stay once done, so they are also the path taken.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `process_id` | bigint |  |
| `item_id` | bigint |  |
| `element` | text |  |
| `name` | text |  |
| `kind` | text | auto \| user \| agent \| timer \| message \| join \| call \| fix \| end |
| `state` | text | ready \| waiting \| done \| cancelled |
| `flow` | text, | the sequence flow it arrived on |
| `role` | text |  |
| `assignee` | text |  |
| `requires_human` | boolean |  |
| `due_at` | timestamptz |  |
| `escalated_at` | timestamptz |  |
| `message_name` | text |  |
| `correlation_key` | text |  |
| `attached_to` | bigint, | boundary event: the activity step it watches |
| `cycles_left` | int |  |
| `run_after` | timestamptz |  |
| `claimed_by` | text |  |
| `lease_until` | timestamptz |  |
| `attempts` | int |  |
| `checkpoint` | jsonb |  |
| `error` | text |  |
| `created_at` | timestamptz |  |
| `finished_at` | timestamptz |  |
| `finished_by` | text |  |

### `work_event`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `item_id` | bigint |  |
| `process_id` | bigint |  |
| `token_id` | bigint |  |
| `definition_id` | bigint |  |
| `kind` | text | created, started, step_started, assigned, claimed, released, completed, commented |
| `actor` | text | a person's name, agent:<role> or system |
| `element` | text |  |
| `data` | jsonb |  |
| `at` | timestamptz |  |

### `work_notification`

Telling the assignee (P5.2, #604): dispatch, hand-back, escalation, automatic assignment and due-soon each tell the person concerned (workflow/notify.py), read from work_event by a cursor so every path that moves work is covered. `key` makes each one once. Every notification shows in the person's list on My work; `delivery` is its send on their chosen channel (null: in-app only; queued, held for quiet hours, sent, failed).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `key` | text |  |
| `person` | text |  |
| `kind` | text | dispatched, handed_back, escalated, assigned, due_soon |
| `item_id` | bigint |  |
| `token_id` | bigint |  |
| `text` | text |  |
| `link` | text |  |
| `channel` | text |  |
| `delivery` | text |  |
| `error` | text |  |
| `created_at` | timestamptz |  |
| `sent_at` | timestamptz |  |
| `read_at` | timestamptz |  |

### `work_notify_cursor`

| Column | Type | Note |
|---|---|---|
| `id` | int |  |
| `last_event` | bigint |  |

### `work_notify_pref`

How each person wants to be told: in_app, email, teams or slack, at `address` (an email address, or for Teams and Slack where their webhook URL is kept: env:NAME), and quiet hours in the project's timezone when nothing external is sent (held until they end; it still shows in-app).

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `channel` | text |  |
| `address` | text |  |
| `quiet_from` | time |  |
| `quiet_to` | time |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `work_home_seen`

Home is my shift (P5.1): when each person last looked at Home, so it can say what changed since.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `seen_at` | timestamptz |  |

### `work_watch`

A watch (P5.1b): a person follows an asset (its fuzer id) or a case ('case:<id>') without being assigned it.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `ref` | text |  |
| `created_at` | timestamptz |  |

### `work_view`

Saved views per role on All work (P5.1c, #651).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `role` | text |  |
| `name` | text |  |
| `list` | text |  |
| `filters` | jsonb |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

### `operating_model_applied`

The operating model each project applied (P8.1, workflow/operating_model.py): the package's name, version and the digest of its content, newest last.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `project` | text |  |
| `name` | text |  |
| `version` | int |  |
| `digest` | text |  |
| `applied_by` | text |  |
| `applied_at` | timestamptz |  |

### `operating_model_deviation`

A project's named deviations from its operating model (P8.1, workflow/operating_model.py): why it differs and who owns the difference. A difference without a name here is an unnamed deviation.

| Column | Type | Note |
|---|---|---|
| `project` | text |  |
| `part` | text |  |
| `key` | text |  |
| `reason` | text |  |
| `owner` | text |  |
| `named_by` | text |  |
| `named_at` | timestamptz |  |

### `operating_model_version`

Versions of an operating model beyond the one that ships (P8.3, workflow/model_changes.py): proposed, test-run on closed cases, reviewed by someone other than its author, published. Shared, since one standard serves every asset.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `version` | int |  |
| `base_version` | int |  |
| `package` | jsonb |  |
| `digest` | text |  |
| `changes` | jsonb |  |
| `summary` | text |  |
| `state` | text |  |
| `proposed_by` | text |  |
| `proposed_at` | timestamptz |  |
| `test_run` | jsonb |  |
| `reviewed_by` | text |  |
| `reviewed_at` | timestamptz |  |
| `review_note` | text |  |
| `published_by` | text |  |
| `published_at` | timestamptz |  |

### `operating_model_rollout`

Each asset's rollouts of a published version, with the version it ran before so it can roll back (P8.3).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `project` | text |  |
| `name` | text |  |
| `version` | int |  |
| `from_name` | text |  |
| `from_version` | int |  |
| `state` | text |  |
| `by` | text |  |
| `at` | timestamptz |  |
| `rolled_back_by` | text |  |
| `rolled_back_at` | timestamptz |  |
| `rollback_reason` | text |  |

## Sources and ingestion

[`src/fuzer/sources/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/sources/schema.sql)

### `source_record`

Raw records exactly as received from each source.

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `record_type` | text |  |
| `source_key` | text |  |
| `payload` | jsonb |  |
| `first_seen` | timestamptz |  |
| `last_seen` | timestamptz |  |

### `llm_extraction`

Cached Claude reads of free text (operator logs). Rebuilds use these and never call the model; an entry is re-read only when its text changes (or on request).

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `source_key` | text |  |
| `text_hash` | text |  |
| `model` | text |  |
| `prompt_version` | text |  |
| `result` | jsonb |  |
| `created_at` | timestamptz |  |

### `source_record_history`

Earlier versions of source records: written by ingest when a record changes or disappears.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `source` | text |  |
| `record_type` | text |  |
| `source_key` | text |  |
| `payload` | jsonb |  |
| `first_seen` | timestamptz |  |
| `replaced_at` | timestamptz |  |
| `change` | text |  |

### `source_system`

Sources (#225): the systems of record, how to reach them, what each dataset is and how it loads, as rows (sources/catalog.py). The built-in mapping files only seed a fresh database (`fuzer sources seed`). One catalog per database, like the records it loads.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `name` | text | what source_record.source says: sap, pi, ec, maximo, ... |
| `kind` | text, | erp \| historian \| allocation \| documents \| gis \| lab \| logs \| other |
| `description` | text |  |
| `owner` | text, | who answers for the system; alerts go to them (#225) |
| `steward` | text, | who looks after its data in Fuzer |
| `timezone` | text, | how the source writes dates and numbers, applied before mapping |
| `encoding` | text |  |
| `decimal` | text |  |
| `date_format` | text |  |
| `rank` | integer, | survivorship: a lower rank wins a field (pack priority order) |
| `enabled` | boolean |  |
| `notes` | text, | reliability, known issues |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `source_connection`

How to reach a system. Secrets are references (env:NAME), never values. A system can have several.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `system_id` | bigint |  |
| `name` | text |  |
| `type` | text | folder \| pi_web_api \| adx (sources/connections.py) |
| `config` | jsonb |  |
| `secrets` | jsonb | name -> reference |
| `enabled` | boolean |  |
| `last_tested_at` | timestamptz |  |
| `last_test` | jsonb, | each step of the last test with its outcome and timing |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `source_dataset`

One table or feed: where it is, how it loads, and what it held at its last load.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `name` | text |  |
| `system_id` | bigint |  |
| `connection_id` | bigint |  |
| `record_type` | text, | tables: the record type its rows land as |
| `reader` | text, | nested exports: the named reader (pi_af, ec_network, ...) |
| `location` | text, | where it is on its connection (default: its name) |
| `connector` | text, | csv \| parquet \| delta \| excel \| json \| geojson (default: by file) |
| `key` | text[], | record key columns, joined by ">" |
| `watermark` | text, | append: rows at or after the last value are read |
| `load_mode` | text |  |
| `drift_policy` | text |  |
| `status` | text |  |
| `schedule` | text, | cron; empty: loads with refresh_all and the new-exports sensor |
| `expected_hours` | real, | freshness: a new delivery within this long, else stale |
| `owner` | text |  |
| `notes` | text |  |
| `seeded` | jsonb, | the values the seed gave it: a later seed updates only those |
| `loaded_with` | text, | state at its last load: connector kind, fingerprint, schema |
| `fingerprint` | text, | watermark position, rows |
| `schema` | jsonb |  |
| `position` | jsonb |  |
| `rows` | bigint |  |
| `loaded_at` | timestamptz |  |
| `checked_at` | timestamptz, | last looked at (unchanged loads too) |
| `last_run_id` | bigint |  |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `mapping_version`

Mapping versions: what each column means, per source and record type. The latest approved version is the one in use; drafts wait for approval, and a rollback approves a copy of an older one (parent_id).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `source` | text |  |
| `record_type` | text |  |
| `dataset` | text |  |
| `yaml` | text |  |
| `approved_by` | text |  |
| `approved_at` | timestamptz |  |

### `source_run`

One row per load of a dataset that changed (unchanged ones only touch source_dataset.checked_at).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `dataset_id` | bigint |  |
| `dataset` | text |  |
| `trigger` | text | manual \| schedule \| sensor \| api \| seed |
| `status` | text | done \| held \| failed |
| `started_at` | timestamptz |  |
| `finished_at` | timestamptz |  |
| `connector` | text |  |
| `fingerprint` | text |  |
| `schema` | jsonb |  |
| `position` | jsonb |  |
| `mappings` | jsonb, | the mapping version ids in use, by source/record type |
| `rows_read` | bigint |  |
| `inserted` | bigint |  |
| `updated` | bigint |  |
| `unchanged` | bigint |  |
| `removed` | bigint |  |
| `error` | text |  |
| `detail` | jsonb, | held: the schema drift; failed: what broke |
| `seconds` | real |  |

### `source_profile`

A profile of each load (sources/quality.py): per column type, fill %, distinct count, top values and samples; the key's duplicates and empties; what changed since the profile before (drift).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `dataset_id` | bigint |  |
| `run_id` | bigint |  |
| `taken_at` | timestamptz |  |
| `rows` | bigint |  |
| `sampled` | boolean |  |
| `columns` | jsonb |  |
| `key` | jsonb |  |
| `drift` | jsonb |  |

### `source_check`

Expectations per dataset and their results: load checks run before a load is applied (a failing blocking one holds it), freshness and coverage are judged by the health summary and after rebuilds.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `dataset_id` | bigint |  |
| `kind` | text |  |
| `'coverage'))` |  |  |
| `column_name` | text |  |
| `params` | jsonb |  |
| `blocking` | boolean |  |
| `enabled` | boolean |  |
| `note` | text |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

### `source_check_result`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `check_id` | bigint |  |
| `run_id` | bigint |  |
| `at` | timestamptz |  |
| `ok` | boolean |  |
| `value` | double precision |  |
| `detail` | jsonb |  |

### `source_match_stat`

Per source after each rebuild (90 days kept): link coverage, how its records matched, what it shares with other sources, golden fields won, open reviews.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `built_at` | timestamptz |  |
| `source` | text |  |
| `stats` | jsonb |  |

### `source_score`

The source quality scorecard (P2.6, sources/scorecard.py): each dataset's four measures, 0-100, kept with every rebuild for 90 days so each has a trend; a drop raises a source_quality_drop case for its owner.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `dataset_id` | bigint |  |
| `at` | timestamptz |  |
| `completeness` | real |  |
| `validity` | real |  |
| `timeliness` | real |  |
| `agreement` | real |  |

### `source_rule`

Survivorship and matching rules (sources/rules.py): a field's own priority and conflict review, an entity kind's match thresholds. The default priority is the systems' rank. Seeded from the pack.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `scope` | text |  |
| `name` | text |  |
| `config` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `source_audit`

Every change to the catalog: who, when, what it was and became, and why.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `at` | timestamptz |  |
| `by` | text |  |
| `object` | text | system \| connection \| dataset \| mapping \| check \| rule |
| `object_id` | bigint |  |
| `name` | text |  |
| `action` | text | create \| update \| delete \| approve \| rollback \| pause \| ... |
| `old` | jsonb |  |
| `new` | jsonb |  |
| `reason` | text |  |

### `equipment_curve`

Reference curves per equipment model (OEM pump curves, choke characteristics): from vendor CSV exports, and from datasheets Claude read once an engineer accepted them (curve_digitised). Rebuilt with the graph.

| Column | Type | Note |
|---|---|---|
| `model_id` | text | equipment_model entity (or an equipment item, for its own compressor map) |
| `source` | text | 'oem' (vendor export) or 'datasheet:<doc number>' |
| `curve` | text, | pump \| choke \| compressor |
| `series` | text | head, efficiency, npshr, power, cv, head@<speed %>, efficiency@<speed %> |
| `x` | double precision |  |
| `y` | double precision |  |
| `x_unit` | text |  |
| `y_unit` | text |  |
| `record` | text |  |

### `curve_digitised`

Curves Claude read off datasheets, accepted in the review queue; the rebuild lands them in equipment_curve.

| Column | Type | Note |
|---|---|---|
| `document_key` | text | the datasheet's source record key |
| `doc_number` | text |  |
| `model_key` | text | MANUFACTURER\|MODEL |
| `result` | jsonb | {curve, series: {name: [[x, y]]}, ratings: {name: [x, y]}, units} |
| `accepted_by` | text |  |
| `accepted_at` | timestamptz |  |

### `source_exception_rule`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `system` | text |  |
| `name` | text |  |
| `dataset` | text |  |
| `type` | text | the exception type raised (usually one the system's pack brought) |
| `config` | jsonb | when, clear_when, key, refs, title, priority, value_usd |
| `enabled` | boolean |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `entity_alias`

Aliases a steward recorded (#476, sources/aliases.py): a value a system writes, as another name for an entity (by one of its keys, so it survives re-numbering). Laid over the pack's aliases on each rebuild; matched by exceptions as they are raised. Retired, not deleted, so the decision log keeps them.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `value` | text |  |
| `norm` | text | lower case, single spaces: what is matched |
| `kind` | text | the entity key it names: tag, well, equipment, ... |
| `key` | text |  |
| `fuzer_id` | text, | the entity when it was recorded |
| `source` | text, | the system whose value it was, when known |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `reason` | text |  |
| `retired_at` | timestamptz |  |
| `retired_by` | text |  |

### `learned_rule`

Rules the register learned from stewards (P6.5, sources/learned.py): when people have answered enough values from one source the same way (an alias whose value is the target's key with the same prefix or suffix around it), the rule is proposed; accepted, it records an alias for every like value now and after each rebuild (created_by rule:<id>). A rejected rule isn't proposed again; a withdrawn one retires the aliases it recorded.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `source` | text |  |
| `kind` | text | the key kind the core of the value is: equipment, tag, floc, ... |
| `prefix` | text | lower case, letters and digits only |
| `suffix` | text |  |
| `status` | text |  |
| `taught_by` | bigint[] | the aliases it was learned from |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `reason` | text |  |

### `detection_provider`

Detection providers (P3.8, sources/providers.py): an outside tool's alerts (condition monitoring, a predictive model, leak detection), mapped to the finding contract and raised as the work type it names.

| Column | Type | Note |
|---|---|---|
| `system` | text |  |
| `name` | text |  |
| `label` | text |  |
| `type` | text | the exception type its alerts raise |
| `mapping` | jsonb | key, refs, title, severity, confidence, link, start, items, clear_when |
| `enabled` | boolean |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `provider_poll`

The last read of a polled provider's alert table (P3.8c): read again only when its fingerprint changes.

| Column | Type | Note |
|---|---|---|
| `system` | text |  |
| `name` | text |  |
| `fingerprint` | text |  |
| `alerts` | integer |  |
| `errors` | jsonb |  |
| `polled_at` | timestamptz |  |

### `provider_trust`

How far a provider is trusted (P3.8b, #705), for every equipment class ('') or one: its alerts open their case for the role their type names (route), for the triage role first (triage), or are counted and kept with no case (mute). A person sets it, with a reason.

| Column | Type | Note |
|---|---|---|
| `system` | text |  |
| `name` | text |  |
| `class` | text |  |
| `trust` | text |  |
| `reason` | text |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

## Graph and golden records

[`src/fuzer/graph/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph/schema.sql)

### `entity`

One row per Fuzer ID. IDs are never reused; merged IDs point at the winner.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `kind` | text |  |
| `created_at` | timestamptz |  |
| `merged_into` | text |  |

### `id_event`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `event` | text |  |
| `fuzer_id` | text |  |
| `other_id` | text |  |
| `at` | timestamptz |  |

### `crosswalk`

Source record -> Fuzer ID.

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `record_type` | text |  |
| `source_key` | text |  |
| `fuzer_id` | text |  |
| `method` | text |  |
| `confidence` | real |  |
| `updated_at` | timestamptz |  |

### `entity_key`

Canonical keys (tag key, serial, well key, ...) -> Fuzer ID.

| Column | Type | Note |
|---|---|---|
| `kind` | text |  |
| `key` | text |  |
| `fuzer_id` | text |  |

### `entity_label`

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `label` | text |  |

### `register_summary`

Summaries computed once per rebuild (e.g. the ontology), read many times.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `value` | jsonb |  |
| `updated_at` | timestamptz |  |

### `assertion`

Reviewer choices (source = 'review'). What each source says is re-derived from its records on demand.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `fuzer_id` | text |  |
| `field` | text |  |
| `value` | jsonb |  |
| `source` | text |  |
| `source_key` | text |  |
| `observed_at` | timestamptz |  |

### `golden`

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `field` | text |  |
| `value` | jsonb |  |
| `source` | text |  |
| `rule` | text |  |

### `edge`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `src` | text |  |
| `rel` | text |  |
| `dst` | text |  |
| `valid_from` | date |  |
| `valid_to` | date |  |
| `sources` | text[] |  |
| `confidence` | real |  |
| `props` | jsonb |  |

### `unlinked`

"Not this link" (P2.1c, graph/contextualized.py): a source's statement of a link a steward said is wrong. The edge build leaves that source's statement out on every rebuild (another source stating the same link keeps it).

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `src` | text |  |
| `rel` | text |  |
| `dst` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `reason` | text |  |

### `placement_decision`

Where a new item sits and which drawing and documents are about it (P2.1d, graph/placement.py): a steward's answer to a proposal (rel part_of, shown_on or applies_to), kept by every rebuild after it.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `rel` | text |  |
| `target` | text |  |
| `accepted` | boolean |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `reason` | text |  |

### `review_item`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text | match, conflict, schema |
| `subject` | text |  |
| `payload` | jsonb |  |
| `status` | text |  |
| `decision` | jsonb |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `created_at` | timestamptz |  |

### `pinned_link`

Reviewer match decisions; applied on every rerun.

| Column | Type | Note |
|---|---|---|
| `left_ref` | text |  |
| `right_ref` | text |  |
| `rule` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |

### `review_rule`

Standing answers (P2.3c): one answer for a group of like questions that also decides new ones as they arrive.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `group_key` | text |  |
| `kind` | text |  |
| `decision` | text |  |
| `source` | text |  |
| `value` | jsonb |  |
| `set_by` | text |  |
| `set_at` | timestamptz |  |
| `withdrawn_by` | text |  |
| `withdrawn_at` | timestamptz |  |

### `match_score`

Every pair the matcher scored on the last rebuild, with what it did (P2.2): what calibration measures against labels.

| Column | Type | Note |
|---|---|---|
| `kind` | text |  |
| `left_ref` | text |  |
| `right_ref` | text |  |
| `sources` | text[] |  |
| `score` | double precision |  |
| `outcome` | text |  |

### `match_calibration`

The last calibration per entity kind and source (P2.2): measured precision at each threshold, the calibrated probability for a score, and the thresholds it recommends.

| Column | Type | Note |
|---|---|---|
| `kind` | text |  |
| `source` | text |  |
| `result` | jsonb |  |
| `labels` | text |  |
| `measured_at` | timestamptz |  |
| `measured_by` | text |  |

### `reviewed_value`

What each source says on a field a steward chose the value of, from the last rebuild (P2.4): how a correction sent back to a source is seen to land.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `field` | text |  |
| `source` | text |  |
| `source_key` | text |  |
| `value` | jsonb |  |

### `source_new_record`

Records a steward said are new items, not ones the register already has (P2.1b, graph/contextualized.py): they no longer count as unmatched, so one answer settles a source's whole group.

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `record_type` | text |  |
| `source_key` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `reason` | text |  |

### `knowledge_index`

What the knowledge search reads: document sections and incidents, with the IDs each is linked to (what its document applies to, what it mentions, its unit) for scoping. Kept by every rebuild.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `kind` | text |  |
| `document` | text |  |
| `title` | text |  |
| `doc_type` | text |  |
| `status` | text |  |
| `heading` | text |  |
| `page` | int |  |
| `body` | text |  |
| `scope_ids` | text[] |  |
| `tsv` | tsvector |  |
| `setweight(to_tsvector('english',` | coalesce(title, '') \|\| ' ' \|\| coalesce(heading, '')), 'A') |  |
| `||` | setweight(to_tsvector('english', body), 'B')) stored |  |

### `lesson`

Lessons learned: drafted from incidents and their corrective work orders, owned by engineers once reviewed. Rebuilds refresh a draft's evidence and numbers, never reviewed wording or decisions.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `scope_kind` | text | equipment_model or equipment_class |
| `scope_key` | text |  |
| `failure_mode` | text |  |
| `title` | text |  |
| `draft` | text | suggested steps, regenerated |
| `text` | text, | the engineer's wording once edited / approved |
| `evidence` | jsonb |  |
| `stats` | jsonb |  |
| `status` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `lesson_history`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `lesson_id` | bigint |  |
| `version` | int |  |
| `action` | text |  |
| `status` | text |  |
| `text` | text |  |
| `by_user` | text |  |
| `at` | timestamptz |  |

### `value_ranking`

Engineers' own ordering of a sample of work (fuzer ids, most valuable first), to calibrate the value score (#100) against: value.calibrate reports how well the score's ranking agrees.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `items` | jsonb |  |
| `by_user` | text |  |
| `comment` | text |  |
| `created_at` | timestamptz |  |

### `feedback`

Engineers' ratings of what Fuzer suggests (briefs, reading, lessons), to measure and improve it.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `target` | text | what was rated, e.g. brief:<fuzer_id> or reading:<fuzer_id> |
| `useful` | boolean |  |
| `comment` | text |  |
| `by_user` | text |  |
| `created_at` | timestamptz |  |

### `geo_index`

Where things are: geometry of every entity that has one (GIS, latitude / longitude), with its bounding box and centre for map and nearby lookups. Rebuilt with the register.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `kind` | text |  |
| `geom_type` | text |  |
| `minx` | double precision |  |
| `miny` | double precision |  |
| `maxx` | double precision |  |
| `maxy` | double precision |  |
| `cx` | double precision |  |
| `cy` | double precision |  |
| `geometry` | jsonb |  |

### `measurement`

Sparse, manual measurements landed from sources (well tests, lab samples, inspection thickness readings), unlike historian values, which stay in PI / ADX. Rebuilt from source records with the graph; values are in the units of units.yaml.

| Column | Type | Note |
|---|---|---|
| `record` | text | source record: <source>:<record_type>:<key> |
| `quantity` | text |  |
| `fuzer_id` | text | what was measured |
| `at` | timestamp |  |
| `value` | double precision |  |
| `unit` | text |  |
| `qualifier` | text, | "<" / ">": beyond a detection limit |
| `reported` | text, | as the source gave it, when converted |
| `method` | text |  |
| `location` | text, | where on it: a CML, a sample point |

### `measurement_role`

What each historian point means for the asset it measures (P1.4, graph/roles.py): one role from core/roles.py, proposed with every rebuild from the point's description, instrument letters, units and the equipment it is on.

| Column | Type | Note |
|---|---|---|
| `point_id` | text | the timeseries entity |
| `role` | text |  |
| `asset` | text, | the equipment or well it is a role of (not the instrument) |
| `confidence` | double precision | 0-1; below core.roles.CONFIDENT a steward confirms it |
| `basis` | jsonb | what said so, in words |

### `measurement_role_pin`

A steward's role for a point (P1.4b): wins over the proposal on every rebuild.

| Column | Type | Note |
|---|---|---|
| `point_id` | text |  |
| `role` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |

### `steward_fix`

Values a steward set across many entities at once (#476: one spelling for a manufacturer, say). Each row keeps what the golden value was, so the batch can be undone; the value itself is held as a 'review' assertion, which wins on every rebuild.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `batch` | text |  |
| `fuzer_id` | text |  |
| `field` | text |  |
| `old_value` | jsonb |  |
| `old_source` | text |  |
| `old_rule` | text |  |
| `new_value` | jsonb |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `reason` | text |  |
| `undone_at` | timestamptz |  |
| `undone_by` | text |  |

### `steward_proposal`

Claude's proposals for the steward (#476): a question built from the register (which entity an unresolved value means, whether two records are one item, which of the sources' values is right, whether two spellings are one value), Claude's pick from its numbered options with a confidence, reason and evidence, and what a person did with it. Cached by the question's hash and the prompt version; nothing is applied until someone accepts it.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text |  |
| `subject` | text |  |
| `question` | jsonb |  |
| `input_hash` | text |  |
| `answer` | jsonb |  |
| `model` | text |  |
| `prompt_version` | text |  |
| `status` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `result` | jsonb |  |
| `created_at` | timestamptz |  |

## Analyses

[`src/fuzer/analysis/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/schema.sql)

### `point_limit`

Operating limits per PI point (#101): alarm setpoints (PI AF limit traits, DCS alarm exports) and integrity operating windows (IOW registers), dated, with the source record they came from. Rebuilt from source records with the graph; values are in the units of units.yaml.

| Column | Type | Note |
|---|---|---|
| `record` | text | source record: <source>:<record_type>:<key>[#n] |
| `limit_type` | text | hihi \| hi \| lo \| lolo |
| `point_id` | text | the timeseries entity |
| `source` | text | pi_af \| dcs \| iow \| ... |
| `basis` | text | alarm \| iow |
| `value` | double precision |  |
| `unit` | text |  |
| `reported` | text, | as the source gave it, when converted |
| `level` | text, | IOW class (critical / standard / informational) or alarm priority |
| `allowed_minutes` | double precision, | IOW: time allowed outside before it must be acted on |
| `enabled` | boolean |  |
| `valid_from` | timestamp |  |
| `valid_to` | timestamp |  |
| `note` | text |  |

### `point_normal`

Normal ranges learned from history (#101) per point and operating mode of its unit: used for points with no limits from a source, and to check the ones that have them. Written by `fuzer limits learn`.

| Column | Type | Note |
|---|---|---|
| `point_id` | text |  |
| `mode` | text | running \| startup \| stopped \| transition \| any |
| `p01` | double precision |  |
| `p50` | double precision |  |
| `p99` | double precision |  |
| `n` | integer |  |
| `share` | double precision | of the learning window spent in this mode |
| `since` | timestamp |  |
| `until` | timestamp |  |
| `learned_at` | timestamptz |  |

### `excursion`

Times a point was outside a limit while its unit was running (#101): what the SRE (#96) raises, and for IOWs past their allowed time, the integrity work item opened for them (#97).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `point_id` | text |  |
| `record` | text | the limit's record ('learned' for learned ranges) |
| `limit_type` | text |  |
| `basis` | text | alarm \| iow \| learned |
| `level` | text |  |
| `limit_value` | double precision |  |
| `start` | timestamp |  |
| `"end"` | timestamp, | null while still outside at the end of the scan |
| `peak` | double precision |  |
| `minutes_outside` | double precision |  |
| `allowed_minutes` | double precision |  |
| `work_item_id` | bigint |  |
| `found_at` | timestamptz |  |

### `debottleneck_option`

Debottleneck options (#107): ranked proposals against back-pressure, kept from the last scan so the work board (#99) and the value score (#100) can read them without re-running the hydraulics.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `scanned_at` | timestamptz |  |
| `as_of` | text |  |
| `rank` | integer |  |
| `kind` | text |  |
| `target` | text |  |
| `action` | text |  |
| `gain_sm3d` | double precision |  |
| `detail` | jsonb |  |

## Operations

[`src/fuzer/ops/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/schema.sql)

### `sre_run`

Agentic SRE runs (#96): each scheduled scan's window; the next run starts where the last done one ended.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `since` | timestamp |  |
| `until` | timestamp |  |
| `status` | text | running \| done \| failed |
| `detections` | integer |  |
| `seconds` | double precision |  |
| `started_at` | timestamptz |  |
| `finished_at` | timestamptz |  |

### `detector_run`

Each run of a registered detector (core/detectors.py): what it found, or why it did not apply on this asset.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `detector` | text |  |
| `status` | text | ran \| not_applicable \| failed |
| `note` | text, | "not applicable: missing X", or the failure |
| `found` | integer |  |
| `seconds` | double precision |  |
| `since` | timestamp |  |
| `until` | timestamp |  |
| `at` | timestamptz |  |

### `detector_tier_run`

One run of a detector tier's job (P3.2): what it cost, for the Jobs page.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `tier` | text | 5min \| 30min \| hourly \| 6h \| daily (ops/cadence.py) |
| `until` | timestamp |  |
| `seconds` | double precision |  |
| `ran` | integer |  |
| `failed` | integer |  |
| `at` | timestamptz |  |

### `detection`

What the SRE's detectors found in the process data (sre.py): an ongoing one is extended, not raised again.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `point_id` | text |  |
| `unit_id` | text |  |
| `detector` | text | excursions \| stops \| steps \| drifts \| instruments \| models |
| `key` | text | what was found: alarm:hi, stopped, step, instrument:noisy, ... |
| `start` | timestamp |  |
| `"end"` | timestamp, | null while still going at the last run |
| `magnitude` | double precision |  |
| `evidence` | jsonb |  |
| `first_run` | bigint |  |
| `last_run` | bigint |  |
| `found_at` | timestamptz |  |

### `point_baseline`

Each point's usual step-to-step spread over the days before `day` (sre.baselines): the SRE's instrument checks read it from the historian once a day, not every window (#203). usual null: too few readings.

| Column | Type | Note |
|---|---|---|
| `point_id` | text |  |
| `day` | date |  |
| `step` | integer | minutes between the values it was worked out from |
| `days` | integer | how many days before `day` it covers |
| `usual` | double precision |  |
| `computed_at` | timestamptz |  |

### `upset`

Detections grouped by time and plant topology (#96b): one upset per cause, its earliest detection the likely first cause. Regrouped after each scan; an upset keeps its id (and status, once a person sets it) while its detections stay together.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text | process \| instrument \| slow |
| `status` | text | detected \| confirmed \| resolved \| false_alarm |
| `start` | timestamp |  |
| `"end"` | timestamp |  |
| `first_detection` | bigint |  |
| `first_unit` | text |  |
| `units` | text[] |  |
| `detections` | integer |  |
| `updated_at` | timestamptz |  |

### `sre_suppress`

Detection patterns an engineer called a false alarm (#96): an upset matching one is closed as a false alarm only where the auto-close gate allows (P5.10, core/gates.py); otherwise it reaches a person saying why.

| Column | Type | Note |
|---|---|---|
| `point_id` | text |  |
| `detector` | text |  |
| `key` | text |  |
| `upset_id` | bigint |  |
| `note` | text |  |
| `created_at` | timestamptz |  |

### `board_card`

The work board (#99): every outstanding piece of work as one ranked card, rebuilt by board.refresh.

| Column | Type | Note |
|---|---|---|
| `key` | text | the leading record: incident:<id>, upset:<id>, work_item:<id>, ... |
| `kind` | text |  |
| `title` | text |  |
| `text` | text |  |
| `unit_id` | text |  |
| `area_id` | text |  |
| `records` | text[] | every record on the card (one piece of work, several systems) |
| `value_usd` | double precision, | #100 score; null when not valued |
| `effective_usd` | double precision, | after any pinned value |
| `reason` | text |  |
| `breakdown` | jsonb |  |
| `rank` | integer |  |
| `prev_rank` | integer |  |
| `moved` | text, | why it moved since the last refresh |
| `pinned` | boolean |  |
| `item_id` | bigint, | its workflow item, if any |
| `first_seen` | timestamp |  |
| `updated_at` | timestamp |  |

### `board_pin`

A person's pinned rank or value for a card, by record key: survives refreshes and rebuilds.

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `rank` | integer |  |
| `value_usd` | double precision |  |
| `reason` | text |  |
| `pinned_by` | text |  |
| `pinned_at` | timestamptz |  |

### `board_history`

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `rank` | integer |  |
| `value_usd` | double precision |  |
| `at` | timestamptz |  |

### `board_project`

Projects created on the board, with scope, owner, dates and expected benefit.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `title` | text |  |
| `unit_id` | text |  |
| `owner` | text |  |
| `due` | date |  |
| `benefit_usd` | double precision |  |
| `effort_usd` | double precision |  |
| `scope` | text |  |
| `status` | text |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

### `daily_ops_report`

Daily operations reports (#98): one row per version of a production day's report. A new version when the day's facts change (late records), with what changed.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `day` | date |  |
| `version` | integer |  |
| `facts` | jsonb | the outline as tools worked it out, each fact with its citations |
| `fingerprint` | text |  |
| `markdown` | text |  |
| `checks` | jsonb | uncited lines, citations and numbers not in the facts |
| `drafted_by` | text | template \| claude |
| `usage` | jsonb, | tokens and cost of Claude's draft |
| `seconds` | double precision |  |
| `changes` | jsonb, | against the previous version |
| `item_id` | bigint |  |
| `created_at` | timestamptz |  |

### `opportunity`

The opportunity scan (#102): gains left on the table, one row per opportunity under a stable key. A decided one (rejected, accepted, project) isn't raised again unless its evidence or gain changes materially.

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `kind` | text |  |
| `title` | text |  |
| `target` | text |  |
| `gain_sm3d` | double precision |  |
| `value_usd` | double precision |  |
| `detail` | jsonb |  |
| `fingerprint` | text |  |
| `item_id` | bigint |  |
| `status` | text | open \| rejected \| accepted \| project \| gone |
| `reason` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `scanned_at` | timestamptz |  |

### `study`

Studies (#106): a correlation or clustering run kept with its parameters, top results and figure, so people can re-run it, an agent can cite it and a work item can attach it. Big ones queue for the background job.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text | correlate \| correlation_matrix \| modes \| entities \| events \| spe |
| `params` | jsonb |  |
| `status` | text | queued \| running \| done \| failed |
| `result` | jsonb |  |
| `summary` | text |  |
| `figure_id` | text |  |
| `error` | text |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `finished_at` | timestamptz |  |

### `exception_record`

Exceptions (core/exceptions.py): anything that should draw the IOC's attention and be worked, from any source, once per open condition (type and the source's own key), matched to the entities it is about and routed to the case that works it. open: the condition holds; cleared: it went away; closed: its case finished while it held.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `type` | text |  |
| `key` | text |  |
| `title` | text |  |
| `source` | text, | the module or system that raised it |
| `state` | text | open \| cleared \| closed |
| `priority` | double precision |  |
| `value_usd` | double precision |  |
| `subjects` | text[] | fuzer ids it was matched to |
| `unmatched` | text[] | references that matched no entity, kept visible |
| `evidence` | jsonb |  |
| `item_id` | bigint, | the case that works it |
| `raised_count` | integer |  |
| `first_seen` | timestamptz |  |
| `last_seen` | timestamptz |  |
| `closed_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `field_photo`

Photos the field attached to a report (P5.3b): resized on the phone, kept with the exception they were taken for.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `exception_id` | bigint |  |
| `by` | text |  |
| `at` | timestamptz |  |
| `mime` | text |  |
| `data` | bytea |  |

### `exception_kind`

Exception types a deployment defines as data (ops/exceptions.define), so other systems (SAP notifications, SCADA alarms, a reporting app) raise them over the API without code. Types declared in code are not stored here.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `label` | text |  |
| `role` | text, | the queue a case goes to |
| `process` | text, | or a published BPMN process to start / feed |
| `case_type` | text |  |
| `audience` | text[] |  |
| `close_case_on_clear` | boolean |  |
| `description` | text |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

### `suppression`

Suppression (P3.5b, ops/suppression.py): a window on some assets in which their detections are recorded and counted here instead of opening cases. Planned work and shutdowns come from plan_item; a person can add one.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `subjects` | text[] |  |
| `start_at` | timestamptz |  |
| `end_at` | timestamptz |  |
| `reason` | text |  |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

### `suppressed_finding`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `type` | text |  |
| `key` | text |  |
| `title` | text |  |
| `source` | text |  |
| `subjects` | text[] |  |
| `evidence` | jsonb |  |
| `value_usd` | double precision |  |
| `window_kind` | text | planned_work \| shutdown \| person \| muted (a provider, P3.8b) |
| `window_id` | bigint | the plan_item or suppression it fell in (0 when muted) |
| `reason` | text |  |
| `raised` | integer |  |
| `first_seen` | timestamptz |  |
| `last_seen` | timestamptz |  |

### `plan_item`

The integrated plan (ops/plan.py): every planned activity from every source on one timeline (SAP work orders, exceptions the IOC planned, well interventions, turnaround tasks, logistics), matched to the assets it touches, so the IOC sees clashes on an asset or a crew and work that could ride along with a shutdown.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `kind` | text | maintenance \| inspection \| well_intervention \| turnaround \| ... |
| `title` | text |  |
| `source` | text, | the system or module that proposed it |
| `source_key` | text, | its identity there (an SAP order number): proposals are idempotent |
| `subjects` | text[] | fuzer ids it touches |
| `area_id` | text, | the area of its first located subject |
| `start_at` | timestamptz |  |
| `end_at` | timestamptz |  |
| `status` | text | proposed \| scheduled \| in_progress \| done \| cancelled |
| `crew` | text, | a person or role:<role> |
| `crew_hours` | double precision |  |
| `needs_shutdown` | boolean |  |
| `deferment_usd` | double precision, | production at risk while it runs |
| `exception_id` | bigint, | the exception it works, if any |
| `notes` | text |  |
| `created_by` | text |  |
| `updated_by` | text |  |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `source_closeout`

Close-outs back to the source (#466, ops/closeout.py): what the field did, drafted as an update for the system that raised the work (complete the record, or a follow-up when the field could not do it), approved on the planner's Inbox (or sent directly) and published as source.update for the system's adapter.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `exception_id` | bigint |  |
| `system` | text |  |
| `record` | text | the system's own key (an SAP notification number) |
| `action` | text | complete \| follow_up |
| `draft` | jsonb | text, status, short_text, ... as sent |
| `state` | text | draft \| sent \| discarded |
| `item_id` | bigint, | the planner's case, when it was drafted for approval |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |
| `applied_at` | timestamptz | the demo applied it to the system's export |

### `shift_handover`

Shift handovers (#467, ops/handover.py): drafted from live work at each shift change, the outgoing lead's notes, and the incoming lead's acknowledgement (also on its case's history).

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `shift_start` | timestamptz |  |
| `shift_end` | timestamptz | the shift change it hands over at |
| `facts` | jsonb | sections: {name: [{text, cites}]}, more: {name: n} |
| `state` | text | draft \| handed_over (notes added) \| acknowledged |
| `item_id` | bigint, | its case on the ops lead queue |
| `created_by` | text |  |
| `drafted_at` | timestamptz |  |
| `notes` | text |  |
| `notes_by` | text |  |
| `notes_at` | timestamptz |  |
| `acknowledged_by` | text |  |
| `acknowledged_at` | timestamptz |  |
| `acknowledged_note` | text |  |

### `writeback`

The approved write-back queue (P5.7, #634; ops/writeback.py): every change Fuzer proposes to a source system, and the only way one is written. drafted -> awaiting_approval -> approved -> sent -> confirmed, or failed (back to the approver with the source's reason) or rejected. `idem_key` makes a proposal, and a send, happen once.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `system` | text |  |
| `kind` | text | notification, safety_notification, order, master_data, annotation, ... |
| `record` | text, | the system's own key, when the change is to an existing record |
| `action` | text |  |
| `payload` | jsonb |  |
| `idem_key` | text |  |
| `state` | text |  |
| `drafted_by` | text |  |
| `drafted_at` | timestamptz |  |
| `approver_role` | text |  |
| `item_id` | bigint, | the approver's case |
| `source_ref` | text, | what proposed it (closeout:12, review:4, ...) |
| `approved_by` | text |  |
| `approved_at` | timestamptz |  |
| `due_at` | timestamptz, | when its cadence lets it go |
| `sent_at` | timestamptz |  |
| `confirmed_at` | timestamptz |  |
| `failed_at` | timestamptz |  |
| `attempts` | integer |  |
| `error` | text |  |
| `reason` | text |  |

### `writeback_event`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `writeback_id` | bigint |  |
| `state` | text |  |
| `by` | text |  |
| `note` | text |  |
| `at` | timestamptz |  |

### `source_correction`

Corrections that flow back to a source (P2.4): what a steward's answer said was wrong in a source, the change or case drafted for its owner, and how it came back.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `review_item_id` | bigint |  |
| `kind` | text | master_data \| drawing_correction \| correction \| field_check |
| `system` | text |  |
| `fuzer_id` | text |  |
| `field` | text |  |
| `wrong` | jsonb, | what the source says |
| `right_value` | jsonb, | what the steward said is right |
| `writeback_id` | bigint, | the drafted change, when the source takes write-backs |
| `item_id` | bigint, | the owner's case, when it doesn't (or a field check) |
| `state` | text |  |
| `outcome` | text |  |
| `created_at` | timestamptz |  |
| `closed_at` | timestamptz |  |

### `case_disposition`

Structured close-out (P6.1a, #655): what a closed case was, in controlled terms.

| Column | Type | Note |
|---|---|---|
| `item_id` | bigint |  |
| `disposition` | text | real \| false_alarm \| duplicate \| data_problem |
| `failure_mode` | text, | ISO 14224 codes from standards/iso14224.yaml |
| `mechanism` | text |  |
| `cause` | text |  |
| `activity` | text |  |
| `outcome` | text, | fixed \| contained \| not_fixed \| monitoring |
| `note` | text |  |
| `duplicate_of` | bigint |  |
| `suggested` | jsonb | what the form was pre-filled with |
| `by` | text |  |
| `at` | timestamptz |  |

### `case_repeat`

| Column | Type | Note |
|---|---|---|
| `again` | bigint | the case (or re-check) that showed the problem came back |
| `first` | bigint | the case reopened as a repeat |
| `at` | timestamptz |  |

### `round_run`

Operator rounds (P5.4, ops/rounds.py): one run of each round per shift, for the operator role, and what was read or checked on it. A run past its shift's end is done (every point taken), partly done or missed.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `round` | text |  |
| `shift_start` | timestamptz |  |
| `shift_end` | timestamptz |  |
| `role` | text |  |
| `points` | integer |  |
| `state` | text | open \| done \| partly_done \| missed |
| `taken_by` | text |  |
| `closed_at` | timestamptz |  |

### `round_reading`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `run_id` | bigint |  |
| `point` | text | the round point's fuzer id |
| `value` | double precision |  |
| `passed` | boolean, | a check: passed or failed; a reading: inside its limits |
| `note` | text |  |
| `by` | text |  |
| `at` | timestamptz |  |
| `exception_id` | bigint, | what an out-of-limit reading or a failed check raised |

### `watch_proposal`

The agentic watch (P3.3a, ops/watch.py): what the watch agent proposed for an area over a window, that no detector raised. In shadow (wave 4) a proposal is kept and scored, never routed.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `area` | text | the process area's fuzer id |
| `since` | timestamp |  |
| `until` | timestamp |  |
| `subject` | text, | what it is about, as the evidence names it |
| `start` | timestamp |  |
| `"end"` | timestamp |  |
| `why` | text | why it is unusual |
| `ruled_out` | text, | what it ruled out |
| `evidence` | jsonb | the ids it cites (points, detections, log entries, notifications) |
| `cited` | boolean | whether every id it cites was in what it was shown |
| `mode` | text |  |
| `model` | text |  |
| `run_at` | timestamptz |  |

### `promoted_rule`

A deterministic rule drafted from a pattern of confirmed proposals (P3.4): its spec, its replay score, and who published it. Published rules run as the promoted_rules window detector.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `pattern` | text |  |
| `spec` | jsonb | role, class, direction, z, minutes, baseline_days |
| `examples` | jsonb | the confirmed proposals it was fitted on |
| `score` | jsonb, | its replay score, when the asset has a fake plant's events |
| `status` | text | draft \| published \| retired |
| `drafted_by` | text |  |
| `drafted_at` | timestamptz |  |
| `published_by` | text |  |
| `published_at` | timestamptz |  |
| `reason` | text |  |

## Charts

[`src/fuzer/plots/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/plots/schema.sql)

### `assistant_figure`

Charts the operations assistant made (plots/): the Plotly figure as built, what the agent was told about it, and the records it came from. A figure is a snapshot of the data when it was asked.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `kind` | text |  |
| `title` | text |  |
| `params` | jsonb |  |
| `spec` | jsonb |  |
| `summary` | jsonb |  |
| `sources` | text[] |  |
| `by_source` | text | 'assistant' when the agent supplied the numbers |
| `created_at` | timestamptz |  |

## Agents and MCP

[`src/fuzer/agents/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/agents/schema.sql)

### `assistant_answer`

Questions to the operations assistant and its answers, kept so engineers can rate them (feedback target answer:<id>) and the answers can be reviewed against the data later.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `conversation_id` | text |  |
| `question` | text |  |
| `answer` | text |  |
| `error` | text |  |
| `specialists` | text[] |  |
| `tools` | jsonb |  |
| `usage` | jsonb |  |
| `seconds` | numeric |  |
| `by_user` | text |  |
| `created_at` | timestamptz |  |

### `assistant_conversation`

The assistant's conversations (#93): each message as MAF wrote it, in order. `json`, not `jsonb`, so a message comes back exactly as stored (Claude checks its thinking blocks byte for byte). Images are kept once per conversation and referenced from the messages.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `by_user` | text |  |
| `title` | text |  |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `assistant_message`

| Column | Type | Note |
|---|---|---|
| `conversation_id` | text |  |
| `seq` | int |  |
| `message` | json |  |
| `created_at` | timestamptz |  |

### `assistant_image`

| Column | Type | Note |
|---|---|---|
| `conversation_id` | text |  |
| `sha256` | text |  |
| `media_type` | text |  |
| `data` | bytea |  |
| `created_at` | timestamptz |  |

### `assistant_fact`

Facts the assistant learned from answers rated useful, or statements an engineer marked "remember this" (#93). Dated in the plant (valid_from / valid_to) and in Fuzer (learned_at). A newer fact that contradicts one ends it (status 'ended', invalidated_by); it is never deleted, except by an engineer.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `text` | text |  |
| `subject` | text |  |
| `entity_ids` | text[] |  |
| `valid_from` | date |  |
| `valid_to` | date |  |
| `learned_at` | timestamptz |  |
| `episode` | text |  |
| `sources` | jsonb |  |
| `status` | text |  |
| `invalidated_by` | bigint |  |
| `by_user` | text |  |
| `edited_by` | text |  |
| `ended_by` | text |  |
| `ended_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

### `skill_version`

Skill versions (P6.4b): a skill's playbook as drafted, published, retired or rolled back. Version 0 is the folder shipped under agents/skills and has no row; the published row, when there is one, is what the agents load.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `skill` | text | the skill's path under agents/skills |
| `version` | int |  |
| `playbook` | text | the whole SKILL.md |
| `status` | text |  |
| `cases` | jsonb | the shortfalls behind the change (P6.4a) |
| `questions` | jsonb | eval questions drawn from them |
| `note` | text |  |
| `eval` | jsonb | the eval scores it was published on (P6.4d) |
| `created_by` | text |  |
| `created_at` | timestamptz |  |
| `published_by` | text |  |
| `published_at` | timestamptz |  |

## Evals

[`src/fuzer/evals/schema.sql`](https://github.com/areed145/fuzer/tree/main/src/fuzer/evals/schema.sql)

### `ops_eval`

Replays of the operations detectors against a fake plant's injected events (#103, replay.py): one row per `fuzer eval --ops`, so a change can be compared with the run before it.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `detector` | text |  |
| `since` | text |  |
| `until` | text |  |
| `scores` | jsonb |  |
| `run_at` | timestamptz |  |
