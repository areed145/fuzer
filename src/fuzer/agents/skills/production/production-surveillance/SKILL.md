---
name: production-surveillance
description: Production surveillance and optimisation - how production is trending against potential, what changed and when, which facilities, wells or systems explain it, and where the upside is.
---

# Production surveillance and optimisation

**Start broad, then narrow.**
1. `production_summary(since, until, by="month")` for the trend: oil actual vs potential, efficiency,
   gas export, lift gas, flare, and deferred oil by reason per period. Use `by="week"` to locate a change.
2. Find the change: the period where oil or efficiency stepped down, or flare / lift gas stepped.
   Then explain it with `deferment_pareto` for that window (`by="reason"`, then `by="tag"` or
   `by="well"`), and `well_performance` for underlying well decline.
3. Separate the two kinds of loss: **deferment** (potential not produced: trips, shut-ins,
   curtailment - recoverable) and **decline** (potential itself falling: reservoir, water cut - managed
   by wells work). The daily report's potential vs actual shows deferment; well tests show decline.
4. Confirm with the operator logs: `get_events` on the units or wells involved around the dates.

**Optimisation levers to look for:** lift gas allocation (`gas_lift_network`: move gas from wells
with little response to wells with a lot, same header; load `gas-lift`), wells with lift gas but
falling rate, lift gas shortfalls when a compressor train is down (`production_constraints`), recurring
trips on one unit (a reliability fix pays back in production), flaring during export curtailment,
wells shut in for interventions longer than planned.

Report: the trend in numbers (start, end, change, %), the top 2-3 explanations ranked by volume with
sources, and the upside you'd chase first with an estimate (Sm³/d) where the data supports one.
