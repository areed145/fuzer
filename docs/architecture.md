# Architecture

Generated from the code: package imports, mappings, the capability and settings registries, the schedules and the modules' `schema.sql`; don't edit by hand (`uv run python -m fuzer.reference`).
Each box is a package under `src/fuzer`; click one to open it. An arrow points from a package to the ones that use it, so data flows along the arrows. Everything uses core, so its arrows are left out.

## Components

```mermaid
flowchart LR
  systems[("Source systems<br>SAP, PI, EC, P&IDs, logs")] --> c_sources
  app(["The app (ui/)"])
  c_api --> app
  c_sources["Sources and ingestion<br><small>fuzer.sources, 34 modules</small>"]
  c_graph["Graph and golden records<br><small>fuzer.graph, 30 modules</small>"]
  c_analysis["Analyses<br><small>fuzer.analysis, 29 modules</small>"]
  c_ops["Operations<br><small>fuzer.ops, 24 modules</small>"]
  c_plots["Charts<br><small>fuzer.plots, 5 modules</small>"]
  c_workflow["Work engine<br><small>fuzer.workflow, 12 modules</small>"]
  c_agents["Agents and MCP<br><small>fuzer.agents, 22 modules</small>"]
  c_evals["Evals<br><small>fuzer.evals, 7 modules</small>"]
  c_api["API<br><small>fuzer.api, 19 modules</small>"]
  c_cli["Command line<br><small>fuzer.cli, 4 modules</small>"]
  c_core["Core<br><small>fuzer.core, 29 modules</small>"]
  c_sources --> c_graph
  c_graph --> c_analysis
  c_sources --> c_analysis
  c_analysis --> c_ops
  c_graph --> c_ops
  c_plots --> c_ops
  c_sources --> c_ops
  c_workflow --> c_ops
  c_analysis --> c_plots
  c_graph --> c_plots
  c_ops --> c_plots
  c_sources --> c_plots
  c_analysis --> c_agents
  c_graph --> c_agents
  c_ops --> c_agents
  c_plots --> c_agents
  c_sources --> c_agents
  c_workflow --> c_agents
  c_agents --> c_evals
  c_analysis --> c_evals
  c_graph --> c_evals
  c_ops --> c_evals
  c_sources --> c_evals
  c_workflow --> c_evals
  c_agents --> c_api
  c_analysis --> c_api
  c_graph --> c_api
  c_ops --> c_api
  c_plots --> c_api
  c_sources --> c_api
  c_workflow --> c_api
  c_agents --> c_cli
  c_analysis --> c_cli
  c_evals --> c_cli
  c_graph --> c_cli
  c_ops --> c_cli
  c_sources --> c_cli
  c_workflow --> c_cli
  click c_sources "https://github.com/areed145/fuzer/tree/main/src/fuzer/sources"
  click c_graph "https://github.com/areed145/fuzer/tree/main/src/fuzer/graph"
  click c_analysis "https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis"
  click c_ops "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops"
  click c_plots "https://github.com/areed145/fuzer/tree/main/src/fuzer/plots"
  click c_workflow "https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow"
  click c_agents "https://github.com/areed145/fuzer/tree/main/src/fuzer/agents"
  click c_evals "https://github.com/areed145/fuzer/tree/main/src/fuzer/evals"
  click c_api "https://github.com/areed145/fuzer/tree/main/src/fuzer/api"
  click c_cli "https://github.com/areed145/fuzer/tree/main/src/fuzer/cli"
  click c_core "https://github.com/areed145/fuzer/tree/main/src/fuzer/core"
```

## What feeds which entities

From the mapping files in `src/fuzer/mappings` (source → entity kind). Claude's reads of logs, drawings and documents add events, links and mentions on top.

