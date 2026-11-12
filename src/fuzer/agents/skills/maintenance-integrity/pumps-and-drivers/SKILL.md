---
name: pumps-and-drivers
description: Pumps and their drivers - head against the OEM curve, where a pump runs against its best efficiency point, motor load against the nameplate, casing vibration, and gas turbine and gas engine driver checks (exhaust spread, hot section hours, cylinders, heat rate), for any pump or driver (ISO 14224 classes PU, EM, GT, CE).
---

# Pumps and drivers

This is the pumps and drivers specialist pack: the playbook below, and `pack.yaml` beside it naming the tools,
methods, detectors, charts and measurement roles it uses and the eval it is held to.

**Start here.** With no machine named ("which pumps need attention?"), run `rotating_screen(at)` and take the pumps
and drivers it puts first. For one machine, `calc("rotating_health", ref)` runs every check that applies; read its
findings with the reliability skill's order (vibration first: `calc("vibration", ref)`, charted with
`plot_vibration`), then go deeper with the checks below. Quote each check's basis, the numbers and dates, and what
could not be checked.

**Against the curve.** `plot_pump_curve(ref)` puts PI operating points on the OEM curve.
Head falling below the curve month by month at the same flow means internal wear (wear rings,
impeller): quote `head_vs_curve_by_month` and the month it started. Running far from the BEP
(outside the preferred region) shortens seal and bearing life; say how much time was spent there.
`equipment_curves(ref)` gives the curve itself; `calc("pump_curve", ref)` the latest month's head
against the curve as one number (`head_loss` at 5 % below).

- **Where it runs** (pumps, `calc("pump_bep", ref)`): flow against the best efficiency point. Time below the
  minimum stable flow (`low_flow`) causes recirculation, which kills seals and bearings: it explains
  repeat seal failures better than the seal does. Time outside 70-120 % of BEP (`off_bep`) shortens life.
- **Motor load** (motor-driven machines, `calc("motor_load", ref)`): measured motor power against the
  nameplate's rated kW and service factor, both on the driven unit's SAP equipment record, so a swapped pump
  brings its own motor's rating. Running above the service factor (`motor_overload`) spends insulation life:
  act on it. Above the rating but inside the service factor (`motor_high`) leaves no margin for a hot day or
  a heavier fluid. A pump that suddenly draws more for the same export is running out along its curve: if its
  flow is above what the export or header meter sees, a minimum-flow recirculation valve is passing, and
  `pump_bep` will show it off its BEP at the same time. Quote the load as a % of rating, the hours above the
  service factor and when they started. A machine with no rating on record says so: ask for the motor
  datasheet rather than assuming one.
- **Drivers**: `calc("exhaust_spread", ref)` names the exhaust thermocouple that wandered when the spread widens
  (a lean or hot combustor, or the thermocouple itself: check it before the borescope); `calc("hot_section",
  ref)` counts fired hours since the last hot section inspection against the plan's interval and gives the due
  date at the recent running rate; `calc("engine_cylinders", ref)` finds a misfiring (cold) or hot cylinder on a
  gas engine driver.
- **Heat rate** (gas turbine drivers, `calc("heat_rate", ref)`): fuel energy per kWh of output against the
  OEM curves at the same load and ambient temperature, so a hot afternoon or part load isn't mistaken for a
  fault. Excess rising a fraction of a percent a week is the air compressor fouling: an offline water wash
  recovers most of it. A step, or an excess a wash doesn't recover, points at the hot section: recommend a
  borescope. Quote the excess, its trend and when it started. The fuel's heating value is the design value,
  not a measured one, so a few percent either way is within the method.

**Already in the queue.** A pump or driver the daily screen flags on its condition is a `machine_health` case for
the engineer. Say when the machine you are asked about already has one.

**Escalate to the IOC** when vibration is in zone D or rising into it within two weeks, when a motor runs above its
service factor, or when head loss or low-flow running coincides with a repeat failure mode. A good answer names the
machine, the status, the two or three findings that drive it with their numbers and dates, what is unknown, and the
recommendation. Spectra need a condition-monitoring source Fuzer doesn't have yet: say so rather than naming a
defect frequency.
