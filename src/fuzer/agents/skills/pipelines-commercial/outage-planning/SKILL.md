---
name: outage-planning
description: Outage planning - each planned outage's customers hit and what they lose, the window that costs them least and why, the crews and planner's rules it keeps, and the customer and regulatory notices it needs, for any plan with a customer and contract register.
---

# Outage planning

This is the outage planning specialist pack: the playbook below, and `pack.yaml` beside it naming the tools, methods,
detectors, charts and measurement roles it uses and the eval it is held to. An outage is planned work that takes
plant down: a schedule whose activities need it down is one outage, any other shutdown job is one on its own.

**Start here.** For "when should we take the line down" or an outage window plan change, run `outage_impact(days)`
for the outages on the plan and who each hits, then `critical_path(project)` for the outage's schedule. Quote the
outage, its dates and hours, each customer it hits with the contracts, barrels and dollars, and the window.

- **What it costs** (`outage_impact(days)`): each contract whose delivery point the outage takes down, or leaves with
  no feed on the flow network, while the contract runs, at its daily volume and rate for the hours hit; by customer,
  a contract's hours counted once where two outages overlap, and a customer hit at two or more separate times
  flagged. `customer_contracts()` lists the register behind it.
- **The window**: the plan change proposes three windows from a week to eight weeks ahead, the cheapest recommended,
  each with its reasons. Weigh them the same way: taking the outage alongside another that hits the same contracts
  (those hours are lost once), a contract not yet running then, a customer no longer hit twice, how far from the plan.
  Quote the dollars each saves against the planned dates.
- **Duration and crews**: the schedule's levelled duration from `critical_path(project)`, shown with
  `plot_gantt(project)`, beside how long similar past outages took on the plan change. `plan_constraints(start)`
  names each crew over capacity and the jobs causing it; a window that puts a crew over or breaks a planner's rule is
  not one to recommend.
- **Notices**: approving a window drafts a notice to each crew and person on it and to each customer it hits, for a
  person to send. A regulator's notice of a planned outage is not drafted: name it, with the regulator and the notice
  period the line's operating authority sets, as the planner's to send.

**Say what you cannot check.** No nominations are loaded (contract volumes stand in), and no hydraulic model says
whether the line could run reduced instead of down; tankage and marine schedules count only where the plan holds
them as resources.

**Advises.** The planner picks and approves the window; a person sends every notice. Findings inform people who
approve priorities, schedules and commitments; nothing here commits anything.
