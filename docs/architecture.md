# Architecture

Generated from the code: package imports, mappings, the capability and settings registries, the schedules and the modules' `schema.sql`; don't edit by hand (`uv run python -m fuzer.reference`).
Each box is a package under `src/fuzer`; click one to open it. An arrow points from a package to the ones that use it, so data flows along the arrows. Everything uses core, so its arrows are left out.

## Components

```mermaid
flowchart LR
  systems[("Source systems<br>SAP, PI, EC, P&IDs, logs")] --> c_sources
  app(["The app (ui/)"])
  c_api --> app
  c_sources["Sources and ingestion<br><small>fuzer.sources</small>"]
  c_graph["Graph and golden records<br><small>fuzer.graph</small>"]
  c_analysis["Analyses<br><small>fuzer.analysis</small>"]
  c_ops["Operations<br><small>fuzer.ops</small>"]
  c_plots["Charts<br><small>fuzer.plots</small>"]
  c_workflow["Work engine<br><small>fuzer.workflow</small>"]
  c_agents["Agents and MCP<br><small>fuzer.agents</small>"]
  c_evals["Evals<br><small>fuzer.evals</small>"]
  c_api["API<br><small>fuzer.api</small>"]
  c_cli["Command line<br><small>fuzer.cli</small>"]
  c_core["Core<br><small>fuzer.core</small>"]
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
  c_api --> c_cli
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
  src_accounting[/"accounting"/]
  src_adx[/"adx"/]
  src_cfihos[/"cfihos"/]
  src_cms[/"cms"/]
  src_dcs[/"dcs"/]
  src_drilling[/"drilling"/]
  src_ec[/"ec"/]
  src_edms[/"edms"/]
  src_gis[/"gis"/]
  src_inspection[/"inspection"/]
  src_jde[/"jde"/]
  src_maximo[/"maximo"/]
  src_moc[/"moc"/]
  src_oplog[/"oplog"/]
  src_pi[/"pi"/]
  src_pi_af[/"pi_af"/]
  src_pid[/"pid"/]
  src_ptw[/"ptw"/]
  src_regulatory[/"regulatory"/]
  src_rounds[/"rounds"/]
  src_safeguards[/"safeguards"/]
  src_sap[/"sap"/]
  src_teams[/"teams"/]
  kind_accounting_volume(["accounting_volume"])
  kind_bypass(["bypass"])
  kind_change(["change"])
  kind_completion_component(["completion_component"])
  kind_control_loop(["control_loop"])
  kind_conversation(["conversation"])
  kind_demand(["demand"])
  kind_document(["document"])
  kind_document_section(["document_section"])
  kind_downtime(["downtime"])
  kind_equipment(["equipment"])
  kind_isolation(["isolation"])
  kind_ledger_entry(["ledger_entry"])
  kind_log_entry(["log_entry"])
  kind_maintenance_plan(["maintenance_plan"])
  kind_material(["material"])
  kind_notification(["notification"])
  kind_permit(["permit"])
  kind_permit_condition(["permit_condition"])
  kind_pipeline(["pipeline"])
  kind_pipeline_feature(["pipeline_feature"])
  kind_pipeline_segment(["pipeline_segment"])
  kind_plant_hierarchy__facility__area__tag__well(["plant hierarchy: facility, area, tag, well"])
  kind_production_day(["production_day"])
  kind_purchase_order(["purchase_order"])
  kind_round_point(["round_point"])
  kind_safeguard(["safeguard"])
  kind_step_test(["step_test"])
  kind_tag(["tag"])
  kind_timeseries(["timeseries"])
  kind_vibration_spectrum(["vibration_spectrum"])
  kind_well(["well"])
  kind_well_test(["well_test"])
  kind_work_center(["work_center"])
  kind_work_order(["work_order"])
  src_accounting --> kind_accounting_volume
  src_accounting --> kind_ledger_entry
  src_adx --> kind_timeseries
  src_cfihos --> kind_document
  src_cfihos --> kind_equipment
  src_cfihos --> kind_tag
  src_cms --> kind_vibration_spectrum
  src_dcs --> kind_control_loop
  src_dcs --> kind_step_test
  src_drilling --> kind_completion_component
  src_ec --> kind_downtime
  src_ec --> kind_plant_hierarchy__facility__area__tag__well
  src_ec --> kind_production_day
  src_ec --> kind_well_test
  src_edms --> kind_document
  src_edms --> kind_document_section
  src_gis --> kind_pipeline
  src_gis --> kind_pipeline_segment
  src_gis --> kind_plant_hierarchy__facility__area__tag__well
  src_gis --> kind_well
  src_inspection --> kind_pipeline_feature
  src_jde --> kind_equipment
  src_maximo --> kind_equipment
  src_moc --> kind_change
  src_oplog --> kind_log_entry
  src_pi --> kind_timeseries
  src_pi_af --> kind_plant_hierarchy__facility__area__tag__well
  src_pid --> kind_document
  src_ptw --> kind_isolation
  src_ptw --> kind_permit
  src_regulatory --> kind_permit_condition
  src_rounds --> kind_round_point
  src_safeguards --> kind_bypass
  src_safeguards --> kind_demand
  src_safeguards --> kind_safeguard
  src_sap --> kind_equipment
  src_sap --> kind_maintenance_plan
  src_sap --> kind_material
  src_sap --> kind_notification
  src_sap --> kind_plant_hierarchy__facility__area__tag__well
  src_sap --> kind_purchase_order
  src_sap --> kind_work_center
  src_sap --> kind_work_order
  src_teams --> kind_conversation
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
| `detector_tuning` | `30 4 * * *` | Tuning: a quieter value proposed, replayed on its closed cases, for a detector whose noise passed its threshold. |
| `report_calendar` | `0 5 * * *` | Obligations: each regulatory report drafted ahead of its due date, and work for one coming due. |
| `sre_scan` | `0 */6 * * *` | SRE every 6 hours: detect, group into upsets, prioritise, diagnose (capped) and hand off. |
| `studies_job` | `*/5 * * * *` | Big correlation and clustering studies, queued from the API or an agent. |
| `day_close` | `0 7 * * *` | Daily operations report: facts from the day's records, a new version when they change, the ops lead's review in the workflow (the reporter agent drafts it). |
| `source_health` | `*/30 * * * *` | Source health: stale data, failed or held loads, failing checks -> exceptions, cases and alerts. |
| `providers_poll` | `*/15 * * * *` | Detection providers: polled alert tables read when changed -> findings joined to cases. |
| `detect_5min` | `*/5 * * * *` | Detectors every 5min: each from its own watermark (P3.2). |
| `detect_30min` | `*/30 * * * *` | Detectors every 30min: each from its own watermark (P3.2). |
| `detect_hourly` | `7 * * * *` | Detectors every hourly: each from its own watermark (P3.2). |
| `detect_6h` | `20 */6 * * *` | Detectors every 6h: each from its own watermark (P3.2). |
| `detect_daily` | `40 2 * * *` | Detectors every daily: each from its own watermark (P3.2). |

## Each component

### Sources and ingestion

The source catalog (systems, connections, datasets, mapping versions, runs) drives loading; connectors read each table; mappings turn rows into records, entities and links; Claude reads logs, P&IDs and documents. More in [sources](sources.md).

- **Code:** [`src/fuzer/sources`](https://github.com/areed145/fuzer/tree/main/src/fuzer/sources)
- **Uses:** core
- **Writes** ([schema](schema.md)):
    - `curve_digitised`
    - `detection_provider`
    - `entity_alias`
    - `learned_rule`
    - `llm_extraction`
    - `mapping_version`
    - `provider_poll`
    - `provider_trust`
    - `review_item`
    - `source_audit`
    - `source_check`
    - `source_check_result`
    - `source_connection`
    - `source_dataset`
    - `source_exception_rule`
    - `source_match_stat`
    - `source_profile`
    - `source_record`
    - `source_record_history`
    - `source_rule`
    - `source_run`
    - `source_score`
    - `source_system`
- **Owns** (its `schema.sql`):
    - `curve_digitised`
    - `detection_provider`
    - `entity_alias`
    - `equipment_curve`
    - `learned_rule`
    - `llm_extraction`
    - `mapping_version`
    - `provider_poll`
    - `provider_trust`
    - `source_audit`
    - `source_check`
    - `source_check_result`
    - `source_connection`
    - `source_dataset`
    - `source_exception_rule`
    - `source_match_stat`
    - `source_profile`
    - `source_record`
    - `source_record_history`
    - `source_rule`
    - `source_run`
    - `source_score`
    - `source_system`
- **Settings:** `notify.app_url`, `providers.triage_role` ([settings](settings.md))
- **Tests:** [`tests/sources`](https://github.com/areed145/fuzer/tree/main/tests/sources)

### Graph and golden records

Resolves records into one entity per real thing, links them, and answers queries over the graph. More in [identity](identity.md).

- **Code:** [`src/fuzer/graph`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph)
- **Uses:** core, sources
- **Writes** ([schema](schema.md)):
    - `assertion`
    - `crosswalk`
    - `edge`
    - `entity`
    - `geo_index`
    - `golden`
    - `id_event`
    - `lesson`
    - `lesson_history`
    - `llm_extraction`
    - `match_calibration`
    - `match_score`
    - `measurement_role`
    - `measurement_role_pin`
    - `ontology_kind`
    - `ontology_relation`
    - `pinned_link`
    - `placement_decision`
    - `register_summary`
    - `review_item`
    - `review_rule`
    - `reviewed_value`
    - `source_dataset`
    - `source_new_record`
    - `steward_fix`
    - `steward_proposal`
    - `unlinked`
- **Owns** (its `schema.sql`):
    - `assertion`
    - `crosswalk`
    - `edge`
    - `entity`
    - `entity_key`
    - `entity_label`
    - `feedback`
    - `geo_index`
    - `golden`
    - `id_event`
    - `knowledge_index`
    - `lesson`
    - `lesson_history`
    - `match_calibration`
    - `match_score`
    - `measurement`
    - `measurement_role`
    - `measurement_role_pin`
    - `pinned_link`
    - `placement_decision`
    - `register_summary`
    - `review_item`
    - `review_rule`
    - `reviewed_value`
    - `source_new_record`
    - `steward_fix`
    - `steward_proposal`
    - `unlinked`
    - `value_ranking`
- **Settings:** `stewardship.bulk_accept_at` ([settings](settings.md))
- **Tests:** [`tests/graph`](https://github.com/areed145/fuzer/tree/main/tests/graph)

### Analyses

Signals, limits, calcs, hydraulics, studies and clustering over the graph and the process values. More in [analyses](analyses.md).

- **Code:** [`src/fuzer/analysis`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis)
- **Uses:** core, graph, sources
- **Writes** ([schema](schema.md)):
    - `debottleneck_option`
    - `excursion`
    - `point_normal`
- **Owns** (its `schema.sql`):
    - `debottleneck_option`
    - `excursion`
    - `point_limit`
    - `point_normal`
- **Settings:** `alarms.failed_start_minutes`, `alarms.flood_per_10min`, `alarms.standing_hours`, `alarms.target_per_hour`, `records.log_tolerance_hours`, `records.lookback_days`, `records.notification_categories`, `records.notification_tolerance_days`, `sre.hops`, `studies.max_points`, `studies.max_values`, `studies.workers` ([settings](settings.md))
- **Tests:** [`tests/analysis`](https://github.com/areed145/fuzer/tree/main/tests/analysis)

### Operations

The scheduled loop: SRE detection and grouping, hand-off, the board, daily reports, opportunities and studies. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/ops`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops)
- **Uses:** analysis, core, graph, plots, sources, workflow
- **Writes** ([schema](schema.md)):
    - `board_card`
    - `board_history`
    - `board_pin`
    - `board_project`
    - `case_disposition`
    - `case_repeat`
    - `daily_ops_report`
    - `detection`
    - `detector_tier_run`
    - `exception_kind`
    - `exception_record`
    - `field_photo`
    - `measurement`
    - `opportunity`
    - `plan_item`
    - `point_baseline`
    - `promoted_rule`
    - `register_summary`
    - `regulatory_report`
    - `report_obligation`
    - `review_item`
    - `round_reading`
    - `round_run`
    - `shift_handover`
    - `source_closeout`
    - `source_correction`
    - `sre_run`
    - `sre_suppress`
    - `study`
    - `suppressed_finding`
    - `suppression`
    - `upset`
    - `watch_proposal`
    - `work_item`
    - `writeback`
    - `writeback_event`
