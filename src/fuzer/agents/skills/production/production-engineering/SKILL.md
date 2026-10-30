---
name: production-engineering
description: Production engineering for wells - well test trends (rate, water cut, GOR, lift gas, WHP), decline and water breakthrough, gas lift performance and valve failures, well interventions and candidates.
---

# Production engineering (wells)

1. **Asset view:** `well_performance()` ranks wells by oil rate change between the first and latest
   test in the window, lists water cut risers, and adds each well's deferment.
2. **One well:** `well_performance(ref="OK-J07-03")` gives its test history. Read it as a trend:
   - steady decline: normal reservoir depletion; compare with the field.
   - sudden rate drop with water cut jump: water breakthrough (check the test dates and log entries).
   - sudden rate drop, then recovery after a slickline job: gas lift valve or downhole problem fixed.
   - lift gas steady but rate falling: poor lift efficiency, a candidate to re-optimise lift gas.
   - lift gas up while oil falls: a gas lift valve failed open or a tubing leak (load `gas-lift`).
3. **Gas lifted wells:** `gas_lift_network(ref=well)` gives its supply path (manifold, any jacket it is
   fed through, header, compression) and its response to more lift gas; load the `gas-lift` skill.
4. **Context:** `get_entity(well)` for lift type and location, `get_events(well)` for choke changes,
   shut-ins, interventions and operator notes, `downtime(well)` for losses, `trace_flow(well, "down")`
   for its route to the separator and export.
5. **Candidates:** wells with the largest recoverable loss: failed lift (restore), high water cut
   (water shut-off), big decline vs neighbours (stimulation / scale squeeze). Rank by Sm³/d at stake.

Quote test dates and values; one test is not a trend, so say how many tests support a conclusion.
