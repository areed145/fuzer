---
name: troubleshooting
description: Method for troubleshooting a unit or incident (a trip, leak, high vibration, carryover, hydrate) - what happened, what the process values did, similar past incidents, lessons and manual sections, and the checks to make.
---

# Troubleshooting a unit or incident

1. **Find the unit and the incident.** Resolve the tag or well with `search` / `get_entity`. If the user
   names an event ("train B tripped last night"), list the unit's incidents with `get_incidents` and pick
   the one that matches; give the date you picked. For "what happened since ..." without a unit, start
   with `what_changed(since)`: log events, notifications, work orders, downtime, incidents, equipment
   swaps, limit changes and excursions in the window. `get_upsets(since)` lists what the SRE found in
   the process data itself, grouped by cause, and `upset_detail(id)` shows its detections in time order,
   the first being the likely first cause.
   **A compressor trip:** `calc("compressor_trip", ref, until=<around the trip>)` on any machine of the train
   rebuilds the sequence of events: the run signal's fall, each point's departure from its own last day, the
   first to move (`first_out`, with how many minutes before) and its cause family (vibration, discharge
   temperature, loss of cooling, liquid carryover, surge, driver, suction pressure; `none` = no precursor: an ESD,
   a power dip or a spurious trip, so check the DCS first-out and the transmitter). `tripped_on` lists the points
   at their trip setting. Quote the restart gate as given: `verdict` `inspect_first` (a second trip of the same
   family within 30 days: inspect, don't restart), `review` (another trip within 45 days) or
   `restart_after_checks`, and the `checklist`. The IOC already has the trip as a `machine_trip` case with the same
   checklist, joined to the upset's case.
2. **One call for the picture:** `troubleshooting_brief(ref)` returns the incident timeline, similar past
   incidents and how they ended, approved lessons, manual / guide sections for the failure modes,
   maintenance history, downtime, spares in stock, suggested checks, and a process-value summary.
3. **Read the process values.** In the brief's `process_values.series`, each point has `lead_in`: the
   mean over the 12 h before the incident against the hours before that, and the extreme value.
   Look for what moved first and furthest (vibration and bearing temperature rising before a trip,
   discharge temperature climbing with a fouled cooler, suction pressure falling with a supply problem).
   For a wider or different window use `process_values(ref, at, hours_before, hours_after)`.
   Note the provider: `synthetic` values are not real plant data, so say so.
   Each series carries its `limits` (alarm hi / lo, IOW windows, or a learned normal range): say which
   limit a value crossed rather than calling it "high". `limits(ref)` adds each source's values, the
   checks that fail (a disabled or standing alarm, AF and DCS disagreeing) and recorded excursions;
   excursions only count while the unit runs, so a stopped unit's low values are not one.
   The brief's `measurements` are the sparse readings PI doesn't have: lab results at the unit's
   sample points (oil in water, BS&W, H2S, dewpoint), well tests, wall thickness. A lab result that
   stepped up weeks before the incident (more oil in the water, a wetter crude) often explains it;
   `measurements(ref, quantity, history=true)` gives the full record.
   For a pump or a well's choke, compare it with its OEM curve: `plot_pump_curve` (lost head, running
   off the BEP), `plot_choke` (eroded or restricted trim); `calc("pump_curve" | "choke", ref)` gives the
   same as one number against expected, with its basis, to cite.
   **Can the reading be trusted?** Before resting a cause on one transmitter, run
   `instrument_health(ref)` over the window: a noisy (hunting), frozen, stuck or out-of-range reading,
   or A / B transmitters drifting apart, is an instrument problem, not the process. A level transmitter
   hunting while the real level runs high is a classic way to carry liquid over; say which it was.
   **When did it change?** `detect_changes(ref, since, until)` gives the steps, drifts and jitter changes
   on each point with their start times; the earliest change on the unit or upstream is usually nearer
   the cause than the trip. `plot_changes` / `plot_instrument_health` chart them.
   **How it spread.** `plot_state_timeline(since, until, scope)` shows each unit's state as swimlanes: which
   stopped first and what followed. `alarm_analysis` gives the alarm load (floods, chattering, standing
   alarms, bad actors); `trip_analysis` the stops, starts and failed starts per unit.
   **Is the vessel overloaded?** Carryover and level upsets follow load: `calc("vessel_loading", tag)` gives a
   separator's or scrubber's loading against its standing rating and how it moved over 90 days. A vessel near or
   over its rating (`near_capacity`, `overloaded`) makes carryover a load problem first and an instrument problem
   second.
4. **History repeats.** Similar incidents with the same failure mode on the same unit mean the earlier
   fix didn't hold; say how long it held. `maintenance_history` shows what was done each time.
5. **Upstream and downstream.** A trip often starts elsewhere: `trace_flow(ref, "up")` finds what feeds
   the unit (separator carryover into a compressor scrubber, lost gas supply into lift gas).
   For a gas lifted well that lost rate or died, trace its lift gas supply:
   `trace_flow(well, "up", network="lift_gas")` (manifold, any jacket it is fed through, header,
   compression) and `gas_lift_network(ref=well)`; then check the header pressure (`process_values`
   on 20-/30-/40-PT-3003) and the well's injection rate and casing pressure (GLIR, CASP) around the
   time. Header pressure falling = supply problem (compression trip, bypass); header fine but GLIR up,
   CASP down, oil down = the well's gas lift valve failed open.
   `get_events` on those units around the same time confirms or rules it out.
6. **Checks.** Recommend checks in order of likelihood and cost, each tied to the evidence and to a
   lesson or manual section where one exists (cite the section id and page). Include spares: say if
   the parts for the likely fix are in stock (`spare_parts`).

**Escalate** a unit still running on the condition that tripped it, a repeat of a failure mode whose fix didn't
hold, or a safety-related instrument that can't be trusted. **Don't trust** synthetic process values as plant
evidence, one transmitter without `instrument_health`, or a cause whose first change came after the trip.

Report: what happened (timeline with times), the most likely cause and the evidence for it, other
causes not yet ruled out, checks to make, and anything that recurs.
