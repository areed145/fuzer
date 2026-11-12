---
name: static-equipment
description: Static equipment and integrity - wall loss and remaining life at CMLs against each CML's minimum required thickness, the condition inspection plan against the interval the wall supports, and exchanger fouling, for any pressure vessel, exchanger or piping (ISO 14224 classes VE, HE, PI).
---

# Static equipment and integrity

This is the static equipment and integrity specialist pack: the playbook below, and `pack.yaml` beside it naming the
tools, methods, detectors, charts and measurement roles it uses and the eval it is held to. The practice is API 510
for vessels and API 570 for piping; each check's response names the basis it used. The integrity skill beside this
pack holds the reading order for CML data; this pack adds the vessel and plan view.

**Start here.** For "how long has this vessel got" or "is its inspection plan right", run
`calc("corrosion_circuit", ref)` then `calc("inspection_plan", ref)`. For a line, the circuit rolls up the lines in
the same service; a vessel or piping tag stands alone. Quote each check's basis, the numbers and dates, and what
could not be checked.

- **Wall loss and remaining life** (`calc("corrosion_circuit", ref)`, charted with `plot_wall_thickness(ref)`): per
  CML the greater of the long-term and short-term rate, so a rate that sped up recently is not averaged away. The
  retirement thickness is, in order: one you pass, each CML's minimum required thickness as the inspection system
  records it (`retirement_basis` says "recorded minimum thickness"), or for a pipeline the pressure design thickness
  on an assumed grade the result names. A vessel with none on record gets no remaining life: say so and ask for the
  minimum thickness from its design (its U-1A or datasheet), then rerun with `min_required_mm`. Name the governing
  CML. `calc("wall_loss", ref)` gives the fitted per-CML trend when you want each location on its own, and
  `measurements(ref, "wall_thickness")` the readings themselves.
- **Inspection plan** (`calc("inspection_plan", ref)`): the item's condition inspection plan from the maintenance
  plans against the interval its wall supports (half the remaining life, at most ten years, after the last survey).
  `inspection_overdue` is a statutory gap: say how many days, and that it needs the unit down when the plan says so.
  `due_sooner` means the wall runs out of margin before the plan's next date: recommend bringing the inspection
  forward. `interval_too_long` means the coming inspection is in time but the plan's interval is longer than the
  wall supports: recommend shortening the interval after it, and quote both dates (`plan.next_due`,
  `supported_due`). When `rate_unchecked` is set, the plan could only be checked against the calendar: say why.
- **Exchanger fouling** (`calc("cooler_fouling", ref)`, charted with `plot_cooler_fouling(ref)`): the outlet
  temperature above its level after the last cleaning, and when it reaches the high limit at that rate. A step
  rather than a climb is a duty or a bypass change, not fouling: check the process before booking a clean.
- **Operating windows**: `limits(ref)` shows the IOWs on the item's points. A breach of a corrosion IOW since the
  last inspection is a reason to revisit the inspection plan even when the wall looks fine.

**Already in the queue.** An IOW breach on the item is an `iow_breach` case, and an exchanger outlet temperature
that steps away from its normal is an `upset` case. Say when the item you are asked about already has one.

**Say what you cannot check.** Fuzer does not compute risk-based inspection (API 580 and 581 need consequence and
probability models it does not hold), fitness for service (API 579), or a predicted corrosion rate from water
chemistry: name them as the integrity engineer's next step rather than estimating them. In-line inspection anomalies
are not in its sources yet.

**Escalate to the integrity engineer** when an item has under two years of remaining life, when its inspection is
overdue or due sooner than the plan, or when a corrosion IOW was breached since the last inspection. A good answer
names the item, the governing CML with its rate, retirement thickness and its basis, the remaining life, the plan's
dates against the supported ones, what is unknown, and the recommendation. Advises: changing an inspection interval,
deferring an inspection or accepting a wall below its minimum is the integrity engineer's accountable decision.
