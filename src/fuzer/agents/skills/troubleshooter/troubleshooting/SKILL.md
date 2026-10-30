---
name: troubleshooting
description: Method for troubleshooting a unit or incident (a trip, leak, high vibration, carryover, hydrate) - what happened, what the process values did, similar past incidents, lessons and manual sections, and the checks to make.
---

# Troubleshooting a unit or incident

1. **Find the unit and the incident.** Resolve the tag or well with `search` / `get_entity`. If the user
   names an event ("train B tripped last night"), list the unit's incidents with `get_incidents` and pick
   the one that matches; give the date you picked.
2. **One call for the picture:** `troubleshooting_brief(ref)` returns the incident timeline, similar past
   incidents and how they ended, approved lessons, manual / guide sections for the failure modes,
   maintenance history, downtime, spares in stock, suggested checks, and a process-value summary.
3. **Read the process values.** In the brief's `process_values.series`, each point has `lead_in`: the
   mean over the 12 h before the incident against the hours before that, and the extreme value.
   Look for what moved first and furthest (vibration and bearing temperature rising before a trip,
   discharge temperature climbing with a fouled cooler, suction pressure falling with a supply problem).
   For a wider or different window use `process_values(ref, at, hours_before, hours_after)`.
   Note the provider: `synthetic` values are not real plant data, so say so.
4. **History repeats.** Similar incidents with the same failure mode on the same unit mean the earlier
   fix didn't hold; say how long it held. `maintenance_history` shows what was done each time.
5. **Upstream and downstream.** A trip often starts elsewhere: `trace_flow(ref, "up")` finds what feeds
   the unit (separator carryover into a compressor scrubber, lost gas supply into lift gas).
   `get_events` on those units around the same time confirms or rules it out.
6. **Checks.** Recommend checks in order of likelihood and cost, each tied to the evidence and to a
   lesson or manual section where one exists (cite the section id and page). Include spares: say if
   the parts for the likely fix are in stock (`spare_parts`).

Report: what happened (timeline with times), the most likely cause and the evidence for it, other
causes not yet ruled out, checks to make, and anything that recurs.