```mermaid
flowchart LR
  src_adx[/"adx"/]
  src_ec[/"ec"/]
  src_edms[/"edms"/]
  src_gis[/"gis"/]
  src_jde[/"jde"/]
  src_maximo[/"maximo"/]
  src_oplog[/"oplog"/]
  src_pi[/"pi"/]
  src_pi_af[/"pi_af"/]
  src_pid[/"pid"/]
  src_ptw[/"ptw"/]
  src_sap[/"sap"/]
  kind_document(["document"])
  kind_document_section(["document_section"])
  kind_downtime(["downtime"])
  kind_equipment(["equipment"])
  kind_isolation(["isolation"])
  kind_log_entry(["log_entry"])
  kind_maintenance_plan(["maintenance_plan"])
  kind_material(["material"])
  kind_notification(["notification"])
  kind_permit(["permit"])
  kind_pipeline(["pipeline"])
  kind_plant_hierarchy__facility__area__tag__well(["plant hierarchy: facility, area, tag, well"])
  kind_production_day(["production_day"])
  kind_purchase_order(["purchase_order"])
  kind_timeseries(["timeseries"])
  kind_well(["well"])
  kind_well_test(["well_test"])
  kind_work_center(["work_center"])
  kind_work_order(["work_order"])
  src_adx --> kind_timeseries
  src_ec --> kind_downtime
  src_ec --> kind_plant_hierarchy__facility__area__tag__well
  src_ec --> kind_production_day
  src_ec --> kind_well_test
  src_edms --> kind_document
  src_edms --> kind_document_section
  src_gis --> kind_pipeline
  src_gis --> kind_plant_hierarchy__facility__area__tag__well
  src_gis --> kind_well
  src_jde --> kind_equipment
  src_maximo --> kind_equipment
  src_oplog --> kind_log_entry
  src_pi --> kind_timeseries
  src_pi_af --> kind_plant_hierarchy__facility__area__tag__well
  src_pid --> kind_document
  src_ptw --> kind_isolation
  src_ptw --> kind_permit
  src_sap --> kind_equipment
  src_sap --> kind_maintenance_plan
  src_sap --> kind_material
  src_sap --> kind_notification
  src_sap --> kind_plant_hierarchy__facility__area__tag__well
  src_sap --> kind_purchase_order
  src_sap --> kind_work_center
  src_sap --> kind_work_order
```

## A scheduled scan, start to finish

```mermaid
flowchart TD
  s0["<b>sre_scan</b><br>every 6 hours"]
  click s0 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/jobs.py"
  s1["<b>scan</b><br>detectors read the process values since the last run"] -.-> t1[("detection")]
  click s1 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/sre.py"
  s0 --> s1
  s2["<b>regroup</b><br>detections close in time and in the plant model become one upset"] -.-> t2[("upset")]
  click s2 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/sre.py"
  s1 --> s2
  s3["<b>handoff</b><br>the assistant diagnoses new upsets; each becomes a work item"] -.-> t3[("work_item")]
  click s3 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/sre_handoff.py"
  s2 --> s3
  s4["<b>cards</b><br>upsets, work, opportunities and reviews ranked by value"] -.-> t4[("board_card")]
  click s4 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/board.py"
  s3 --> s4
  s5["<b>close_day</b><br>the day's facts and a drafted report, checked against them"] -.-> t5[("daily_ops_report")]
  click s5 "https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/daily_report.py"
  s4 --> s5
```

## Scheduled jobs

| Job | When (cron) | What it does |
|---|---|---|
| `refresh_logs` | `0 * * * *` | Load the operator logs and rebuild. |
| `refresh_all` | `0 2 * * *` | Load every export and rebuild. |
| `claude_reading` | `0 3 * * *` | Claude reads new log entries and drawings (costs money). |
| `work_tick` | `*/5 * * * *` | Work engine timers and SLAs: re-checks that are due, reminders and escalations. |
| `work_notify` | `* * * * *` | Notifications: work sent, handed back, escalated or coming due, told to the person within a minute. |
| `writeback_run` | `* * * * *` | Write-backs: approved changes sent to source systems by their cadence; nothing unapproved. |
| `closeout_sweep` | `*/5 * * * *` | Close-out: ask whether a fix held when its re-check is due; reopen a case whose problem returned. |
| `sre_scan` | `0 */6 * * *` | SRE every 6 hours: detect, group into upsets, prioritise, diagnose (capped) and hand off. |
| `studies_job` | `*/5 * * * *` | Big correlation and clustering studies, queued from the API or an agent. |
| `day_close` | `0 7 * * *` | Daily operations report: facts from the day's records, a new version when they change, the ops lead's review in the workflow (the reporter agent drafts it). |
| `source_health` | `*/30 * * * *` | Source health: stale data, failed or held loads, failing checks -> exceptions, cases and alerts. |

