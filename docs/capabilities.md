# Capabilities

Generated from the capability registry (`src/fuzer/agents/capabilities/`, #133); don't edit by hand. Each tool is declared once with the agents that use it. `?` marks an optional parameter.

| Agent | Tools |
|---|---|
| orchestrator | 20 |
| troubleshooter | 70 |
| production | 55 |
| maintenance | 50 |

## Shared (50)

Used by every agent or by several specialists.

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `get_ontology` | query |  | The data model as it exists in Fuzer: entity kinds with counts, fields and feeding sources, and which link types connect which kinds (with counts and sources). |
| `search` | query | query, kind?, limit? | Find entities by tag, well, PI point, serial number, source key or description text. |
| `get_entity` | query | ref, as_of? | Everything Fuzer knows about one entity: chosen values (with source and any disagreeing sources), the source records merged into it, and its links grouped by relationship. |
| `trace_flow` | query | ref, direction?, as_of?, max_depth?, network? | Follow a flow network from an entity. |
| `equipment_history` | query | tag | Physical equipment installed at a tag over time (serial, manufacturer, model, install dates). |
| `get_events` | query | ref?, since?, until?, event_type?, limit? | What happened, from the operator logs: trips, starts, stops, changeovers, shut-ins, choke changes, alarms, leaks, maintenance, instrument faults. |
| `maintenance_history` | query | ref, since?, limit? | SAP notifications and work orders for a tag, a piece of equipment, every instance of a model, or a whole class (e.g. |
| `downtime` | query | ref?, since?, until?, rank_by?, limit? | Production downtime and deferment from Energy Components, with lost oil (Sm3) and hours. |
| `get_incidents` | query | ref?, since?, until?, limit? | Incidents: logged events, downtime and SAP notifications on one piece of plant within a few days of each other, grouped into one upset with lost oil, failure modes and event types. |
| `search_knowledge` | query | question, ref?, limit?, include_superseded?, failure_modes? | Search OEM manuals, troubleshooting guides, procedures and past incidents, returning cited passages (document, revision, page, section, snippet with [[matches]]). |
| `documents_for` | query | ref | Current documents that apply to a tag / equipment / model / class: its OEM manual (via model), class procedures and area troubleshooting guides, closest first, with why each applies. |
| `lessons_learned` | query | status?, limit? | Lessons learned per equipment model (or class) and ISO 14224 failure mode: the corrective work that has fixed it before, how often it held for 30 days, and the incidents behind it. |
| `spare_parts` | query | ref | Spare parts for a tag, piece of equipment or equipment model: SAP material, manufacturer part number, stock on hand (0 = none in stock), storage bin, how many units use it, and manual sections naming it. |
| `process_values` | query | ref, at?, hours_before?, hours_after? | Process values (PI points on the unit and its instruments) around a time: per point min / max / mean / last and the provider used. |
| `limits` | query | ref, at? | Operating limits on a PI point, or on every point of a unit (tag, equipment, well): the alarm limits that count (hihi / hi / lo / lolo, from the DCS or PI AF), integrity operating windows (IOWs: class, low / high and the time allowed outside), the normal range learned from history while the unit runs (used as the limit, labelled learned, when no source gives one), each source's values, checks that fail (sources disagree, disabled, standing, out of order, implausible, missing) and the latest excursions with any work item opened for them. |
| `calc` | calc | check, ref, method?, model?, days?, breakeven?, min_required_mm?, until? | One diagnostic check as a number against what it should be: {value, expected, deviation, deviation_pct, flag, basis, sources}. |
| `detect_changes` | calc | ref, since?, until?, limit? | Where a PI point (or each point on a unit) stepped (a trip, a choke move), drifted (fouling, wear) or changed its jitter (a transmitter starting to hunt), with when it started, the value before and after, and a score; the `limit` strongest. |
| `board` | query | area?, unit?, kind?, text?, since?, limit? | The work board (#99): outstanding work across SAP backlog, PMs and inspections, incidents and SRE upsets, workflow items, opportunities, reviews and projects, one card per piece of work, ranked by value (#100). |
| `get_daily_ops_report` | query | day?, version? | The daily operations report for a production day (YYYY-MM-DD; default the latest): headline, production and deferment, upsets and events, wells, maintenance and integrity, look-ahead, every line citing its records. |
| `pressure_profile` | calc | well, at? | Pressure at each node from a well's wellhead down its flow path (jacket production header, separator, compressor suction) and the drop across each segment: where the back-pressure comes from. |
| `alarm_analysis` | calc | since, until, scope? | Alarm performance (ISA-18.2 / EEMUA 191) over a window: alarms per hour against 6, floods (over 10 in 10 minutes), chattering and standing alarms, and the top bad-actor points. |
| `trip_analysis` | calc | since, until, scope? | Stops (trips), starts and failed starts per unit with a run signal over a window, with the days between stops and the share of time running. |
| `compare_periods` | calc | ref, before_since, before_until, after_since, after_until | Did the change work? Each point on a PI point or unit in two windows (before and after a choke move, a repair, a swap): medians, the change, and whether it is significant (Welch's t with autocorrelation taken into account). |
| `similar_cases` | calc | ref | Past incidents like this one (an incident, "upset:<id>", or a tag or well's latest incident): same unit or class, shared failure modes and event types, with the work orders done for each and whether the fix held (no incident on the unit within 90 days). |
| `start_study` | calc | kind, params, agent? | Keep a correlation or clustering study (params as for correlate / correlation_matrix / cluster_modes / cluster_entities / cluster_events, plus spe: scope, train_since, train_until, since, until). |
| `get_study` | calc | study_id | A kept study: its parameters, status, summary, figure and (when done) its top results. |
| `what_changed` | query | since, until?, limit? | What happened in the records between two times (plant time): new log events, notifications, work orders started and finished, downtime started and ended, incidents, equipment installed or removed, limits taking or ending effect, excursions and new well tests, with counts. |
| `measurements` | query | ref, quantity?, since?, until?, history? | Sparse measurements that aren't in PI, on a well, tag, equipment, pipeline or an incident's unit: well tests (oil_rate, water_cut, gor, lift_gas_rate, wellhead_pressure), lab results at sample points (bsw, salt, density, oil_in_water, h2s, co2, water_dewpoint, chloride) and wall thickness at CMLs (wall_thickness, mm). |
| `deferment_pareto` | query | by?, since?, until?, limit? | Deferred (lost) oil ranked by downtime reason, reason category, causing tag, its ISO 14224 class, or affected well, with each one's share. |
| `well_performance` | query | ref?, since?, limit? | Well tests. |
| `gas_lift_network` | query | ref?, since?, limit? | The gas lift network: compression -> lift gas headers -> jacket gas lift manifolds (some through another jacket) -> gas lifted wells. |
| `link_text` | query | text | Find tags, wells and known plant nicknames ("test sep") in free text and link them to entities. |
| `recall` | query | ref, include_ended? | What the assistant learned in earlier conversations about an entity and the units it is part of: short dated facts from answers engineers rated useful (or statements they marked to remember). |
| `plot_trend` | plot | refs, at?, hours_before?, hours_after? | Chart process values (PI / ADX) on stacked panels sharing time, with incident lead-ins shaded. |
| `plot_measurements` | plot | ref, quantity?, since?, until? | Chart landed measurements over time on a well, tag, equipment or pipeline (well tests, lab results, wall thickness): a panel per quantity, a line per location. |
| `plot_xy` | plot | x, y, at?, hours_before?, hours_after? | Chart one PI point against another at the same times (operating envelope), coloured by time, with a straight-line fit and r. |
| `plot_bar` | plot | dataset, since?, until?, limit? | Chart a ranking as bars: deferred oil by reason / well / tag / class (Pareto with the cumulative share), failures by unit, or oil rate change by well. |
| `plot_distribution` | plot | ref, split_at, hours_before?, hours_after? | Chart one PI point before and after a moment (a repair, a setpoint change) as two boxes, with medians and spread in the summary. |
| `plot_custom` | plot | title, traces, x_title?, y_title? | Chart numbers you worked out yourself, when no other chart covers them: up to 8 traces [{name, x, y, kind: line | markers | bar}], 500 points in all. |
| `plot_vibration` | plot | ref, at?, days? | Chart a machine's overall vibration against ISO 20816 zones A-D, with temperature on a second axis and incident lead-ins shaded. |
| `plot_pump_curve` | plot | ref, days? | Chart a pump against its OEM curve: operating points from PI (head from discharge minus suction pressure, flow) coloured by time over the head and efficiency curves, with the BEP and preferred operating region. |
| `plot_choke` | plot | ref, days? | Chart a well's production choke against its OEM characteristic: effective Cv at each well test (oil wells) or from PI rates (gas wells) against the opening. |
| `plot_pressure_profile` | plot | well, at?, ref_at? | Chart pressure at each node down a well's flow path (wellhead, jacket header, separator, compressor suction), today and against a reference date: where back-pressure builds. |
| `plot_dp_drift` | plot | well, days? | Chart a well's wellhead-to-header pressure drop over time against its baseline band, with any rise marked (a flowline waxing or scaling). |
| `plot_compressor_performance` | plot | ref, days? | Chart a compressor stage's daily polytropic efficiency against its first week (fouling, wear, hot recycle). |
| `plot_cooler_fouling` | plot | ref, days? | Chart a cooler's daily outlet temperature against its clean baseline and its high limit. |
| `equipment_curves` | calc | ref | The OEM curves (pump head / efficiency / NPSHr / power against flow, or choke Cv against opening) and ratings (BEP, operating regions, max Cv) for an equipment model, or the model of an equipment item or tag, and where they came from (vendor export or an accepted datasheet reading). |
| `look_at_figure` | query | figure | See a chart you made, as an image. |
| `source_health` | query | dataset? | How each source dataset is doing (#225): state (ok, stale, held, failed, checks failing, profile drift), when it last loaded against how often it should, failing checks, link coverage and open reviews of its source. |
| `why_value` | query | ref, field | Why an entity's field says what it says (#225): the rule that chose the value, and each source record that states one, with the mapping version that read it and the load that delivered it. |

## Orchestrator (1)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `get_stats` | query |  | Counts of entities by kind, links, source records and open review items. |

## Troubleshooter (21)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `equipment_changes` | query | since? | Equipment removals and installs (swaps) across the facility, newest first. |
| `incident_detail` | query | ref | One incident's timeline (every event, downtime record and notification in it, in order), the most similar past incidents on the same unit or class (how those went is often the best clue), and suggested reading: manual / guide sections in scope of the unit that cover the incident's failure modes. |
| `record_history` | query | ref, limit? | How the source records behind an entity changed: which system, when, and each field's old and new value (e.g. |
| `troubleshooting_brief` | query | ref | Start here when troubleshooting. |
| `instrument_health` | calc | ref, since?, until? | Can the readings be trusted? On a PI point, or every point on a unit: frozen (no change while the unit runs), stuck (jitter collapsed), noisy (hunting: jitter several times its usual), out of range, and redundant transmitters (A / B) drifting apart. |
| `detections` | query | since?, detector?, limit? | What the SRE's scheduled scans found in the process data (#96), newest first: the point and unit, detector (excursions outside limits, unit stops, steps, drifts, bad instruments, equipment against its curve), what it found (key), start and end (null = still going) and evidence. |
| `get_upsets` | query | since?, status?, kind?, limit? | Upsets the SRE found in the process data (#96), newest first: detections close in time and connected in the plant model grouped into one, with the earliest as the likely first cause (unit, point and what it found), the units it reached and its status. |
| `upset_detail` | query | upset_id | One upset: its detections in time order (the first is the likely first cause), with evidence, and how many on each unit. |
| `correlate` | calc | target, scope?, since?, until?, max_lag_minutes?, path?, method?, top? | What moves with a PI point, and what moved first. |
| `correlation_matrix` | calc | scope, since?, until?, path?, method?, top? | Which points in a scope move together: the strongest pairs (r, and whether the plant model connects them) and the points ordered so related groups sit together. |
| `cluster_modes` | calc | scope, since, until, k? | Operating modes of a unit, train or list of points over a window: stopped (its run signals) and its running regimes (k-means on the points that carry them: fouling, a new setpoint), with each mode's share, the points that differ most, how often it moves between modes and the mode before each stop. |
| `cluster_events` | calc | since?, until? | SRE upsets grouped by signature (which kinds of unit raised which findings): recurring upset types. |
| `plot_incident` | plot | ref | Chart an incident on one page (an incident, or a unit's latest): the process values that moved most in the lead-in, stacked, with its log events, trips, notifications and downtime as markers. |
| `plot_correlation` | plot | ref, at?, hours_before?, hours_after?, upstream?, target? | Chart how the process values on a unit (and on what feeds it) move together, as a correlation heatmap. |
| `plot_instrument_health` | plot | ref, since?, until? | Chart a point's or a unit's readings with the stretches instrument_health flags shaded (noisy, frozen or stuck, out of range, apart from its redundant twin). |
| `plot_changes` | plot | ref, since?, until? | Chart a point's or a unit's readings with the steps and jitter changes detect_changes finds marked and drifts shaded. |
| `plot_state_timeline` | plot | since, until, scope? | Chart each unit's state (running, startup, stopped, transition) as swimlanes over a window: an upset spreading through a train or the plant. |
| `plot_alarms` | plot | since, until, scope? | Chart alarms per 10 minutes over a window against the flood line, with floods, chattering, standing alarms and the bad actors in the summary. |
| `plot_propagation` | plot | upset_id | Chart an SRE upset on the plant model: units coloured by when each was first detected, plant model links between them, the likely first cause marked. |
| `plot_operating_window` | plot | ref, since, until | Chart a PI point against its limits (alarm, IOW, learned range) with the minutes outside per day. |
| `plot_modes` | plot | scope, since, until, k? | Chart a unit's operating modes over time as a coloured strip under its regime-carrying point. |

## Production (16)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `backpressure_sensitivity` | calc | scope?, at? | Oil gained per bar less back-pressure, for each back-pressure-limited well in scope (a well, a jacket area or "facility") and summed per header: from the inflow and choke equations, and from the well's tests against the header pressure at the time. |
| `debottleneck_options` | calc | scope?, at?, limit? | Ranked actions against back-pressure: lower a separator to its next limit, pig a flowline whose pressure drop is rising, reroute a well to a lower header, open a throttled choke. |
| `capacity_headroom` | calc | ref, at? | How close each point on a unit (compressor, separator, pump) runs to its nearest limit (alarm, IOW or learned range): what a debottleneck would run into next. |
| `opportunities` | query | status?, limit? | Gains left on the table from the daily opportunity scan (#102): lift gas to reallocate between wells, wells below their decline or with stale tests, units worth a permanent fix, shut-in wells that may be recoverable. |
| `cluster_entities` | calc | kind?, scope?, since?, until? | Peer groups: wells by decline, water cut and lift gas response; or the points in a scope by how they move together (redundant points, mislabelled tags, natural sub-units; needs scope, since, until). |
| `production_summary` | query | since?, until?, by? | Oil actual vs potential (production efficiency %), gas export, lift gas and flare per month or week, with deferred oil by reason. |
| `production_constraints` | query | since?, until? | What has been limiting production: losses by reason with the units that cause them, frequency, last occurrence and trend (worse / steady / better). |
| `plot_decline` | plot | well, model?, forecast_months? | Chart a well's oil rate decline from its tests (log scale) with an Arps fit after the last step change (valve failure, breakthrough, repair), and a forecast. |
| `plot_water_diagnostic` | plot | well | Chart a well's water diagnostic: water cut against cumulative oil, and WOR and its derivative on log scales (Chan). |
| `plot_gas_lift_curve` | plot | well, breakeven? | Chart a gas lifted well's performance curve: oil against lift gas from its tests (decline taken out), the current point, the marginal response there, and the optimum where one more kSm3/d of gas gains `breakeven` Sm3/d of oil. |
| `plot_gas_lift_allocation` | plot | ref? | Chart every gas lifted well's marginal response (oil per extra lift gas) grouped by lift gas header, with suggested gas moves. |
| `plot_backpressure_sensitivity` | plot | scope?, at? | Chart oil per bar of back-pressure for each back-pressure-limited well: the model's figure and the tests' side by side. |
| `plot_choke_criticality` | plot | well, days? | Chart back over wellhead pressure through a well's choke over time against the critical ratio (0.55): under it the choke limits the well and back-pressure doesn't reach it. |
| `plot_debottleneck` | plot | at? | Chart the debottleneck options ranked by oil gained, coloured by kind. |
| `plot_loss_waterfall` | plot | since, until | Chart oil potential, planned losses, unplanned losses by reason and actual over a period, priced. |
| `plot_flare` | plot | since, until | Chart daily flare from DAY001 and its CO2 per barrel of oil (an estimate). |

## Maintenance (11)

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `list_members` | query | ref, kind?, limit?, offset? | Everything in an equipment class (including its subtypes), e.g. |
| `bad_actors` | query | since?, until?, limit? | Equipment ranked by failures (SAP notifications), downtime caused and repeat ISO 14224 failure modes, with MTBF per unit and per class. |
| `work_order_backlog` | query | as_of?, limit? | Open SAP work orders by priority, type and age; overdue orders (past basic finish); the oldest; open notifications with no order planned yet. |
| `maintenance_compliance` | query | as_of?, horizon_days?, limit? | Preventive maintenance (PM) and inspection (INSP) plans: on-time share, overdue tasks and tasks due within the horizon; tasks that need a shutdown are marked. |
| `turnaround_scope` | query | revision? | Turnaround (shutdown) scope: work orders planned for the revision (default: the next one), plans that need the unit down and aren't in scope yet, bad actors not in scope, and spares for scoped units that are out of stock (order long-lead items now). |
| `value_score` | calc | ref?, kinds?, by?, as_of?, limit? | The shared value / risk score: upsets, open work orders, overdue or due maintenance plans and gas lift opportunities ranked in money over the pack's horizon (by value, or by value per effort). |
| `plot_wall_thickness` | plot | ref?, kind?, min_required_mm? | Chart wall thickness: with ref, each CML on that vessel / piping / pipeline over time with loss rates (and retirement dates if min_required_mm is given); without ref, the items losing wall fastest across the asset (kind narrows to pipelines or tags). |
| `plot_reliability` | plot | ref, method? | Chart reliability from failure notifications on a unit or an equipment class: a Weibull plot of times between failures (beta, eta), or Crow-AMSAA cumulative failures (beta > 1: speeding up). |
| `plot_bad_actors` | plot | since?, until? | Chart bad actors as bubbles: failures against oil lost to the downtime they caused, size = downtime events, colour = ISO 14224 class. |
| `plot_peers` | plot | ref | Chart the units of one equipment class (or a unit's class) by failures per year, outliers marked. |
| `plot_score_breakdown` | plot | ref | Chart what makes up an item's value score (#100): production, risk and compliance at stake, urgency and effort, to show why one card ranks above another. |

## MCP only (7)

Called by agents as themselves through the workflow engine.

| Tool | Kind | Parameters | What it does |
|---|---|---|---|
| `next_work` | query | agent, limit? | The workflow steps your agent role can take now, highest on the board first. |
| `list_reviews` | query | kind?, limit? | Open questions waiting for a person: possible duplicate records ("match") and sources that disagree on a value ("conflict"). |
| `my_work` | query | agent, limit? | Your open work as agent role `agent`: agent steps waiting for your role (claimed_by and lease_until show whether a worker has one), person steps you claimed or were handed, and work items in your queue. |
| `claim_work` | work | task_id, agent | Take a step before working on it, so no worker or other agent takes it too. |
| `complete_work` | work | task_id, agent, result | Finish a step with your result (merged into the process data; a proposal, findings, a draft). |
| `hand_off` | work | task_id, agent, to, reason | Give a step to someone better placed: a person by name, a role's queue (`role:engineer`) or another agent (`agent:maintenance`). |
| `send_evidence` | work | agent, evidence, correlation_key?, item_id?, process_key?, name? | Add evidence (new findings, a figure id, a SAP notification raised) to an open process: by its correlation key (an upset's incident id), or by work item id. |

## Calc checks

`calc(check, ref)` runs one; each returns {value, expected, deviation, flag, basis, sources}.

| Check | On | Also used by |
|---|---|---|
| `decline` | well | chart `plot_decline` |
| `gas_lift` | well | chart `plot_gas_lift_curve` |
| `pump_curve` | pump tag or equipment | chart `plot_pump_curve` |
| `choke` | well or its choke tag | chart `plot_choke` |
| `reliability` | tag, equipment or equipment class | chart `plot_reliability` |
| `wall_loss` | vessel, piping or pipeline | chart `plot_wall_thickness` |
| `well_constraint` | well |  |
| `dp_drift` | well | SRE detector `sre.hydraulics` |
| `compressor_performance` | compressor stage tag | chart `plot_compressor_performance` |
| `cooler_fouling` | cooler tag | chart `plot_cooler_fouling` |
| `peers` | tag, equipment class or model | chart `plot_peers` |
