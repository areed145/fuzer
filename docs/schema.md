# Database schema

Generated from `src/fuzer/schema.sql`; don't edit by hand (`uv run python -m fuzer.reference`).
One Postgres database; `fuzer` creates or updates it on start (idempotent).

## `source_record`

Raw records exactly as received from each source.

| Column | Type | Note |
|---|---|---|
| `source` | text |  |
| `record_type` | text |  |
| `source_key` | text |  |
| `payload` | jsonb |  |
| `first_seen` | timestamptz |  |
| `last_seen` | timestamptz |  |

## `entity`

One row per Fuzer ID. IDs are never reused; merged IDs point at the winner.

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `kind` | text |  |
| `created_at` | timestamptz |  |
| `merged_into` | text |  |

## `id_event`

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `event` | text |  |
| `fuzer_id` | text |  |
| `other_id` | text |  |
| `at` | timestamptz |  |

## `crosswalk`

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

## `entity_key`

Canonical keys (tag key, serial, well key, ...) -> Fuzer ID.

| Column | Type | Note |
|---|---|---|
| `kind` | text |  |
| `key` | text |  |
| `fuzer_id` | text |  |

## `entity_label`

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `label` | text |  |

## `register_summary`

Summaries computed once per rebuild (e.g. the ontology), read many times.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `value` | jsonb |  |
| `updated_at` | timestamptz |  |

## `assertion`

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

## `golden`

| Column | Type | Note |
|---|---|---|
| `fuzer_id` | text |  |
| `field` | text |  |
| `value` | jsonb |  |
| `source` | text |  |
| `rule` | text |  |

## `edge`

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

## `review_item`

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

## `pinned_link`

Reviewer match decisions; applied on every rerun.

| Column | Type | Note |
|---|---|---|
| `left_ref` | text |  |
| `right_ref` | text |  |
| `rule` | text |  |
| `decided_by` | text |  |
| `decided_at` | timestamptz |  |

## `llm_extraction`

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

## `knowledge_index`

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

## `source_record_history`

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

## `lesson`

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

## `value_ranking`

Engineers' own ordering of a sample of work (fuzer ids, most valuable first), to calibrate the value score (#100) against: value.calibrate reports how well the score's ranking agrees.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `items` | jsonb |  |
| `by_user` | text |  |
| `comment` | text |  |
| `created_at` | timestamptz |  |

## `feedback`

Engineers' ratings of what Fuzer suggests (briefs, reading, lessons), to measure and improve it.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `target` | text | what was rated, e.g. brief:<fuzer_id> or reading:<fuzer_id> |
| `useful` | boolean |  |
| `comment` | text |  |
| `by_user` | text |  |
| `created_at` | timestamptz |  |

## `dataset_state`

What each dataset looked like when last loaded: an unchanged fingerprint skips it, a changed schema is compared column by column, and position is the watermark for append-only lake tables.

| Column | Type | Note |
|---|---|---|
| `dataset` | text |  |
| `connector` | text |  |
| `fingerprint` | text |  |
| `schema` | jsonb |  |
| `position` | jsonb |  |
| `rows` | bigint |  |
| `loaded_at` | timestamptz |  |

## `mapping_version`

Mappings approved in the review queue (suggested for a new table, as is or edited). The latest version per source and record type is used, after the built-in files.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `source` | text |  |
| `record_type` | text |  |
| `dataset` | text |  |
| `yaml` | text |  |
| `approved_by` | text |  |
| `approved_at` | timestamptz |  |

## `geo_index`

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

## `assistant_answer`

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

## `assistant_conversation`

The assistant's conversations (#93): each message as MAF wrote it, in order. `json`, not `jsonb`, so a message comes back exactly as stored (Claude checks its thinking blocks byte for byte). Images are kept once per conversation and referenced from the messages.

| Column | Type | Note |
|---|---|---|
| `id` | text |  |
| `by_user` | text |  |
| `title` | text |  |
| `created_at` | timestamptz |  |
| `updated_at` | timestamptz |  |

## `assistant_message`

| Column | Type | Note |
|---|---|---|
| `conversation_id` | text |  |
| `seq` | int |  |
| `message` | json |  |
| `created_at` | timestamptz |  |

## `assistant_image`

