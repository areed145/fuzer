# Metrics

Generated from the metric registry (`core/metrics.py`); don't edit by hand (`uv run python -m fuzer.reference`).
Every KPI is declared once, with its definition, formula, unit, the standard it follows where there is one, the grains it can be cut by and the records behind it. Pages, the daily report and agents read the same registered metric (`GET /metrics/{key}`, the `get_metric` tool), so a number is the same wherever it is shown, and every number drills to its cases or records.

| Metric | Group | Unit | Cut by | Period |
|---|---|---|---|---|
| [`context.completeness`](#contextcompleteness) Asset context completeness | Context | % | area, class | now |
| [`maintenance.backlog_ageing`](#maintenancebacklog_ageing) Corrective backlog older than 90 days | Maintenance | % | - | now |
| [`maintenance.urgent_overdue`](#maintenanceurgent_overdue) Urgent work orders overdue | Maintenance | orders | - | now |
| [`production.efficiency`](#productionefficiency) Production efficiency | Production | % | facility | the 14 days to the latest daily report |
| [`sources.agreement`](#sourcesagreement) Source agreement | Sources | % | dataset | the latest rebuild |
| [`sources.completeness`](#sourcescompleteness) Source completeness | Sources | % | dataset | the latest rebuild |
| [`sources.straight_through`](#sourcesstraight_through) Records decided without a steward | Sources | % | system | now |
| [`sources.timeliness`](#sourcestimeliness) Source timeliness | Sources | % | dataset | the latest rebuild |
| [`sources.validity`](#sourcesvalidity) Source validity | Sources | % | dataset | the latest rebuild |
| [`work.arrivals`](#workarrivals) Cases opened | Work | cases | queue, type | this week |
| [`work.backlog`](#workbacklog) Open cases | Work | cases | queue, type | this week |
| [`work.closed`](#workclosed) Cases closed | Work | cases | queue, type | this week |
| [`work.sla_misses`](#worksla_misses) Steps past due | Work | cases | queue, type | this week |

## context.completeness

**Asset context completeness.** Of what an asset's class usually has (a check at least half its class passes), the share it has: class, model, maintenance, historian points, roles, limits, drawing, documents, criticality.

- **Formula:** mean over assets of (expected checks passed / expected checks) x 100
- **Unit:** % (higher is better)
- **Records:** assets (top-level tags and wells) and what the register holds about each
- **Cut by:** area, class

## maintenance.backlog_ageing

**Corrective backlog older than 90 days.** Of the open corrective work orders, the share opened more than 90 days ago: a backlog that isn't being worked down, not just a big one.

- **Formula:** open corrective (PM01) orders older than 90 days / open corrective orders x 100
- **Unit:** % (lower is better)
- **Records:** SAP work orders (open, corrective)

## maintenance.urgent_overdue

**Urgent work orders overdue.** Priority 1 and 2 work orders past their basic finish date.

- **Formula:** count(open orders with priority 1 or 2 and basic finish before today)
- **Unit:** orders (lower is better)
- **Records:** SAP work orders (open, priority, basic finish)

## production.efficiency

**Production efficiency.** Oil allocated to the facility against the most it could have delivered on the same days, the binding system capacity each day.

- **Formula:** sum(allocated oil) / sum(dynamic maximum) x 100, over days with both
- **Unit:** % (higher is better)
- **Records:** production days (daily reports) and the facility's system capacity measurements
- **Standard:** ISO 20815 production efficiency
- **Cut by:** facility

## sources.agreement

**Source agreement.** Of a source's records that join an item another source also has, the share with no open disagreement about them.

- **Formula:** (shared - in conflict) / shared x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset

## sources.completeness

**Source completeness.** A dataset's mean column fill, from its latest profile.

- **Formula:** mean over its columns of filled rows / rows x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset

## sources.straight_through

**Records decided without a steward.** Of a source's records, the share the last rebuild matched, linked or kept as new on its own, leaving nothing for a steward.

- **Formula:** (records - records needing a steward) / records x 100
- **Unit:** % (higher is better)
- **Records:** each source's records (crosswalk) and the questions open about them
- **Cut by:** system

## sources.timeliness

**Source timeliness.** 100 while a delivery is on time; past its expected interval, the interval over its age.

- **Formula:** min(100, expected hours / hours since the last delivery x 100); mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset

## sources.validity

**Source validity.** The share of a dataset's quality checks passing, its key counted as one.

- **Formula:** passing checks / checks x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset

## work.arrivals

**Cases opened.** Cases opened this week (Monday to now).

- **Formula:** count(cases created this week)
- **Unit:** cases (neither better nor worse)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type

## work.backlog

**Open cases.** Cases still open now.

- **Formula:** count(cases open now)
- **Unit:** cases (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type

## work.closed

**Cases closed.** Cases closed this week (Monday to now).

- **Formula:** count(cases closed this week)
- **Unit:** cases (higher is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type

## work.sla_misses

**Steps past due.** Person steps due this week that finished late or are still open past due.

- **Formula:** count(person steps due this week, done after their due time or still open past it)
- **Unit:** cases (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
