# Metrics

Generated from the metric registry (`core/metrics.py`, `core/measures.yaml`); don't edit by hand (`uv run python -m fuzer.reference`).
Every KPI is declared once, with its definition, formula, unit, the standard it follows where there is one, the grains it can be cut by and the records behind it. Pages, the daily report, reports and agents read the same registered metric (`GET /metrics/{key}`, the `get_metric` tool), so a number is the same wherever it is shown, and every number drills to its cases or records. The Metrics page (under Reports) lists them with their value now and opens each one by any of its grains. No page computes a KPI of its own (`tests/core/test_page_kpis.py`).

A **scoped** metric answers for any entity (`?scope=<id>`) and rolls up everything beneath it, as the entity pages roll work up. A **windowed** one answers for a named window (`?window=7d`: `1d`, `7d`, `30d`, `90d`, `qtd`, `ytd`) ending on a day (`?at=YYYY-MM-DD`); its cut-off says what that day is when none is given (today, or the last production day), so a report read again later for the same day gives the same number. A **stock** is what is open at the window's end; the rest add up what happened inside it. A metric that isn't scoped answers for the whole register and says so. Measures over the register's records are declared in `core/measures.yaml`, and a pack adds or rebinds one under `measures:` in its pack.yaml (see [Reports](reports.md)).