## Each component

### Sources and ingestion

The source catalog (systems, connections, datasets, mapping versions, runs) drives loading; connectors read each table; mappings turn rows into records, entities and links; Claude reads logs, P&IDs and documents. More in [sources](sources.md).

- **Code:** [`src/fuzer/sources`](https://github.com/areed145/fuzer/tree/main/src/fuzer/sources): `_catalog_common`, `_catalog_entities`, `_catalog_io`, `_catalog_packs`, `_catalog_versions`, `adx`, `alerts`, `aliases`, `catalog`, `connections`, `connectors`, `curves`, `documents`, `extract`, `formats`, `impact`, `ingest`, `llm_curves`, `llm_docs`, `llm_pids`, `mapper`, `mappings`, `onboard`, `packs`, `pi_web_api`, `preview`, `process_model`, `quality`, `relative_dates`, `rules`, `scorecard`, `suggest`, `timeseries`, `workflows`
- **Uses:** core
- **Writes:** `curve_digitised`, `entity_alias`, `llm_extraction`, `mapping_version`, `review_item`, `source_audit`, `source_check`, `source_check_result`, `source_connection`, `source_dataset`, `source_exception_rule`, `source_match_stat`, `source_profile`, `source_record`, `source_record_history`, `source_rule`, `source_run`, `source_score`, `source_system` ([schema](schema.md))
- **Owns:** `source_record`, `llm_extraction`, `source_record_history`, `source_system`, `source_connection`, `source_dataset`, `mapping_version`, `source_run`, `source_profile`, `source_check`, `source_check_result`, `source_match_stat`, `source_score`, `source_rule`, `source_audit`, `equipment_curve`, `curve_digitised`, `source_exception_rule`, `entity_alias` (its `schema.sql`)
- **Tests:** [`tests/sources`](https://github.com/areed145/fuzer/tree/main/tests/sources)

### Graph and golden records

Resolves records into one entity per real thing, links them, and answers queries over the graph. More in [identity](identity.md).

- **Code:** [`src/fuzer/graph`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph): `calibration`, `contextualized`, `criticality`, `curation`, `data_work`, `edges`, `gaps`, `golden`, `incidents`, `investigation`, `knowledge`, `lessons`, `llm_links`, `measurements`, `ontology`, `operations`, `placement`, `proposals`, `quality`, `queries.documents`, `queries.downtime`, `queries.maintenance`, `queries.network`, `queries.records`, `resolve`, `review`, `roles`, `spatial`, `structure`, `topology`
- **Uses:** core, sources
- **Writes:** `assertion`, `crosswalk`, `edge`, `entity`, `golden`, `id_event`, `lesson`, `llm_extraction`, `match_calibration`, `match_score`, `measurement_role`, `measurement_role_pin`, `ontology_kind`, `ontology_relation`, `pinned_link`, `placement_decision`, `register_summary`, `review_item`, `review_rule`, `reviewed_value`, `source_dataset`, `source_new_record`, `steward_fix`, `steward_proposal`, `unlinked` ([schema](schema.md))
- **Owns:** `entity`, `id_event`, `crosswalk`, `entity_key`, `entity_label`, `register_summary`, `assertion`, `golden`, `edge`, `unlinked`, `placement_decision`, `review_item`, `pinned_link`, `review_rule`, `match_score`, `match_calibration`, `reviewed_value`, `source_new_record`, `knowledge_index`, `lesson`, `value_ranking`, `feedback`, `geo_index`, `measurement`, `measurement_role`, `measurement_role_pin`, `steward_fix`, `steward_proposal` (its `schema.sql`)
- **Settings:** `stewardship.bulk_accept_at` ([settings](settings.md))
- **Tests:** [`tests/graph`](https://github.com/areed145/fuzer/tree/main/tests/graph)

### Analyses

Signals, limits, calcs, hydraulics, studies and clustering over the graph and the process values. More in [analyses](analyses.md).

- **Code:** [`src/fuzer/analysis`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis): `alarms`, `backlog`, `calcs`, `capacity_bounds`, `cases`, `clusters`, `completeness`, `compression`, `condition`, `drivers`, `equipment`, `facilities`, `flow_capacity`, `forecast`, `hydraulics`, `integrity`, `limits`, `production_efficiency`, `prognosis`, `protection`, `recip`, `records`, `rotating`, `signals`, `studies`, `surveillance`, `trips`, `value`, `values`
- **Uses:** core, graph, sources
- **Writes:** `debottleneck_option`, `excursion`, `point_normal` ([schema](schema.md))
- **Owns:** `point_limit`, `point_normal`, `excursion`, `debottleneck_option` (its `schema.sql`)
- **Settings:** `alarms.failed_start_minutes`, `records.log_tolerance_hours`, `records.lookback_days`, `records.notification_categories`, `records.notification_tolerance_days`, `sre.hops`, `studies.max_points`, `studies.max_values`, `studies.workers` ([settings](settings.md))
- **Tests:** [`tests/analysis`](https://github.com/areed145/fuzer/tree/main/tests/analysis)

### Operations

The scheduled loop: SRE detection and grouping, hand-off, the board, daily reports, opportunities and studies. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/ops`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops): `around`, `board`, `cascades`, `closeout`, `corrections`, `daily_report`, `disposition`, `exceptions`, `handover`, `intake`, `jobs`, `metrics`, `opportunities`, `performance`, `pipeline`, `plan`, `readiness`, `routing`, `sre`, `sre_handoff`, `study`, `suppression`, `turnaround`, `writeback`
- **Uses:** analysis, core, graph, plots, sources, workflow
- **Writes:** `board_card`, `board_history`, `board_pin`, `board_project`, `case_disposition`, `case_repeat`, `daily_ops_report`, `detection`, `exception_kind`, `exception_record`, `field_photo`, `opportunity`, `plan_item`, `point_baseline`, `register_summary`, `review_item`, `shift_handover`, `source_closeout`, `source_correction`, `sre_run`, `sre_suppress`, `study`, `suppressed_finding`, `suppression`, `upset`, `work_item`, `writeback`, `writeback_event` ([schema](schema.md))
- **Owns:** `sre_run`, `detector_run`, `detection`, `point_baseline`, `upset`, `sre_suppress`, `board_card`, `board_pin`, `board_history`, `board_project`, `daily_ops_report`, `opportunity`, `study`, `exception_record`, `field_photo`, `exception_kind`, `suppression`, `suppressed_finding`, `plan_item`, `source_closeout`, `shift_handover`, `writeback`, `writeback_event`, `source_correction`, `case_disposition`, `case_repeat` (its `schema.sql`)
- **Settings:** `closeout.apply_to_exports`, `closeout.recheck_days`, `daily_report.close_cron`, `daily_report.mover_pct`, `exceptions.around_after_hours`, `exceptions.around_before_hours`, `exceptions.join_hours`, `exceptions.suppress_planned_work`, `handover.shift_times`, `intake.sap_notifications`, `intake.sap_notifications.due_hours`, `intake.sap_notifications.max_age_days`, `opportunities.gas_lift_breakeven`, `opportunities.material`, `opportunities.stale_days`, `readiness.permit_types`, `sre.drift_rise`, `sre.group_gap_hours`, `sre.handoff.max_diagnoses`, `sre.handoff.max_usd`, `sre.handoff.min_detections`, `sre.hops`, `sre.learned_minutes`, `sre.lookback_hours`, `sre.merge_minutes`, `sre.spe_minutes`, `sre.step_minutes`, `studies.max_values` ([settings](settings.md))
- **Tests:** [`tests/ops`](https://github.com/areed145/fuzer/tree/main/tests/ops)

### Charts

Interactive charts the agents and the app build from Fuzer data. More in [agents](agents.md).

- **Code:** [`src/fuzer/plots`](https://github.com/areed145/fuzer/tree/main/src/fuzer/plots): `analytics`, `general`, `maintenance`, `production`, `troubleshooting`
- **Uses:** analysis, core, graph, ops, sources
- **Writes:** `assistant_figure` ([schema](schema.md))
- **Owns:** `assistant_figure` (its `schema.sql`)
- **Tests:** [`tests/plots`](https://github.com/areed145/fuzer/tree/main/tests/plots)

### Work engine

BPMN processes that move work between agents and people. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/workflow`](https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow): `assignment`, `bpmn`, `canvas`, `definitions`, `engine`, `home`, `notify`, `roles`, `runtime`, `views`, `work_drafts`, `work_items`
- **Uses:** core
- **Writes:** `work_definition`, `work_draft`, `work_event`, `work_form`, `work_home_seen`, `work_item`, `work_notification`, `work_notify_cursor`, `work_notify_pref`, `work_person`, `work_process`, `work_role`, `work_token`, `work_view`, `work_watch` ([schema](schema.md))
- **Owns:** `work_definition`, `work_form`, `work_person`, `work_person_cover`, `work_role`, `work_item`, `work_draft`, `work_process`, `work_token`, `work_event`, `work_notification`, `work_notify_cursor`, `work_notify_pref`, `work_home_seen`, `work_watch`, `work_view` (its `schema.sql`)
- **Settings:** `notify.app_url`, `notify.from`, `work.agent_fallback_minutes`, `work.due_soon_minutes` ([settings](settings.md))
- **Tests:** [`tests/workflow`](https://github.com/areed145/fuzer/tree/main/tests/workflow)

### Agents and MCP

The operations assistant, its specialists, and the MCP server with their tools. More in [agents](agents.md).

- **Code:** [`src/fuzer/agents`](https://github.com/areed145/fuzer/tree/main/src/fuzer/agents): `assistant`, `brief`, `capabilities.__main__`, `capabilities.catalog`, `capabilities.graph`, `capabilities.maintenance`, `capabilities.metrics`, `capabilities.operations`, `capabilities.plots`, `capabilities.production`, `capabilities.sources`, `capabilities.studies`, `capabilities.troubleshooting`, `capabilities.work`, `history`, `ledger`, `mcp_server`, `memory`, `packs`, `service`, `worker`, `workflow`
- **Uses:** analysis, core, graph, ops, plots, sources, workflow
- **Writes:** `assistant_answer`, `assistant_conversation`, `assistant_fact`, `assistant_image`, `assistant_message`, `lesson`, `work_event`, `work_token` ([schema](schema.md))
- **Owns:** `assistant_answer`, `assistant_conversation`, `assistant_message`, `assistant_image`, `assistant_fact` (its `schema.sql`)
- **Settings:** `agents.brief_cap_usd`, `agents.briefs`, `agents.case_cap_usd`, `agents.effort`, `agents.fallbacks`, `agents.model`, `agents.provider`, `agents.specialist_effort`, `agents.specialist_model`, `assistant.concurrent_questions`, `assistant.retention_days`, `assistant.timeout_seconds`, `work.lease_seconds` ([settings](settings.md))
- **Tools:** asset-performance-reporting 37, field-operations-facilities 83, hse-incident-management 39, maintenance-integrity 65, orchestrator 23, production-surveillance 64, wells-subsurface 54, work-management-supply-chain 32 ([capabilities](capabilities.md))
- **Tests:** [`tests/agents`](https://github.com/areed145/fuzer/tree/main/tests/agents)

### Evals

Answer keys, the history replay and the ops eval that score it all. More in [evals](evals.md).

- **Code:** [`src/fuzer/evals`](https://github.com/areed145/fuzer/tree/main/src/fuzer/evals): `evaluate`, `loop`, `ops_eval`, `replay`, `roles`, `runs`, `steward_eval`
- **Uses:** agents, analysis, core, graph, ops, sources, workflow
- **Writes:** `assistant_answer`, `assistant_fact`, `golden`, `lesson`, `ops_eval`, `point_normal`, `source_record` ([schema](schema.md))
- **Owns:** `ops_eval` (its `schema.sql`)
- **Settings:** `agents.model`, `opportunities.gas_lift_breakeven` ([settings](settings.md))
- **Tests:** [`tests/evals`](https://github.com/areed145/fuzer/tree/main/tests/evals)

### API

The HTTP API the app uses. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/api`](https://github.com/areed145/fuzer/tree/main/src/fuzer/api): `assistant`, `auth`, `catalog`, `deps`, `entities`, `graph`, `home`, `hooks`, `investigation`, `jobs`, `knowledge`, `metrics`, `ontology`, `ops`, `security`, `settings`, `sources`, `stewardship`, `workflow`
- **Uses:** agents, analysis, core, graph, ops, plots, sources, workflow
- **Writes:** `access_login`, `access_session`, `feedback`, `pinned_link`, `value_ranking` ([schema](schema.md))
- **Settings:** `access.session_hours`, `agents.case_cap_usd`, `demo.enabled` ([settings](settings.md))
- **Tests:** [`tests/api`](https://github.com/areed145/fuzer/tree/main/tests/api)

### Command line

The fuzer command: up, eval, settings and the rest. More in [cli](cli.md).

- **Code:** [`src/fuzer/cli`](https://github.com/areed145/fuzer/tree/main/src/fuzer/cli): `demo_seed`, `deploy`, `sources`, `up`
- **Uses:** agents, analysis, core, evals, graph, ops, sources, workflow
- **Writes:** `llm_extraction` ([schema](schema.md))
- **Tests:** [`tests/cli`](https://github.com/areed145/fuzer/tree/main/tests/cli)

### Core

Database, settings, projects and packs, Claude calls, tags and units: used by everything. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/core`](https://github.com/areed145/fuzer/tree/main/src/fuzer/core): `access`, `assistant`, `bundles`, `capabilities`, `db`, `detectors`, `events`, `exceptions`, `findings`, `gates`, `jsonsafe`, `labels`, `llm`, `methods`, `metrics`, `model`, `models`, `nel`, `ontology`, `pack`, `plan`, `pricing`, `projects`, `roles`, `settings`, `tags`, `taxonomy`, `units`, `work`
- **Uses:** nothing else in fuzer
- **Writes:** `access_account`, `access_event`, `access_membership`, `access_project_grant`, `access_role`, `access_session`, `detector_run`, `event_outbox`, `gate_decision`, `gate_policy`, `ontology_audit`, `ontology_kind`, `ontology_relation`, `project`, `schema_version`, `setting`, `setting_history`, `work_person` ([schema](schema.md))
- **Owns:** `schema_version`, `access_account`, `access_role`, `access_membership`, `access_session`, `access_project_grant`, `access_login`, `access_event`, `setting`, `setting_history`, `project`, `ontology_kind`, `ontology_relation`, `ontology_audit`, `event_outbox`, `gate_policy`, `gate_decision` (its `schema.sql`)
- **Settings:** `access.session_hours`, `llm.effort`, `llm.model`, `sre.handoff.max_usd` ([settings](settings.md))
- **Tests:** [`tests/core`](https://github.com/areed145/fuzer/tree/main/tests/core)