| Column | Type | Note |
|---|---|---|
| `conversation_id` | text |  |
| `sha256` | text |  |
| `media_type` | text |  |
| `data` | bytea |  |
| `created_at` | timestamptz |  |

## `assistant_fact`

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

## `measurement`

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

## `point_limit`

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

## `point_normal`

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

## `excursion`

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

## `ops_eval`

Replays of the operations detectors against a fake plant's injected events (#103, replay.py): one row per `fuzer eval --ops`, so a change can be compared with the run before it.

| Column | Type | Note |
|---|---|---|
| `id` | bigserial |  |
| `detector` | text |  |
| `since` | text |  |
| `until` | text |  |
| `scores` | jsonb |  |
| `run_at` | timestamptz |  |

## `assistant_figure`

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

## `equipment_curve`

Reference curves per equipment model (OEM pump curves, choke characteristics): from vendor CSV exports, and from datasheets Claude read once an engineer accepted them (curve_digitised). Rebuilt with the graph.

| Column | Type | Note |
|---|---|---|
| `model_id` | text | equipment_model entity |
| `source` | text | 'oem' (vendor export) or 'datasheet:<doc number>' |
| `curve` | text, | pump \| choke |
| `series` | text | head, efficiency, npshr, power, cv, or a rating (bep, por, cv_max, ...) |
| `x` | double precision |  |
| `y` | double precision |  |
| `x_unit` | text |  |
| `y_unit` | text |  |
| `record` | text |  |

## `curve_digitised`

Curves Claude read off datasheets, accepted in the review queue; the rebuild lands them in equipment_curve.

| Column | Type | Note |
|---|---|---|
| `document_key` | text | the datasheet's source record key |
| `doc_number` | text |  |
| `model_key` | text | MANUFACTURER\|MODEL |
| `result` | jsonb | {curve, series: {name: [[x, y]]}, ratings: {name: [x, y]}, units} |
| `accepted_by` | text |  |
| `accepted_at` | timestamptz |  |

## `work_definition`

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

## `work_form`

form-js task forms, by the key a userTask names in fuzer:form, with versions. A task shows the latest.

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `version` | int |  |
| `schema` | jsonb | the form-js schema (plus `fuzer.bulk`: bulk actions) |
| `created_by` | text |  |
| `created_at` | timestamptz |  |

## `work_person`

People by name and the roles they hold (everyone sees everything; Entra groups later).

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `roles` | text[] |  |

## `work_item`

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

## `work_process`

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

## `work_token`

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

## `work_event`

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

## `sre_run`

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

## `detection`

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

## `upset`

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

## `sre_suppress`

Detection patterns an engineer called a false alarm (#96): the SRE doesn't hand them off again.

| Column | Type | Note |
|---|---|---|
| `point_id` | text |  |
| `detector` | text |  |
| `key` | text |  |
| `upset_id` | bigint |  |
| `note` | text |  |
| `created_at` | timestamptz |  |

## `board_card`

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

## `board_pin`

A person's pinned rank or value for a card, by record key: survives refreshes and rebuilds.

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `rank` | integer |  |
| `value_usd` | double precision |  |
| `reason` | text |  |
| `pinned_by` | text |  |
| `pinned_at` | timestamptz |  |

## `board_history`

| Column | Type | Note |
|---|---|---|
| `key` | text |  |
| `rank` | integer |  |
| `value_usd` | double precision |  |
| `at` | timestamptz |  |

## `board_project`

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

## `daily_ops_report`

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

## `debottleneck_option`

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

## `opportunity`

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

## `study`

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

## `setting`

Settings (#131): values people set, per project ('project:<name>') or for all ('global'); the registry in core/settings.py holds each setting's type, default and range. Every change is kept.

| Column | Type | Note |
|---|---|---|
| `scope` | text |  |
| `key` | text |  |
| `value` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |

## `setting_history`

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

## `project`

Projects (#131): what one name selects (pack, data folder, mappings, time-series provider, timezone); projects/<name>.yaml in the repository is the fallback when there is no row.

| Column | Type | Note |
|---|---|---|
| `name` | text |  |
| `config` | jsonb |  |
| `updated_by` | text |  |
| `updated_at` | timestamptz |  |
