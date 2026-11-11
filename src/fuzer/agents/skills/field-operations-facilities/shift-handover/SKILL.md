---
name: shift-handover
description: Interpret a shift or period for handover - what changed in the records, what is still open, which alarms and excursions matter, and what to verify in the field.
---

# Shift handover

A handover says what changed in a stated window. Always give that window, and the time the records run to: the
data can lag the plant, so never call a record live or current.

1. `what_changed(since, until)` is the backbone: log events, notifications, work orders, downtime, incidents,
   equipment swaps, limit changes, excursions and new well tests. Keep each item's time and record id.
2. `get_events(ref)` for what operators wrote about a unit. An operator log entry is an observation, not a verified
   cause; quote what it says rather than upgrading it.
3. `get_daily_ops_report()` for the latest report. Name its production date; it is historical, not this shift.
4. `alarm_analysis(since, until)` when alarm load changed; `trip_analysis(since, until)` for stops and failed starts.
5. `limits(ref)` for a unit in excursion.
6. `records_mismatch(since, until)` before you close: downtime with no log entry or notification about the same
   plant, and logged stops with no downtime record. Give its tolerances and the time the logs run to. Put each one
   as a question for the shift ("was the 02:40 separator trip logged elsewhere?"), never as an accusation or proof
   that an event is missing; ones too recent to judge may still be written. Open ones are also on the ops lead
   queue and in the handover's Records to check.

**Write it as:** what changed, what is unresolved (open downtime, open notifications, repeat alarms), records that
don't agree, and what to verify in the field. Keep event time apart from the time the record was entered or loaded. If a window has no
records, say so; quiet records are not proof of a quiet plant.

You summarize records. The accountable shift supervisor owns the handover, and operators decide any field action.
