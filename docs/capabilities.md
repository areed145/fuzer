# Capabilities and agent catalog

Generated from the capability registry (`src/fuzer/agents/capabilities/`, #133); don't edit by hand. Each tool is declared once with the agents that use it. `?` marks an optional parameter.

## Domain agents

Tool availability is not evidence that every source exists. Each specialist stays within its listed data prerequisites and human-decision boundary, which are part of its prompt; unsupported inputs remain explicit unknowns.

| Agent | Routes here | Not for | Required data | Expected output | Limits | Human decision |
|---|---|---|---|---|---|---|
| Orchestrator | Routes questions to read-only domain specialists and synthesizes cited findings. | Domain analysis, which the specialists do. | Fuzer entity graph and whichever linked records a question requires. | A concise, cross-domain answer with citations, dates, units, gaps, and next steps. | Never present synthetic or historical data as live; state when evidence or prerequisites are missing. | Never make operating, maintenance, HSE, emergency, or schedule decisions. |
| Production & Surveillance | Asset production trends, production efficiency, deferment, constraints, gas lift allocation, back-pressure and optimisation opportunities. | One well's history or intervention case (Wells & Subsurface); why a unit tripped (Field Operations & Facilities). | Allocated production, potential, well tests, events, and linked facility records; dates and units must be available. | Dated production trends, ranked losses or constraints, and evidence-backed opportunities with uncertainty. | Do not infer reservoir causes or upside where the underlying evidence is absent. | Operators and accountable engineers approve operating changes and optimization actions. |
| Wells & Subsurface | One well or a set of wells: test and pressure trends, decline, water breakthrough, gas lift response, recorded intervention history and candidate screening. | Asset-wide production efficiency and deferment (Production & Surveillance); facility constraints (Field Operations & Facilities). | Linked well identity, dated well tests or production records, and intervention history when present. | Well-level evidence summaries and candidate questions for specialist review. | Reservoir state, completion design, and intervention outcomes are unknown unless recorded by an authoritative source. | Subsurface and well intervention decisions remain with accountable engineers. |
| Field Operations & Facilities | Troubleshooting a unit or upset, shift handover, alarms, instrument health, process values, operating limits, and facility constraints and headroom. | Failure history and backlog (Maintenance & Integrity); a formal incident investigation record (HSE & Incident Management). | Linked equipment and event records; process values require a configured time-series source. | Dated event timelines, observed signal changes, and questions for field verification. | Synthetic process values are not plant evidence; no control or operating-envelope validation is implied. | No control changes or field actions; operators and control-system owners make those decisions. |
| Maintenance & Integrity | Reliability, failure history, bad actors, PM and inspection compliance, condition evidence, wall thickness and integrity risk. | Planning and readiness of work (Work Management & Supply Chain); what a unit's process values did in an upset (Field Operations & Facilities). | Linked work orders, incidents, PM/inspection records, and measured condition data where available. | Ranked evidence summaries with data dates, units, and explicit coverage gaps. | OEM curves cover only the models loaded, so `equipment_curves` decides whether a curve comparison is possible; vibration is overall mm/s from PI, with no spectra; no inferred inspection status. | Maintenance execution, deferral, integrity acceptance, and shutdown decisions require accountable human approval. |
| Work Management & Supply Chain | Work backlog priorities, job scope and readiness, turnaround scope, and recorded spares and stock. | Why equipment keeps failing (Maintenance & Integrity). | Work orders, turnaround scope, and spares stock records when linked; vendor and transport data may be absent. | Readiness and backlog findings with explicit missing-resource information. | A recorded stock count is not a reservation; no vendor, transport, access, or schedule status is inferred. | People approve work priorities, schedules, resource commitments, and logistics. |
| HSE & Incident Management | Evidence-only incident timelines, investigation records, corrective-action tracking and recorded flare; no regulatory or emergency authority. | Live troubleshooting of a unit (Field Operations & Facilities). | Recorded incidents, event logs, and linked corrective work only; environmental and regulatory sources are not configured. | Source-linked incident timelines and clearly scoped evidence gaps for human review. | No compliance determination, hazard/barrier assurance, emergency procedure, or response advice without authoritative data. | Escalate safety-critical judgments and emergency response to accountable people and approved procedures. |
| Asset Performance & Reporting | Cited cross-domain KPI summaries, handovers and opportunity trade-offs, from the linked evidence available to Fuzer. | Explaining one domain's cause in depth (the domain specialist); accounting reconciliation (no authoritative source). | Current operational measures and provenance; authoritative allocation, cost, and accounting data are required for reconciliation. | Cross-domain KPI summaries with units, time windows, provenance, and explicit unknowns. | Commercial and accounting reconciliation is unavailable without authoritative accounting and cost sources. | Asset trade-offs and external/commercial reporting require human validation and approval. |

## Persona coverage

Every persona has one owner and at least one skill.

| Persona | Owner | Skills |
|---|---|---|
| Production Surveillance | Production & Surveillance | production-surveillance, production-efficiency, metering-and-balances |
| Production / Operations Engineer | Production & Surveillance | production-efficiency, constraint-management, gas-lift, production-engineering, metering-and-balances |
| Optimization Engineer | Production & Surveillance | production-surveillance, constraint-management, back-pressure, gas-lift |
| Subsurface Engineer | Wells & Subsurface | gas-lift, production-engineering, well-review, wells-and-lift |
| Drilling, Completions & Intervention Specialist | Wells & Subsurface | production-engineering, well-review, wells-and-lift |
| Field Operator | Field Operations & Facilities | troubleshooting, upset-correlation, gas-treating, shift-handover, alarms-and-instruments |
| Surface / Facilities Engineer | Field Operations & Facilities | constraint-management, back-pressure, troubleshooting, upset-correlation, gas-treating, shift-handover |
| Instrumentation, Controls & OT Specialist | Field Operations & Facilities | alarms-and-instruments |
| Maintenance Technician / Coordinator | Maintenance & Integrity | reliability, compression, pumps-and-drivers, work-orders |
| Reliability Engineer | Maintenance & Integrity | reliability, compression, pumps-and-drivers |
| Inspection Specialist | Maintenance & Integrity | reliability, static-equipment, integrity |
| Asset Integrity Engineer | Maintenance & Integrity | static-equipment, integrity |
| Work Planner | Work Management & Supply Chain | work-orders, turnarounds, job-readiness |
| Work Scheduler | Work Management & Supply Chain | work-orders, turnarounds, job-readiness |
| Supply Chain Specialist | Work Management & Supply Chain | turnarounds, job-readiness |
| Logistics Coordinator | Work Management & Supply Chain | job-readiness |
| HSE Specialist | HSE & Incident Management | incident-evidence |
| Environmental / Regulatory Specialist | HSE & Incident Management | incident-evidence |
| Incident Investigator | HSE & Incident Management | incident-evidence |
| Emergency Response Lead | HSE & Incident Management | incident-evidence |
| Asset Manager | Asset Performance & Reporting | operations-summary |
| Operations Reporter | Asset Performance & Reporting | operations-summary |
| Commercial / Production Accounting Specialist | Asset Performance & Reporting | operations-summary |
| Data Scientist / Operations Research Analyst | Shared analytical methods in domain agents (`shared/analysis`); no separate agent | analysis |
| Data Steward / Platform Administrator | Platform administration, Sources/Settings/Jobs, and governance; outside the operational-agent roster | none |

## Skills

Tools are read from each `SKILL.md`. A shared skill is loaded by every agent, each of which can call only the tools in its own list; a skill never grants a capability.

| Skill | Owner | Also used by | Personas | Purpose | Tools | Required data | Output | Human boundary |
|---|---|---|---|---|---|---|---|---|
| `orchestrator/answering` | Orchestrator | none | all | How to write the final answer to an engineer - sources on every fact, units and dates, what to check next, and the safety rules. Load before writing any final answer. |  | Findings returned by specialists and tools. | One cited answer with units, periods, gaps and next steps. | Never tells anyone to operate plant; recommends checks under site procedures. |
| `production-surveillance/production-surveillance` | Production & Surveillance | none | Production Surveillance; Optimization Engineer | Production surveillance and optimisation - how production is trending against potential, what changed and when, which facilities, wells or systems explain it, and where the upside is. | `calc`, `deferment_pareto`, `forecast_vs_actual`, `gas_lift_network`, `get_events`, `plot_forecast_vs_actual`, `production_constraints`, `production_summary`, `well_performance` | EC daily production and potential, deferment records, well tests, operator log. | A dated trend against potential, the change points and the systems that explain them. | Recommend checks and decisions to the accountable operator or engineer; never give operating instructions. |
| `production-surveillance/production-efficiency` | Production & Surveillance | none | Production Surveillance; Production / Operations Engineer | Production efficiency (PE) and deferment accounting - PE by period, deferment Pareto by reason, category, unit, class and well, planned vs unplanned losses, and what drives the gap to potential. | `deferment_pareto`, `downtime`, `production_summary` | EC daily production, potential and downtime records with reason codes. | Production efficiency and a deferment Pareto by reason, category, unit or well for a stated period. | Reports recorded losses; it is not production accounting. |
| `production-surveillance/constraint-management` | Production & Surveillance | Field Operations & Facilities | Production / Operations Engineer; Optimization Engineer; Surface / Facilities Engineer | Constraint management - what is limiting throughput (compression, lift gas, export, process trips, wells), how often and how much, whether it is getting worse, and how constraints cascade through the flow network. | `calc`, `debottleneck_options`, `get_events`, `process_values`, `production_constraints`, `trace_flow`, `well_performance` | Deferment by reason, causing units, the flow network and the operator log. | Ranked constraints with volume, hours, trend and how they cascade. | Recommend checks and decisions to the accountable operator or engineer; never give operating instructions. |
| `production-surveillance/back-pressure` | Production & Surveillance | Field Operations & Facilities | Optimization Engineer; Surface / Facilities Engineer | Back-pressure and hydraulics - what holds wells back without a deferment record (jacket header, separator, a fouling flowline, the choke), how much oil a bar is worth, and the ranked debottleneck options with their next constraint. | `backpressure_sensitivity`, `calc`, `capacity_headroom`, `debottleneck_options`, `plot_backpressure_sensitivity`, `plot_choke_criticality`, `plot_debottleneck`, `plot_dp_drift`, `plot_pressure_profile`, `pressure_profile` | Well and header pressures, well tests and operating limits; process values need a time-series source. | What holds wells back, what a bar is worth and ranked debottleneck options with their next constraint. | Options are for engineering review and management of change, not instructions. |
| `production-surveillance/gas-lift` | Production & Surveillance | Wells & Subsurface | Optimization Engineer; Production / Operations Engineer; Subsurface Engineer | Gas lift - how it works, how the lift gas network is laid out (compression, headers, jacket manifolds, wells, jackets fed through other jackets), and how to evaluate it - lift gas allocation and response per well, header pressure, shortfalls and who loses lift first, valve problems, and reallocation. | `deferment_pareto`, `gas_lift_network`, `gas_lift_stability`, `plot_gas_lift_heading`, `plot_gas_lift_screen`, `process_values`, `production_constraints`, `trace_flow`, `well_performance` | The lift gas network, well tests with lift gas rate, header and flow signals. | Allocation and response per well, shortfalls, suspected valve problems and reallocation candidates. | Reallocation is a recommendation for the accountable production engineer. |
| `production-surveillance/production-engineering` | Production & Surveillance | Wells & Subsurface | Production / Operations Engineer; Subsurface Engineer; Drilling, Completions & Intervention Specialist | Production engineering for wells - well test trends (rate, water cut, GOR, lift gas, WHP), decline and water breakthrough, gas lift performance and valve failures, well interventions and candidates. | `calc`, `detect_changes`, `downtime`, `gas_lift_network`, `get_entity`, `get_events`, `measurements`, `plot_choke`, `plot_custom`, `plot_decline`, `plot_measurements`, `trace_flow`, `well_performance` | Well tests, wellhead samples, choke records, downtime and operator log. | Well trends read against decline and water breakthrough, and intervention candidates with their evidence. | Reservoir causes stay hypotheses unless a source records them. |
| `wells-subsurface/well-review` | Wells & Subsurface | none | Subsurface Engineer; Drilling, Completions & Intervention Specialist | Review one well or a set of wells - dated history, test and pressure trends, recorded interventions, sidetracks, and intervention candidates with the evidence for and against, without implying reservoir certainty. | `calc`, `cluster_entities`, `detect_changes`, `downtime`, `get_entity`, `get_events`, `intervention_gains`, `maintenance_history`, `measurements`, `opportunities`, `plot_decline`, `plot_gas_lift_curve`, `plot_gor_trend`, `plot_intervention_gains`, `plot_water_diagnostic`, `record_history`, `test_staleness`, `well_diagnostics`, `well_performance` | Well identity and sidetracks, well tests, wellhead samples, downtime, operator log and work on the well. | A dated well history, what changed and when, and candidates with supporting and missing evidence. | No reservoir certainty, completion design or intervention decision; those stay with accountable engineers. |
| `wells-subsurface/wells-and-lift` | Wells & Subsurface | none | Subsurface Engineer; Drilling, Completions & Intervention Specialist | Wells and artificial lift - whether the latest well test can be trusted, the well's decline and forecast with its range, gas lift performance against the economic optimum, the choke's condition, and what limits the well (choke, back-pressure or lift), for any producing well. | `calc`, `detect_changes`, `downtime`, `intervention_gains`, `maintenance_history`, `opportunities`, `plot_choke`, `plot_decline`, `plot_dp_drift`, `plot_gas_lift_curve`, `plot_intervention_gains`, `plot_water_diagnostic`, `well_performance` | Well tests, wellhead pressure and temperature, choke position, lift gas rate and the manifold each well flows into. | Each well's decline on its own product, whether a test can be trusted, what holds it back (choke or back pressure), its lift response and flowline drift, with the measurements it could not read. | Advises; a choke, lift gas or intervention change is the accountable production engineer's call. |
| `field-operations-facilities/troubleshooting` | Field Operations & Facilities | none | Field Operator; Surface / Facilities Engineer | Method for troubleshooting a unit or incident (a trip, leak, high vibration, carryover, hydrate) - what happened, what the process values did, similar past incidents, lessons and manual sections, and the checks to make. | `alarm_analysis`, `calc`, `detect_changes`, `gas_lift_network`, `get_entity`, `get_events`, `get_incidents`, `get_upsets`, `instrument_health`, `limits`, `maintenance_history`, `measurements`, `plot_changes`, `plot_choke`, `plot_instrument_health`, `plot_pump_curve`, `plot_state_timeline`, `process_values`, `search`, `spare_parts`, `trace_flow`, `trip_analysis`, `troubleshooting_brief`, `upset_detail`, `what_changed` | Incidents, events, process values, limits, past failures, lessons and manuals. | What happened, what the values did, similar past cases, and checks to make. | Recommend checks and decisions to the accountable operator or engineer; never give operating instructions. |
| `field-operations-facilities/upset-correlation` | Field Operations & Facilities | none | Field Operator; Surface / Facilities Engineer | Method for upsets that belong together - correlated events and cascades across units - so the IOC triages one event from where it started, merges duplicate cases and sends one operator; and for the cascades that keep recurring. | `alarm_analysis`, `cascade_patterns`, `plot_propagation`, `plot_state_timeline`, `related_upsets`, `start_study`, `trace_flow`, `trip_analysis`, `upset_detail` | SRE upsets and their detections, the plant model's links, unit run states and alarm history. | One event told first cause first, the knock-ons with their lags, upsets to merge and recurring cascades. | Recommends merges and where to send one operator; the IOC merges cases and dispatches. Recommend checks and decisions to the accountable operator or engineer; never give operating instructions. |
| `production-surveillance/metering-and-balances` | Production & Surveillance | none | Production / Operations Engineer; Production Surveillance | Metering and balances - a meter's allocation factor against the well tests upstream of it, and its agreement with the other meter on its stream (custody against check meter), for any flow meter on the allocation network (ISO 14224 class IP). | `calc`, `field_allocation_factor`, `measurements`, `plot_field_allocation_factor`, `plot_trend`, `test_staleness`, `trace_flow` | Meter flow points, the allocation network around each meter, the well tests upstream and the other meters on the same stream. | Each meter's allocation factor against the tests upstream and its agreement with the check meter on its stream, which meter is suspect and why, and what could not be checked (proving records, composition). | Advises; proving, recalibration, a meter factor change or a correction to allocated volumes is the measurement engineer's call. |
| `field-operations-facilities/gas-treating` | Field Operations & Facilities | none | Surface / Facilities Engineer; Field Operator | Gas treating and dehydration - amine and glycol contactors foaming or fouling, treated gas (acid gas, water content) against its window, reboiler temperatures drifting toward a limit, and refrigeration chillers holding the dew point, for any treating unit (ISO 14224 classes VE, HE, HB). | `calc`, `limits`, `measurements`, `plot_operating_window`, `plot_trend` | Contactor differential pressure, treated gas analysers, reboiler and chiller temperatures, and their recorded operating windows (IOW, alarm settings or learned). | Each treating unit's status with the checks that drive it (foaming, treated gas, reboiler, chiller), the window each was judged against, the link along the train and the lab samples it could not read. | Advises; antifoam, a solvent change-out, a rate cut or a reboiler setpoint change is the operator's and engineer's call. |
| `field-operations-facilities/shift-handover` | Field Operations & Facilities | none | Field Operator; Surface / Facilities Engineer | Interpret a shift or period for handover - what changed in the records, what is still open, which alarms and excursions matter, and what to verify in the field. | `alarm_analysis`, `get_daily_ops_report`, `get_events`, `limits`, `records_mismatch`, `trip_analysis`, `what_changed` | Operator log events, notifications, downtime, the daily report and excursions for a stated window. | A dated handover of what changed, what is unresolved, which records don't agree and what to verify in the field. | Summarises records; the accountable shift supervisor owns the handover. |
| `field-operations-facilities/alarms-and-instruments` | Field Operations & Facilities | none | Instrumentation, Controls & OT Specialist; Field Operator | Alarm performance, instrument evidence and control loops - alarm rates, floods, chattering and standing alarms, whether readings can be trusted, whether a loop is cycling, signal changes, and how close a unit runs to its limits. | `alarm_analysis`, `calc`, `capacity_headroom`, `detect_changes`, `instrument_health`, `limits`, `plot_alarms`, `plot_changes`, `plot_instrument_health`, `plot_operating_window` | DCS / PI AF limits, process values and run signals; requires a configured time-series source. | Alarm performance, whether readings can be trusted and when a signal changed, with the window. | No control, tuning or alarm changes; no validated operating envelope is implied. |
| `maintenance-integrity/reliability` | Maintenance & Integrity | none | Reliability Engineer; Maintenance Technician / Coordinator; Inspection Specialist | Reliability, maintenance and inspection - rotating equipment health (vibration zones, BEP, curves, failure fits), bad actors, MTBF by unit and ISO 14224 class, failure modes and repeat failures, PM and inspection compliance, overdue statutory inspections, and root-cause leads. | `bad_actors`, `calc`, `cost_of_unreliability`, `get_entity`, `lessons_learned`, `limits`, `maintenance_compliance`, `maintenance_history`, `maintenance_spend`, `measurements`, `plot_compressor_performance`, `plot_cooler_fouling`, `plot_peers`, `plot_reliability`, `rotating_screen`, `search_knowledge`, `spare_parts`, `well_performance` | SAP notifications and work orders with ISO 14224 coding, maintenance plans and downtime. | Bad actors, MTBF, failure modes, compliance and root-cause leads with data dates. | Root causes stay leads until an accountable engineer confirms them. |
| `maintenance-integrity/compression` | Maintenance & Integrity | none | Reliability Engineer; Maintenance Technician / Coordinator | Compressor health - centrifugal surge margin and surges, reciprocating rod load and valves, API 670 protection channels, dry gas seals, lube oil, and real-gas and fixed-k performance, for any compressor (ISO 14224 class CO). | `calc`, `plot_compressor_performance`, `plot_cooler_fouling`, `plot_vibration`, `rotating_screen` | Compressor process and protection points (flows, pressures, temperatures, vibration, axial position, seal vent, lube oil), the machine's OEM map, cylinder data and frame rating, and the latest gas analysis. | Each compressor's status with the checks that drive it (surge margin, rod load, valves, protection channels, seals, lube oil, performance), their basis and data dates. | Advises; stopping, unloading or re-staging a machine is the operator's and machinery engineer's call. |
| `maintenance-integrity/pumps-and-drivers` | Maintenance & Integrity | none | Reliability Engineer; Maintenance Technician / Coordinator | Pumps and their drivers - head against the OEM curve, where a pump runs against its best efficiency point, motor load against the nameplate, casing vibration, and gas turbine and gas engine driver checks (exhaust spread, hot section hours, cylinders, heat rate), for any pump or driver (ISO 14224 classes PU, EM, GT, CE). | `calc`, `equipment_curves`, `plot_pump_curve`, `plot_vibration`, `rotating_screen` | Pump flow, pressures and vibration, the OEM curve, the motor's nameplate on the SAP equipment record, and gas turbine and gas engine driver points (exhaust and cylinder temperatures, fuel, output). | Each pump or driver's status with the checks that drive it (curve, operating region, motor load, vibration, exhaust spread, hot section hours, cylinders, heat rate), their basis and data dates. | Advises; stopping a pump, changing a duty or deferring an inspection is the operator's and engineer's call. |
| `maintenance-integrity/static-equipment` | Maintenance & Integrity | none | Asset Integrity Engineer; Inspection Specialist | Static equipment and integrity - wall loss and remaining life at CMLs against each CML's minimum required thickness, the condition inspection plan against the interval the wall supports, and exchanger fouling, for any pressure vessel, exchanger or piping (ISO 14224 classes VE, HE, PI). | `calc`, `limits`, `measurements`, `plot_cooler_fouling`, `plot_wall_thickness` | CML wall thickness readings and each CML's recorded minimum thickness, the maintenance plans' condition inspections, exchanger outlet temperatures and integrity operating windows. | Each vessel, exchanger or line's remaining life at its governing CML, its inspection plan against the interval its wall supports, fouling, and what could not be checked (RBI, fitness for service). | Advises; inspection intervals, deferrals and fitness for service are the integrity engineer's decisions. |
| `maintenance-integrity/integrity` | Maintenance & Integrity | none | Asset Integrity Engineer; Inspection Specialist | Asset integrity and inspection evidence - wall loss and remaining life at CMLs, overdue statutory and internal inspections, integrity operating window excursions, and risk ranking of what to review first. | `calc`, `limits`, `maintenance_compliance`, `measurements`, `plot_compliance`, `plot_operating_window`, `plot_wall_thickness`, `value_score` | Wall thickness at CMLs, inspection plans, integrity operating windows and excursions. | Wall loss and remaining life ranked by item, overdue inspections and IOW excursions, with coverage gaps. | Fitness for service, integrity acceptance and inspection deferral are accountable human decisions. |
| `work-management-supply-chain/work-orders` | Work Management & Supply Chain | Maintenance & Integrity | Work Planner; Work Scheduler; Maintenance Technician / Coordinator | Work order management - the open backlog by priority, type and age, overdue orders, notifications with no order, and how to prioritise the backlog against production risk. | `backlog_burn`, `backlog_health`, `bad_actors`, `deferment_pareto`, `maintenance_compliance`, `maintenance_history`, `plot_backlog_burn`, `work_order_backlog` | SAP work orders, notifications and maintenance plans. | The open backlog by priority, type and age, what is overdue, and a suggested order with reasons. | Findings inform people who approve priorities, schedules and commitments; nothing here commits anything. |
| `work-management-supply-chain/turnarounds` | Work Management & Supply Chain | Maintenance & Integrity | Work Planner; Work Scheduler; Supply Chain Specialist | Turnaround (TAR / shutdown) scope - work planned for the revision, shutdown-only inspections and PM due before or around it, bad actors that should be added, long-lead spares to order, and scope gaps. | `deferment_pareto`, `maintenance_history`, `turnaround_scope` | SAP work orders with a revision, shutdown-only plans, bad actors and stock. | Turnaround scope, what is missing from it and out-of-stock spares for scoped units. | Findings inform people who approve priorities, schedules and commitments; nothing here commits anything. |
| `work-management-supply-chain/job-readiness` | Work Management & Supply Chain | none | Work Planner; Work Scheduler; Supply Chain Specialist; Logistics Coordinator | Job and turnaround readiness from recorded evidence - scope, dates, spares and stock per job, and what is missing or unknown about materials, resources, schedule conflicts and logistics. | `bad_actors`, `maintenance_compliance`, `maintenance_history`, `plot_backlog`, `spare_parts`, `turnaround_scope`, `value_score`, `work_order_backlog` | Work orders, their dates and turnaround revision, and BOM spares with stock on hand. | Per job, what is recorded as ready and what is missing or unknown (materials, dates, resources, logistics). | A stock count is not a reservation; crew, vendor, transport and access status stay unknown. Findings inform people who approve priorities, schedules and commitments; nothing here commits anything. |
| `hse-incident-management/incident-evidence` | HSE & Incident Management | none | HSE Specialist; Environmental / Regulatory Specialist; Incident Investigator; Emergency Response Lead | Organize recorded incident, event and corrective-work evidence for accountable HSE review - timelines, investigation records, corrective-action status, past lessons and recorded flare - without making safety or compliance decisions. | `alarm_analysis`, `compare_periods`, `downtime`, `get_events`, `get_incidents`, `get_upsets`, `incident_detail`, `incident_repeats`, `lessons_learned`, `maintenance_history`, `plot_flare`, `plot_incident`, `plot_incident_repeats`, `plot_propagation`, `plot_state_timeline`, `record_history`, `similar_cases`, `trip_analysis`, `upset_detail` | Incidents, events, upsets, linked work and lessons; flare from EC DAY001. No HSE system of record. | Source-linked timelines, corrective-work status and evidence gaps for accountable review. | No compliance finding, barrier assurance or emergency advice; escalates to accountable people. |
| `asset-performance-reporting/operations-summary` | Asset Performance & Reporting | none | Asset Manager; Operations Reporter; Commercial / Production Accounting Specialist | Produce cited cross-domain operational summaries from the linked production and work records Fuzer actually has. | `accounting_reconciliation`, `bad_actors`, `compare_periods`, `deferment_pareto`, `get_daily_ops_report`, `kpi_scorecard`, `maintenance_compliance`, `opportunities`, `plot_accounting_reconciliation`, `plot_backlog`, `plot_bad_actors`, `plot_compliance`, `plot_kpi_scorecard`, `plot_loss_waterfall`, `plot_score_breakdown`, `production_constraints`, `production_summary`, `turnaround_scope`, `unit_cost`, `value_score`, `well_performance`, `work_order_backlog` | The cited daily report plus production, deferment, constraints, reliability and work summaries. | Dated cross-domain KPIs, trade-offs and opportunities with units, windows and unknowns. | Internal evidence only: not an approved decision, an external report or accounting reconciliation. |
| `shared/charts` | Shared | every agent | all | When and how to put interactive charts in an answer - which plot tool fits which question, how to read and cite a chart's summary, and when (rarely) to look at the image. | `look_at_figure`, `plot_backlog`, `plot_bad_actors`, `plot_bar`, `plot_changes`, `plot_choke`, `plot_compliance`, `plot_correlation`, `plot_custom`, `plot_decline`, `plot_distribution`, `plot_gas_lift_allocation`, `plot_gas_lift_curve`, `plot_incident`, `plot_instrument_health`, `plot_measurements`, `plot_pump_curve`, `plot_reliability`, `plot_trend`, `plot_vibration`, `plot_wall_thickness`, `plot_water_diagnostic`, `plot_xy` | Whichever records each plot tool reads. | Interactive figures with their markers and a sentence on what each shows. | Says when a chart rests on synthetic data; an agent calls only the plot tools in its own list. |
| `shared/analysis` | Shared | every agent | Data Scientist / Operations Research Analyst | Reusable analysis methods - comparing periods, detecting changes, correlation, clustering and kept studies - with the assumptions, data sufficiency and uncertainty to state with each result. | `cluster_entities`, `cluster_events`, `cluster_modes`, `compare_periods`, `correlate`, `correlation_matrix`, `detect_changes`, `get_study`, `plot_correlation`, `start_study`, `trace_flow` | PI process values, detected upsets and well tests; results depend on the time-series source. | Correlations, clusters and period comparisons with assumptions, sample size and uncertainty. | Correlation is not cause. Findings on synthetic values show how the generator works, not plant physics. |

## Specialist packs

A pack is a skill folder whose `pack.yaml` names what its playbook relies on (P4.1, `agents/packs.py`); each pack passes the pack conformance test. Each shipped bundle holds its answer keys in `evals/<pack>.yaml`.

### Control loops and alarms

- **Skill:** `field-operations-facilities/alarms-and-instruments`, loaded by Field Operations & Facilities
- **Equipment classes (ISO 14224):** IP, VA, CL
- **Methods:** `alarm_performance` (ANSI/ISA-18.2 and EEMUA 191), `instrument_health` (Engineering practice), `loop_oscillation` (Engineering practice)
- **Detectors:** `instruments`, `excursions`, `steps`
- **Briefs:** none
- **Measurement roles:** `pressure`, `differential_pressure`, `temperature`, `flow`, `level`, `valve_position`, `composition`
- **Charts:** `plot_instrument_health`, `plot_alarms`, `plot_changes`, `plot_operating_window`
- **Eval:** 7 questions with answer keys across 3 bundles

### Gas treating and dehydration

- **Skill:** `field-operations-facilities/gas-treating`, loaded by Field Operations & Facilities
- **Equipment classes (ISO 14224):** VE, HE, HB
- **Methods:** `chiller` (Engineering practice), `contactor_foaming` (Engineering practice), `regenerator` (Engineering practice), `treated_gas` (Engineering practice)
- **Detectors:** `drifts`, `iow_breaches`
- **Briefs:** none
- **Measurement roles:** `differential_pressure`, `composition`, `temperature`, `outlet_temperature`, `vibration`, `bearing_temperature`, `discharge_temperature`
- **Charts:** `plot_operating_window`, `plot_trend`
- **Eval:** 5 questions with answer keys across 3 bundles

### Compression

- **Skill:** `maintenance-integrity/compression`, loaded by Maintenance & Integrity
- **Equipment classes (ISO 14224):** CO
- **Methods:** `compressor_performance` (Engineering practice), `cooler_fouling` (Engineering practice), `gas_performance` (ASME PTC 10 (Schultz polytropic analysis); ISO 5389), `lube_oil` (API 614), `power_balance` (ASME PTC 10), `protection` (API 670), `rod_load` (API 618), `rotating_health` (ISO 14224 (rotating classes); each check that applies cites its own standard), `seal_gas` (API 692), `surge` (Engineering practice), `surge_events` (Engineering practice), `valve_health` (API 618)
- **Detectors:** `machine_health`, `compressor_trips`
- **Briefs:** `machine_health`, `machine_trip`
- **Measurement roles:** `running_status`, `speed`, `suction_pressure`, `interstage_pressure`, `discharge_pressure`, `suction_temperature`, `discharge_temperature`, `cylinder_discharge_temperature`, `suction_flow`, `anti_surge_valve_position`, `vibration`, `shaft_vibration`, `axial_position`, `bearing_temperature`, `seal_vent_flow`, `lube_oil_pressure`, `lube_oil_temperature`
- **Charts:** `plot_compressor_performance`, `plot_cooler_fouling`, `plot_vibration`
- **Eval:** 6 questions with answer keys across 3 bundles

### Pumps and drivers

- **Skill:** `maintenance-integrity/pumps-and-drivers`, loaded by Maintenance & Integrity
- **Equipment classes (ISO 14224):** PU, EM, GT, CE
- **Methods:** `engine_cylinders` (Engineering practice), `exhaust_spread` (Engineering practice), `heat_rate` (Engineering practice), `hot_section` (Engineering practice), `motor_load` (Engineering practice), `pump_bep` (API 610 (preferred and allowable operating regions)), `pump_curve` (Engineering practice), `rotating_health` (ISO 14224 (rotating classes); each check that applies cites its own standard), `vibration` (ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors)
- **Detectors:** `machine_health`, `models`
- **Briefs:** `machine_health`
- **Measurement roles:** `running_status`, `speed`, `power`, `flow`, `suction_flow`, `suction_pressure`, `discharge_pressure`, `vibration`, `bearing_temperature`, `exhaust_temperature`, `fuel_flow`, `ambient_temperature`
- **Charts:** `plot_pump_curve`, `plot_vibration`
- **Eval:** 7 questions with answer keys across 3 bundles

### Reliability and maintenance

- **Skill:** `maintenance-integrity/reliability`, loaded by Maintenance & Integrity
- **Equipment classes (ISO 14224):** CO, PU, EM, GT, CE, VE, HE, PI, VA
- **Methods:** `compressor_performance` (Engineering practice), `cooler_fouling` (Engineering practice), `corrosion_circuit` (API 570 7.1), `peers` (Engineering practice), `pm_interval` (IEC 61649 (Weibull analysis); IEC 60300-3-11 (reliability centred maintenance)), `reliability` (IEC 61649 (Weibull analysis); IEC 61164 (reliability growth, power law)), `remaining_life` (ISO 13381-1), `rotating_health` (ISO 14224 (rotating classes); each check that applies cites its own standard), `spares_coverage` (ISO 14224 (equipment and failure data); ISO 55000 (criticality-based asset management)), `vibration` (ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors), `wall_loss` (API 570 / API 510)
- **Detectors:** `machine_health`
- **Briefs:** none
- **Measurement roles:** `running_status`, `vibration`, `bearing_temperature`, `suction_pressure`, `suction_flow`, `discharge_pressure`, `discharge_temperature`, `outlet_temperature`
- **Charts:** `plot_compressor_performance`, `plot_cooler_fouling`, `plot_peers`, `plot_reliability`
- **Eval:** 7 questions with answer keys across 3 bundles

### Static equipment and integrity

- **Skill:** `maintenance-integrity/static-equipment`, loaded by Maintenance & Integrity
- **Equipment classes (ISO 14224):** VE, HE, PI
- **Methods:** `cooler_fouling` (Engineering practice), `corrosion_circuit` (API 570 7.1), `inspection_plan` (API 510 / API 570), `wall_loss` (API 570 / API 510)
- **Detectors:** `drifts`, `iow_breaches`
- **Briefs:** none
- **Measurement roles:** `outlet_temperature`, `temperature`, `pressure`, `differential_pressure`, `vibration`, `bearing_temperature`, `discharge_temperature`
- **Charts:** `plot_cooler_fouling`, `plot_wall_thickness`
- **Eval:** 5 questions with answer keys across 3 bundles

### Metering and balances

- **Skill:** `production-surveillance/metering-and-balances`, loaded by Production & Surveillance
- **Equipment classes (ISO 14224):** IP
- **Methods:** `allocation_factor` (Engineering practice), `meter_agreement` (Engineering practice)
- **Detectors:** `instruments`
- **Briefs:** none
- **Measurement roles:** `flow`
- **Charts:** `plot_field_allocation_factor`, `plot_trend`
- **Eval:** 6 questions with answer keys across 3 bundles

### Wells and artificial lift

- **Skill:** `wells-subsurface/wells-and-lift`, loaded by Wells & Subsurface
- **Equipment classes (ISO 14224):** well
- **Methods:** `choke` (Engineering practice), `decline` (Arps, Analysis of Decline Curves (1945)), `dp_drift` (Engineering practice), `gas_lift` (Engineering practice), `test_check` (Engineering practice), `well_constraint` (Engineering practice)
- **Detectors:** `models`, `hydraulics`, `opportunities`
- **Briefs:** none
- **Measurement roles:** `wellhead_pressure`, `wellhead_temperature`, `casing_pressure`, `choke_position`, `gas_lift_rate`, `pressure`, `flow`
- **Charts:** `plot_decline`, `plot_water_diagnostic`, `plot_gas_lift_curve`, `plot_choke`, `plot_dp_drift`, `plot_intervention_gains`
- **Eval:** 5 questions with answer keys across 3 bundles

## Not available

No agent can do these yet, whatever the question. A specialist asked for one says it is unavailable.

| Agent | Capability | Waiting for |
|---|---|---|
| Wells & Subsurface | Reservoir pressure and model evidence; completion design | An authoritative subsurface or well-file source. |
| Field Operations & Facilities | Validated operating envelopes | Envelopes validated by the control-system owner; alarm limits and IOWs are not that. |
| Field Operations & Facilities | Control loop performance and tuning | Setpoint, controller output and mode points, with loop parameters. |
| Maintenance & Integrity | Vibration spectra (bearing defect frequencies, 1x / 2x running speed) | A condition-monitoring source with spectra or waveforms and bearing geometry; overall vibration is checked. |
| Maintenance & Integrity | Curve and map comparison for machines without OEM data | OEM pump curves for those models and compressor maps for those machines (a datasheet Claude reads counts). |
| Work Management & Supply Chain | Schedule-conflict and crew resource analysis | A planning system with crews, calendars and resource loading. |
| Work Management & Supply Chain | Vendor, transport and site-access constraints; material reservations | Procurement and logistics sources; SAP stock on hand alone does not show them. |
| HSE & Incident Management | Hazard and barrier status | An authoritative barrier or safety-case register. |
| HSE & Incident Management | Environmental and regulatory obligation tracking | Permits and obligations from an authoritative register. |
| HSE & Incident Management | Emergency response procedures | Approved emergency response plans; the assistant only supports with information. |
| Asset Performance & Reporting | Commercial reconciliation: sales, nominations, liftings and entitlements | The commercial system's sales and nomination records; production accounting volumes and the cost ledger are reconciled (accounting_reconciliation, unit_cost). |

## Tool coverage

| Agent | Tools |
|---|---|
| Orchestrator | 23 |
| Production & Surveillance | 78 |
| Wells & Subsurface | 63 |
| Field Operations & Facilities | 83 |
| Maintenance & Integrity | 73 |
| Work Management & Supply Chain | 34 |
| HSE & Incident Management | 41 |
| Asset Performance & Reporting | 50 |

## Shared (117)

Used by every agent or by several specialists.

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `get_ontology` | query |  | The data model: entity kinds with what each is, counts, fields and feeding sources; which link types connect which kinds (with counts and sources); and what each link type means. |
| `search` | query | query, kind?, limit? | Find entities by tag, well, PI point, serial number, source key or description text. |
| `get_entity` | query | ref, as_of? | Everything Fuzer knows about one entity: chosen values (with source and any disagreeing sources), the source records merged into it, and its links grouped by relationship. |
| `trace_flow` | query | ref, direction?, as_of?, max_depth?, network? | Follow a flow network from an entity. |
| `equipment_history` | query | tag | Physical equipment installed at a tag over time (serial, manufacturer, model, install dates). |
| `get_events` | query | ref?, since?, until?, event_type?, limit? | What happened, from the operator logs: trips, starts, stops, changeovers, shut-ins, choke changes, alarms, leaks, maintenance, instrument faults. |
| `maintenance_history` | query | ref, since?, limit? | SAP notifications and work orders for a tag, a piece of equipment, every instance of a model, or a whole class (e.g. |
| `downtime` | query | ref?, since?, until?, rank_by?, limit? | Production downtime and deferment from Energy Components, with lost oil (Sm3) and hours. |
| `get_incidents` | query | ref?, since?, until?, limit? | Incidents: logged events, downtime and SAP notifications on one piece of plant within a few days of each other, grouped into one upset with lost oil, failure modes and event types. |
| `spare_parts` | query | ref | Spare parts for a tag, piece of equipment or equipment model: SAP material, manufacturer part number, stock on hand (0 = none in stock), storage bin, how many units use it, and manual sections naming it. |
| `link_text` | query | text | Find tags, wells and known plant nicknames ("test sep") in free text and link them to entities. |
| `recall` | query | ref, include_ended? | What the assistant learned in earlier conversations about an entity and the units it is part of: short dated facts from answers engineers rated useful (or statements they marked to remember). |
| `bad_actors` | query | since?, until?, limit? | Equipment ranked by failures (SAP notifications), downtime caused and repeat ISO 14224 failure modes, with MTBF per unit and per class. |
| `work_order_backlog` | query | as_of?, limit? | Open SAP work orders by priority, type and age; overdue orders (past basic finish); the oldest; open notifications with no order planned yet. |
| `backlog_burn` | calc | since?, until?, days? | Is the backlog under control (#348)? Per month: work orders created (by start) against closed (by finish) and the orders open at the month's end, with whether the backlog is growing, steady or shrinking; then the days from a notification to the order raised for it (median, 90th percentile, longest). |
| `plot_backlog_burn` | plot | since?, until?, days? | Chart work orders created and closed each month, with the open backlog at each month's end. |
| `rotating_screen` | query | at?, limit? | Every rotating machine (pumps, compressors, gas turbines, motors) through the rotating health checks at one date, worst first: `act` (a severe flag: vibration zone D, head loss, accelerating failures, low-flow running), then `watch`. |
| `backlog_health` | query | as_of?, limit? | Backlog health: ageing of open corrective (PM01) orders (flag when over 25 % are older than 90 days), priority 1-2 orders past basic finish, unplanned notifications on bad actors, and the open orders ranked by value / risk in money (production at risk, criticality, compliance, effort). |
| `maintenance_compliance` | query | as_of?, horizon_days?, limit? | Preventive maintenance (PM) and inspection (INSP) plans: on-time share, overdue tasks and tasks due within the horizon; tasks that need a shutdown are marked. |
| `turnaround_scope` | query | revision?, as_of? | Turnaround (shutdown) scope: work orders planned for the revision (default: the next one), plans that need the unit down and aren't in scope yet, bad actors not in scope, spares for scoped units that are out of stock (order long-lead items now), and the planning schedule's activities for the revision when one is loaded: what is late (finish passed, not done) or slipped past its baseline, as of `as_of` (default: the data's latest day). |
| `value_score` | calc | ref?, kinds?, by?, as_of?, limit? | The shared value / risk score: upsets, open work orders, overdue or due maintenance plans and gas lift opportunities ranked in money over the pack's horizon (by value, or by value per effort). |
| `plot_bad_actors` | plot | since?, until? | Chart bad actors as bubbles: failures against oil lost to the downtime they caused, size = downtime events, colour = ISO 14224 class. |
| `plot_backlog` | plot | as_of? | Chart open SAP work orders by priority and age, with overdue counts and notifications with no order in the summary. |
| `plot_compliance` | plot | as_of?, horizon_days? | Chart PM and inspection plans by kind: overdue, due within the horizon and later, with the worst overdue plans in the summary. |
| `plot_score_breakdown` | plot | ref | Chart what makes up an item's value score (#100): production, risk and compliance at stake, urgency and effort, to show why one card ranks above another. |
| `maintenance_spend` | calc | by?, since?, until?, days? | Work order cost (#335) by unit, ISO 14224 class or order type for a window (default the last year), totalled per currency and never across them; an order whose source states no currency is in `unknown`. |
| `cost_of_unreliability` | calc | since?, until?, days?, limit? | The cost of unreliability by unit (#352): oil lost to the downtime each unit caused, at the pack's oil price and currency, beside its corrective orders' cost in their own currency, ranked. |
| `plot_maintenance_spend` | plot | by?, since?, until?, days? | Chart work order cost ranked by unit, class or order type, one series per currency (unknown labelled). |
| `plot_cost_of_unreliability` | plot | since?, until?, days? | Chart each unit's lost oil value beside its corrective order cost, each with its currency. |
| `list_metrics` | query |  | Every registered KPI: key, label, definition, formula, unit, standard, the grains it can be cut by and the records behind it. |
| `get_metric` | query | key, grain?, at? | One registered KPI now: its value, with its definition and unit, and by a grain (one of the metric's grains: queue, type, area, class, facility, system, dataset) its rows and the cases or records behind each. |
| `board` | query | area?, unit?, kind?, text?, since?, limit? | The work board (#99): outstanding work across SAP backlog, PMs and inspections, incidents and SRE upsets, workflow items, opportunities, reviews and projects, one card per piece of work, ranked by value (#100). |
| `get_daily_ops_report` | query | day?, version? | The daily operations report for a production day (YYYY-MM-DD; default the latest): headline, production and deferment, upsets and events, wells, maintenance and integrity, look-ahead, every line citing its records. |
| `what_changed` | query | since, until?, limit? | What happened in the records between two times (plant time): new log events, notifications, work orders started and finished, downtime started and ended, incidents, equipment installed or removed, limits taking or ending effect, excursions and new well tests, with counts. |
| `records_mismatch` | calc | since?, until? | Records that don't agree: downtime records with no operator log entry (or, for equipment reasons, no notification) about the same plant within the tolerances, and log entries that say something stopped with no downtime record. |
| `deferment_pareto` | query | by?, since?, until?, limit? | Deferred (lost) oil ranked by downtime reason, reason category, causing tag, its ISO 14224 class, or affected well, with each one's share. |
| `well_performance` | query | ref?, since?, limit? | Well tests. |
| `gas_lift_network` | query | ref?, since?, limit? | The gas lift network: compression -> lift gas headers -> jacket gas lift manifolds (some through another jacket) -> gas lifted wells. |
| `kpi_scorecard` | calc | until?, days? | How the plant is running (#353): production efficiency, deferred oil, flare, corrective backlog, preventive work on time, failures and repeat failures for the `days` ending on `until` (YYYY-MM-DD; default the latest daily report) against the same length before. |
| `accounting_reconciliation` | calc | since?, until?, days? | Operational against accounting volumes (#372): each month's daily allocated oil and gas (EC DAY001) summed over the accounting period, against what production accounting closed on the custody meters. |
| `plot_trend` | plot | refs, at?, hours_before?, hours_after? | Chart process values (PI / ADX) on stacked panels sharing time, with incident lead-ins shaded. |
| `plot_measurements` | plot | ref, quantity?, since?, until? | Chart landed measurements over time on a well, tag, equipment or pipeline (well tests, lab results, wall thickness): a panel per quantity, a line per location. |
| `plot_xy` | plot | x, y, at?, hours_before?, hours_after? | Chart one PI point against another at the same times (operating envelope), coloured by time, with a straight-line fit and r. |
| `plot_bar` | plot | dataset, since?, until?, limit? | Chart a ranking as bars: deferred oil by reason / well / tag / class (Pareto with the cumulative share), failures by unit, or oil rate change by well. |
| `plot_distribution` | plot | ref, split_at, hours_before?, hours_after? | Chart one PI point before and after a moment (a repair, a setpoint change) as two boxes, with medians and spread in the summary. |
| `plot_custom` | plot | title, traces, x_title?, y_title? | Chart numbers you worked out yourself, when no other chart covers them: up to 8 traces [{name, x, y, kind: line | markers | bar}], 500 points in all. |
| `plot_vibration` | plot | ref, at?, days? | Chart a machine's overall vibration against ISO 20816 zones A-D, with temperature on a second axis and incident lead-ins shaded. |
| `plot_pump_curve` | plot | ref, days? | Chart a pump against its OEM curve: operating points from PI (head from discharge minus suction pressure, flow) coloured by time over the head and efficiency curves, with the BEP and preferred operating region. |
| `plot_choke` | plot | ref, days? | Chart a well's production choke against its OEM characteristic: effective Cv at each well test (oil wells) or from PI rates (gas wells) against the opening. |
| `plot_pressure_profile` | plot | well, at?, ref_at? | Chart pressure at each node down a well's flow path (wellhead, jacket header, separator, compressor suction), today and against a reference date: where back-pressure builds. |
| `plot_dp_drift` | plot | well, days? | Chart a well's wellhead-to-header pressure drop over time against its baseline band, with any rise marked (a flowline waxing or scaling). |
| `plot_compressor_performance` | plot | ref, days? | Chart a compressor stage's daily polytropic efficiency against its first week (fouling, wear, hot recycle). |
| `plot_cooler_fouling` | plot | ref, days? | Chart a cooler's daily outlet temperature against its clean baseline and its high limit. |
| `look_at_figure` | query | figure | See a chart you made, as an image. |
| `backpressure_sensitivity` | calc | scope?, at? | Oil gained per bar less back-pressure, for each back-pressure-limited well in scope (a well, a jacket area or "facility") and summed per header: from the inflow and choke equations, and from the well's tests against the header pressure at the time. |
| `debottleneck_options` | calc | scope?, at?, limit? | Ranked actions against back-pressure: lower a separator to its next limit, pig a flowline whose pressure drop is rising, reroute a well to a lower header, open a throttled choke. |
| `capacity_headroom` | calc | ref, at? | How close each point on a unit (compressor, separator, pump) runs to its nearest limit (alarm, IOW or learned range): what a debottleneck would run into next. |
| `opportunities` | query | status?, limit? | Gains left on the table from the daily opportunity scan (#102): lift gas to reallocate between wells, wells below their decline or with stale tests, units worth a permanent fix, shut-in wells that may be recoverable. |
| `cluster_entities` | calc | kind?, scope?, since?, until? | Peer groups: wells by decline, water cut and lift gas response; or the points in a scope by how they move together (redundant points, mislabelled tags, natural sub-units; needs scope, since, until). |
| `production_summary` | query | since?, until?, by? | Oil actual vs potential (production efficiency %), gas export, lift gas and flare per month or week, with deferred oil by reason. |
| `production_constraints` | query | since?, until? | What has been limiting production: losses by reason with the units that cause them, frequency, last occurrence and trend (worse / steady / better). |
| `plot_decline` | plot | well, model?, forecast_months? | Chart a well's oil rate decline from its tests (log scale) with an Arps fit after the last step change (valve failure, breakthrough, repair), and a forecast. |
| `plot_water_diagnostic` | plot | well | Chart a well's water diagnostic: water cut against cumulative oil, and WOR and its derivative on log scales (Chan). |
| `plot_gas_lift_curve` | plot | well, breakeven? | Chart a gas lifted well's performance curve: oil against lift gas from its tests (decline taken out), the current point, the marginal response there, and the optimum where one more kSm3/d of gas gains `breakeven` Sm3/d of oil. |
| `gas_lift_stability` | calc | well?, days? | Gas lift casing heading and instability (#341). |
| `plot_gas_lift_heading` | plot | well, days? | Chart one gas lifted well's casing pressure, wellhead pressure and lift gas over the last `days`: a heading well's three cycle together. |
| `plot_gas_lift_screen` | plot | days? | Chart the gas lifted wells ranked by casing pressure swing over the last `days`, heading wells marked. |
| `plot_gas_lift_allocation` | plot | ref? | Chart every gas lifted well's marginal response (oil per extra lift gas) grouped by lift gas header, with suggested gas moves. |
| `plot_backpressure_sensitivity` | plot | scope?, at? | Chart oil per bar of back-pressure for each back-pressure-limited well: the model's figure and the tests' side by side. |
| `plot_choke_criticality` | plot | well, days? | Chart back over wellhead pressure through a well's choke over time against the critical ratio (0.55): under it the choke limits the well and back-pressure doesn't reach it. |
| `plot_debottleneck` | plot | at? | Chart the debottleneck options ranked by oil gained, coloured by kind. |
| `plot_loss_waterfall` | plot | since, until | Chart oil potential, planned losses, unplanned losses by reason and actual over a period, priced. |
| `plot_flare` | plot | since, until | Chart daily flare from DAY001 and its CO2 per barrel of oil (an estimate). |
| `forecast_vs_actual` | calc | well?, since?, until?, days? | Each producing well's own decline forecast (fitted before the window, 90 % range) against its tests in the window (default the last year), ranked by the gap, with a field line. |
| `plot_forecast_vs_actual` | plot | well?, since?, until?, days? | Chart a well's forecast band against its tests with its downtime marked; without a well, the wells furthest under their forecast. |
| `intervention_gains` | calc | well?, since?, until?, days? | Did the well work pay off (#340)? Each recorded intervention (a well-category downtime: slickline, scale squeeze, valve change, survey) with oil rate, water cut and GOR before and after. |
| `plot_intervention_gains` | plot | since?, until?, days? | Chart each intervention's oil gain net of decline, with its range; unknown ones are listed, not drawn. |
| `well_diagnostics` | calc | well? | Water breakthrough and gas screening (#339). |
| `test_staleness` | calc | max_days? | Every well's latest test and its age at the data's latest day; stale beyond `max_days` (default 90). |
| `plot_gor_trend` | plot | well | Chart one well's GOR over its tests, with its first and latest levels when it climbed. |
| `search_knowledge` | query | question, ref?, limit?, include_superseded?, failure_modes? | Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited passages (document, revision, page, section, snippet with [[matches]]). |
| `documents_for` | query | ref | Current documents that apply to a tag / equipment / model / class: its OEM manual (via model), class procedures and area troubleshooting guides, closest first, with why each applies. |
| `lessons_learned` | query | status?, limit? | Lessons learned per equipment model (or class) and ISO 14224 failure mode: the corrective work that has fixed it before, how often it held for 30 days, and the incidents behind it. |
| `measurements` | query | ref, quantity?, since?, until?, history? | Sparse measurements that aren't in PI, on a well, tag, equipment, pipeline or an incident's unit: well tests (oil_rate, water_cut, gor, lift_gas_rate, wellhead_pressure; gas wells: gas_rate, condensate_rate), daily meter readings from an onboarded measurement system (volume, energy, hhv), lab results at sample points (bsw, salt, density, oil_in_water, h2s, co2, water_dewpoint, chloride) and wall thickness at CMLs (wall_thickness, mm). |
| `equipment_curves` | calc | ref | The OEM curves (pump head / efficiency / NPSHr / power against flow, or choke Cv against opening) and ratings (BEP, operating regions, max Cv) for an equipment model, or the model of an equipment item or tag, and where they came from (vendor export or an accepted datasheet reading). |
| `source_health` | query | dataset? | How each source dataset is doing (#225): state (ok, stale, held, failed, checks failing, profile drift), when it last loaded against how often it should, failing checks, link coverage and open reviews of its source. |
| `why_value` | query | ref, field | Why an entity's field says what it says (#225): the rule that chose the value, and each source record that states one, with the mapping version that read it and the load that delivered it. |
| `process_values` | query | ref, at?, hours_before?, hours_after? | Process values (PI points on the unit and its instruments) around a time: per point min / max / mean / last and the provider used. |
| `limits` | query | ref, at? | Operating limits on a PI point, or on every point of a unit (tag, equipment, well): the alarm limits that count (hihi / hi / lo / lolo, from the DCS or PI AF), integrity operating windows (IOWs: class, low / high and the time allowed outside), the normal range learned from history while the unit runs (used as the limit, labelled learned, when no source gives one), each source's values, checks that fail (sources disagree, disabled, standing, out of order, implausible, missing) and the latest excursions with any work item opened for them. |
| `calc` | calc | check, ref, method?, model?, days?, breakeven?, min_required_mm?, until? | One diagnostic check as a number against what it should be: {value, expected, deviation, deviation_pct, flag, basis, sources}. |
| `detect_changes` | calc | ref, since?, until?, limit? | Where a PI point (or each point on a unit) stepped (a trip, a choke move), drifted (fouling, wear) or changed its jitter (a transmitter starting to hunt), with when it started, the value before and after, and a score; the `limit` strongest. |
| `pressure_profile` | calc | well, at? | Pressure at each node from a well's wellhead down its flow path (jacket production header, separator, compressor suction) and the drop across each segment: where the back-pressure comes from. |
| `alarm_analysis` | calc | since, until, scope? | Alarm performance (ISA-18.2 / EEMUA 191) over a window: alarms per hour against 6, floods (over 10 in 10 minutes), chattering and standing alarms, and the top bad-actor points. |
| `trip_analysis` | calc | since, until, scope? | Stops (trips), starts and failed starts per unit with a run signal over a window, with the days between stops and the share of time running. |
| `compare_periods` | calc | ref, before_since, before_until, after_since, after_until | Did the change work? Each point on a PI point or unit in two windows (before and after a choke move, a repair, a swap): medians, the change, and whether it is significant (Welch's t with autocorrelation taken into account). |
| `similar_cases` | calc | ref | Past incidents like this one (an incident, "upset:<id>", or a tag or well's latest incident): same unit or class, shared failure modes and event types, with the work orders done for each and whether the fix held (no incident on the unit within 90 days). |
| `start_study` | calc | kind, params, agent? | Keep a correlation or clustering study (params as for correlate / correlation_matrix / cluster_modes / cluster_entities / cluster_events / cascade_patterns, plus spe: scope, train_since, train_until, since, until). |
| `get_study` | calc | study_id | A kept study: its parameters, status, summary, figure and (when done) its top results. |
| `incident_detail` | query | ref | One incident's timeline (every event, downtime record and notification in it, in order), the most similar past incidents on the same unit or class (how those went is often the best clue), and suggested reading: manual / guide sections in scope of the unit that cover the incident's failure modes. |
| `record_history` | query | ref, limit? | How the source records behind an entity changed: which system, when, and each field's old and new value (e.g. |
| `get_upsets` | query | since?, status?, kind?, limit? | Upsets the SRE found in the process data (#96), newest first: detections close in time and connected in the plant model grouped into one, with the earliest as the likely first cause (unit, point and what it found), the units it reached and its status. |
| `upset_detail` | query | upset_id | One upset: its detections in time order (the first is the likely first cause), with evidence, and how many on each unit. |
| `related_upsets` | query | upset_id, hours? | Other upsets starting within `hours` (max 72) of this one, most likely related first: each with its lag in minutes (negative = started earlier), plant-model hops between their units (None = not connected), how often the two signatures came together in the last 90 days, and a role: likely_cause (earlier, connected), likely_knock_on (later, connected), same_time, seen_together (no link but history) or coincident. |
| `cascade_patterns` | calc | since?, until?, window_minutes?, min_support? | Recurring cascades across history: a finding on one unit (first unit and detector) followed within `window_minutes` by a finding on another linked unit, how many times (support), the share of times the first was followed (confidence), the median and range of lags and example upsets. |
| `correlate` | calc | target, scope?, since?, until?, max_lag_minutes?, path?, method?, top? | What moves with a PI point, and what moved first. |
| `correlation_matrix` | calc | scope, since?, until?, path?, method?, top? | Which points in a scope move together: the strongest pairs (r, and whether the plant model connects them) and the points ordered so related groups sit together. |
| `cluster_modes` | calc | scope, since, until, k? | Operating modes of a unit, train or list of points over a window: stopped (its run signals) and its running regimes (k-means on the points that carry them: fouling, a new setpoint), with each mode's share, the points that differ most, how often it moves between modes and the mode before each stop. |
| `cluster_events` | calc | since?, until? | SRE upsets grouped by signature (which kinds of unit raised which findings): recurring upset types. |
| `plot_incident` | plot | ref | Chart an incident on one page (an incident, or a unit's latest): the process values that moved most in the lead-in, stacked, with its log events, trips, notifications and downtime as markers. |
| `plot_correlation` | plot | ref, at?, hours_before?, hours_after?, upstream?, target? | Chart how the process values on a unit (and on what feeds it) move together, as a correlation heatmap. |
| `plot_changes` | plot | ref, since?, until? | Chart a point's or a unit's readings with the steps and jitter changes detect_changes finds marked and drifts shaded. |
| `plot_state_timeline` | plot | since, until, scope? | Chart each unit's state (running, startup, stopped, transition) as swimlanes over a window: an upset spreading through a train or the plant. |
| `plot_alarms` | plot | since, until, scope? | Chart alarms per 10 minutes over a window against the flood line, with floods, chattering, standing alarms and the bad actors in the summary. |
| `plot_propagation` | plot | upset_id | Chart an SRE upset on the plant model: units coloured by when each was first detected, plant model links between them, the likely first cause marked. |
| `plot_operating_window` | plot | ref, since, until | Chart a PI point against its limits (alarm, IOW, learned range) with the minutes outside per day. |
| `plot_modes` | plot | scope, since, until, k? | Chart a unit's operating modes over time as a coloured strip under its regime-carrying point. |
| `incident_repeats` | calc | since?, until?, days? | Repeat incidents and corrective work ageing (#350): per unit, its incidents in the window and the days between them, the ones that came back after linked corrective work was recorded closed, and every linked order with the age of what is still open. |

## Orchestrator (2)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `get_stats` | query |  | Counts of entities by kind, links, source records and open review items. |
| `draft_work_item` | query | title, role, summary, evidence, subjects? | Propose work for a person's queue. |

## Production & Surveillance (2)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `field_allocation_factor` | calc | since?, until?, days? | Well test against allocated oil, field-wide, month by month (#338): the oil the daily report books over what the producing wells offer at their latest test within 120 days, cut by each well's own downtime, with the factor ignoring downtime beside it and the median age of the tests used. |
| `plot_field_allocation_factor` | plot | since?, until?, days? | Chart allocated oil over the wells' tested offer each month, with the band and the age of the tests. |

## Field Operations & Facilities (5)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `equipment_changes` | query | since? | Equipment removals and installs (swaps) across the facility, newest first. |
| `troubleshooting_brief` | query | ref | Start here when troubleshooting. |
| `instrument_health` | calc | ref, since?, until? | Can the readings be trusted? On a PI point, or every point on a unit: frozen (no change while the unit runs), stuck (jitter collapsed), noisy (hunting: jitter several times its usual), out of range, and redundant transmitters (A / B) drifting apart. |
| `detections` | query | since?, detector?, limit? | What the SRE's scheduled scans found in the process data (#96), newest first: the point and unit, detector (excursions outside limits, unit stops, steps, drifts, bad instruments, equipment against its curve), what it found (key), start and end (null = still going) and evidence. |
| `plot_instrument_health` | plot | ref, since?, until? | Chart a point's or a unit's readings with the stretches instrument_health flags shaded (noisy, frozen or stuck, out of range, apart from its redundant twin). |

## Maintenance & Integrity (4)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `list_members` | query | ref, kind?, limit?, offset? | Everything in an equipment class (including its subtypes), e.g. |
| `plot_wall_thickness` | plot | ref?, kind?, min_required_mm? | Chart wall thickness: with ref, each CML on that vessel / piping / pipeline over time with loss rates (and retirement dates if min_required_mm is given); without ref, the items losing wall fastest across the asset (kind narrows to pipelines or tags). |
| `plot_reliability` | plot | ref, method? | Chart reliability from failure notifications on a unit or an equipment class: a Weibull plot of times between failures (beta, eta), or Crow-AMSAA cumulative failures (beta > 1: speeding up). |
| `plot_peers` | plot | ref | Chart the units of one equipment class (or a unit's class) by failures per year, outliers marked. |

## HSE & Incident Management (1)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `plot_incident_repeats` | plot | since?, until?, days? | Chart incidents per unit with repeats marked, beside the open linked corrective orders by age band. |

## Asset Performance & Reporting (3)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `plot_kpi_scorecard` | plot | until?, days? | Chart each KPI's change against the period before, coloured better or worse, unknown ones left as gaps. |
| `unit_cost` | calc | since?, until?, days? | Operating cost per accounted Sm3 of oil (#372): the cost ledger's booked cost by month and cost element over that month's closed accounted oil, each currency apart and never converted or summed. |
| `plot_accounting_reconciliation` | plot | since?, until?, days? | Chart each month's daily allocated oil against production accounting, in % of the accounted volume, with the 1 % tolerance; months that can't be compared are listed, not drawn. |

## MCP only (7)

Called by agents as themselves through the workflow engine.

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `next_work` | query | agent, limit? | The workflow steps your agent role can take now, highest on the board first. |
| `list_reviews` | query | kind?, limit? | Open questions waiting for a person: possible duplicate records ("match") and sources that disagree on a value ("conflict"). |
| `my_work` | query | agent, limit? | Your open work as agent role `agent`: agent steps waiting for your role (claimed_by and lease_until show whether a worker has one), person steps you claimed or were handed, and work items in your queue. |
| `claim_work` | work | task_id, agent | Take a step before working on it, so no worker or other agent takes it too. |
| `complete_work` | work | task_id, agent, result | Finish a step with your result (merged into the process data; a proposal, findings, a draft). |
| `hand_off` | work | task_id, agent, to, reason | Give a step to someone better placed: a person by name, a role's queue (`role:engineer`) or another agent (`agent:maintenance`). |
| `send_evidence` | work | agent, evidence, correlation_key?, item_id?, process_key?, name? | Add evidence (new findings, a figure id, a SAP notification raised) to an open process: by its correlation key (an upset's incident id), or by work item id. |

## Calc checks

`calc(check, ref)` runs one; each returns {value, expected, deviation, flag, basis, sources, method}. [Methods](methods.md) has each one's standard, inputs and test vectors.

| Check | On | Method | Also used by |
|---|---|---|---|
| `decline` | well | Arps, Analysis of Decline Curves (1945) | chart `plot_decline` |
| `gas_lift` | well | Engineering practice | chart `plot_gas_lift_curve` |
| `pump_curve` | pump tag or equipment | Engineering practice | chart `plot_pump_curve` |
| `choke` | well or its choke tag | Engineering practice | chart `plot_choke` |
| `reliability` | tag, equipment or equipment class | IEC 61649 (Weibull analysis); IEC 61164 (reliability growth, power law) | chart `plot_reliability` |
| `pm_interval` | equipment class, or a unit or tag in it | IEC 61649 (Weibull analysis); IEC 60300-3-11 (reliability centred maintenance) |  |
| `wall_loss` | vessel, piping or pipeline | API 570 / API 510 | chart `plot_wall_thickness` |
| `well_constraint` | well | Engineering practice |  |
| `dp_drift` | well | Engineering practice | SRE detector `sre.hydraulics` |
| `compressor_performance` | compressor stage tag | Engineering practice | chart `plot_compressor_performance` |
| `cooler_fouling` | cooler tag | Engineering practice | chart `plot_cooler_fouling` |
| `peers` | tag, equipment class or model | Engineering practice | chart `plot_peers` |
| `vibration` | machine tag or equipment | ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors |  |
| `pump_bep` | pump tag or equipment | API 610 (preferred and allowable operating regions) |  |
| `surge` | compressor stage tag or equipment | Engineering practice |  |
| `heat_rate` | gas turbine tag or equipment | Engineering practice |  |
| `motor_load` | motor-driven machine tag or equipment | Engineering practice |  |
| `rotating_health` | machine tag or equipment | ISO 14224 (rotating classes); each check that applies cites its own standard |  |
| `rod_load` | reciprocating compressor tag or equipment | API 618 |  |
| `valve_health` | reciprocating compressor tag or equipment | API 618 |  |
| `compressor_trip` | compressor (any machine of the train) | ISO 13379-1 (symptom to cause) | SRE detector `trips.scan` |
| `protection` | compressor or other machine with API 670 channels | API 670 |  |
| `seal_gas` | compressor with dry gas seals | API 692 |  |
| `lube_oil` | gas turbine, engine or compressor with a lube oil system | API 614 |  |
| `surge_events` | centrifugal compressor stage | Engineering practice |  |
| `gas_performance` | centrifugal compressor stage | ASME PTC 10 (Schultz polytropic analysis); ISO 5389 |  |
| `power_balance` | gas turbine or a machine on its train | ASME PTC 10 |  |
| `exhaust_spread` | gas turbine | Engineering practice |  |
| `hot_section` | gas turbine | Engineering practice |  |
| `engine_cylinders` | gas engine driven machine | Engineering practice |  |
| `remaining_life` | rotating machine | ISO 13381-1 |  |
| `failed_starts` | compressor or its train | Engineering practice |  |
| `loop_oscillation` | a controlled unit (vessel, heater, reboiler) or one point | Engineering practice |  |
| `test_check` | oil well | Engineering practice |  |
| `deferment_attribution` | jacket, area or facility (or one well) | Engineering practice |  |
| `vessel_loading` | separator, scrubber or meter tag with a rating | Engineering practice |  |
| `corrosion_circuit` | pipeline, vessel or piping tag | API 570 7.1 |  |
| `spares_coverage` | tag or equipment, equipment class or model, area or facility | ISO 14224 (equipment and failure data); ISO 55000 (criticality-based asset management) |  |
| `inspection_plan` | vessel, exchanger or piping tag with an inspection plan | API 510 / API 570 |  |
| `contactor_foaming` | amine or glycol contactor | Engineering practice |  |
| `treated_gas` | contactor with a treated gas analyser | Engineering practice |  |
| `regenerator` | amine or glycol reboiler (or a stabilizer's) | Engineering practice |  |
| `chiller` | low temperature separator | Engineering practice |  |
| `allocation_factor` | meter on the allocation network | Engineering practice |  |
| `meter_agreement` | meter with another on the same stream | Engineering practice |  |
