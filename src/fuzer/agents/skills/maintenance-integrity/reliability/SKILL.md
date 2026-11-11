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
   nothing upstream changed, say the cause isn't in the records. Remaining life needs the minimum required
   thickness from the design, which Fuzer doesn't hold; say so rather than guessing it. With it,
   `calc("wall_loss", ref, min_required_mm=...)` gives the fastest CML against the item's median and the
   remaining life per CML (`retire_soon`, `localised`).
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
   - **Vibration** (`calc("vibration", ref)`): overall mm/s RMS against ISO 10816-3 zones for the machine's
     group (group 1: compressors, turbines and large machines; group 2: most pumps). Zone D means damage is
     likely: recommend stopping to inspect. Zone C is not for long-term running: plan the repair. A steady
     rise (`rising`, `trend_mm_s_per_week`, `reaches_zone_d`) matters even in zone B. Quote the zone, the
     value, the trend and the date it reaches zone D. Overall vibration cannot tell a bearing from
     misalignment: spectra need a condition-monitoring source Fuzer doesn't have, so say so rather than
     naming a defect frequency.
   - **Where it runs** (pumps, `calc("pump_bep", ref)`): flow against the best efficiency point. Time below the
     minimum stable flow (`low_flow`) causes recirculation, which kills seals and bearings: it explains
     repeat seal failures better than the seal does. Time outside 70-120 % of BEP (`off_bep`) shortens life.
   - **Surge** (centrifugal compressor stages, `calc("surge", ref)`): surge margin from the suction flow and
     the driver's speed against the machine's own OEM map, and how often the anti-surge valve is open. A 5th
     percentile margin under 5 % (`near_surge`) is a surge risk: surge reverses flow through the wheels and can
     wreck the thrust bearing and seals in seconds, so act on it. A valve open much of the time (`recycling`)
     is safe but burns fuel compressing gas twice. Compare `siblings` (the same stage on the other trains): one
     train starved while the others run full is a load-sharing problem the IOC can fix by moving gas between
     trains, not a machine fault. Maps are issued per machine; a stage without one says so, and then only the
     anti-surge valve's position tells you anything.
   - **Performance**: `pump_curve` (head below the curve at the same flow means internal wear) or
     `compressor_performance` (an efficiency drop means fouling, wear or recycle).
   - **Failure history**: Weibull beta (`wear_out` suits a PM interval, `early_life` points at repair
     quality, random failures mean PM won't help) and Crow-AMSAA (`failures_accelerating` marks a bad actor).
     Fewer than about six failures make a fit indicative only: say "low confidence".
   - **Peers**: an outlier is the unit; `class_wide_failures` is the model or its service.
   **When not to trust a result:** a curve from a datasheet waiting for review; vibration with few readings; a
   pump that hardly ran in the window; a synthetic or demo source (say it is demo data). Escalate to the IOC
   when vibration is in zone D or rising into it within two weeks, when a compressor runs near surge, or when head loss or low-flow running
   coincides with a repeat failure mode. A good answer names the machine, the status, the two or three
   findings that drive it with their numbers and dates, what is unknown, and the recommendation.
8. **Spares:** `spare_parts(ref)` shows stock for the unit's BOM parts; a bad actor with no spares in
   stock is a production risk.

Report: the top bad actors with failures, MTBF, downtime and repeat modes; the likely root cause per
unit with evidence; overdue inspections that carry risk; and recommended actions (redesign, condition
monitoring, PM interval change, spares holding).
