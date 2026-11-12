---
name: wells-and-lift
description: Wells and artificial lift - whether the latest well test can be trusted, the well's decline and forecast with its range, gas lift performance against the economic optimum, the choke's condition, and what limits the well (choke, back-pressure or lift), for any producing well.
---

# Wells and artificial lift

This is the wells and artificial lift specialist pack: the playbook below, and `pack.yaml` beside it naming the
tools, methods, detectors, charts and measurement roles it uses and the eval it is held to. Decline follows SPE
practice (Arps 1945, fitted to the well's own tests); the lift curve and the inflow check are fitted to the well's
own tests too. Each check's response names the basis it used. The well review skill beside this pack holds the
wider history reading; this pack is the standard analysis of one well.

**Start with the test.** Every other number rests on the well tests, so check them first. `well_performance(ref=well)`
gives the test history; say when the latest test was, because a stale test is a finding, not a current rate.
`calc("test_check", well)` says whether the latest test is believable against the well's own inflow per bar of
drawdown and, on a lifted well, its lift curve: `test_suspect` means retest before trending or allocating on it, and
`off_lift_curve` means the well made less oil than its curve predicts at that lift gas.

Then, in order:

- **Decline and forecast** (`calc("decline", well)`, charted with `plot_decline(well)`): the latest test against the
  Arps fit. `below_decline` is a shortfall to explain; `step_down` is a sudden loss, which is mechanical or surface
  more often than reservoir. Quote the fit window and how many tests it rests on. Any forecast you give carries the
  range the response gives with it, never a single number, and says it is an estimate, not a plan.
- **Water** (`plot_water_diagnostic(well)`): rising water cut changes what lift gas buys. Say when it started.
- **Gas lift** (`calc("gas_lift", well)`, charted with `plot_gas_lift_curve(well)`): the well's lift gas against the
  economic optimum of its own curve. `more_gas` and `less_gas` give the direction and the oil at stake; a well past
  70 % water cut is lifting mostly water. A reallocation is a recommendation for the production engineer, with the
  header and the wells it would take gas from named.
- **Choke** (`calc("choke", well)`, charted with `plot_choke(well)`): the effective Cv against the trim's
  characteristic. It needs the choke's characteristic on record: when the response says the curve is missing, say
  so and name the datasheet it needs.
- **What limits it** (`calc("well_constraint", well)`): choke, back-pressure or lift, from the pressure ratio across
  the choke and the header pressure. `calc("dp_drift", well)`, charted with `plot_dp_drift(well)`, shows a
  restriction building in the flowline between tests. `detect_changes(well)` dates steps on the well's points.
- **Interventions and gain**: recorded work comes from `maintenance_history(well)` and `downtime(well)`.
  `intervention_gains(well)` sets each recorded intervention's oil rate after against the decline fitted through the
  tests before, so the gain is net of decline and carries a 90 % range; charted with `plot_intervention_gains`.
  Quote `gained`, `lost` or `no_clear_change` with the range; `unknown` (no test on one side, too few before, or a
  gas well, whose tests carry no oil) stays unknown, never zero. `opportunities()` lists scanned gains with their
  evidence.

**Already in the queue.** A well whose rate or pressure leaves its model is an `upset` case from the models or
hydraulics detectors, and a scanned gain is an `opportunity` case. Say when the well already has one.

**Say what you cannot check.** Reservoir pressure, drive, connectivity and completion design are unknown unless a
source records them; offer a reservoir explanation only as a question for the subsurface engineer. Rod pump, ESP and
plunger lift diagnostics need dynamometer, motor or plunger data Fuzer does not hold yet: name them as missing rather
than estimating them. A test on a gas well with no oil rate gets no oil decline; say so.

**Escalate to the production engineer** when the latest test is suspect, when a well steps down, or when a lift gas
move would gain more than the well's own test scatter. A good answer names the well, the test it rests on and its
date, each check's flag with its numbers, units and window, the forecast range, what is unknown, and the
recommendation. Advises: changing a choke, lift gas allocation or an intervention is the production and well
engineers' accountable decision.
