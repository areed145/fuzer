---
name: constraint-management
description: Constraint management - what is limiting throughput (compression, lift gas, export, process trips, wells), how often and how much, whether it is getting worse, and how constraints cascade through the flow network.
---

# Constraint management

A constraint is whatever stops the asset producing its potential. The data shows constraints through
their effects: deferment by reason, the units causing it, and the operator log.

1. `production_constraints(since, until)`: each loss reason with volume, events, hours, last
   occurrence, trend (second half of the window vs first) and the units that caused it.
2. **Cascades.** A constraint in one system shows up as losses elsewhere. Examples to check:
   a compressor train trip cuts lift gas to gas-lifted wells (LIFT-GAS losses caused by a compressor
   tag); a gas producer hydrate cuts lift gas supply; export curtailment forces flaring or choke-backs.
   `trace_flow(ref, "down")` from the constraining unit shows what it feeds (`network="lift_gas"` for
   lift gas: compressor -> header -> jacket manifolds -> wells, including jackets fed through another
   jacket, which lose lift first); `get_events` around the loss dates shows what operators did (bypass,
   prioritising lift gas, choking back). Load the `gas-lift` skill for lift gas constraints.
3. **Rank** by lost oil, then by trend: a worsening constraint outranks a bigger but improving one.
4. **Debottleneck options** follow from the cause: reliability work on a tripping unit
   (hand to maintenance), spare capacity or redistribution (lift gas priority to the best responders,
   from `well_performance`), operating envelope (process values near limits via `process_values`).
   The data has no nameplate capacities; say so when an answer depends on one.

Report: the top constraints ranked, each with its cause unit(s), volume, frequency and trend, the
cascade it triggers, and the options to relieve it.
