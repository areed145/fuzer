---
name: reliability
description: Reliability, maintenance and inspection - rotating equipment health (vibration zones, BEP, curves, failure fits), bad actors, MTBF by unit and ISO 14224 class, failure modes and repeat failures, PM and inspection compliance, overdue statutory inspections, and root-cause leads.
---

# Reliability, maintenance and inspection

**Definitions.** A failure is a corrective SAP notification (ISO 14224 failure mode and mechanism).
MTBF = window length / failures, per unit; per class = window x units with failures / failures.
A repeat failure is the same failure mode on the same unit more than once in the window.

1. **Bad actors:** `bad_actors(since, until)` ranks units by downtime caused and failures, with MTBF,
   repeat failure modes and the class MTBF table. Units that both fail often and cause deferment come first.
2. **Why it fails:** `maintenance_history(ref)` shows notifications with failure modes and mechanisms and
   the work orders that followed. A repeat after the same fix means the fix treated a symptom.
   `lessons_learned(ref)` has reviewed lessons for the model or class; `search_knowledge` finds OEM
   manual sections on the failure mode.
3. **Compliance:** `maintenance_compliance(as_of, horizon_days)` gives on-time % for PM and INSP plans,
   overdue tasks (statutory inspections are the serious ones) and what is due soon. Tasks marked
   `needs_shutdown` can only be done in a turnaround.
4. **Wall thickness:** `measurements(ref, "wall_thickness")` on a vessel, piping tag or pipeline gives
   each CML's readings (mm; older surveys were converted from inches) and `per_year`, the fitted wall
   change per year (negative = loss). Compare CMLs on the same item and items in the same service: one
   losing several times faster than its neighbours points at a changed condition (water, sand, flow
   regime) worth tying to well tests or lab results. **For a pipeline, find what flows through it:**
   `get_entity(pipeline)` shows where it starts (`starts_at`, a jacket or area); the wells there feed it.
   `well_performance()` lists water cut risers across the asset: a well on that jacket whose water cut
   jumped (breakthrough) makes its flowline wetter and more corrosive, and `measurements(well)` shows its
   chloride. Name the well and the date its water cut rose, against when the line's loss sped up. If
   nothing upstream changed, say the cause isn't in the records. `calc("corrosion_circuit", pipeline)` rolls
   the line up with the others in its service: rate, remaining life and next inspection date per line (see the
   integrity skill). Its retirement thickness is estimated from the MOP on an assumed grade unless one is given;
   say so. With the design minimum, `calc("wall_loss", ref, min_required_mm=...)` gives the fastest CML against
   the item's median and the remaining life per CML (`retire_soon`, `localised`).
   Integrity operating windows (IOWs) are the process side of this: `limits(ref)` lists a unit's IOWs
   (class, limits, time allowed outside) and excursions past them; a breach past its allowed time
   already has an `iow_breach` work item. Repeated breaches explain faster wall loss or seal damage.
5. **Pumps against their curve:** `plot_pump_curve(ref)` puts PI operating points on the OEM curve.
   Head falling below the curve month by month at the same flow means internal wear (wear rings,
   impeller): quote `head_vs_curve_by_month` and the month it started. Running far from the BEP
   (outside the preferred region) shortens seal and bearing life; say how much time was spent there.
   `equipment_curves(ref)` gives the curve itself; `calc("pump_curve", ref)` the latest month's head
   against the curve as one number (`head_loss` at 5 % below).
   **Citing a fit:** `calc("reliability", ref, method="weibull" | "crow_amsaa")` returns beta against 1
   with its basis (`wear_out`, `early_life`, `failures_accelerating`); quote it rather than reading the
   number off a chart.
6. **Equipment performance.** `calc("compressor_performance", stage)` gives a stage's polytropic
   efficiency against its first week (`efficiency_drop`: fouling, wear, recycle through a hot cooler);
   `calc("cooler_fouling", cooler)` the outlet temperature above its clean baseline and when it reaches its
   limit; `calc("peers", ref)` the class's units side by side, an outlier being a bad unit and
   `class_wide_failures` a problem with the model or its service. Charts: `plot_compressor_performance`,
   `plot_cooler_fouling`, `plot_peers`.
