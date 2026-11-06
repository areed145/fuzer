---
name: well-review
description: Review one well or a set of wells - dated history, test and pressure trends, recorded interventions, sidetracks, and intervention candidates with the evidence for and against, without implying reservoir certainty.
---

# Well review

Separate what the records show from what they only suggest. Each finding carries its well, date range and source.

1. **Identity.** `get_entity(well)` gives the well, its sidetracks and links. Check which sidetrack the records
   belong to before you trend them; `record_history(ref)` shows whether its source records changed.
2. **Tests and trends.** `well_performance(ref=well)` gives the test history and `measurements(ref)` adds wellhead
   lab samples. `detect_changes(ref)` dates steps and drifts on the well's PI points. Say when the latest test was;
   a stale test is a finding, not a current rate.
3. **Decline and water.** `calc("decline", well)` with `plot_decline(well)`, and `plot_water_diagnostic(well)` for
   water cut. State the fit window and how many tests it rests on.
4. **Lift.** On a gas lifted well, `calc("gas_lift", well)` and `plot_gas_lift_curve(well)`.
5. **Interventions.** Fuzer has no well file. Recorded work comes from `maintenance_history(ref)`, `downtime(ref)`
   and `get_events(ref)`: slickline, shut-ins, choke changes, valve work. Give the dates and what changed in the
   next test. No test before and after means the effect is unknown.
6. **Candidates.** `opportunities()` lists scanned gains. Give each candidate its gain, supporting evidence and the
   evidence that is missing. `cluster_entities("wells")` finds peers to compare with.

**Not known here.** Reservoir pressure, drive, connectivity and completion design are unknown unless a source
records them. Offer a reservoir explanation only as a question for the subsurface engineer. Whether to intervene is
theirs and the intervention specialist's to decide.
