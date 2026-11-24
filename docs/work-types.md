# Work types

Generated from the work type registry (`core/exceptions.py`) and the detector registry; don't edit by hand (`uv run python -m fuzer.reference`).
The 47 types of work declared in code, grouped by where the routing table sends each one when it arrives. A deployment adds its own as data (on **Settings > Routing**) or in a source pack's `exception_types`; **Settings > Routing** shows the table as it stands on a running site, those included. [Where work goes](work-map.md) draws the path each group takes through the pages, and [every source of work](work-sources.md) says how each source is matched, routed, dispatched and closed.

Every type is listed on the **List** (All work), whichever group it is in. *The field can be sent* means a case of the type can be dispatched to a field operator's **My work**; *joins one event's case* means it joins an open case about the same asset instead of opening another; *held back by planned work* means a finding during a shutdown or planned job on its asset is counted under that window and opens no case.

## Waits for the IOC

No workflow and no queue: it waits on the List's **To dispatch** until the IOC dispatches it, plans it for a day or shelves it.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **SAP notification** `sap_notification` | An open SAP maintenance notification with no work order raised for it yet. | - | [`ops.intake`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/intake.py) | the field can be sent; joins one event's case; due by priority (`intake.sap_notifications.due_hours`) |

## Starts its own workflow

Starts a named BPMN workflow (or adds its evidence to the one already open). Its steps show on **Waiting on me** for whoever holds each lane, and the workflow is drawn under **Settings > Workflows**.

