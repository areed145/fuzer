---
name: incident-evidence
description: Organize recorded incident and event evidence for accountable HSE review without making safety or compliance decisions.
---

# Incident evidence for HSE review

Use only recorded evidence:
1. `get_incidents` for the incident set in scope, then `incident_detail` for the linked timeline and source records.
2. `get_events` for dated operator-log entries and `get_upsets` / `upset_detail` for recorded process detections.
3. Use `similar_cases` only to identify past recorded incidents and their work history, not to assert the same cause.

Report the record IDs and event dates, distinguish event time from observation time, and state when incident,
corrective-action, or source coverage is incomplete. Do not infer a hazard, barrier state, exposure, compliance
status, regulatory obligation, or effectiveness of a corrective action from missing or indirect evidence.

This skill supports evidence organization only. Escalate safety-critical judgments, regulatory interpretation,
and emergency response to accountable people and approved procedures. Do not give response instructions.
