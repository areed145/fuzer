---
name: metering-and-balances
description: Metering and balances - a meter's allocation factor against the well tests upstream of it, and its agreement with the other meter on its stream (custody against check meter), for any flow meter on the allocation network (ISO 14224 class IP).
---

# Metering and balances

This is the metering and balances specialist pack: the playbook below, and `pack.yaml` beside it naming the tools,
methods, detectors, charts and measurement roles it uses and the eval it is held to. The practice is API MPMS
(Chapter 20 for allocation, Chapter 21 and the proving chapters for flow measurement and check metering); each check's
response names the basis it used. Everything reads the allocation network, so start by placing the meter on it.

**Start here.** For "is this meter right" or "why is allocation off", run `trace_flow(ref, "up")` to see what feeds
the meter, then `calc("allocation_factor", ref)` and `calc("meter_agreement", ref)`. Quote the factor or the ratio,
the window, the meter it was checked against and what could not be checked.

- **Allocation factor** (`calc("allocation_factor", ref)`): the meter's measured volume over its flowing days
  against what the wells upstream offer at their latest tests within 120 days: oil for a liquid meter, formation gas
  for a gas meter, never lift gas. `af_low` (under 90 %) or `af_high` (over 110 %) means either the meter reads
  wrong or the tests no longer describe the wells: check the test dates before blaming the meter. `af_shift` is a
  move of 5 % or more since the window before, inside the band: something changed, quote both factors (`value`,
  `previous`). A meter downstream of processing carries shrinkage and fuel, so it sits below one: judge it against
  its own previous factor. Pass `days` to look at a shorter window when a change is recent.
- **The field over time** (`field_allocation_factor()`, `plot_field_allocation_factor()`): for "are the tests still
  good" or "why is allocation off field-wide", the oil the daily report books each month over what the producing
  wells offer at their latest tests, with each well's own downtime taken out and the median test age beside it.
  Quote the factor, `flags` (low, high, drifting) and the factor ignoring downtime. Well status history isn't
  loaded, so a well shut in without a downtime record still offers its test and pulls the factor down: say so, and
  that `test_staleness()` shows which tests are old.
- **Meter against meter** (`calc("meter_agreement", ref)`): the daily ratio to the nearest other meter on the same
  stream over the last week against its level over the 30 days before. `meter_drift` (2 % or more) is one of the two
  drifting. Two meters can't say which: run the allocation factor on both, and the one whose factor moved is the
  one to prove. Say so, and that a prover run or the meter's last calibration settles it.
- **The reading itself**: `plot_trend([ref])` to show the step, and `measurements(ref)` for what the measurement
  system recorded where it is loaded. A frozen, stuck or noisy meter is the controls specialist's
  instrument health check: hand it over rather than reading allocation off it.

**Already in the queue.** A frozen or stuck meter is raised by the `instruments` detector as instrument work. Say
when the meter you are asked about already has one.

**Say what you cannot check.** Fuzer does not hold meter proving records, flow computer configurations, gas
composition for AGA 8 or the orifice plate's inspection, and it does not compute uncertainty to API MPMS
Chapter 13: name them as the measurement technician's next step. A fuel, vent and flare balance needs those meters on
the allocation network; where they aren't, say which are missing.

**Escalate to the measurement engineer** when a custody (sales or fiscal) meter drifts or allocates out of the band,
since every allocated well and the sales invoice carry the error. A good answer names the meter, the check it failed
with its numbers and window, the meter or tests it was checked against, which one is suspect and why, and the
recommendation (prove it, recalibrate it, or retest the wells). Advises: proving, recalibration, a meter factor
change and any correction to allocated volumes are the measurement engineer's accountable decision.
