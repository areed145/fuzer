---
name: reliability
description: Reliability, maintenance and inspection - rotating equipment health (vibration zones, BEP, curves, failure fits), bad actors, MTBF by unit and ISO 14224 class, failure modes and repeat failures, PM and inspection compliance, overdue statutory inspections, and root-cause leads.
---

# Reliability, maintenance and inspection

This is the reliability and maintenance specialist pack: the playbook below, and `pack.yaml` beside it naming the
tools, methods, detectors, charts and measurement roles it uses and the eval it is held to. It is the fleet and history
view (ISO 14224 failure data, ISO 55000 and IEC 60300 practice); the compression, pumps-and-drivers and
static-equipment packs hold the condition analysis of one machine or one item.

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
5. **Pumps and drivers** (curves, where a pump runs, motor load, gas turbine and engine drivers): load the
   `pumps-and-drivers` skill and follow it.
   **Citing a fit:** `calc("reliability", ref, method="weibull" | "crow_amsaa")` returns beta against 1
   with its basis (`wear_out`, `early_life`, `failures_accelerating`); quote it rather than reading the
   number off a chart. `plot_reliability(ref)` charts the fit for a person.
   **Does the PM fit how it fails?** `calc("pm_interval", class or unit)` sets the class's median preventive plan
   interval against its Weibull characteristic life (eta): `random_failures` and `early_life` mean a fixed interval
   won't help (condition monitoring, or repair quality), `interval_too_long` that wear-out failures come before the
   PM; `fails_before_pm_pct` is the share expected to fail first. `insufficient_failures` is unknown, not fine.
   Inspection plans are left out, and a statutory interval is never proposed for change.
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
   - **Pumps, motors and drivers** (BEP, motor load, exhaust spread, hot section, engine cylinders, heat rate):
     load the `pumps-and-drivers` skill and follow it.
   - **Compressors** (surge, reciprocating rod load and valves, API 670 protection channels, surges, dry gas
     seals, lube oil and real-gas performance): load the `compression` skill and follow it.
   - **Will it make the window?** `calc("remaining_life", ref)` gathers every trend heading for a limit (zone D, the
     protection trips, the seal vent's high-high, the lube oil trip, the inspection hours), each with a 90 % range
     (ISO 13381-1), and sets the earliest against the next turnaround or shutdown. `fails_before_window` means act
     now or pull the window forward; `tight` means only the early end of the range misses it. Quote the date, the
     range and the window, never a bare "remaining life".
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
   stock is a production risk. `calc("spares_coverage", area, class or unit)` ranks the units in scope:
   stock-outs first, A-critical before B and C, then the most failures (`critical_stock_out` when an A unit
   lacks a part). A unit with no bill of materials is `unknown`, never covered. "Covered" means one or more on
   the shelf, not enough: minimum stock and lead times are not recorded. A stock count is not a reservation;
   ordering stays with supply chain.
9. **What it costs:** `maintenance_spend(by)` gives work order cost by unit, ISO 14224 class or order type, per
   currency, with the orders that carry no cost counted. `cost_of_unreliability()` ranks units by the oil lost to the
   downtime they caused (at the pack's price) beside their corrective order cost. Quote the price and currency with
   every number. Never add an unknown currency to a known one: when the source states no currency, say the cost is
   in an unknown currency. Both are operational estimates, not accounting figures.

Report: the top bad actors with failures, MTBF, downtime and repeat modes; the likely root cause per
unit with evidence; overdue inspections that carry risk; and recommended actions (redesign, condition
monitoring, PM interval change, spares holding).
