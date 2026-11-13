---
title: Methods
description: Every calculation an agent or a detector can call, with the standard behind it.
---

Generated from the method registry (`src/fuzer/standards/methods.yaml`); don't edit by hand (`uv run python -m fuzer.reference`).

A result names its method, and a method that cannot answer refuses by name instead of estimating. Standards are cited as the calculation applies them: check clauses against your licensed copy.

| Method | Standard or practice | Inputs | Needs |
|---|---|---|---|
| [`decline`](#decline) Latest well test against the Arps decline | Arps, Analysis of Decline Curves (1945) | Well tests (oil rate (Sm³/d or kSm³/d) | At least four well tests. |
| [`gas_lift`](#gas_lift) Lift gas against the economic optimum of the well's lift curve | Engineering practice | Well tests (oil rate) (Sm³/d); Lift gas rate (kSm³/d) | At least four well tests with a lift gas rate. |
| [`pump_curve`](#pump_curve) Pump head against the OEM curve at the same flow | Engineering practice | Flow (m³/h); Suction and discharge pressure (barg); OEM head curve (m) | A minimum count of running readings in the recent window. |
| [`choke`](#choke) Choke effective Cv against its characteristic | Engineering practice | Choke opening (%); Upstream and downstream pressure (barg); Well test rates (Sm³/d) | Three or more readings since the trim was last replaced, else the latest readings before. |
| [`reliability`](#reliability) Weibull and Crow-AMSAA on the failure history | IEC 61649 (Weibull analysis); IEC 61164 (reliability growth, power law) | Failure notifications (dates) | Three times between failures for Weibull; four failures for Crow-AMSAA. |
| [`pm_interval`](#pm_interval) Preventive maintenance interval against the class's failure pattern | IEC 61649 (Weibull analysis); IEC 60300-3-11 (reliability centred maintenance) | Failure notifications on the class's units (dates); Preventive plan intervals (days) | Three times between failures in the class, else the class is reported as insufficient. |
| [`spares_coverage`](#spares_coverage) Critical spares coverage, ranked by criticality and failure history | ISO 14224 (equipment and failure data); ISO 55000 (criticality-based asset management) | Bill of materials of each unit's installed equipment (materials); Stock on hand (each); Criticality (A (class); Failure notifications (count) | A bill of materials on the unit's installed equipment. |
| [`wall_loss`](#wall_loss) Wall loss rate and remaining life per CML | API 570 / API 510 | Wall thickness readings per CML (mm); Minimum required thickness (mm) | Wall thickness readings on the item. |
| [`corrosion_circuit`](#corrosion_circuit) Corrosion circuit: long and short term rate, remaining life, next inspection | API 570 7.1 | Wall thickness readings per CML (mm); Maximum operating pressure (barg); Nominal diameter (in) | Wall thickness readings on a line of the circuit. |
| [`well_constraint`](#well_constraint) What limits a well (choke, back-pressure or lift) | Engineering practice | Wellhead and header pressure (barg); Choke opening (%) | A production header pressure for the well's jacket. |
| [`dp_drift`](#dp_drift) Wellhead-to-header pressure drop drift | Engineering practice | Wellhead and header pressure (barg) | A production header pressure for the well's jacket. |
| [`compressor_performance`](#compressor_performance) Compressor stage polytropic efficiency and head | Engineering practice | Suction and discharge pressure (barg); Suction and discharge temperature (°C) | A suction temperature for the stage and enough running time in the window. |
| [`cooler_fouling`](#cooler_fouling) Cooler outlet temperature against its clean baseline | Engineering practice | Outlet temperature (°C) | At least 14 days of outlet temperature. |
| [`peers`](#peers) Units of a class side by side | Engineering practice | Failure notifications (dates); Vibration (mm/s) | At least three units to compare. |
| [`vibration`](#vibration) Overall vibration against the evaluation zones | ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors | Overall vibration velocity (mm/s RMS) | A vibration point in mm/s with enough readings in the window. |
| [`pump_bep`](#pump_bep) Pump operating region against best efficiency point | API 610 (preferred and allowable operating regions) | Flow (m³/h); OEM curve with best efficiency point (m³/h) | An OEM curve with a best efficiency point. |
| [`surge`](#surge) Compressor surge margin against the OEM map | Engineering practice | Suction flow (m³/h); Speed (rpm); Anti-surge valve opening (%) | An OEM map for the stage and enough running time in the window. |
| [`surge_events`](#surge_events) Surge events from their signature | Engineering practice | Suction flow (m³/h); Anti-surge valve opening (%) | Suction flow and valve position over the window. |
| [`heat_rate`](#heat_rate) Gas turbine heat rate against the OEM curves | Engineering practice | Fuel gas flow (Sm³/h); Output power (kW); Ambient temperature (°C) | OEM curves for the model and enough running time in the window. |
| [`motor_load`](#motor_load) Motor load against nameplate and service factor | Engineering practice | Motor power (kW); Nameplate rating (kW) | A motor power point and enough running time in the window. |
| [`rotating_health`](#rotating_health) Rotating machine health summary | ISO 14224 (rotating classes); each check that applies cites its own standard | The inputs of every check that applies to the machine's class (various) | At least one check able to run on the machine. |
| [`rod_load`](#rod_load) Reciprocating compressor rod load and reversal | API 618 | Cylinder suction and discharge pressure (barg); Rated rod load (kN) | Cylinder pressures and enough running time in the window. |
| [`valve_health`](#valve_health) Reciprocating compressor valve health | API 618 | Cylinder discharge temperatures (°C); Stage pressures (barg) | Two cylinder discharge temperatures on the same stage and enough running time. |
| [`compressor_trip`](#compressor_trip) Trip diagnosis from the first point to move | ISO 13379-1 (symptom to cause) | Run signal (kW or rpm); Every point on the train (various); Alarm and trip settings (various) | A trip of the train in the two days before the time asked about. |
| [`failed_starts`](#failed_starts) Failed start attempts | Engineering practice | Run signal (kW or rpm) | A run signal over the window. |
| [`protection`](#protection) Machinery protection channels against their settings | API 670 | Shaft vibration (µm); Axial position (mm); Bearing temperature (°C) | Protection channels on the machine and enough running time in the window. |
| [`seal_gas`](#seal_gas) Dry gas seal primary vent flow | API 692 | Primary vent flow (Nm³/h) | At least 14 running days of seal vent flow. |
| [`lube_oil`](#lube_oil) Lube oil header pressure and supply temperature | API 614 | Header pressure (barg); Supply temperature (°C) | A lube oil pressure or temperature point and enough running time. |
| [`gas_performance`](#gas_performance) Compressor stage performance on real-gas properties | ASME PTC 10 (Schultz polytropic analysis); ISO 5389 | Suction and discharge pressure (barg); Suction and discharge temperature (°C); Gas analysis (mol %) | A centrifugal stage and enough running time in the window. |
| [`power_balance`](#power_balance) Stage gas power against driver output | ASME PTC 10 | Stage gas power (kW); Gas turbine output power (kW) | An output power point on the driver and every stage measurable in the window. |
| [`exhaust_spread`](#exhaust_spread) Gas turbine exhaust temperature spread | Engineering practice | Exhaust thermocouples (°C) | Three exhaust thermocouples and enough running time in the window. |
| [`hot_section`](#hot_section) Fired hours against the hot section inspection interval | Engineering practice | Run signal (kW or rpm); Hot section inspection plan (h) | A hot section inspection plan with a last-done date and an interval. |
| [`engine_cylinders`](#engine_cylinders) Gas engine cylinder exhaust temperatures | Engineering practice | Cylinder exhaust temperatures (°C) | Cylinder exhaust temperatures and running time in the last two days. |
| [`remaining_life`](#remaining_life) Remaining life from the trend to its limit | ISO 13381-1 | A trended condition parameter (various); Its alarm or trip limit (various) | A trend on the machine that heads for a limit. |
| [`test_check`](#test_check) Latest well test against inflow and the lift curve | Engineering practice | Well tests with water cut (Sm³/d); Flowing and shut-in wellhead pressure (barg) | At least four oil well tests with a water cut, and a shut-in wellhead pressure. |
| [`deferment_attribution`](#deferment_attribution) Shortfall against decline, by cause | Engineering practice | Well tests (oil rate) (Sm³/d); Deferment records (Sm³) | At least one oil well in scope with enough tests for a decline fit. |
| [`vessel_loading`](#vessel_loading) Offered load against a rating | Engineering practice | Well test rates of the wells upstream (Sm³/d); Rating (the rating's own) | A rating on record and wells upstream of the item. |
| [`backlog_health`](#backlog_health) Open maintenance backlog by age, urgency and criticality | Engineering practice | Open work orders (created (orders) | At least one open order. |
| [`voidage_replacement`](#voidage_replacement) Water injected against the reservoir volume produced | Engineering practice | Allocated oil (Sm³); Water injected (Sm³); Well tests (oil rate (Sm³/d, %, Sm³/Sm³); Formation volume factors and solution GOR (rm³/Sm³, Sm³/Sm³) | A daily report, injection records and the four PVT values; without PVT the ratio is unknown, and with no injection there is no ratio. |
| [`hall_plot`](#hall_plot) An injector's Hall plot slope against its own earlier slope | Engineering practice | Monthly water injected (Sm³); Days on injection (days); Average wellhead injection pressure (barg) | Twice the comparison months of injection. |
| [`lead_in`](#lead_in) Process values in the hours before an event against the hours before that | Engineering practice | Historian readings on the asset's points (each point's own) | Readings both before and inside the lead-in. |
| [`data_sufficiency`](#data_sufficiency) Whether a series is enough to quote a result from | Engineering practice | Times of the readings (dates) | The times of the readings; each caller sets its own minimum points, span and largest gap. |
| [`forecast_range`](#forecast_range) A forecast with its prediction range | Engineering practice | The series the fit was made on (the series' own); Where to forecast (the fit's time axis) | At least three points and a sufficiency verdict that is quotable. |
| [`period_comparison`](#period_comparison) Each measurement in two windows, before and after a change | Engineering practice | Historian readings on the point or the unit's points (each point's own) | Enough readings in each window for the shared sufficiency check; a point without them is listed as not compared. |
| [`alarm_performance`](#alarm_performance) Alarm system performance over a window | ANSI/ISA-18.2 and EEMUA 191 | Alarm limits on each point and the historian readings that cross them (alarms) | Enabled alarm limits and readings over the window. |
| [`instrument_health`](#instrument_health) Whether a transmitter's readings can be trusted | Engineering practice | Historian readings on the point or every point on the unit (each point's own) | A week of readings before the window and readings inside it. |
| [`loop_oscillation`](#loop_oscillation) Whether a control loop is cycling, with its period and amplitude | Engineering practice | Historian readings on the unit's points or one point (each point's own) | A day or more of 5-minute readings, and at least four cycles in the window. |
| [`contactor_foaming`](#contactor_foaming) Contactor differential pressure against its clean level (foaming and fouling) | Engineering practice | Contactor differential pressure (bar); Treated gas analyser (optional) (mol% or mg/Sm³) | A week of differential pressure history before the last two days. |
| [`treated_gas`](#treated_gas) Treated gas analyser against its operating window | Engineering practice | Treated gas analyser (mol% or mg/Sm³); Operating window (same) | Seven days of analyser readings and an operating window on the point. |
| [`regenerator`](#regenerator) Reboiler temperature against its operating window | Engineering practice | Reboiler temperature (°C); Operating window (°C) | Seven days of temperature readings and an operating window on the point. |
| [`chiller`](#chiller) Low temperature separator temperature against its operating window | Engineering practice | Separator temperature (°C); Operating window (°C) | Seven days of temperature readings and an operating window on the point. |
| [`inspection_plan`](#inspection_plan) Condition inspection plan against the interval the wall loss supports | API 510 / API 570 | Inspection plan interval and dates (days); Wall thickness readings per CML (mm); Minimum required thickness (mm) | A condition inspection plan concerning the item. |
| [`allocation_factor`](#allocation_factor) Allocation factor of a meter against the well tests upstream of it | Engineering practice | Meter flow (Sm³/d or kSm³/d); Well tests upstream (oil rate (Sm³/d or kSm³/d) | A well test within the test age for the wells upstream, and days with the meter flowing. |
| [`meter_agreement`](#meter_agreement) A meter against the nearest other meter on the same stream | Engineering practice | Meter flow (Sm³/d or kSm³/d); Other meter's flow on the same stream (same) | Three days with both flowing in the window and seven in the month before it. |

## Refusals

| Name | Meaning |
|---|---|
| `missing_input` | A measurement, curve, rating or record the method needs is not on the asset. |
| `too_little_data` | The input is there, but too few readings, tests or running hours to support a result. |
| `not_applicable` | The method does not apply to this kind of asset. |
| `insufficient_inputs` | The inputs cannot support a result, for the reason given. |

## decline

**Latest well test against the Arps decline.** Arps, Analysis of Decline Curves (1945).

- **Valid for:** Wells on natural or artificial lift with a declining rate; the fit restarts after a step change. A well whose tests carry no oil is fitted on its gas rate.
- **Needs:** At least four well tests.
- **Test vector:** A rate of 100 falling 20 % a year, sampled yearly, fits back to qi 100 and D 0.2. (`arps`)

## gas_lift

**Lift gas against the economic optimum of the well's lift curve.** Engineering practice. Gas lift performance curve fitted to the well's own tests; the optimum is where one more unit of gas gains the breakeven oil.

- **Valid for:** Gas lifted wells with tests at three or more distinct gas rates.
- **Needs:** At least four well tests with a lift gas rate.
- **Threshold `lifting_mostly_water`:** Water cut at which lift gas is mostly lifting water. (70 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_checks_flag_the_okan_stories_as_of_then`, on the fake plant

## pump_curve

**Pump head against the OEM curve at the same flow.** Engineering practice. Differential head from suction and discharge pressure compared with the manufacturer's head curve.

- **Valid for:** Centrifugal pumps with an accepted OEM curve, while running.
- **Needs:** A minimum count of running readings in the recent window.
- **Threshold `head_loss`:** Head this far below the OEM curve at the same flow. (5 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_checks_flag_the_okan_stories_as_of_then`, on the fake plant

## choke

**Choke effective Cv against its characteristic.** Engineering practice. Effective flow coefficient from the tested rate and pressure drop, as a ratio to the trim's characteristic at the same opening.

- **Valid for:** Production chokes with a characteristic on record.
- **Needs:** Three or more readings since the trim was last replaced, else the latest readings before.
- **Worked example:** `tests/analysis/test_calcs.py::test_checks_flag_the_okan_stories_as_of_then`, on the fake plant

## reliability

**Weibull and Crow-AMSAA on the failure history.** IEC 61649 (Weibull analysis); IEC 61164 (reliability growth, power law).

- **Valid for:** Repairable units or a class of them with repeat failures.
- **Needs:** Three times between failures for Weibull; four failures for Crow-AMSAA.
- **Test vector:** Five times placed exactly on the median-rank line of a Weibull with beta 2 and eta 100 fit back to it. (`weibull`)
- **Test vector:** Failures at 400, 900, 1600 and 2500 days after the first follow N = 0.1 t^0.5 exactly. (`crow_amsaa`)

## pm_interval

**Preventive maintenance interval against the class's failure pattern.** IEC 61649 (Weibull analysis); IEC 60300-3-11 (reliability centred maintenance).

- **Valid for:** Equipment classes with preventive plans and repeat failures. Inspection plans are left out; a statutory interval is never proposed for change.
- **Needs:** Three times between failures in the class, else the class is reported as insufficient.
- **Test vector:** A wear-out class (beta 2) on a 200-day median interval at its characteristic life loses 1 - e^-1 of its units first. (`pm_pattern`)
- **Test vector:** With beta 1 failures are random in time, so a fixed interval cannot prevent them. (`pm_pattern`)
- **Test vector:** A wear-out class serviced past its characteristic life fails before its PM. (`pm_pattern`)

## spares_coverage

**Critical spares coverage, ranked by criticality and failure history.** ISO 14224 (equipment and failure data); ISO 55000 (criticality-based asset management).

- **Valid for:** Tagged units whose equipment carries a bill of materials. A unit with none is unknown, never covered; minimum stock and lead times are not in the records, so covered means one or more on the shelf.
- **Needs:** A bill of materials on the unit's installed equipment.
- **Test vector:** A-critical stock-outs come first, then other stock-outs, then units nobody can check, then covered ones. (`rank`)

## wall_loss

**Wall loss rate and remaining life per CML.** API 570 / API 510.

- **Valid for:** Vessels, piping and pipelines with repeat thickness surveys at the same locations.
- **Needs:** Wall thickness readings on the item.
- **Test vector:** 8.0 mm losing 0.5 mm a year reaches a 5.0 mm minimum in 6 years. (`remaining_life`)

## corrosion_circuit

**Corrosion circuit: long and short term rate, remaining life, next inspection.** API 570 7.1.

- **Valid for:** In-service lines of one service. The retirement thickness is the Barlow pressure design thickness (ASME B31.4 / B31.8) on an assumed API 5L X52 grade unless one is given.
- **Needs:** Wall thickness readings on a line of the circuit.
- **Threshold `retire_soon`:** Remaining life under this goes to the integrity engineer now. (5 years)
- **Threshold `longest_interval`:** The next inspection is at half the remaining life, at most this long after the last survey. (10 years)
- **Test vector:** 1.4 mm lost over six years is 0.233 mm/year long term; 0.4 mm in the last year governs. (`cml_rate`)
- **Test vector:** Barlow at 100 barg on 323.9 mm OD, X52 at a 0.72 design factor: 10 x 323.9 / (2 x 359 x 0.72). (`design_thickness`)

## well_constraint

**What limits a well (choke, back-pressure or lift).** Engineering practice. Critical or subcritical choke flow from the pressure ratio across the choke, with the header pressure and the lift curve.

- **Valid for:** Producing wells with a wellhead pressure and their jacket's header pressure.
- **Needs:** A production header pressure for the well's jacket.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## dp_drift

**Wellhead-to-header pressure drop drift.** Engineering practice. Trend of the flowline pressure drop between well tests; a sustained rise is a restriction building.

- **Valid for:** Producing wells over a window with no choke move or reroute.
- **Needs:** A production header pressure for the well's jacket.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## compressor_performance

**Compressor stage polytropic efficiency and head.** Engineering practice. Polytropic efficiency from suction and discharge pressure and temperature on ideal-gas properties, the last days against the window's first week.

- **Valid for:** Compressor stages with all four measurements, while running. Use gas_performance where real-gas properties matter.
- **Needs:** A suction temperature for the stage and enough running time in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## cooler_fouling

**Cooler outlet temperature against its clean baseline.** Engineering practice. Rise of the outlet temperature above the level after the last cleaning, and when it reaches the high limit at that rate.

- **Valid for:** Coolers with an outlet temperature point and steady duty.
- **Needs:** At least 14 days of outlet temperature.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## peers

**Units of a class side by side.** Engineering practice. Failures per year, MTBF, oil lost and vibration per unit, with outliers by robust z-score from the class median.

- **Valid for:** Equipment classes or models with several installed units.
- **Needs:** At least three units to compare.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## vibration

**Overall vibration against the evaluation zones.** ISO 20816-3 (formerly ISO 10816-3); ISO 20816-8 for reciprocating compressors.

- **Valid for:** Group 1 (300 kW to 50 MW) and group 2 (15 to 300 kW) machines on rigid or flexible foundations, while running.
- **Needs:** A vibration point in mm/s with enough readings in the window.
- **Test vector:** 5.0 mm/s on a group 1 machine on a rigid foundation is between 4.5 and 7.1, which is zone C. (`zone_of`)

## pump_bep

**Pump operating region against best efficiency point.** API 610 (preferred and allowable operating regions).

- **Valid for:** Centrifugal pumps whose OEM curve gives a best efficiency point.
- **Needs:** An OEM curve with a best efficiency point.
- **Threshold `off_bep_low`:** Below this share of best efficiency flow is outside the preferred region. (70 %)
- **Threshold `off_bep_high`:** Above this share of best efficiency flow is outside the preferred region. (120 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## surge

**Compressor surge margin against the OEM map.** Engineering practice. Suction flow over the surge flow at the running speed from the manufacturer's map, with recycle valve duty and head against the map.

- **Valid for:** Centrifugal stages with an OEM map that gives a rated speed and design point.
- **Needs:** An OEM map for the stage and enough running time in the window.
- **Threshold `near_surge`:** A fifth percentile surge margin under this is a surge risk. (5 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## surge_events

**Surge events from their signature.** Engineering practice. A collapse of suction flow with the anti-surge valve snapping open in the same minutes, while the train runs.

- **Valid for:** Centrifugal stages with a suction flow and an anti-surge valve position.
- **Needs:** Suction flow and valve position over the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## heat_rate

**Gas turbine heat rate against the OEM curves.** Engineering practice. Fuel energy per unit of output against the manufacturer's expected heat rate at the same load and ambient temperature.

- **Valid for:** Gas turbine drivers with OEM performance curves for the model.
- **Needs:** OEM curves for the model and enough running time in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## motor_load

**Motor load against nameplate and service factor.** Engineering practice. Median running power as a share of the nameplate rating, with hours above the rating and above the service factor.

- **Valid for:** Electric motor driven machines with a power point.
- **Needs:** A motor power point and enough running time in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## rotating_health

**Rotating machine health summary.** ISO 14224 (rotating classes); each check that applies cites its own standard.

- **Valid for:** Any rotating machine. Checks whose inputs are missing are listed as not checked.
- **Needs:** At least one check able to run on the machine.
- **Worked example:** `tests/analysis/test_condition.py::test_the_screen_raises_the_new_findings_with_their_remaining_life`, on the fake plant

## rod_load

**Reciprocating compressor rod load and reversal.** API 618.

- **Valid for:** Reciprocating compressors with cylinder geometry on record.
- **Needs:** Cylinder pressures and enough running time in the window.
- **Worked example:** `tests/analysis/test_recip.py::test_a_leaking_second_stage_valve_pushes_first_stage_rod_load_past_the_rating`, on the fake plant

## valve_health

**Reciprocating compressor valve health.** API 618.

- **Valid for:** Reciprocating compressors with two or more cylinders on a stage.
- **Needs:** Two cylinder discharge temperatures on the same stage and enough running time.
- **Worked example:** `tests/analysis/test_recip.py::test_the_leaking_cylinder_runs_hot_and_the_stage_ratios_shift`, on the fake plant

## compressor_trip

**Trip diagnosis from the first point to move.** ISO 13379-1 (symptom to cause).

- **Valid for:** Compression and pump trains with a run signal. Each point is judged against its own hours before the trip.
- **Needs:** A trip of the train in the two days before the time asked about.
- **Threshold `inspect_first`:** A second trip of the same family on the train within this: inspect, don't restart. (30 days)
- **Threshold `review`:** Any earlier trip on the train within this: ask reliability to review. (45 days)
- **Worked example:** `tests/analysis/test_trips.py::test_train_b_tripped_on_the_bearing_vibration_it_showed_for_hours`, on the fake plant

## failed_starts

**Failed start attempts.** Engineering practice. Starts that did not reach running within the start window, with the reason each one stopped.

- **Valid for:** Compression trains with a run signal.
- **Needs:** A run signal over the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## protection

**Machinery protection channels against their settings.** API 670.

- **Valid for:** Machines with protection channels and their alarm and trip settings on record, while running.
- **Needs:** Protection channels on the machine and enough running time in the window.
- **Threshold `trip_range`:** The range given around the date a channel reaches its trip. (90 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## seal_gas

**Dry gas seal primary vent flow.** API 692.

- **Valid for:** Compressors with dry gas seals and a vent flow point.
- **Needs:** At least 14 running days of seal vent flow.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## lube_oil

**Lube oil header pressure and supply temperature.** API 614.

- **Valid for:** Machines with a lube oil system and its alarm and trip settings on record.
- **Needs:** A lube oil pressure or temperature point and enough running time.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## gas_performance

**Compressor stage performance on real-gas properties.** ASME PTC 10 (Schultz polytropic analysis); ISO 5389.

- **Valid for:** Centrifugal compressor stages with a gas analysis for the service.
- **Needs:** A centrifugal stage and enough running time in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## power_balance

**Stage gas power against driver output.** ASME PTC 10.

- **Valid for:** Gas turbine driven centrifugal trains with every stage measurable.
- **Needs:** An output power point on the driver and every stage measurable in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## exhaust_spread

**Gas turbine exhaust temperature spread.** Engineering practice. Spread between exhaust thermocouples against its clean level, naming the thermocouple that wandered.

- **Valid for:** Gas turbines with three or more exhaust thermocouples, while running.
- **Needs:** Three exhaust thermocouples and enough running time in the window.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## hot_section

**Fired hours against the hot section inspection interval.** Engineering practice. Running hours since the last hot section inspection against the maintenance plan's interval.

- **Valid for:** Gas turbines with a hot section inspection plan that has a last-done date and an interval.
- **Needs:** A hot section inspection plan with a last-done date and an interval.
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## engine_cylinders

**Gas engine cylinder exhaust temperatures.** Engineering practice. Each cylinder's exhaust temperature against the others; a cold one misfires, a hot one is overloaded or lean.

- **Valid for:** Gas engine driven machines with a thermocouple per cylinder, while running.
- **Needs:** Cylinder exhaust temperatures and running time in the last two days.
- **Worked example:** `tests/analysis/test_recip.py::test_the_gas_engine_driver_has_a_misfiring_cylinder`, on the fake plant

## remaining_life

**Remaining life from the trend to its limit.** ISO 13381-1.

- **Valid for:** Rotating machines with a trend heading for a limit. The date carries a 90 % range.
- **Needs:** A trend on the machine that heads for a limit.
- **Worked example:** `tests/analysis/test_condition.py::test_the_screen_raises_the_new_findings_with_their_remaining_life`, on the fake plant

## test_check

**Latest well test against inflow and the lift curve.** Engineering practice. The test's liquid rate against the well's own inflow per bar of drawdown and, on a lifted well, its lift curve.

- **Valid for:** Oil wells with a shut-in wellhead pressure in the last two years.
- **Needs:** At least four oil well tests with a water cut, and a shut-in wellhead pressure.
- **Threshold `test_suspect`:** The test departs this far from the well's own inflow and choke: don't use it. (25 %)
- **Threshold `off_lift_curve`:** Oil this far below the lift curve at the test's lift gas. (25 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## deferment_attribution

**Shortfall against decline, by cause.** Engineering practice. Each well's latest test below its Arps decline, given one cause and summed by cause across the scope.

- **Valid for:** A jacket, area or facility with oil wells that have enough tests for a decline fit.
- **Needs:** At least one oil well in scope with enough tests for a decline fit.
- **Threshold `unexplained_shortfall`:** This share of the shortfall has no cause on record. (30 %)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## vessel_loading

**Offered load against a rating.** Engineering practice. What the wells upstream offer at their tested rates against the item's standing rating, in the rating's own units.

- **Valid for:** Separators, scrubbers and meters with a liquid, oil or gas rating and wells upstream on the flow network.
- **Needs:** A rating on record and wells upstream of the item.
- **Threshold `overloaded`:** Loading at or over the standing rating. (100 %)
- **Threshold `near_capacity`:** Loading close to the standing rating. (90 %)
- **Threshold `compared_with`:** How far back the loading is compared with. (90 days)
- **Worked example:** `tests/analysis/test_calcs.py::test_every_check_returns_evidence_and_names_its_users`, on the fake plant

## backlog_health

**Open maintenance backlog by age, urgency and criticality.** Engineering practice. Backlog ageing as maintenance planners read it; the shares are this registry's own.

- **Valid for:** Preventive and corrective orders in the work management system of record.
- **Needs:** At least one open order.
- **Run by:** `backlog_health`
- **Threshold `ageing`:** This share of open orders older than the ageing age: the backlog isn't being worked down. (25 %)
- **Threshold `ageing_age`:** The age past which an open order counts as ageing. (90 days)
- **Worked example:** `tests/analysis/test_backlog.py::test_backlog_health_flags_and_ranks`, on the fake plant

## voidage_replacement

**Water injected against the reservoir volume produced.** Engineering practice. Voidage replacement ratio as reservoir engineers screen waterfloods (Craig, The Reservoir Engineering Aspects of Waterflooding, SPE Monograph 3): injected water times its formation volume factor over produced oil, water and free gas at reservoir conditions. The formation volume factors and solution GOR are the field's own PVT.

- **Valid for:** Fields with water injection and producing oil wells with tests. Free gas from the producing GOR above the solution GOR; gas injection and aquifer influx are not counted.
- **Needs:** A daily report, injection records and the four PVT values; without PVT the ratio is unknown, and with no injection there is no ratio.
- **Run by:** `voidage_replacement`
- **Threshold `under`:** The latest months' ratio below this: the field is under-replacing what it produces. (0.9)
- **Threshold `over`:** Above this: injecting more than it produces. (1.1)
- **Threshold `months`:** The latest months with injection the verdict reads. (3 months)
- **Worked example:** `tests/analysis/test_water_injection.py::test_voidage_replacement_reads_injection_against_produced_reservoir_volume`, on the fake plant

## hall_plot

**An injector's Hall plot slope against its own earlier slope.** Engineering practice. Hall (1963), Analyze Injection Well Performance: cumulative pressure x time against cumulative injection is a straight line while the well takes water the same way; it bends up with plugging or skin and down with fracturing or channelling. Read here at the wellhead, so each injector against itself.

- **Valid for:** Water injectors with monthly injection records carrying a wellhead pressure.
- **Needs:** Twice the comparison months of injection.
- **Run by:** `injector_hall`
- **Threshold `months`:** The latest months whose slope is set against the first half of the injector's history. (3 months)
- **Threshold `rise`:** Latest slope over the earlier one above this: harder to inject (plugging or skin). (1.25)
- **Threshold `fall`:** Below this: easier to inject (a fracture opening or channelling). (0.8)
- **Worked example:** `tests/analysis/test_water_injection.py::test_the_plugging_injector_bends_its_hall_plot`, on the fake plant

## lead_in

**Process values in the hours before an event against the hours before that.** Engineering practice. A before and after comparison of each measurement's mean and extreme around the event.

- **Valid for:** Any measured point with readings either side of the lead-in.
- **Needs:** Readings both before and inside the lead-in.
- **Run by:** `process_values`
- **Threshold `lead_in`:** The stretch before the event that is compared with what came before it. (12 h)
- **Worked example:** `tests/sources/test_historian.py::test_live_ingest_loads_pi_and_adx_and_values_come_from_them`, on the fake plant

## data_sufficiency

**Whether a series is enough to quote a result from.** Engineering practice. One shared check (P4.6) on the readings behind a result, before any forecast or ranking is quoted; too few readings or too short a span is too little data, a long gap is quotable but thin.

- **Valid for:** Any time series, record list or set of tests a method projects or ranks from.
- **Needs:** The times of the readings; each caller sets its own minimum points, span and largest gap.
- **Run by:** `every forecast (fuzer.analysis.forecast:sufficiency)`
- **Threshold `trend_min_days`:** Daily values a condition trend needs before it projects a date to a limit. (7 days)
- **Threshold `trend_max_gap`:** A longer gap between a trend's daily values (a stop, an outage) makes it thin. (7 days)
- **Test vector:** Three readings where seven are needed is too little data, not a number. (`sufficiency`)
- **Test vector:** Seven readings with a 14 day hole are quotable, but thin, and say why. (`sufficiency`)

## forecast_range

**A forecast with its prediction range.** Engineering practice. The 90 % prediction interval of a least squares fit (Student t on the residual scatter, widening away from the data); for an Arps decline, on log rate, where the fit is made. A forecast is an estimate, never a plan.

- **Valid for:** A fit to three or more points; the range assumes the scatter about the fit stays as it was.
- **Needs:** At least three points and a sufficiency verdict that is quotable.
- **Run by:** `every forecast (fuzer.analysis.forecast:trend_interval, arps_interval)`
- **Test vector:** The line 1.4 + 0.8x at x = 6 is 6.2, and a new point there falls in 1.89 to 10.51 nine times in ten (t 2.353, s 1.095). (`trend_interval`)
- **Test vector:** The same decline through scattered tests forecasts 36.79 at year 5 within 33.2 to 40.7. (`arps_interval`)

## period_comparison

**Each measurement in two windows, before and after a change.** Engineering practice. Medians and the change, with Welch's t on the means and each window's sample cut to its effective size for autocorrelation (n (1 - r1) / (1 + r1)); each window through the shared sufficiency check first. A significant change after a fix does not prove the fix caused it.

- **Valid for:** Any measured point with readings in both windows; windows of similar operation compare best.
- **Needs:** Enough readings in each window for the shared sufficiency check; a point without them is listed as not compared.
- **Run by:** `compare_periods`
- **Threshold `significant`:** The p value under which a change is called significant. (0.05)
- **Threshold `min_readings`:** Readings each window needs before a point is compared. (5)
- **Threshold `window_gap`:** A gap this long inside a window makes the comparison thin. (1 days)
- **Worked example:** `tests/plots/test_views.py::test_compare_periods_and_similar_cases`, on the fake plant

## alarm_performance

**Alarm system performance over a window.** ANSI/ISA-18.2 and EEMUA 191.

- **Valid for:** Points with an enabled DCS or AF alarm limit; the alarm journal is each reading past a limit while the unit runs, until a real journal is loaded.
- **Needs:** Enabled alarm limits and readings over the window.
- **Run by:** `alarm_analysis`
- **Threshold `target_per_hour`:** Alarms per hour an operator can handle, about one every 10 minutes. (6)
- **Threshold `flood`:** Alarms in 10 minutes past which it is a flood. (10)
- **Threshold `standing`:** An alarm on this long is standing. (24 h)
- **Threshold `chattering`:** Alarms on one point, each within a minute of the last clearing, that make it chattering. (3)
- **Worked example:** `tests/analysis/test_alarms.py::test_alarm_analysis_reads_the_week`, on the fake plant

## instrument_health

**Whether a transmitter's readings can be trusted.** Engineering practice. Frozen, stuck, noisy, out of range and redundant transmitters drifting apart, each against the point's own last week; the spans and ratios are this registry's own.

- **Valid for:** Any measured point with a week of readings before the window.
- **Needs:** A week of readings before the window and readings inside it.
- **Run by:** `instrument_health`
- **Threshold `frozen`:** No change at all for this long: frozen (communications, a stale tag). (2 h)
- **Threshold `stuck`:** High-frequency spread far under its usual for this long without being frozen: sticking. (6 h)
- **Threshold `stuck_ratio`:** The share of its usual spread under which it counts as stuck. (0.1)
- **Threshold `noisy`:** Spread far over its usual for this long: hunting or a loose connection. (1 h)
- **Threshold `noisy_ratio`:** Times its usual spread over which it counts as noisy. (4)
- **Threshold `out_of_range`:** At or past the end of its range for this long. (30 min)
- **Threshold `drift`:** Redundant transmitters apart for this long. (2 h)
- **Worked example:** `tests/analysis/test_calcs.py::test_health_finds_the_hunting_level_transmitter`, on the fake plant

## loop_oscillation

**Whether a control loop is cycling, with its period and amplitude.** Engineering practice. Regularity of the autocorrelation's zero crossings (Thornhill, Huang and Zhang, 2003), with a check that the autocorrelation comes back one period on; the minimum cycles and that check are this registry's own.

- **Valid for:** A measurement a controller holds near one level (a level, temperature, pressure or flow), over a window of several cycles.
- **Needs:** A day or more of 5-minute readings, and at least four cycles in the window.
- **Threshold `regular`:** Regularity r over which the cycling is regular, not noise. (1)
- **Threshold `min_cycles`:** Cycles the window must hold before regularity is judged. (4)
- **Threshold `acf_peak`:** How high the autocorrelation must come back one period on. (0.4)
- **Test vector:** Half periods of 10, 10, 10, 11 and 9 samples: periods average 20 with a spread of 1.26, so r = 20 / 3.79 = 5.27, a regular cycle. (`regularity`)
- **Worked example:** `tests/analysis/test_loops.py::test_the_test_separator_level_cycles_while_its_valve_sticks`, on the fake plant

## contactor_foaming

**Contactor differential pressure against its clean level (foaming and fouling).** Engineering practice. GPSA Engineering Data Book (hydrocarbon treating and dehydration): foaming shows first as a sudden rise in column differential pressure, with carryover and off-spec gas; a slow climb is fouling or damaged packing.

- **Valid for:** Amine and glycol contactors with a differential pressure point, while gas flows through them.
- **Needs:** A week of differential pressure history before the last two days.
- **Threshold `foaming`:** Peak differential pressure as a share of the clean level, reached within a day, that reads as foaming. (150 %)
- **Threshold `fouling`:** The last week's median as a share of the clean level that reads as fouling. (125 %)
- **Worked example:** `tests/analysis/test_treating.py::test_the_amine_contactor_foamed_after_the_restart`, on the fake plant

## treated_gas

**Treated gas analyser against its operating window.** Engineering practice. GPSA Engineering Data Book; API 12GDU for glycol dehydration units. The limit is the site's recorded window (IOW, alarm settings or the learned range), not a number in the code.

- **Valid for:** Contactors with a treated gas analyser (acid gas after an amine column, water after a glycol one).
- **Needs:** Seven days of analyser readings and an operating window on the point.
- **Threshold `approaching`:** On course for the limit within this many days. (30 days)
- **Worked example:** `tests/analysis/test_treating.py::test_the_glycol_unit_lost_purity_as_its_reboiler_cooled`, on the fake plant

## regenerator

**Reboiler temperature against its operating window.** Engineering practice. GPSA Engineering Data Book; API 12GDU for glycol dehydration units: too cold leaves the solvent under-stripped, too hot degrades it. The window is the site's recorded one.

- **Valid for:** Amine, glycol and stabilizer reboilers with a temperature point.
- **Needs:** Seven days of temperature readings and an operating window on the point.
- **Threshold `drifting`:** On course for a limit within this many days. (30 days)
- **Worked example:** `tests/analysis/test_treating.py::test_the_glycol_unit_lost_purity_as_its_reboiler_cooled`, on the fake plant

## chiller

**Low temperature separator temperature against its operating window.** Engineering practice. GPSA Engineering Data Book (refrigeration, hydrocarbon dew point control): the separator temperature sets the dew point of the gas leaving it. The limit is the site's recorded window.

- **Valid for:** Chilled (low temperature) separators of a dew point control unit with a temperature point.
- **Needs:** Seven days of temperature readings and an operating window on the point.
- **Worked example:** `tests/analysis/test_treating.py::test_the_chiller_holds_the_dew_point`, on the fake plant

## inspection_plan

**Condition inspection plan against the interval the wall loss supports.** API 510 / API 570.

- **Valid for:** Pressure vessels, exchangers and piping with a condition inspection plan in the maintenance plans. The supported interval needs repeat thickness readings and a retirement thickness (recorded per CML, or the line's design).
- **Needs:** A condition inspection plan concerning the item.
- **Threshold `longest_interval`:** The supported interval is half the remaining life, at most this long. (10 years)
- **Worked example:** `tests/analysis/test_static_equipment.py::test_the_amine_contactor_plan_outlasts_its_wall`, on the fake plant

## allocation_factor

**Allocation factor of a meter against the well tests upstream of it.** Engineering practice. API MPMS Chapter 20.1 (allocation measurement): the measured volume at a reference meter divided by the sum of the wells' theoretical volumes (their latest tests, while flowing). Off one, the tests are stale or the meter reads wrong, and every well's allocated volume carries the error.

- **Valid for:** Meters on the allocation network with wells upstream and a flow point; oil for a liquid meter, formation gas (lift gas left out) for a gas meter. A meter downstream of processing carries shrinkage and fuel, so read it against its own previous factor.
- **Needs:** A well test within the test age for the wells upstream, and days with the meter flowing.
- **Threshold `band_low`:** Below this the meter reads low or the tests overstate the wells. (90 %)
- **Threshold `band_high`:** Above this the meter reads high or the tests understate the wells. (110 %)
- **Threshold `shift`:** Change in the factor since the window before (in points) that is flagged inside the band. (5 %)
- **Threshold `test_age`:** A well test older than this doesn't count towards a day's offer. (120 days)
- **Worked example:** `tests/analysis/test_metering.py::test_the_sales_meter_allocates_low_once_it_drifts`, on the fake plant

## meter_agreement

**A meter against the nearest other meter on the same stream.** Engineering practice. API MPMS Chapter 21.1 and 5 (flow measurement, meter proving and check metering): a custody meter is checked against a check meter on the same stream; the ratio of the two holds while both read true, and a step in it is one of them drifting.

- **Valid for:** Meters with another meter of the same phase within four steps up or down the allocation network.
- **Needs:** Three days with both flowing in the window and seven in the month before it.
- **Threshold `drift`:** Change in the two meters' daily ratio against its level over the month before that reads as one of them drifting. (2 %)
- **Threshold `baseline`:** The days before the window the ratio's level is taken over. (30 days)
- **Worked example:** `tests/analysis/test_metering.py::test_the_check_meter_sees_the_sales_meter_step`, on the fake plant
