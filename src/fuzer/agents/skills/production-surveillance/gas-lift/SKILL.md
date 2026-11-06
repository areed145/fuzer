---
name: gas-lift
description: Gas lift - how it works, how the lift gas network is laid out (compression, headers, jacket manifolds, wells, jackets fed through other jackets), and how to evaluate it - lift gas allocation and response per well, header pressure, shortfalls and who loses lift first, valve problems, and reallocation.
---

# Gas lift

## How it works
A gas lifted well gets gas injected into its annulus. The gas enters the tubing through a gas lift
valve deep in the well and lightens the fluid column above it. That lowers the flowing bottomhole
pressure, so the reservoir pushes more liquid into the well.

More gas helps up to a point. The **gas lift performance curve** (oil rate against injection rate)
rises and then flattens. Past the optimum, extra gas only adds friction and buys little or nothing.
- **Response:** the slope of that curve, in Sm3/d of oil per extra kSm3/d of gas. It is what matters
  when deciding where gas goes.
- **Oil per lift gas** (average) only tells you how hard a well is being lifted, not where the next
  unit of gas does most.

Natural-flow (NF) wells don't use lift gas, and gas producers supply it.

## The network (Okan)
`trace_flow(network="lift_gas")` follows it; `gas_lift_network()` summarises it.
- **Source.** Gas from the inlet separator is compressed by the GGCP trains (20-K-2x00A/B/C) into the
  GGCP lift gas header (20-FT-3002 flow, 20-PT-3003 pressure). Compression bypass 20-XV-3100 can send
  uncompressed gas to the header when trains are down, at a lower pressure.
- **Headers.** The GGCP header feeds the PP-area jackets and NWP directly. It also feeds the ST1 and
  ST2 headers (30-/40-FT-3002, -PT-3003) by pipeline, and they feed the jackets around the satellites.
- **Jackets.** Each jacket has a gas lift manifold (GLM, located at the jacket) that feeds its gas
  lifted wells. Some jackets get their lift gas **through another jacket's manifold** (shared
  corridors to save pipe), so those wells sit further down the network, at lower pressure. Upsets
  upstream hit them first and hardest. Production flows back along the same routes.
- **Data.**
  - Per well PI points: GLIR (injection rate), CASP (casing pressure), WHP, WHT and choke.
  - Per jacket: the gas lift supply meter (FT-3001).
  - Well tests carry lift gas, oil, water cut and GOR.
  - Downtime with reason LIFT-GAS records lift gas shortfalls.

## Evaluating it
1. **Allocation.** `gas_lift_network()`: per header, the jackets, wells, lift gas and oil, and how
   many jackets are fed through another. Per well, lift gas, oil per lift gas, and **response** from
   its tests (decline taken out).
2. **Reallocation.** Gas is worth more on wells with high response than on wells with near-zero
   response. Moving gas from `least_response` to `most_response` wells on the same header, within
   header capacity, raises oil at no extra gas. Quote the Sm3/d gained per kSm3/d moved, and say it is
   an estimate from test scatter that needs a step-rate test to confirm.
3. **Wasted gas.** Wells in `lifting_mostly_water` (water cut 70%+) and wells with near-zero or
   negative response spend lift gas for little oil. Candidates: cut the gas, water shut-off, or a
   valve check.
4. **Valve problems.** In `well_performance(well)` and `process_values(well)`:
   - **Failed open** (or a hole in the tubing): injection up, casing pressure down, oil down, often
     casing heading. The test history shows more lift gas with less oil, a negative response.
   - **Not unloading / injecting too shallow:** low rate despite injection, casing pressure high.
   - Fixes are slickline valve change-outs. Check the events and downtime for the well.
5. **Shortfalls.** When a compression train trips, the GGCP header pressure (20-PT-3003) falls.
   - Wells furthest down the network lose lift first: satellite headers, then jackets fed through
     another jacket. Some die and need unloading to restart.
   - Use `trace_flow(train or header, "down", network="lift_gas")` to list who is exposed, and
     `deferment_pareto(by="reason")` / `production_constraints()` for LIFT-GAS losses and their
     causing units.
   - During a shortfall, priority should go to the highest-response wells.
6. **Header health.** `process_values` on the header pressure and flow tags around events. Pressure
   that sags as flow rises points to compression or pipeline limits; a satellite header sagging alone
   points to its supply line.

Report:
- lift gas and oil per header
- the 3-5 best reallocation moves, with the estimated gain
- wells wasting gas or showing valve symptoms, with their test evidence
- exposure to compression trips: which jackets and wells, and how deep in the network
