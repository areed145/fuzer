---
name: reliability
description: Reliability, maintenance and inspection - bad actors, MTBF by unit and ISO 14224 class, failure modes and repeat failures, PM and inspection compliance, overdue statutory inspections, and root-cause leads.
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
4. **Spares:** `spare_parts(ref)` shows stock for the unit's BOM parts; a bad actor with no spares in
   stock is a production risk.

Report: the top bad actors with failures, MTBF, downtime and repeat modes; the likely root cause per
unit with evidence; overdue inspections that carry risk; and recommended actions (redesign, condition
monitoring, PM interval change, spares holding).
