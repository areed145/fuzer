---
name: production-efficiency
description: Production efficiency (PE) and deferment accounting - PE by period, deferment Pareto by reason, category, unit, class and well, planned vs unplanned losses, and what drives the gap to potential.
---

# Production efficiency and deferment

**Definitions.**
- Production efficiency (PE) = actual oil / potential oil over the period, in %. From EC DAY001
  (`production_summary`: `efficiency_pct` per period and in `total`).
- Deferment = potential not produced, recorded in EC downtime records with a reason code and lost
  oil (Sm³) and gas (kSm³). Categories: planned (maintenance, TAR), equipment (trips, failures),
  process (hydrates), well (interventions, GL valve), external (export curtailment, weather).
- Unplanned PE excludes planned deferment: (actual) / (potential - planned deferment).

**Method.**
1. `production_summary` for PE per month and the total; note the worst months.
2. `deferment_pareto(by="category")` then `by="reason"`: the Pareto; quote each top item's share.
3. Drill into the top equipment-driven losses with `deferment_pareto(by="tag")` and `by="class"`, and
   well losses with `by="well"`. Name the units and wells behind the losses.
4. Check repeats: the same unit or reason recurring is the best improvement target. `downtime(ref)`
   lists the individual events with their comments.

Report: PE for the period (and unplanned PE), the Pareto top 5 with volumes and shares, the top
recurring causes behind them, and which ones are recoverable soonest.