| Metric | Group | Unit | Cut by | Scope | Window |
|---|---|---|---|---|---|
| [`context.completeness`](#contextcompleteness) Asset context completeness | Context | % | area, class | any entity | now |
| [`downtime.events`](#downtimeevents) Downtime events | Downtime | events | facility, area, well, tag, cause | any entity | 30d |
| [`downtime.hours`](#downtimehours) Downtime hours | Downtime | h | facility, area, well, tag, cause | any entity | 30d |
| [`downtime.repeat_stops`](#downtimerepeat_stops) Repeat stops | Downtime | events | - | any entity | 30d |
| [`maintenance.backlog_ageing`](#maintenancebacklog_ageing) Corrective backlog older than 90 days | Maintenance | % | - | whole register | now |
| [`maintenance.urgent_overdue`](#maintenanceurgent_overdue) Urgent work orders overdue | Maintenance | orders | - | whole register | now |
| [`plan.actual_cost`](#planactual_cost) Actual cost | Plan | USD | program | any entity | qtd |
| [`plan.actual_hours`](#planactual_hours) Actual hours | Plan | h | program | any entity | qtd |
| [`plan.estimate_cost`](#planestimate_cost) Planned cost | Plan | USD | program | any entity | qtd |
| [`plan.estimate_hours`](#planestimate_hours) Planned hours | Plan | h | program | any entity | qtd |
| [`plan.unplanned_share`](#planunplanned_share) Unplanned work | Plan | % | - | any entity | qtd |
| [`production.deferred`](#productiondeferred) Oil deferred | Production | bbl | facility, area, well, tag, cause | any entity | 30d |
| [`production.efficiency`](#productionefficiency) Production efficiency | Production | % | facility | any entity | 14d |
| [`production.flare`](#productionflare) Gas flared | Production | Mscf | facility | any entity | 30d |
| [`production.gas`](#productiongas) Gas produced | Production | Mscf | facility | any entity | 30d |
| [`production.oil`](#productionoil) Oil produced | Production | bbl | facility | any entity | 30d |
| [`production.target`](#productiontarget) Oil target | Production | bbl | facility | any entity | 30d |
| [`production.test_rate`](#productiontest_rate) Oil rate at test | Production | bbl/d | facility, area, well | any entity | 30d |
| [`production.uptime`](#productionuptime) Uptime | Production | % | facility | any entity | 30d |
| [`production.water_injected`](#productionwater_injected) Water injected | Production | bbl | facility | any entity | 30d |
| [`safety.bypass_age`](#safetybypass_age) Bypass age | Safety | days | facility, area | any entity | 1d, at the end |
| [`safety.bypasses_active`](#safetybypasses_active) Safeguards bypassed | Safety | bypasses | facility, area | any entity | 1d, at the end |
| [`safety.days_since_incident`](#safetydays_since_incident) Days since the last incident | Safety | days | - | any entity | 1d, at the end |
| [`safety.demands`](#safetydemands) Safeguard demands | Safety | demands | facility, area | any entity | 30d |
| [`safety.incidents`](#safetyincidents) Incidents | Safety | incidents | facility, area, severity | any entity | 30d |
| [`safety.inspections_overdue`](#safetyinspections_overdue) Inspections overdue | Safety | items | - | any entity | 1d, at the end |
| [`safety.ldar_leaks`](#safetyldar_leaks) Leaks found | Safety | leaks | - | any entity | 90d |
| [`safety.permits_open`](#safetypermits_open) Permits in date | Safety | permits | facility, area, type | any entity | 1d, at the end |
| [`sources.agreement`](#sourcesagreement) Source agreement | Sources | % | dataset | whole register | the latest rebuild |
| [`sources.completeness`](#sourcescompleteness) Source completeness | Sources | % | dataset | whole register | the latest rebuild |
| [`sources.held_loads`](#sourcesheld_loads) Loads held | Sources | datasets | dataset | whole register | the latest load of each dataset |
| [`sources.straight_through`](#sourcesstraight_through) Records decided without a steward | Sources | % | system | whole register | now |
| [`sources.timeliness`](#sourcestimeliness) Source timeliness | Sources | % | dataset | whole register | the latest rebuild |
| [`sources.validity`](#sourcesvalidity) Source validity | Sources | % | dataset | whole register | the latest rebuild |
| [`work.arrivals`](#workarrivals) Cases opened | Work | cases | queue, type | any entity | 7d |
| [`work.backlog`](#workbacklog) Open cases | Work | cases | queue, type | any entity | 7d, at the end |
| [`work.closed`](#workclosed) Cases closed | Work | cases | queue, type | any entity | 7d |
| [`work.first_time_fix`](#workfirst_time_fix) First-time fix | Work | % | queue, type | any entity | 7d |
| [`work.planned_share`](#workplanned_share) Planned work | Work | % | queue, type | any entity | 7d |
| [`work.reopened`](#workreopened) Cases reopened | Work | cases | queue, type | any entity | 7d |
| [`work.schedule_compliance`](#workschedule_compliance) Schedule compliance | Work | % | - | any entity | 30d |
| [`work.sla_misses`](#worksla_misses) Steps late | Work | cases | queue, type | any entity | 7d |
| [`work.time_on_site`](#worktime_on_site) Time on site | Work | h | queue, type | any entity | 7d |
| [`work.time_to_close`](#worktime_to_close) Time to close | Work | h | queue, type | any entity | 7d |
| [`work.time_to_dispatch`](#worktime_to_dispatch) Time to dispatch | Work | h | queue, type | any entity | 7d |
| [`work.time_to_triage`](#worktime_to_triage) Time to triage | Work | h | queue, type | any entity | 7d |

## context.completeness

**Asset context completeness.** Of what an asset's class usually has (a check at least half its class passes), the share it has: class, model, maintenance, historian points, roles, limits, drawing, documents, criticality.

- **Formula:** mean over assets of (expected checks passed / expected checks) x 100
- **Unit:** % (higher is better)
- **Records:** assets (top-level tags and wells) and what the register holds about each
- **Cut by:** area, class
- **Target:** 90 %
- **Period:** now
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## downtime.events

**Downtime events.** Stops that started in the window, each a downtime record against what it affects or was caused by.

- **Formula:** count(downtime starting in the window)
- **Unit:** events (lower is better)
- **Records:** downtime records and what each affects or was caused by
- **Cut by:** facility, area, well, tag, cause
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## downtime.hours

**Downtime hours.** The hours of the stops that started in the window, as each downtime record states its duration.

- **Formula:** sum(duration_h) over downtime starting in the window
- **Unit:** h (lower is better)
- **Records:** downtime records (start, duration) and what each affects or was caused by
- **Cut by:** facility, area, well, tag, cause
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## downtime.repeat_stops

**Repeat stops.** Stops that started in the window on a well or unit that had already stopped in the 30 days before: the same thing failing again, not just a busy month. Facility-wide stops are left out.

- **Formula:** count(downtime starting in the window whose well or unit had another stop starting in the 30 days before it)
- **Unit:** events (lower is better)
- **Records:** downtime records and the wells and units each affects or was caused by
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## maintenance.backlog_ageing

**Corrective backlog older than 90 days.** Of the open corrective work orders, the share opened more than 90 days ago: a backlog that isn't being worked down, not just a big one.

- **Formula:** open corrective (PM01) orders older than 90 days / open corrective orders x 100
- **Unit:** % (lower is better)
- **Records:** SAP work orders (open, corrective)
- **Target:** 20 %
- **Period:** now
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## maintenance.urgent_overdue

**Urgent work orders overdue.** Priority 1 and 2 work orders past their basic finish date.

- **Formula:** count(open orders with priority 1 or 2 and basic finish before today)
- **Unit:** orders (lower is better)
- **Records:** SAP work orders (open, priority, basic finish)
- **Target:** 0 orders
- **Period:** now
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## plan.actual_cost

**Actual cost.** The cost SAP records on the orders of the jobs of the cases matched to the plan's lines.

- **Formula:** sum(order cost) over the matched cases' jobs
- **Unit:** USD (neither better nor worse)
- **Records:** business plan lines (estimates, period, assets) and the cases matched to them, their jobs and orders
- **Cut by:** program
- **Window:** qtd by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## plan.actual_hours

**Actual hours.** The hours worked and still booked on the jobs of the cases matched to the plan's lines, each job's spread evenly over its dates, for the part of them in the window (the window the planned hours are read for).

- **Formula:** sum(job hours x days of the job in the window / days of the job) over the matched cases' jobs
- **Unit:** h (neither better nor worse)
- **Records:** business plan lines (estimates, period, assets) and the cases matched to them, their jobs and orders
- **Cut by:** program
- **Window:** qtd by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## plan.estimate_cost

**Planned cost.** The cost the business plan's lines estimate for their work, each line's estimate spread evenly over its period, for the part of it in the window.

- **Formula:** sum(estimate_cost x days of the line's period in the window / days in its period) over plan lines
- **Unit:** USD (neither better nor worse)
- **Records:** business plan lines (estimates, period, assets) and the cases matched to them, their jobs and orders
- **Cut by:** program
- **Window:** qtd by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## plan.estimate_hours

**Planned hours.** The hours the business plan's lines estimate for their work, each line's estimate spread evenly over its period, for the part of it in the window.

- **Formula:** sum(estimate_hours x days of the line's period in the window / days in its period) over plan lines
- **Unit:** h (neither better nor worse)
- **Records:** business plan lines (estimates, period, assets) and the cases matched to them, their jobs and orders
- **Cut by:** program
- **Window:** qtd by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## plan.unplanned_share

**Unplanned work.** Of the cases closed in the window inside a plan or program, the share no plan line covers: work the plan did not foresee.

- **Formula:** cases closed in the window in a plan container and matched to no line / cases closed in the window in a plan container x 100
- **Unit:** % (lower is better)
- **Records:** cases in plan containers (their jobs or their container) and the plan lines they are matched to
- **Window:** qtd by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## production.deferred

**Oil deferred.** Oil lost to downtime that started in the window, as each downtime record states it, by its cause and by what it stopped. The deferment by cause and by entity on a report is this measure cut by cause or by kind. A window with no downtime record is unknown, not 0: a loss nobody recorded is still a loss.

- **Formula:** sum(lost_oil_bbl) over downtime starting in the window
- **Unit:** bbl (lower is better)
- **Records:** downtime records (start, cause, lost volume) and what each affects or was caused by
- **Cut by:** facility, area, well, tag, cause
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.efficiency

**Production efficiency.** Oil allocated to the facility against the most it could have delivered on the same days, the binding system capacity each day.

- **Formula:** sum(allocated oil) / sum(dynamic maximum) x 100, over days with both
- **Unit:** % (higher is better)
- **Records:** production days (daily reports) and the facility's system capacity measurements
- **Standard:** ISO 20815 production efficiency
- **Cut by:** facility
- **Target:** 95 %
- **Window:** 14d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## production.flare

**Gas flared.** Gas flared at the facility on the production days in the window, as the daily reports state it.

- **Formula:** sum(flare_mscf) over the production days in the window
- **Unit:** Mscf (lower is better)
- **Records:** production days (daily reports)
- **Cut by:** facility
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.gas

**Gas produced.** Gas produced at the facility on the production days in the window, as the daily reports state it.

- **Formula:** sum(gas_mscf) over the production days in the window
- **Unit:** Mscf (higher is better)
- **Records:** production days (daily reports)
- **Cut by:** facility
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.oil

**Oil produced.** Oil allocated to the facility on the production days in the window, as the daily reports state it.

- **Formula:** sum(oil_bbl) over the production days in the window
- **Unit:** bbl (higher is better)
- **Records:** production days (daily reports)
- **Cut by:** facility
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.target

**Oil target.** The oil the facility was expected to deliver on the same days. By default the potential each daily report states; a pack binds it to its business plan or forecast by declaring production.target in its own measures.

- **Formula:** sum(oil_potential_bbl) over the production days in the window
- **Unit:** bbl (neither better nor worse)
- **Records:** production days (daily reports)
- **Cut by:** facility
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.test_rate

**Oil rate at test.** The oil rate the well tests in the window measured, on average: a well's own rate when the scope is a well, the mean over its wells' tests otherwise. Compare it with the well's forecast on Wells against their own forecast.

- **Formula:** mean(oil_bbld) over well tests in the window
- **Unit:** bbl/d (higher is better)
- **Records:** well tests and the well each tests
- **Cut by:** facility, area, well
- **Window:** 30d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## production.uptime

**Uptime.** The share of the window's hours the facility was not stopped: no downtime against the facility itself was open. A well or a unit stopping alone is deferment, not facility downtime. For the whole register, the mean over the facilities the daily reports cover.

- **Formula:** 100 - hours covered by downtime against the facility / hours in the window x 100
- **Unit:** % (higher is better)
- **Records:** downtime records (start, end) against the facility, and the facilities the daily reports cover
- **Cut by:** facility
- **Target:** 95 %
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## production.water_injected

**Water injected.** Water injected at the facility on the production days in the window, as the daily reports state it.

- **Formula:** sum(water_inj_bbl) over the production days in the window
- **Unit:** bbl (neither better nor worse)
- **Records:** production days (daily reports)
- **Cut by:** facility
- **Window:** 30d by default, ending on the last production day when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## safety.bypass_age

**Bypass age.** How long the safeguards bypassed at the window's end have been out of service, on average.

- **Formula:** mean(days from going in to the window's end) over bypasses in place
- **Unit:** days (lower is better)
- **Records:** bypass register (state, in since) and the safeguard each bypasses
- **Cut by:** facility, area
- **Window:** 1d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## safety.bypasses_active

**Safeguards bypassed.** Safeguards (trips, interlocks, shutdowns) taken out of service and still in place at the window's end, against what the safeguard protects. The bypass register gives the time each went in, not when it came out, so a past window counts the ones still in place now.

- **Formula:** count(bypasses in place that went in before the window's end)
- **Unit:** bypasses (lower is better)
- **Records:** bypass register (state, in since) and the safeguard each bypasses
- **Cut by:** facility, area
- **Target:** 0 bypasses
- **Window:** 1d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## safety.days_since_incident

**Days since the last incident.** Whole days from the start of the last incident in scope to the window's end.

- **Formula:** days from the latest incident start to the window's end
- **Unit:** days (higher is better)
- **Records:** incident records and the unit each is about
- **Window:** 1d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## safety.demands

**Safeguard demands.** Times a safeguard was called on to act in the window, from the site's demand log (IEC 61511-1). A spurious trip is not a demand.

- **Formula:** count(demands in the window, not spurious)
- **Unit:** demands (lower is better)
- **Records:** the demand log and the safeguard each demanded
- **Standard:** IEC 61511-1 demand log
- **Cut by:** facility, area
- **Window:** 30d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## safety.incidents

**Incidents.** Incidents that started in the window, against the unit they are about, by severity where the source gives one.

- **Formula:** count(incidents starting in the window)
- **Unit:** incidents (lower is better)
- **Records:** incident records and the unit each is about
- **Cut by:** facility, area, severity
- **Target:** 0 incidents
- **Window:** 30d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## safety.inspections_overdue

**Inspections overdue.** Items with wall thickness readings whose next inspection has passed: the inspection their wall supports, or the date their condition inspection plan set.

- **Formula:** count(items whose wall-based inspection or inspection plan is past due on the window's day)
- **Unit:** items (lower is better)
- **Records:** wall thickness readings (CMLs) and the condition inspection plans (ops.integrity)
- **Target:** 0 items
- **Window:** 1d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## safety.ldar_leaks

**Leaks found.** Components an LDAR survey found leaking in the window: a screening reading at or above the leak threshold. Equipment no survey reached is unknown, not clean.

- **Formula:** count(LDAR screening readings at or above the leak threshold in the window)
- **Unit:** leaks (lower is better)
- **Records:** LDAR survey readings (ppm) on the tags screened
- **Standard:** EPA Method 21 screening
- **Window:** 90d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## safety.permits_open

**Permits in date.** Permits to work whose validity covers the window's end, by permit type. A permit closed early still counts until its valid-to time: the permit system gives no closing time to place it by.

- **Formula:** count(permits with valid_from <= the window's end < valid_to, not cancelled)
- **Unit:** permits (neither better nor worse)
- **Records:** permit records (validity, state, type) and the equipment each covers
- **Cut by:** facility, area, type
- **Window:** 1d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `core/measures.yaml`

## sources.agreement

**Source agreement.** Of a source's records that join an item another source also has, the share with no open disagreement about them.

- **Formula:** (shared - in conflict) / shared x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset
- **Target:** 95 %
- **Period:** the latest rebuild
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## sources.completeness

**Source completeness.** A dataset's mean column fill, from its latest profile.

- **Formula:** mean over its columns of filled rows / rows x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset
- **Target:** 95 %
- **Period:** the latest rebuild
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## sources.held_loads

**Loads held.** Datasets whose latest load was held at its checks (duplicate keys, a failing blocking check or changed columns) and waits for a steward to release or fix it.

- **Formula:** count(datasets whose latest run is held)
- **Unit:** datasets (lower is better)
- **Records:** each dataset's runs (source_run), the latest first
- **Cut by:** dataset
- **Target:** 0 datasets
- **Period:** the latest load of each dataset
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## sources.straight_through

**Records decided without a steward.** Of a source's records, the share the last rebuild matched, linked or kept as new on its own, leaving nothing for a steward.

- **Formula:** (records - records needing a steward) / records x 100
- **Unit:** % (higher is better)
- **Records:** each source's records (crosswalk) and the questions open about them
- **Cut by:** system
- **Target:** 95 %
- **Period:** now
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## sources.timeliness

**Source timeliness.** 100 while a delivery is on time; past its expected interval, the interval over its age.

- **Formula:** min(100, expected hours / hours since the last delivery x 100); mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset
- **Target:** 95 %
- **Period:** the latest rebuild
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## sources.validity

**Source validity.** The share of a dataset's quality checks passing, its key counted as one.

- **Formula:** passing checks / checks x 100; mean over datasets
- **Unit:** % (higher is better)
- **Records:** each dataset's scorecard row (source_score), kept with every rebuild
- **Cut by:** dataset
- **Target:** 95 %
- **Period:** the latest rebuild
- **Scope:** the whole register
- **Declared in:** `ops/metrics.py`

## work.arrivals

**Cases opened.** Cases opened in the window.

- **Formula:** count(cases created in the window)
- **Unit:** cases (neither better nor worse)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.backlog

**Open cases.** Cases open at the window's end (now, for a window ending today).

- **Formula:** count(cases created before the window's end and not closed by it)
- **Unit:** cases (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given; counts what is open at the window's end
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.closed

**Cases closed.** Cases closed in the window.

- **Formula:** count(cases closed in the window)
- **Unit:** cases (higher is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.first_time_fix

**First-time fix.** Of the cases closed in the window, the share done on the first visit: the field never reported it could not do the job, and the problem has not come back as a repeat.

- **Formula:** cases closed in the window with no 'could not do' and no repeat / cases closed in the window x 100
- **Unit:** % (higher is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Target:** 90 %
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.planned_share

**Planned work.** Of the cases closed in the window, the share that was planned: booked on the plan or counted against a business plan line before it closed. The rest was reactive.

- **Formula:** cases closed in the window with a plan bar or a plan line / cases closed in the window x 100
- **Unit:** % (higher is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.reopened

**Cases reopened.** Cases reopened in the window because the problem came back (a repeat of a case already closed).

- **Formula:** count(repeats recorded in the window)
- **Unit:** cases (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Target:** 0 cases
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.schedule_compliance

**Schedule compliance.** Of the preventive and inspection work orders due in the window, the share finished by their due date. One still open past its due date is late.

- **Formula:** PM02 and PM03 orders due in the window and finished by their basic finish / orders due in the window x 100
- **Unit:** % (higher is better)
- **Records:** SAP work orders (PM02, PM03: basic finish, finish) and what each concerns
- **Target:** 90 %
- **Window:** 30d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.sla_misses

**Steps late.** Person steps due in the window that finished late or are still open past due.

- **Formula:** count(person steps due in the window, done after their due time or still open past it)
- **Unit:** cases (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Target:** 0 cases
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.time_on_site

**Time on site.** How long the field spent on a job, from reporting on site to reporting it done, the median over the cases closed in the window that the field reported on.

- **Formula:** median(hours from the field's first 'on site' to its 'done') over cases closed in the window
- **Unit:** h (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.time_to_close

**Time to close.** How long the cases closed in the window took from opening to closing, the median.

- **Formula:** median(hours from opening to closing) over cases closed in the window
- **Unit:** h (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.time_to_dispatch

**Time to dispatch.** How long a case opened in the window waited to be handed off to the people who do it, the median, over the cases that were handed off.

- **Formula:** median(hours from opening to the first hand-off) over cases opened in the window and handed off
- **Unit:** h (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## work.time_to_triage

**Time to triage.** How long a case opened in the window waited for the first thing a person did on it, the median.

- **Formula:** median(hours from opening to the first person's action) over cases opened in the window
- **Unit:** h (lower is better)
- **Records:** cases and their history (work items)
- **Cut by:** queue, type
- **Window:** 7d by default, ending on today when no day is given
- **Scope:** any entity, rolling up what is beneath it
- **Declared in:** `ops/metrics.py`

## Row sets

A row set is a registered list with fixed columns, each row opening its case or entity (`GET /row-sets/{key}`); a report's table names one by key. Declared in `core/row_sets.yaml` and in code, and a pack adds or rebinds one under `row_sets:`.

| Row set | Rows | Columns | Window |
|---|---|---|---|
| `bypasses.active` Safeguards bypassed | Safeguards out of service at the window's end, the longest out first. | Bypass, In since, Expires, Why, On | 1d |
| `cases.open` Open cases | Cases open at the window's end (now, for a window ending today), the highest priority first. | Case, Type, Queue, Opened, Due | 1d |
| `downtime.events` Downtime events | Stops that started in the window, the largest loss first. | Start, On, Cause, Hours, Oil lost | 30d |
| `downtime.open` Still down | Stops in progress at the window's end (now, for a window ending today), the longest down first. | On, Down since, Cause, Oil lost so far | 1d |
| `incidents` Incidents | Incidents that started in the window, newest first. | Start, Unit, What happened, Oil lost | 30d |
| `permits.open` Permits in date | Permits to work whose validity covers the window's end, the soonest to lapse first. | Permit, Type, State, Covers, Valid to | 1d |
| `plan.lines` Plan lines | The business plan's open lines whose period meets the window, each estimate against its actuals, both for the part in the window, as Planned hours and Actual hours read them. | Program, Line, Planned hours, Actual hours, Hours variance, Planned cost, Actual cost, Cost variance, Cases | qtd |
