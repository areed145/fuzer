# Architecture

Generated from the code: package imports, mappings, the capability and settings registries, the schedules and `schema.sql`; don't edit by hand (`uv run python -m fuzer.reference`).
Each box is a package under `src/fuzer`; click one to open it. An arrow points from a package to the ones that use it, so data flows along the arrows. Everything uses core, so its arrows are left out.

## Components

```mermaid
flowchart LR
  systems[("Source systems<br>SAP, PI, EC, P&IDs, logs")] --> c_sources
  app(["The app (ui/)"])
  c_api --> app
  c_sources["Sources and ingestion<br><small>fuzer.sources, 22 modules</small>"]
  c_graph["Graph and golden records<br><small>fuzer.graph, 16 modules</small>"]
  c_analysis["Analyses<br><small>fuzer.analysis, 11 modules</small>"]
  c_ops["Operations<br><small>fuzer.ops, 8 modules</small>"]
  c_plots["Charts<br><small>fuzer.plots, 4 modules</small>"]
  c_workflow["Work engine<br><small>fuzer.workflow, 2 modules</small>"]
  c_agents["Agents and MCP<br><small>fuzer.agents, 12 modules</small>"]
  c_evals["Evals<br><small>fuzer.evals, 4 modules</small>"]
  c_api["API<br><small>fuzer.api, 13 modules</small>"]
  c_cli["Command line<br><small>fuzer.cli, 2 modules</small>"]
  c_core["Core<br><small>fuzer.core, 14 modules</small>"]
  c_workflow --> c_sources
  c_sources --> c_graph
  c_graph --> c_analysis
  c_sources --> c_analysis
  c_workflow --> c_analysis
  c_agents --> c_ops
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
  src_sap[/"sap"/]
  kind_daily_report(["daily_report"])
  kind_document(["document"])
  kind_document_section(["document_section"])
  kind_downtime(["downtime"])
  kind_equipment(["equipment"])
  kind_log_entry(["log_entry"])
  kind_maintenance_plan(["maintenance_plan"])
  kind_material(["material"])
  kind_notification(["notification"])
  kind_pipeline(["pipeline"])
  kind_plant_hierarchy__facility__area__tag__well(["plant hierarchy: facility, area, tag, well"])
  kind_timeseries(["timeseries"])
  kind_well(["well"])
  kind_well_test(["well_test"])
  kind_work_order(["work_order"])
  src_adx --> kind_timeseries
  src_ec --> kind_daily_report
  src_ec --> kind_downtime
  src_ec --> kind_plant_hierarchy__facility__area__tag__well
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
  src_sap --> kind_equipment
  src_sap --> kind_maintenance_plan
  src_sap --> kind_material
  src_sap --> kind_notification
  src_sap --> kind_plant_hierarchy__facility__area__tag__well
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
  s3["<b>handoff</b><br>the troubleshooter diagnoses new upsets; each becomes a work item"] -.-> t3[("work_item")]
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
| `sre_scan` | `0 */6 * * *` | SRE every 6 hours: detect, group into upsets, prioritise, diagnose (capped) and hand off. |
| `studies_job` | `*/5 * * * *` | Big correlation and clustering studies, queued from the API or an agent. |
| `day_close` | `0 7 * * *` | Daily operations report: facts from the day's records, a new version when they change, the ops lead's review in the workflow (the reporter agent drafts it). |
| `source_health` | `*/30 * * * *` | Source health: stale data, failed or held loads, failing checks -> work items and alerts. |

## Each component

### Sources and ingestion

The source catalog (systems, connections, datasets, mapping versions, runs) drives loading; connectors read each table; mappings turn rows into records, entities and links; Claude reads logs, P&IDs and documents. More in [sources](sources.md).

- **Code:** [`src/fuzer/sources`](https://github.com/areed145/fuzer/tree/main/src/fuzer/sources): `adx`, `alerts`, `catalog`, `connections`, `connectors`, `curves`, `documents`, `extract`, `formats`, `impact`, `ingest`, `llm_curves`, `llm_docs`, `llm_pids`, `mappings`, `pi_web_api`, `preview`, `process_model`, `quality`, `rules`, `suggest`, `timeseries`
- **Uses:** core, workflow
- **Writes:** `curve_digitised`, `llm_extraction`, `mapping_version`, `review_item`, `source_audit`, `source_check`, `source_check_result`, `source_connection`, `source_dataset`, `source_match_stat`, `source_profile`, `source_record`, `source_record_history`, `source_rule`, `source_run`, `source_system` ([schema](schema.md))
- **Tests:** [`tests/sources`](https://github.com/areed145/fuzer/tree/main/tests/sources)

### Graph and golden records

Resolves records into one entity per real thing, links them, and answers queries over the graph. More in [identity](identity.md).

- **Code:** [`src/fuzer/graph`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph): `edges`, `golden`, `incidents`, `knowledge`, `lessons`, `llm_links`, `measurements`, `operations`, `queries.documents`, `queries.downtime`, `queries.maintenance`, `queries.network`, `queries.records`, `resolve`, `spatial`, `topology`
- **Uses:** core, sources
- **Writes:** `crosswalk`, `edge`, `entity`, `id_event`, `lesson`, `llm_extraction`, `review_item` ([schema](schema.md))
- **Tests:** [`tests/graph`](https://github.com/areed145/fuzer/tree/main/tests/graph)

### Analyses

Signals, limits, calcs, hydraulics, studies and clustering over the graph and the process values. More in [analyses](analyses.md).

- **Code:** [`src/fuzer/analysis`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis): `alarms`, `calcs`, `cases`, `clusters`, `equipment`, `hydraulics`, `limits`, `signals`, `studies`, `value`, `values`
- **Uses:** core, graph, sources, workflow
- **Writes:** `debottleneck_option`, `excursion`, `point_normal` ([schema](schema.md))
- **Settings:** `alarms.failed_start_minutes`, `sre.hops`, `studies.max_points`, `studies.max_values`, `studies.workers` ([settings](settings.md))
- **Tests:** [`tests/analysis`](https://github.com/areed145/fuzer/tree/main/tests/analysis)

### Operations

The scheduled loop: SRE detection and grouping, hand-off, the board, daily reports, opportunities and studies. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/ops`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops): `board`, `daily_report`, `jobs`, `opportunities`, `pipeline`, `sre`, `sre_handoff`, `study`
- **Uses:** agents, analysis, core, graph, plots, sources, workflow
- **Writes:** `board_card`, `board_history`, `board_pin`, `board_project`, `daily_ops_report`, `detection`, `opportunity`, `register_summary`, `sre_run`, `sre_suppress`, `study`, `upset`, `work_item` ([schema](schema.md))
- **Settings:** `daily_report.close_cron`, `daily_report.mover_pct`, `opportunities.gas_lift_breakeven`, `opportunities.material`, `opportunities.stale_days`, `sre.drift_rise`, `sre.group_gap_hours`, `sre.handoff.max_diagnoses`, `sre.handoff.max_usd`, `sre.handoff.min_detections`, `sre.hops`, `sre.learned_minutes`, `sre.lookback_hours`, `sre.merge_minutes`, `sre.spe_minutes`, `sre.step_minutes`, `studies.max_values` ([settings](settings.md))
- **Tests:** [`tests/ops`](https://github.com/areed145/fuzer/tree/main/tests/ops)

### Charts

Interactive charts the agents and the app build from Fuzer data. More in [agents](agents.md).

- **Code:** [`src/fuzer/plots`](https://github.com/areed145/fuzer/tree/main/src/fuzer/plots): `general`, `maintenance`, `production`, `troubleshooting`
- **Uses:** analysis, core, graph, ops, sources
- **Writes:** `assistant_figure` ([schema](schema.md))
- **Tests:** [`tests/plots`](https://github.com/areed145/fuzer/tree/main/tests/plots)

### Work engine

BPMN processes that move work between agents and people. More in [workflow](workflow.md).

- **Code:** [`src/fuzer/workflow`](https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow): `bpmn`, `engine`
- **Uses:** nothing else in fuzer
- **Writes:** `work_definition`, `work_event`, `work_form`, `work_item`, `work_person`, `work_process`, `work_token` ([schema](schema.md))
- **Tests:** [`tests/workflow`](https://github.com/areed145/fuzer/tree/main/tests/workflow)

### Agents and MCP

The operations assistant, its specialists, and the MCP server with their tools. More in [agents](agents.md).

- **Code:** [`src/fuzer/agents`](https://github.com/areed145/fuzer/tree/main/src/fuzer/agents): `assistant`, `capabilities.__main__`, `capabilities.maintenance`, `capabilities.production`, `capabilities.shared`, `capabilities.troubleshooting`, `capabilities.work`, `history`, `mcp_server`, `memory`, `service`, `worker`
- **Uses:** analysis, core, graph, ops, plots, sources, workflow
- **Writes:** `assistant_answer`, `assistant_conversation`, `assistant_fact`, `assistant_image`, `assistant_message`, `lesson`, `work_token` ([schema](schema.md))
- **Settings:** `agents.effort`, `agents.fallbacks`, `agents.model`, `agents.specialist_effort`, `agents.specialist_model`, `assistant.retention_days`, `work.lease_seconds` ([settings](settings.md))
- **Tools:** maintenance 50, orchestrator 20, production 55, troubleshooter 70 ([capabilities](capabilities.md))
- **Tests:** [`tests/agents`](https://github.com/areed145/fuzer/tree/main/tests/agents)

### Evals

Answer keys, the history replay and the ops eval that score it all. More in [evals](evals.md).

- **Code:** [`src/fuzer/evals`](https://github.com/areed145/fuzer/tree/main/src/fuzer/evals): `evaluate`, `ops_eval`, `replay`, `runs`
- **Uses:** agents, analysis, core, graph, ops, sources, workflow
- **Writes:** `assistant_answer`, `assistant_fact`, `lesson`, `ops_eval`, `point_normal` ([schema](schema.md))
- **Settings:** `opportunities.gas_lift_breakeven` ([settings](settings.md))
- **Tests:** [`tests/evals`](https://github.com/areed145/fuzer/tree/main/tests/evals)

### API

The HTTP API the app uses. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/api`](https://github.com/areed145/fuzer/tree/main/src/fuzer/api): `architecture`, `assistant`, `catalog`, `deps`, `entities`, `graph`, `home`, `jobs`, `knowledge`, `ops`, `settings`, `sources`, `workflow`
- **Uses:** agents, analysis, core, graph, ops, plots, sources, workflow
- **Writes:** `assertion`, `feedback`, `golden`, `pinned_link`, `review_item`, `source_dataset`, `value_ranking` ([schema](schema.md))
- **Tests:** [`tests/api`](https://github.com/areed145/fuzer/tree/main/tests/api)

### Command line

The fuzer command: up, eval, settings and the rest. More in [cli](cli.md).

- **Code:** [`src/fuzer/cli`](https://github.com/areed145/fuzer/tree/main/src/fuzer/cli): `sources`, `up`
- **Uses:** agents, analysis, core, evals, graph, ops, sources, workflow
- **Writes:** `llm_extraction` ([schema](schema.md))
- **Tests:** [`tests/cli`](https://github.com/areed145/fuzer/tree/main/tests/cli)

### Core

Database, settings, projects and packs, Claude calls, tags and units: used by everything. More in [deployment](deployment.md).

- **Code:** [`src/fuzer/core`](https://github.com/areed145/fuzer/tree/main/src/fuzer/core): `db`, `jsonsafe`, `labels`, `llm`, `model`, `models`, `nel`, `pack`, `pricing`, `projects`, `settings`, `tags`, `taxonomy`, `units`
- **Uses:** nothing else in fuzer
- **Writes:** `project`, `schema_version`, `setting`, `setting_history` ([schema](schema.md))
- **Settings:** `llm.effort`, `llm.model`, `sre.handoff.max_usd` ([settings](settings.md))
- **Tests:** [`tests/core`](https://github.com/areed145/fuzer/tree/main/tests/core)
