---
name: incident-evidence
description: Organize recorded incident, event and corrective-work evidence for accountable HSE review - timelines, investigation records, corrective-action status, past lessons and recorded flare - without making safety or compliance decisions.
---

# Incident evidence for HSE review

Start from the incident record and build outward, in time order. Escalate at once anything that reads as ongoing
(a release, a fire, a person hurt): that is the orchestrator's emergency path, not an evidence review. Don't trust
a timeline built from observation timestamps alone, or a corrective action whose order is closed but whose readings
never changed.

Use only recorded evidence:
1. `get_incidents` for the incident set in scope, then `incident_detail` for the linked timeline and source records.
2. `get_events` for dated operator-log entries and `get_upsets` / `upset_detail` for recorded process detections.
3. Use `similar_cases` only to identify past recorded incidents and their work history, not to assert the same cause.
4. Build the timeline with `plot_incident(ref)` for one incident, `plot_propagation(upset_id)` for how an upset spread,
   and `plot_state_timeline(since, until)` for unit states. `alarm_analysis`, `trip_analysis` and `downtime` add what
   the alarms, stops and lost production recorded in the same window.
5. Track corrective work: `maintenance_history(ref)` for notifications and orders on the unit and their status,
   `compare_periods(ref, before_since, before_until, after_since, after_until)` for whether readings changed afterwards, and `lessons_learned()` for
   past fixes. Order status says what was recorded, not that the corrective action worked. `record_history(ref)`
   shows whether a source record was later edited.
6. `plot_flare(since, until)` shows recorded daily flare from DAY001 with an estimated CO2 per barrel. It is
   evidence for a review, not an emissions inventory or a compliance figure.

Report the record IDs and event dates, distinguish event time from observation time, and state when incident,
corrective-action, or source coverage is incomplete. Do not infer a hazard, barrier state, exposure, compliance
status, regulatory obligation, or effectiveness of a corrective action from missing or indirect evidence.

Fuzer has no hazard or barrier register, permits, regulatory obligations or emergency response plans. When asked
for one, say it is unavailable and who holds it. This skill supports evidence organization only. Escalate safety-critical judgments, regulatory interpretation,
and emergency response to accountable people and approved procedures. Do not give response instructions.
