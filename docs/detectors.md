# Detectors

Generated from the detector registry (`core/detectors.py`); don't edit by hand (`uv run python -m fuzer.reference`).
Everything that looks for trouble in the data is declared once, where it is written. A detector whose needs are missing on an asset reports "not applicable: missing X" and does not run. Each detector is switched on or off per asset, and a setting marked *per class* takes a value for one equipment class on one asset, on the Detection page under Settings; both changes are kept in the settings history.

| Detector | Kind | Runs | Raises | Applies to |
|---|---|---|---|---|
| [`excursions`](#excursions) Outside an alarm limit, an IOW or the learned normal range while the unit runs | Rule | Every 6 hours | `upset` | Any asset with the inputs |
| [`stops`](#stops) A unit going from running to stopped | Rule | Every 6 hours | `upset` | Any asset with the inputs |
| [`steps`](#steps) A measurement stepping on a running unit | Statistical | Every 6 hours | `upset` | Any asset with the inputs |
| [`drifts`](#drifts) A slow climb in what wears and fouls | Statistical | Once a day | `upset` | Any asset with the inputs |
| [`instruments`](#instruments) A frozen, stuck, noisy or out of range instrument | Statistical | Every 6 hours | `upset` | Any asset with the inputs |
| [`models`](#models) Equipment off its curve | Model | Once a day | `upset` | pump, well |
| [`hydraulics`](#hydraulics) A flowline pressure drop still rising | Model | Once a day | `upset` | well |
| [`multivariate`](#multivariate) A stretch a process area's own model cannot explain | Statistical | Once a day | `upset` | Any asset with the inputs |
| [`iow_breaches`](#iow_breaches) Time outside an integrity operating window past its allowed time | Rule | On request | `iow_breach` | Any asset with the inputs |
| [`compressor_trips`](#compressor_trips) A compression train tripping, with its first cause | Rule | Every 5 minutes | `machine_trip` | compressor |
| [`pump_trips`](#pump_trips) A pump tripping, with its first cause | Rule | Every 5 minutes | `machine_trip` | pump |
| [`machine_health`](#machine_health) A rotating machine whose condition needs attention | Model | Every hour | `machine_health` | compressor, pump, gas turbine, electric motor, turboexpander, blower, steam turbine |
| [`records_gaps`](#records_gaps) Downtime, the operator log and notifications that do not agree | Rule | Once a day | `records_gap` | Any asset with the inputs |
| [`source_health`](#source_health) A dataset that failed, is held, is stale or fails its checks | Rule | Every 30 minutes | `source_issue` | Any asset with the inputs |
| [`opportunities`](#opportunities) Production that the day's data says is there to be had | Model | Once a day | `opportunity` | well |
| [`bypass_overdue`](#bypass_overdue) A safeguard still bypassed past its expiry | Rule | Every 30 minutes | `bypass_overdue` | Any asset with the inputs |
| [`unrecorded_changes`](#unrecorded_changes) An alarm or IOW limit changed with no change record | Rule | Once a day | `unrecorded_change` | Any asset with the inputs |
| [`loop_oscillation`](#loop_oscillation) A measurement cycling as a hunting control loop does | Statistical | Once a day | `loop_candidate` | Any asset with the inputs |
| [`well_tests`](#well_tests) A well test overdue, or a water cut or GOR shift on the latest test | Rule | Once a day | `well_test_review` | well |
| [`gas_lift_instability`](#gas_lift_instability) A gas-lifted well heading: its casing pressure surging regularly | Statistical | Once a day | `gas_lift_instability` | well |
| [`quality_drift`](#quality_drift) A lab analyte at a sample point drifting off its own baseline | Rule | Once a day | `quality_drift` | Any asset with the inputs |
| [`flare_events`](#flare_events) A flare above its baseline, matched to the records around it | Rule | Once a day | `flare_event` | Any asset with the inputs |
| [`alarm_performance`](#alarm_performance) Alarm floods and bad actors against ISA-18.2 and EEMUA 191 | Rule | Once a day | `alarm_flood`, `alarm_bad_actor` | Any asset with the inputs |

## excursions

**Outside an alarm limit, an IOW or the learned normal range while the unit runs.** A point outside its alarm limit, its integrity operating window or the range it normally keeps, while its unit is running. A brief wander outside a learned range is left out.

- **Kind:** Rule
- **Runs:** Every 6 hours
- **Raises:** `upset`
- **Needs:** historian points, alarm limits, IOWs or learned normal ranges
- **Setting `sre.learned_minutes`:** Minutes a value must stay outside a learned normal range (one no source set) to count (default 60)
- **Setting `sre.step_minutes`:** Minutes between the values the detectors read (default 10)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:excursions`

## stops

**A unit going from running to stopped.** A unit with its own run signal going from running to stopped: a trip or a stop.

- **Kind:** Rule
- **Runs:** Every 6 hours
- **Raises:** `upset`
- **Measurement roles:** running status
- **Needs:** historian points, a run signal on a unit
- **Setting `sre.step_minutes`:** Minutes between the values the detectors read (default 10)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:stops`

## steps

**A measurement stepping on a running unit.** A point stepping to a new level while its unit runs either side, judged against how it moved the day before.

- **Kind:** Statistical
- **Runs:** Every 6 hours
- **Raises:** `upset`
- **Needs:** historian points
- **Setting `signals.step_z`:** A step: the median moves this many times its usual window-to-window move (default 6.0)
- **Setting `sre.lookback_hours`:** Hours before the window that a step is judged against (default 24)
- **Setting `sre.step_minutes`:** Minutes between the values the detectors read (default 10)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:steps`

## drifts

**A slow climb in what wears and fouls.** Vibration, bearing and discharge temperatures, cooler outlets and differential pressures climbing over the last month and still climbing.

- **Kind:** Statistical
- **Runs:** Once a day
- **Raises:** `upset`
- **Measurement roles:** casing vibration, bearing temperature, discharge temperature, outlet temperature, differential pressure
- **Needs:** historian points
- **Setting `sre.drift_rise`:** Share of its starting value a drift must climb before it is raised (default 0.05), *per class*
- **Setting `signals.drift_z`:** A drift: the fitted trend moves this many times the spread around it (default 4.0)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:drifts`

## instruments

**A frozen, stuck, noisy or out of range instrument.** A reading that is frozen, stuck, noisy or at the end of its range, against the point's own usual spread: instrument work, not an upset.

- **Kind:** Statistical
- **Runs:** Every 6 hours
- **Raises:** `upset`
- **Needs:** historian points
- **Setting `signals.flat_hours`:** Hours with no change at all, while the unit runs, before a reading is frozen (default 2.0)
- **Setting `signals.stuck_hours`:** Hours a reading moves far less than usual (under stuck_ratio x), while the unit runs, before it is stuck (default 6.0)
- **Setting `signals.stuck_ratio`:** A stuck reading's step-to-step spread is under this share of its usual (default 0.1)
- **Setting `signals.noisy_hours`:** Hours a reading moves far more than usual (over noisy_ratio x) before it is noisy (default 1.0)
- **Setting `signals.noisy_ratio`:** A noisy reading's step-to-step spread is over this many times its usual (default 4.0)
- **Setting `signals.range_minutes`:** Minutes at or past the end of its instrument range before a reading is out of range (default 30)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:instruments`

## models

**Equipment off its curve.** Equipment against its curve, once a day; a detection while the check flags it.

- **Kind:** Model
- **Runs:** Once a day
- **Raises:** `upset`
- **Equipment classes:** pump, well
- **Needs:** historian points, a pump curve or a gas lifted well
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:models`

## hydraulics

**A flowline pressure drop still rising.** Once a day: each jacket well's wellhead-to-header pressure drop still rising (a flowline waxing or scaling, #107).

- **Kind:** Model
- **Runs:** Once a day
- **Raises:** `upset`
- **Equipment classes:** well
- **Measurement roles:** wellhead pressure, process pressure
- **Needs:** historian points, a well with a choke
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:hydraulics`

## multivariate

**A stretch a process area's own model cannot explain.** Once a day: each process area's PCA model of the week before against the last day; a stretch the model can't explain (squared prediction error past its 99.9th percentile for sre.spe_minutes) on the point contributing most (#106).

- **Kind:** Statistical
- **Runs:** Once a day
- **Raises:** `upset`
- **Needs:** historian points, a process area with 10 to 300 points
- **Setting `sre.spe_minutes`:** Minutes a multivariate anomaly lasts before it is raised (default 120)
- **Scored by:** the replay harness, `fuzer eval --ops --detector sre:multivariate`

## iow_breaches

**Time outside an integrity operating window past its allowed time.** Find excursions in [start, end] and keep them in the excursion table; one still open from the last scan is extended. IOW breaches past their allowed time (critical, standard) open an iow_breach work item, one per IOW while it is open (later breaches go to it as evidence).

- **Kind:** Rule
- **Runs:** On request
- **Raises:** `iow_breach`
- **Needs:** historian points, alarm limits, IOWs or learned normal ranges
- **Scored by:** the replay harness, `fuzer eval --ops --detector limits`

## compressor_trips

**A compression train tripping, with its first cause.** Diagnose every compression train's trips in the window and raise each as a `machine_trip` exception.

- **Kind:** Rule
- **Runs:** Every 5 minutes
- **Raises:** `machine_trip`
- **Equipment classes:** compressor
- **Measurement roles:** running status, casing vibration, bearing temperature, discharge temperature
- **Needs:** historian points, a compression train
- **Scored by:** `tests/analysis/test_trips.py::test_trips_reach_the_ioc_with_the_restart_checklist`, on the fake plant

## pump_trips

**A pump tripping, with its first cause.** Diagnose every pump train's trips in the window and raise each as a `machine_trip` exception: a pipeline's mainline pumps trip on vibration, bearing temperature or suction pressure the way a compressor does.

- **Kind:** Rule
- **Runs:** Every 5 minutes
- **Raises:** `machine_trip`
- **Equipment classes:** pump
- **Measurement roles:** power, casing vibration, bearing temperature
- **Needs:** historian points, a pump on the piping
- **Scored by:** `tests/evals/test_trips_harrier.py::test_a_mainline_pump_trip_reaches_the_ioc_with_its_cause`, on the fake plant

## machine_health

**A rotating machine whose condition needs attention.** The fleet screen at `until` raised as `machine_health` exceptions: one per machine whose condition is flagged (failure-history flags stay with the reliability review), kept while the screen flags it (new evidence updates the open one), cleared when it doesn't. `act` machines come at priority 2, `watch` ones at 0.5 (left out with `watch_level=False`). Each carries its status, the flags in words, the first recommendation and the findings behind them, so the engineer the case reaches starts from the evidence.

- **Kind:** Model
- **Runs:** Every hour
- **Raises:** `machine_health`
- **Equipment classes:** compressor, pump, gas turbine, electric motor, turboexpander, blower, steam turbine
- **Measurement roles:** casing vibration, bearing temperature, suction flow, suction pressure, discharge pressure
- **Needs:** a rotating machine
- **Scored by:** `tests/analysis/test_trips.py::test_machine_health_raises_flagged_machines_and_clears_them_when_fixed`, on the fake plant

## records_gaps

**Downtime, the operator log and notifications that do not agree.** The mismatches in the last `records.lookback_days` before the latest log entry, raised as `records_gap` exceptions on the ops lead queue (one per record, keyed by it); open ones whose gap has closed are cleared.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `records_gap`
- **Needs:** operator log entries
- **Setting `records.lookback_days`:** Records mismatch: days before the latest log entry whose mismatches are raised for the shift (older ones stay in the calc) (default 7)
- **Setting `records.log_tolerance_hours`:** Records mismatch: hours either side of a downtime record (and during it) an operator log entry about the same plant counts as its log (default 12)
- **Setting `records.notification_tolerance_days`:** Records mismatch: days either side of a downtime record a notification about the same plant counts as its notification (default 3)
- **Scored by:** `tests/analysis/test_records.py::test_gaps_reach_the_ops_lead_queue_and_the_handover_and_clear`, on the fake plant

## source_health

**A dataset that failed, is held, is stale or fails its checks.** Raise exceptions for new problems and clear the ones that went away. Returns what was opened and closed.

- **Kind:** Rule
- **Runs:** Every 30 minutes
- **Raises:** `source_issue`
- **Scored by:** `tests/api/test_catalog_api.py::test_rules_health_alerts_and_lineage`, on the fake plant

## opportunities

**Production that the day's data says is there to be had.** Find the day's opportunities, keep them, open a work item for each new one, close the ones the data no longer supports, and leave decided ones alone unless the data changed materially.

- **Kind:** Model
- **Runs:** Once a day
- **Raises:** `opportunity`
- **Equipment classes:** well
- **Setting `opportunities.gas_lift_breakeven`:** Sm3/d of oil per kSm3/d of lift gas below which more gas isn't worth it (default 0.5)
- **Setting `opportunities.stale_days`:** Days without a well test before the test is stale (default 90)
- **Setting `opportunities.material`:** A decided opportunity comes back when its gain moves by this share (default 0.5)
- **Scored by:** `tests/ops/test_opportunities.py::test_scan_board_and_rejection`, on the fake plant

## bypass_overdue

**A safeguard still bypassed past its expiry.** Each bypass in place past its expiry raised as `bypass_overdue` for the ops lead (keyed by the bypass); open ones whose bypass is no longer overdue are cleared.

- **Kind:** Rule
- **Runs:** Every 30 minutes
- **Raises:** `bypass_overdue`
- **Needs:** safeguard bypass records
- **Scored by:** `tests/evals/test_safeguards_kestrel.py::test_an_overdue_bypass_is_work_for_the_ops_lead_until_it_is_removed`, on the fake plant

## unrecorded_changes

**An alarm or IOW limit changed with no change record.** Each limit changed with no change record raised as `unrecorded_change` for the engineer (keyed by the limit); open ones that a change record now covers, or that left the lookback, are cleared.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `unrecorded_change`
- **Needs:** change records
- **Scored by:** `tests/evals/test_safeguards_kestrel.py::test_a_limit_changed_with_no_change_record_is_a_question_until_one_turns_up`, on the fake plant

## loop_oscillation

**A measurement cycling as a hunting control loop does.** Each controlled measurement cycling regularly over the last `days` raised as `loop_candidate` for the engineer (keyed by the point), with its period and amplitude; open ones whose point no longer cycles are cleared.

- **Kind:** Statistical
- **Runs:** Once a day
- **Raises:** `loop_candidate`
- **Measurement roles:** level, process pressure, differential pressure, process flow, process temperature, outlet temperature, suction pressure, discharge pressure, suction flow, fuel flow
- **Needs:** historian points, measurement roles of what a controller holds
- **Scored by:** `tests/analysis/test_loops.py::test_a_cycling_level_is_a_loop_candidate_until_it_stops`, on the fake plant

## well_tests

**A well test overdue, or a water cut or GOR shift on the latest test.** Each well flagged by `review` raised as `well_test_review` for the production engineer (keyed by the well); open ones a newer test no longer flags are cleared.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `well_test_review`
- **Equipment classes:** well
- **Needs:** well tests
- **Scored by:** `tests/analysis/test_well_tests.py::test_a_well_whose_water_cut_jumps_is_raised_until_a_newer_test_settles_it`, on the fake plant

## gas_lift_instability

**A gas-lifted well heading: its casing pressure surging regularly.** Each heading well raised as `gas_lift_instability` for the production engineer (keyed by the well), with the casing pressure's period and swing; open ones whose casing has settled are cleared.

- **Kind:** Statistical
- **Runs:** Once a day
- **Raises:** `gas_lift_instability`
- **Equipment classes:** well
- **Measurement roles:** casing pressure, wellhead pressure
- **Needs:** casing pressure on a well
- **Scored by:** `tests/analysis/test_gas_lift.py::test_a_heading_well_is_raised_until_its_casing_settles`, on the fake plant

## quality_drift

**A lab analyte at a sample point drifting off its own baseline.** Each drift raised as `quality_drift` for the engineer (keyed by subject, sample point and analyte), with the level against its baseline; open ones whose samples have come back are cleared.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `quality_drift`
- **Needs:** lab results at a sample point
- **Scored by:** `tests/analysis/test_quality_drift.py::test_a_drifting_analyte_is_raised_until_it_comes_back`, on the fake plant

## flare_events

**A flare above its baseline, matched to the records around it.** Each flare episode raised as `flare_event` for the HSE specialist (keyed by where and when it started), with the records it was matched to; an episode is a record of what happened, so it stays until a person closes it.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `flare_event`
- **Measurement roles:** flare gas flow
- **Needs:** a flare flow point or a daily flare volume
- **Scored by:** `tests/analysis/test_flare.py::test_a_flare_event_is_raised_with_its_records_or_unknown`, on the fake plant

## alarm_performance

**Alarm floods and bad actors against ISA-18.2 and EEMUA 191.** Each flood raised as one `alarm_flood` case with its top contributors, and each standing or repeating point as one `alarm_bad_actor` case (keyed by the point), quiet while it goes on and raised again only if it recurs after its case was closed.

- **Kind:** Rule
- **Runs:** Once a day
- **Raises:** `alarm_flood`, `alarm_bad_actor`
- **Needs:** historian points, alarm limits
- **Scored by:** `tests/analysis/test_alarm_performance.py::test_a_flood_is_one_case_and_a_bad_actor_is_raised_once`, on the fake plant
