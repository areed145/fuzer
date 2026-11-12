---
title: Methods
description: Every calculation an agent or a detector can call, with the standard behind it.
---

Generated from the method registry (`src/fuzer/standards/methods.yaml`); don't edit by hand (`uv run python -m fuzer.reference`).

A result names its method, and a method that cannot answer refuses by name instead of estimating. Standards are cited as the calculation applies them: check clauses against your licensed copy.

| Method | Standard or practice | Inputs | Needs |
|---|---|---|---|
| [`decline`](#decline) Latest well test against the Arps decline | Arps, Analysis of Decline Curves (1945) | Well tests (oil rate) (Sm³/d) | At least four well tests. |
| [`gas_lift`](#gas_lift) Lift gas against the economic optimum of the well's lift curve | Engineering practice | Well tests (oil rate) (Sm³/d); Lift gas rate (kSm³/d) | At least four well tests with a lift gas rate. |
| [`pump_curve`](#pump_curve) Pump head against the OEM curve at the same flow | Engineering practice | Flow (m³/h); Suction and discharge pressure (barg); OEM head curve (m) | A minimum count of running readings in the recent window. |
| [`choke`](#choke) Choke effective Cv against its characteristic | Engineering practice | Choke opening (%); Upstream and downstream pressure (barg); Well test rates (Sm³/d) | Three or more readings since the trim was last replaced, else the latest readings before. |
| [`reliability`](#reliability) Weibull and Crow-AMSAA on the failure history | IEC 61649 (Weibull analysis); IEC 61164 (reliability growth, power law) | Failure notifications (dates) | Three times between failures for Weibull; four failures for Crow-AMSAA. |
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
| [`lead_in`](#lead_in) Process values in the hours before an event against the hours before that | Engineering practice | Historian readings on the asset's points (each point's own) | Readings both before and inside the lead-in. |
| [`data_sufficiency`](#data_sufficiency) Whether a series is enough to quote a result from | Engineering practice | Times of the readings (dates) | The times of the readings; each caller sets its own minimum points, span and largest gap. |
| [`forecast_range`](#forecast_range) A forecast with its prediction range | Engineering practice | The series the fit was made on (the series' own); Where to forecast (the fit's time axis) | At least three points and a sufficiency verdict that is quotable. |
| [`period_comparison`](#period_comparison) Each measurement in two windows, before and after a change | Engineering practice | Historian readings on the point or the unit's points (each point's own) | Enough readings in each window for the shared sufficiency check; a point without them is listed as not compared. |

## Refusals

| Name | Meaning |
|---|---|
| `missing_input` | A measurement, curve, rating or record the method needs is not on the asset. |
| `too_little_data` | The input is there, but too few readings, tests or running hours to support a result. |
| `not_applicable` | The method does not apply to this kind of asset. |
| `insufficient_inputs` | The inputs cannot support a result, for the reason given. |

## decline

**Latest well test against the Arps decline.** Arps, Analysis of Decline Curves (1945).

- **Valid for:** Wells on natural or artificial lift with a declining rate; the fit restarts after a step change.
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

- **Valid for:** Compression trains with a run signal. Each point is judged against its own hours before the trip.
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
