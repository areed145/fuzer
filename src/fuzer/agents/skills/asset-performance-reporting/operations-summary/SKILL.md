---
name: operations-summary
description: Produce cited cross-domain operational summaries from the linked production and work records Fuzer actually has.
---

# Cross-domain operations summary

Start with `get_daily_ops_report` for its recorded production date and cited operational sections. For "how did we
do this month?" or any period against the one before, use `kpi_scorecard` (and `plot_kpi_scorecard` to show it): it
reads production efficiency, deferred oil, flare, corrective backlog, preventive work on time, failures and repeat
failures for two equal periods, and says unknown where the records can't. Quote a KPI with its window, unit and
source as the scorecard gives them. For a requested
time window, use `production_summary`, `deferment_pareto`, and `production_constraints`; add `well_performance`
when the question concerns well tests or well-level production. For maintenance and work context, use
`bad_actors`, `work_order_backlog`, `maintenance_compliance`, and `turnaround_scope` as applicable. `value_score`
can compare recorded risk and value inputs when those inputs are present; `plot_score_breakdown` shows why one item
ranks above another. `opportunities` lists scanned gains for a trade-off summary.

For charts, use `plot_loss_waterfall(since, until)` for potential against actual, `plot_bad_actors` for reliability,
`plot_backlog` and `plot_compliance` for work, and `compare_periods` for before and after a change.

Lead with what changed and what needs a decision; a KPI without its driver is not a summary. Take the driver from
the owning specialist's finding rather than reading it off a chart (Production & Surveillance for unrecorded
losses, Maintenance & Integrity for machines on "act" and lines near retirement). Escalate anything a specialist flagged as act or
overdue; don't average it into a trend.

Keep dates, time windows, units, evidence IDs, and missing-data caveats with every numeric claim. A daily report is
historical to its recorded report date, not necessarily live. Do not derive commercial or production-accounting
reconciliation, cost, or financial value without authoritative accounting and cost sources; do not turn an
operational summary into an approved decision or external report.