7. **Rotating equipment health (start here for "how is this pump / compressor").** When the question names no
   machine ("which machines need attention?", "anything wrong with the compressors?"), start with
   `rotating_screen(at)`: every pump, compressor, gas turbine and motor through the same checks, `act` first.
   Take the date from the question. Then run `calc("rotating_health", ref)` on the ones it names.
   `calc("rotating_health", ref)` runs every check that applies and returns a status (`act`, `watch`, `ok`),
   each finding with its basis, data dates and confidence, what couldn't be checked and why, and
   recommendations. Then read the findings in this order, because it is the order an experienced machinery
   engineer trusts them:
   - **Vibration** (`calc("vibration", ref)`): overall mm/s RMS against ISO 20816-3 zones for the machine's
     group (formerly ISO 10816-3; group 1: compressors, turbines and large machines; group 2: most pumps). Zone D means damage is
     likely: recommend stopping to inspect. Zone C is not for long-term running: plan the repair. A steady
     rise (`rising`, `trend_mm_s_per_week`, `reaches_zone_d`) matters even in zone B. Quote the zone, the
     value, the trend and the date it reaches zone D. Overall vibration cannot tell a bearing from
     misalignment: spectra need a condition-monitoring source Fuzer doesn't have, so say so rather than
     naming a defect frequency.
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
   - **Compressors** (surge, reciprocating rod load and valves, API 670 protection channels, surges, dry gas
     seals, lube oil and real-gas performance): load the `compression` skill and follow it.
   - **Drivers**: `calc("exhaust_spread", ref)` names the exhaust thermocouple that wandered when the spread widens
     (a lean or hot combustor, or the thermocouple itself: check it before the borescope); `calc("hot_section",
     ref)` counts fired hours since the last hot section inspection against the plan's interval and gives the due
     date at the recent running rate; `calc("engine_cylinders", ref)` finds a misfiring (cold) or hot cylinder on a
     gas engine driver.
   - **Will it make the window?** `calc("remaining_life", ref)` gathers every trend heading for a limit (zone D, the
     protection trips, the seal vent's high-high, the lube oil trip, the inspection hours), each with a 90 % range
     (ISO 13381-1), and sets the earliest against the next turnaround or shutdown. `fails_before_window` means act
     now or pull the window forward; `tight` means only the early end of the range misses it. Quote the date, the
     range and the window, never a bare "remaining life".
   - **Heat rate** (gas turbine drivers, `calc("heat_rate", ref)`): fuel energy per kWh of output against the
     OEM curves at the same load and ambient temperature, so a hot afternoon or part load isn't mistaken for a
     fault. Excess rising a fraction of a percent a week is the air compressor fouling: an offline water wash
     recovers most of it. A step, or an excess a wash doesn't recover, points at the hot section: recommend a
     borescope. Quote the excess, its trend and when it started. The fuel's heating value is the design value,
     not a measured one, so a few percent either way is within the method.
   - **Performance**: `pump_curve` (head below the curve at the same flow means internal wear) or
     `compressor_performance` (an efficiency drop means fouling, wear or recycle).
   - **Failure history**: Weibull beta (`wear_out` suits a PM interval, `early_life` points at repair
     quality, random failures mean PM won't help) and Crow-AMSAA (`failures_accelerating` marks a bad actor).
     Fewer than about six failures make a fit indicative only: say "low confidence".
   - **Peers**: an outlier is the unit; `class_wide_failures` is the model or its service.
   **Already in the queue:** a machine the daily screen flags on its condition (not on failure history alone)
   is a `machine_health` case for the engineer, priority 2 when a severe flag drives it; it clears when the
   screen stops flagging it. Say when the machine you are asked about already has one; its evidence carries the
   remaining life and whether it makes the next window.
   **When not to trust a result:** a curve from a datasheet waiting for review; vibration with few readings; a
   pump that hardly ran in the window; a synthetic or demo source (say it is demo data). Escalate to the IOC
   when vibration is in zone D or rising into it within two weeks, when a compressor runs near surge or has surged, when a thrust bearing, dry gas seal or lube oil system is
   past its alarm, when a machine won't make the next window, when a motor runs above its service factor, or when head loss or low-flow running
   coincides with a repeat failure mode. A good answer names the machine, the status, the two or three
   findings that drive it with their numbers and dates, what is unknown, and the recommendation.
8. **Spares:** `spare_parts(ref)` shows stock for the unit's BOM parts; a bad actor with no spares in
   stock is a production risk.

Report: the top bad actors with failures, MTBF, downtime and repeat modes; the likely root cause per
unit with evidence; overdue inspections that carry risk; and recommended actions (redesign, condition
monitoring, PM interval change, spares holding).