- **Owns** (its `schema.sql`):
    - `board_card`
    - `board_history`
    - `board_pin`
    - `board_project`
    - `case_disposition`
    - `case_repeat`
    - `daily_ops_report`
    - `detection`
    - `detector_run`
    - `detector_tier_run`
    - `exception_kind`
    - `exception_record`
    - `field_photo`
    - `opportunity`
    - `plan_item`
    - `point_baseline`
    - `promoted_rule`
    - `regulatory_report`
    - `report_obligation`
    - `round_reading`
    - `round_run`
    - `shift_handover`
    - `source_closeout`
    - `source_correction`
    - `sre_run`
    - `sre_suppress`
    - `study`
    - `suppressed_finding`
    - `suppression`
    - `upset`
    - `watch_proposal`
    - `writeback`
    - `writeback_event`
- **Settings:** `agents.model`, `closeout.apply_to_exports`, `closeout.recheck_days`, `daily_report.close_cron`, `daily_report.mover_pct`, `detectors.cadence`, `exceptions.around_after_hours`, `exceptions.around_before_hours`, `exceptions.join_hours`, `exceptions.max_shelve_days`, `exceptions.suppress_planned_work`, `handover.shift_times`, `intake.sap_notifications`, `intake.sap_notifications.due_hours`, `intake.sap_notifications.max_age_days`, `opportunities.gas_lift_breakeven`, `opportunities.material`, `opportunities.stale_days`, `readiness.permit_types`, `sre.drift_rise`, `sre.group_gap_hours`, `sre.handoff.max_diagnoses`, `sre.handoff.max_usd`, `sre.handoff.min_detections`, `sre.hops`, `sre.learned_minutes`, `sre.lookback_hours`, `sre.merge_minutes`, `sre.spe_minutes`, `sre.step_minutes`, `studies.max_values`, `tuning.max_windows`, `tuning.min_cases`, `tuning.noise_rate`, `watch.max_areas`, `watch.max_usd`, `watch.promote_after` ([settings](settings.md))
- **Tests:** [`tests/ops`](https://github.com/areed145/fuzer/tree/main/tests/ops)

### Charts

Interactive charts the agents and the app build from Fuzer data. More in [agents](agents.md).

- **Code:** [`src/fuzer/plots`](https://github.com/areed145/fuzer/tree/main/src/fuzer/plots)
- **Uses:** analysis, core, graph, ops, sources
- **Writes** ([schema](schema.md)):
    - `assistant_figure`
- **Owns** (its `schema.sql`):
    - `assistant_figure`
- **Tests:** [`tests/plots`](https://github.com/areed145/fuzer/tree/main/tests/plots)

### Work engine

BPMN processes that move work between agents and people. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/workflow`](https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow)
- **Uses:** core
- **Writes** ([schema](schema.md)):
    - `exception_kind`
    - `operating_model_applied`
    - `operating_model_deviation`
    - `work_definition`
    - `work_draft`
    - `work_event`
    - `work_form`
    - `work_home_seen`
    - `work_item`
    - `work_notification`
    - `work_notify_cursor`
    - `work_notify_pref`
    - `work_person`
    - `work_process`
    - `work_role`
    - `work_token`
    - `work_view`
    - `work_watch`
- **Owns** (its `schema.sql`):
    - `operating_model_applied`
    - `operating_model_deviation`
    - `operating_model_rollout`
    - `operating_model_version`
    - `work_definition`
    - `work_draft`
    - `work_event`
    - `work_form`
    - `work_home_seen`
    - `work_item`
    - `work_notification`
    - `work_notify_cursor`
    - `work_notify_pref`
    - `work_person`
    - `work_person_cover`
    - `work_process`
    - `work_role`
    - `work_token`
    - `work_view`
    - `work_watch`
- **Settings:** `notify.app_url`, `notify.from`, `work.agent_fallback_minutes`, `work.due_soon_minutes` ([settings](settings.md))
- **Tests:** [`tests/workflow`](https://github.com/areed145/fuzer/tree/main/tests/workflow)

### Agents and MCP

The operations assistant, its specialists, and the MCP server with their tools. More in [agents](agents.md).

- **Code:** [`src/fuzer/agents`](https://github.com/areed145/fuzer/tree/main/src/fuzer/agents)
- **Uses:** analysis, core, graph, ops, plots, sources, workflow
- **Writes** ([schema](schema.md)):
    - `assistant_answer`
    - `assistant_conversation`
    - `assistant_fact`
    - `assistant_image`
    - `assistant_message`
    - `lesson`
    - `skill_version`
    - `work_event`
    - `work_token`
- **Owns** (its `schema.sql`):
    - `assistant_answer`
    - `assistant_conversation`
    - `assistant_fact`
    - `assistant_image`
    - `assistant_message`
    - `skill_version`
- **Settings:** `agents.brief_cap_usd`, `agents.briefs`, `agents.case_cap_usd`, `agents.effort`, `agents.fallbacks`, `agents.model`, `agents.provider`, `agents.skill_drafts`, `agents.specialist_effort`, `agents.specialist_model`, `assistant.concurrent_questions`, `assistant.retention_days`, `assistant.timeout_seconds`, `work.lease_seconds` ([settings](settings.md))
- **Tools:** asset-performance-reporting 50, field-operations-facilities 97, hse-incident-management 51, maintenance-integrity 86, orchestrator 23, production-surveillance 80, wells-subsurface 73, work-management-supply-chain 39 ([capabilities](capabilities.md))
- **Tests:** [`tests/agents`](https://github.com/areed145/fuzer/tree/main/tests/agents)

### Evals

Answer keys, the history replay and the ops eval that score it all. More in [evals](evals.md).

- **Code:** [`src/fuzer/evals`](https://github.com/areed145/fuzer/tree/main/src/fuzer/evals)
- **Uses:** agents, analysis, core, graph, ops, sources, workflow
- **Writes** ([schema](schema.md)):
    - `assistant_answer`
    - `assistant_fact`
    - `exception_record`
    - `golden`
    - `lesson`
    - `llm_extraction`
    - `ops_eval`
    - `point_normal`
    - `source_record`
    - `source_system`
    - `work_event`
    - `work_item`
- **Owns** (its `schema.sql`):
    - `ops_eval`
- **Settings:** `agents.model`, `opportunities.gas_lift_breakeven` ([settings](settings.md))
- **Tests:** [`tests/evals`](https://github.com/areed145/fuzer/tree/main/tests/evals)

### API

The HTTP API the app uses. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/api`](https://github.com/areed145/fuzer/tree/main/src/fuzer/api)
- **Uses:** agents, analysis, core, graph, ops, plots, sources, workflow
- **Writes** ([schema](schema.md)):
    - `access_login`
    - `access_session`
    - `feedback`
    - `pinned_link`
    - `value_ranking`
- **Settings:** `access.session_hours`, `agents.case_cap_usd`, `demo.enabled`, `notify.app_url` ([settings](settings.md))
- **Tests:** [`tests/api`](https://github.com/areed145/fuzer/tree/main/tests/api)

### Command line

The fuzer command: up, eval, settings and the rest. More in [cli](cli.md).

- **Code:** [`src/fuzer/cli`](https://github.com/areed145/fuzer/tree/main/src/fuzer/cli)
- **Uses:** agents, analysis, api, core, evals, graph, ops, sources, workflow
- **Writes** ([schema](schema.md)):
    - `llm_extraction`
- **Tests:** [`tests/cli`](https://github.com/areed145/fuzer/tree/main/tests/cli)

### Core

Database, settings, projects and packs, Claude calls, tags and units: used by everything. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/core`](https://github.com/areed145/fuzer/tree/main/src/fuzer/core)
- **Uses:** nothing else in fuzer
- **Writes** ([schema](schema.md)):
    - `access_account`
    - `access_area_grant`
    - `access_document_group`
    - `access_event`
    - `access_group_grant`
    - `access_membership`
    - `access_project_grant`
    - `access_role`
    - `access_session`
    - `detector_run`
    - `event_outbox`
    - `gate_decision`
    - `gate_policy`
    - `ontology_audit`
    - `ontology_kind`
    - `ontology_relation`
    - `project`
    - `schema_version`
    - `setting`
    - `setting_history`
    - `work_person`
- **Owns** (its `schema.sql`):
    - `access_account`
    - `access_area_grant`
    - `access_document_group`
    - `access_event`
    - `access_group_grant`
    - `access_login`
    - `access_membership`
    - `access_project_grant`
    - `access_role`
    - `access_session`
    - `event_outbox`
    - `gate_decision`
    - `gate_policy`
    - `ontology_audit`
    - `ontology_kind`
    - `ontology_relation`
    - `project`
    - `schema_version`
    - `setting`
    - `setting_history`
- **Settings:** `access.session_hours`, `llm.effort`, `llm.model`, `sre.handoff.max_usd` ([settings](settings.md))
- **Tests:** [`tests/core`](https://github.com/areed145/fuzer/tree/main/tests/core)
