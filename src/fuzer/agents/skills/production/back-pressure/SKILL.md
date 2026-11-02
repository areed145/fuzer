---
name: back-pressure
description: Back-pressure and hydraulics - what holds wells back without a deferment record (jacket header, separator, a fouling flowline, the choke), how much oil a bar is worth, and the ranked debottleneck options with their next constraint.
---

# Back-pressure

Wells often flow less than they could because something downstream holds the pressure up: the jacket
production header, the separator, compressor suction, or a flowline waxing or scaling. None of this raises
a deferment record. Work through it in this order, citing the numbers each tool returns:

1. **Classify the wells.** `calc("well_constraint", well)` reads back over wellhead pressure:
   - under 0.55 the choke is at critical flow, so back-pressure doesn't reach the well (choke-limited);
   - over it, the header holds the well back (back-pressure-limited);
   - `lift_limited` means its gas lift curve also says more gas pays (load `gas-lift`).

   For a jacket, check its wells one by one and count them.
2. **Find where the pressure goes.** `pressure_profile(well)` gives the pressure at each node from the
   wellhead to compressor suction, and the drop across each segment. The largest drop is where to look.
   `calc("dp_drift", well)` says whether the wellhead-to-header drop is rising (`dp_rising`, and since
   when): a flowline waxing or scaling, not a choke move.
3. **What a bar is worth.** `backpressure_sensitivity(scope)` gives oil per bar for each back-pressure-
   limited well, from the inflow and choke model and from its tests against the header pressure. Quote
   both. When they disagree (`disagree`), say the estimate is uncertain and why: few tests, or a header
   that hardly moved.
4. **The next constraint.** `capacity_headroom(unit)` shows how close the separator, compressor or pump
   runs to its nearest limit (alarm, IOW or learned range). A gain that runs into a limit is capped there.
5. **Options.** `debottleneck_options(scope)` ranks:
   - lowering a separator to its next limit;
   - pigging a flowline whose drop is rising;
   - rerouting a well to a lower header;
   - opening a throttled choke.

   Each comes with oil gained, confidence, the next constraint and what would confirm it (a stepped
   setpoint change, a well test, a step-rate test). The same options are on the work board as
   opportunities.
6. **Uncertainty.** Say what the answer rests on: the latest test's age, whether the shut-in pressure
   was seen in PI, and whether the data and the model agree.

Charts: `plot_pressure_profile` (today against a reference date), `plot_backpressure_sensitivity`,
`plot_choke_criticality` (pressure ratio over time against 0.55), `plot_dp_drift`, `plot_debottleneck`.

These are proposals for people to decide. Never give operating instructions ("lower the setpoint to
10 bar"); say what the option is worth and what would confirm it.
