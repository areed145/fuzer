---
name: work-orders
description: Work order management - the open backlog by priority, type and age, overdue orders, notifications with no order, and how to prioritise the backlog against production risk.
---

# Work order management

**Terms.** SAP order types: PM01 corrective, PM02 preventive, PM03 shutdown / turnaround work.
Status: CRTD created, REL released, TECO complete. Notification OSNO is open. `due` is the order's
basic finish date; overdue means past it at `as_of`. Priority 1 is most urgent.

1. `work_order_backlog(as_of)`: counts by priority, type and age bucket, overdue items, the oldest
   orders, and open notifications that nobody has planned an order for.
2. `backlog_health(as_of)`: the specialist check. It flags `ageing` (over 25 % of open PM01 orders
   older than 90 days: the backlog isn't being worked down), `urgent_overdue` (priority 1-2 past basic
   finish) and `unplanned_risk` (notifications with no order on bad actors), and gives `ranked`, the
   open orders by value / risk in money. Quote each flag with its threshold and lead with `ranked`.
3. **Prioritise** by risk, not age: high-priority overdue orders first, then orders on bad actors or
   units that caused deferment (`bad_actors`, `deferment_pareto(by="tag")`), then statutory
   inspection work (`maintenance_compliance`). Orders flagged with a `revision` wait for the turnaround.
4. **Unplanned notifications** are risk without a plan; name them and their units.
5. For one unit: `maintenance_history(ref)` shows its notifications and orders over time.

Report: backlog size and shape (by priority, type, age), what is overdue and why it matters, the
notifications that need an order, and a short ranked list of what to do first.