| Type of work | What it is | Workflow | Found by | Declared in | How it behaves |
|---|---|---|---|---|---|
| **IOW breach** `iow_breach` | An integrity operating window held past its allowed time. | `iow_breach` | [`iow_breaches`](detectors.md#iow_breaches) | [`analysis.limits`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/limits.py) | the field can be sent; joins one event's case |
| **Process upset** `upset` | Detections grouped into one upset by time and plant topology. | `upset` | [`excursions`](detectors.md#excursions), [`stops`](detectors.md#stops), [`steps`](detectors.md#steps), [`drifts`](detectors.md#drifts), [`instruments`](detectors.md#instruments), [`models`](detectors.md#models), [`hydraulics`](detectors.md#hydraulics), [`multivariate`](detectors.md#multivariate), [`peer_drifts`](detectors.md#peer_drifts), [`promoted_rules`](detectors.md#promoted_rules) | [`ops.sre_handoff`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/sre_handoff.py) | the field can be sent; joins one event's case; held back by planned work |

## Goes to a role's queue

Starts the standard one-step `queue_item` workflow in the role's queue, on each holder's **Waiting on me** with **Take it**, and with the role's SLA and escalation. Whoever takes it can work it at the desk or send it to the field and get it back.

### Engineer queue

`engineer`: Process and facilities engineers: triage upsets, confirm fixes. A step is due in 1 day, then escalates to `ops_lead`.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Alarm bad actor** `alarm_bad_actor` | An alarm standing for a day or more, or one point alarming over and over: rationalize the alarm, fix the instrument or review the setpoint. | [`alarm_performance`](detectors.md#alarm_performance) | [`analysis.alarm_performance`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/alarm_performance.py) | - |
| **Alarm flood** `alarm_flood` | More alarms than an operator can handle (EEMUA 191: over 10 in 10 minutes), raised once per flood with the points that drove it, for the alarm owner to rationalize. | [`alarm_performance`](detectors.md#alarm_performance) | [`analysis.alarm_performance`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/alarm_performance.py) | - |
| **Change with no record** `unrecorded_change` | An alarm or integrity operating window limit that changed with no change record on file around it. | [`unrecorded_changes`](detectors.md#unrecorded_changes) | [`ops.safeguards`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/safeguards.py) | - |
| **Compressor trip** `machine_trip` | A compression train tripped: the likely cause from what moved first, and the checks before it restarts. | [`compressor_trips`](detectors.md#compressor_trips), [`pump_trips`](detectors.md#pump_trips) | [`analysis.trips`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/trips.py) | the field can be sent; joins one event's case; held back by planned work |
| **Detector tuning to review** `detector_tuning` | A detector whose cases on one equipment class keep closing as false alarms or duplicates, with a quieter value for one of its parameters, replayed on those cases before and after, and the real cases the change would have missed. | - | [`ops.tuning`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/tuning.py) | - |
| **Gas balance not closing** `gas_balance_gap` | The daily gas balance (gas handled against export, lift gas, flare, fuel and vent) has stayed open past its threshold for days running. | [`gas_balance_gaps`](detectors.md#gas_balance_gaps) | [`ops.emissions`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/emissions.py) | - |
| **Inspection or wall loss finding** `inspection_due` | A line or vessel whose wall is running out, or whose inspection is overdue for its wall loss or its plan. | [`inspection_findings`](detectors.md#inspection_findings) | [`ops.integrity`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/integrity.py) | - |
| **Leak repair not holding** `repeat_leak` | A component found leaking in consecutive LDAR surveys and still leaking at its latest screening. | [`repeat_leaks`](detectors.md#repeat_leaks) | [`ops.emissions`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/emissions.py) | - |
| **Loop cycling** `loop_candidate` | A measurement that cycles regularly, as a control loop does when it hunts or limit-cycles on a sticking valve. | [`loop_oscillation`](detectors.md#loop_oscillation) | [`analysis.loops`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/loops.py) | - |
| **Machine health** `machine_health` | A rotating machine the fleet screen flags: vibration in zone C or D, near surge, a motor past its service factor, head or efficiency loss, a valve or rod load problem. | [`machine_health`](detectors.md#machine_health) | [`analysis.rotating`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/rotating.py) | the field can be sent; joins one event's case; held back by planned work |
| **Pig run anomaly over the screen** `ili_anomaly` | A metal loss anomaly from an in-line inspection run that crosses the strength or depth screen now, or will at its growth rate within the horizon (modified ASME B31G). | [`ili_anomalies`](detectors.md#ili_anomalies) | [`ops.integrity`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/integrity.py) | - |
| **Product or water quality drift** `quality_drift` | A lab analyte at a sample point (oil in water, BS&W, water in gas) whose latest samples have all moved off its own recent level. | [`quality_drift`](detectors.md#quality_drift) | [`analysis.quality`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/quality.py) | - |
| **Proof test overdue** `proof_test_overdue` | A safeguard past its proof test interval, or with no proof test on file. | [`proof_test_overdue`](detectors.md#proof_test_overdue) | [`ops.safeguards`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/safeguards.py) | - |
| **Proposed by the watch** `watch_finding` | The agentic watch, reading a process area as an engineer scanning the day would, proposed something that looks unusual that no rule raised. | [`watch`](detectors.md#watch) | [`ops.watch`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/watch.py) | - |
| **Safeguard demand to review** `safeguard_demand` | A safeguard that failed to act on a demand, or was called on more often than the rate its risk assessment assumed. | [`safeguard_demands`](detectors.md#safeguard_demands) | [`ops.safeguards`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/safeguards.py) | - |
| **Skill change to review** `skill_change` | A change an agent drafted to a skill's playbook, from the briefs people did not follow and the answers they rated not useful, with the cases behind it and new eval questions drawn from them. | - | [`ops.skill_review`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/skill_review.py) | - |
| **Temporary MOC overdue** `temporary_moc_overdue` | A temporary management of change still open past the date it was approved to end. | [`temporary_moc_overdue`](detectors.md#temporary_moc_overdue) | [`ops.safeguards`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/safeguards.py) | - |

### Data steward queue

`data_steward`: Master data steward: source problems and register gaps. A step is due in 1 day, then escalates to `ops_lead`.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Asset context missing** `context_gap` | An asset lacks context its class usually has, and a check can't run on it until it is filled. | - | [`analysis.completeness`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/completeness.py) | - |
| **Criticality not assessed** `criticality_not_assessed` | An asset has no criticality, so its work can't be ranked by consequence; rate it. | - | [`graph.criticality`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph/criticality.py) | - |
| **Datasheet curve to confirm** `curve_to_confirm` | A curve read from a datasheet; confirm it before methods use it. | - | - | - |
| **Drawing disagrees with a system** `drawing_disagreement` | A P&ID and the DCS, AF or SAP disagree; record which one needs changing. | - | - | - |
| **Drawing line to confirm** `drawing_line_to_confirm` | A line read from a P&ID joins two items; confirm it before it is drawn as piping. | - | - | - |
| **Export changed its columns** `mapping_drift` | A dataset's columns changed and its load is held; accept the change or keep it held. | - | - | - |
| **Links that name nothing** `unresolved_links` | A source's rows name a parent, location or other item the register doesn't have; make each value an alias of the item it means, or fix the source. | - | [`graph.contextualized`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph/contextualized.py) | - |
| **Load failed a quality check** `failed_quality_check` | A blocking check held a load; fix the export and load it again. | - | - | - |
| **Measurement role to confirm** `measurement_role_to_confirm` | What a historian point measures on its asset is uncertain; confirm it before detectors read it. | - | - | - |
| **Possible match to confirm** `unmatched_record` | Two systems' records may be the same item; confirm or keep them apart. | - | - | - |
| **Records that match nothing** `unmatched_records` | A source's records describe items the register should already have, but these matched nothing; say they are new, or match each to the item it means. | - | [`graph.contextualized`](https://github.com/areed145/fuzer/tree/main/src/fuzer/graph/contextualized.py) | - |
| **Source problem** `source_issue` | A dataset failed, is held, fails its checks, changed shape or is stale. | [`source_health`](detectors.md#source_health) | - | - |
| **Source quality dropped** `source_quality_drop` | A dataset's completeness, validity, timeliness or agreement with other sources fell well below where it was; its owner looks at the source. | - | [`sources.scorecard`](https://github.com/areed145/fuzer/tree/main/src/fuzer/sources/scorecard.py) | - |
| **Sources disagree** `conflicting_sources` | Systems give an item different values for one field; choose the one that is right. | - | - | - |
| **Suggested mapping to approve** `mapping_to_approve` | A suggested mapping for a new dataset; approve it, or leave the dataset unmapped. | - | - | - |

### Ops lead queue

`ops_lead`: Operations lead: approves actions with an effect outside Fuzer, reviews daily reports. A step is due in 8 hours.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Bypass overdue** `bypass_overdue` | A safeguard still bypassed past the expiry it was authorised to. | [`bypass_overdue`](detectors.md#bypass_overdue) | [`ops.safeguards`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/safeguards.py) | - |
| **Deviation review due** `deviation_review_due` | A named deviation from the operating model is past its review date. | [`deviation_reviews`](detectors.md#deviation_reviews) | [`workflow.conformance`](https://github.com/areed145/fuzer/tree/main/src/fuzer/workflow/conformance.py) | - |
| **Permit lapsed with plant still isolated** `permit_lapsed` | A permit to work past its valid-to time, not closed, with isolations still in place. | [`permit_lapsed`](detectors.md#permit_lapsed) | [`ops.permits`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/permits.py) | the field can be sent |
| **Records don't agree** `records_gap` | Downtime with no operator log entry or notification about the same plant, or a log entry that says something stopped with no downtime record. | [`records_gaps`](detectors.md#records_gaps) | [`analysis.records`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/records.py) | - |
| **Round finding** `round_finding` | A reading outside its limits, or a failed check, taken by an operator on a round. | - | [`ops.rounds`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/rounds.py) | the field can be sent; joins one event's case |

### HSE specialist queue

`hse_specialist`: HSE and environmental: flaring and emissions evidence, incidents. A step is due in 1 day, then escalates to `ops_lead`.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Flare event** `flare_event` | A flare above its own baseline, with the downtime, log entries and detections around it, or its cause unknown when nothing matches. | [`flare_events`](detectors.md#flare_events) | [`analysis.flare`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/flare.py) | - |
| **Regulatory report coming due** `report_due` | A regulatory report whose due date is coming with no draft started, or that is past its due date and not submitted. | - | [`ops.reports`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/reports.py) | - |
| **Regulatory report to review** `regulatory_report` | A regulatory or emissions report drafted from its records for one period. | - | [`ops.reports`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/reports.py) | - |

### Maintenance planner queue

`maintenance_planner`: Plans maintenance: raises and schedules SAP notifications. A step is due in 2 days, then escalates to `ops_lead`.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Parts not ready for planned work** `parts_not_ready` | A planned work order with a reserved part that won't arrive by the day the job needs it: on order too late, or short with nothing on order and too little time to get it. | [`parts_not_ready`](detectors.md#parts_not_ready) | [`ops.materials`](https://github.com/areed145/fuzer/tree/main/src/fuzer/ops/materials.py) | - |
| **Routine due** `routine_due` | A routine falling due: a maintenance or inspection plan's next date, or a routine duty kept in Fuzer. | - | - | - |

### Production engineer queue

`production_engineer`: Production engineering: wells, gas lift, deferment. A step is due in 1 day, then escalates to `ops_lead`.

| Type of work | What it is | Found by | Declared in | How it behaves |
|---|---|---|---|---|
| **Gas lift heading** `gas_lift_instability` | A gas-lifted well whose casing pressure surges and falls back regularly, as a well heading on lift does. | [`gas_lift_instability`](detectors.md#gas_lift_instability) | [`analysis.gas_lift`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/gas_lift.py) | - |
| **Well test to review** `well_test_review` | A well whose test is overdue against its own interval, or whose latest test moved its water cut or gas-oil ratio well past its recent tests. | [`well_tests`](detectors.md#well_tests) | [`analysis.well_tests`](https://github.com/areed145/fuzer/tree/main/src/fuzer/analysis/well_tests.py) | - |
