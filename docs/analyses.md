# Analyses

Calcs, SRE detection, studies and the knowledge tools the agents use.

## Production-efficiency dashboards

The **Analytics** page starts the configurable visualization framework with two linked panels:
dynamic maximum oil capacity versus allocated oil, and the active constraint as a categorical daily
band. They share a time axis, hover and zoom, with units, tooltips and constraint legends. The initial
window is **14 daily samples**, ending on the latest report for the selected facility, not necessarily
the wall-clock date. This follows the tool's historical-asset convention. An explicit end date selects
another 14-day window.

The initial panels are declared in `ui/src/analytics/production-efficiency.json`, not embedded in
page-specific chart components. Its versioned JSON spec declares a whitelisted data source and query
variables (`$end`, `$facility`), renderer type, fields, axis units, thresholds, theme-color tokens and
grid row weights. `ui/src/analytics/dashboard.ts` validates the spec and dispatches through the
`time-series` / `constraint-band` renderer registry; one Plotly subplot grid shares the date axis.
Plotly is already a UI dependency, so no new charting library is introduced.

Use **Dashboard JSON · customize & save** to import or edit the spec, apply a preview, save it in this
browser, or reset it. The gap series is optional and can be toggled in the legend. Saved specs use
localStorage, not server-wide dashboard storage. Imported JSON is size-limited and validated:
arbitrary URLs, HTML chart options and unregistered renderers are rejected. This first version
intentionally supports one data source and two linked panels; multi-dashboard server persistence,
arbitrary grid layouts, new queries and additional renderer types remain future extensions.

### Data and calculation contract

`GET /analytics/production-efficiency?ref=<facility>&end=YYYY-MM-DD` returns `since` (inclusive),
`until` (exclusive), `end` (inclusive), `step: "day"`, `unit: "Sm3/d"`, the resolved facility, a
constraint catalog, daily `points`, data-quality warnings and a period summary. Omit `ref` to use the
pack's facility; omit `end` to use that facility's latest `production_day` report. With no reports,
the window ends today and remains missing. A non-facility reference is rejected.

The backend reads the **existing resolved graph**, not a new connector:

- `production_day --reports_on--> facility` supplies `report_date`, `oil_sm3` (allocated daily
  standard-volume oil) and `oil_potential_sm3` (reported daily potential). For a one-day bucket their
  numeric values are daily-average rates in `Sm3/d`. `Sm3` means volume at the source's standard
  conditions; this feature does not mix gas volumes, convert standard conditions or infer oil
  equivalence from gas flow.
- Dated `part_of`, `located_at` and upstream `flows_to` links assemble the facility's areas, tags,
  wells and allocation nodes for each day. Link validity is `[valid_from, valid_to)`; cycles are
  deduplicated. Retired/merged entities and other facility roots are excluded. `lift_gas_to` is not
  a production-capacity path.
- The existing measurement store can supply explicit daily bounds with quantity
  **`system_oil_capacity`**, unit **`Sm3/d`**, no detection qualifier, and the constrained entity's
  Fuzer ID. The quantity deliberately means **equivalent total facility oil throughput allowed by
  this constraint**, not that item's local measured flow or nameplate rating. Each day's bound must
  already incorporate fluid composition, operating conditions and parallel-train/network effects.
  Category comes from that entity's golden `constraint_category`, then `ec_type`, otherwise
  `unknown`; examples are wells, separator, compressor, pipeline, export and facility. Existing
  source mappings' `measurements` blocks can land this quantity without a schema change.

For comparable bounds `C_i(d)` and allocation `A(d)`:

```text
dynamic_max(d) = min_i C_i(d)
binding_constraint_ids(d) = all IDs tied at that minimum
active_constraint_id(d) = lexically first binding ID (stable representative)
gap(d) = dynamic_max(d) - A(d)
PE(d) = 100 * A(d) / dynamic_max(d), when dynamic_max(d) > 0
period_PE = 100 * sum(A(d)) / sum(dynamic_max(d)), over paired valid days
```

The summary includes `paired_days` and paired volumes in `Sm3`, so coverage is explicit. The signed
gap is headroom/shortfall against this capacity reference, **not a separately reconciled deferment
volume**. Negative gaps and PE above 100% are retained and flagged, not clipped. Zero capacity is a
real value with undefined PE. Missing, negative, non-finite and ambiguous inputs are not converted
to zero. Multiple resolved reports for one facility/day are ambiguous, not summed. Different
bounds for the same constraint/day are ambiguous; identical duplicate bounds are harmless.
Daily bounds are not carried forward. The feature does not claim intraday bottleneck identification.
Unrepresentable derived percentages or volume totals are null with flags/warnings rather than
non-JSON-safe infinity; the period ratio uses scaled daily volumes to avoid overflowing its sums.

**Important coverage limitation:** the shipped sources contain reported oil potential, well tests,
equipment curves and pressure limits, but no declared `system_oil_capacity` measurements. When no
daily bounds exist, `oil_potential_sm3` is used as a **reported-potential fallback**;
`capacity_basis` is `reported_potential` and the physical `active_constraint_id` is null.
When bounds exist, `capacity_basis` is `declared_limits`; it is the minimum of those declared bounds,
not proof that every possible system constraint has been covered. An invalid declared bound prevents
silently falling back to potential or taking an optimistic minimum of the remaining bounds.
With neither usable bounds nor reported potential, capacity is missing.

Measured well-test rates are not unconstrained well potential. Pressure alarm/IOW thresholds,
learned normal ranges and downtime cause codes are not oil-throughput capacities and are never
compared directly or relabelled as binding constraints. A compressor gas rating or a separator's
local liquid rating needs an engineering conversion/network model before becoming a
`system_oil_capacity` bound. For parallel branches, taking the minimum of local capacities would
be wrong; this initial framework does not replace a network/nodal solver.

### Standards and reference verification

This is a documented **project metric**, not a claim of ISO conformity or a transcription of a
normative PE definition:

- [ISO 20815:2018](https://www.iso.org/standard/69983.html), *Production assurance and reliability
  management*, provides the production-assurance framework. Public
  [ISO 20815:2026 metadata](https://github.com/relaton/relaton-data-iso/blob/main/data/iso-20815-2026.yaml)
  identifies a replacement edition covering lower-carbon energy as well. The scope supports
  distinguishing production performance, system capability and reliability; it does not verify
  this implementation's denominator.
- [ISO 14224:2016](https://www.iso.org/standard/64076.html), *Collection and exchange of reliability
  and maintenance data for equipment*, maps to the graph's equipment taxonomy, failures and
  maintenance evidence. Its public scope includes availability applications, but is not a
  substitute for a production-assurance calculation methodology.
- [ISO/TS 3250:2021](https://www.iso.org/standard/79553.html), *Calculation and reporting production
  efficiency in the operating phase*, is the more direct PE-reporting reference. Its
  [public metadata](https://github.com/relaton/relaton-data-iso/blob/main/data/iso-ts-3250-2021.yaml)
  explicitly links ISO 20815:2018 Clause E.3/Annex G and ISO 14224:2016 Annexes A/B.
- OREDA-style equipment/failure terminology is useful for equipment reliability evidence, but
  time-based availability is not the same as this volume-based efficiency ratio.

Public metadata verifies these titles/scopes, not the full normative definitions. The requested
Burney Waring reference, *Practical Petroleum Production System Optimization*, could not be
independently verified from accessible bibliographic/contents material. **Chapter numbers,
definitions and attribution remain pending an authorized contents page or excerpt supplied by the
maintainer.** No chapters or formulas are invented or attributed to Waring. The system-bound
minimum above is an explicit simplifying assumption; nodal analysis requires coupled pressure/flow
models and is not implemented by this minimum calculation.

## Diagnostic calcs that return numbers

The charts were built for a person asking a question; detectors, the replay scorer and workflow steps
need the number behind them. Each check is a plain function used three ways: a `plot_*` chart for
people, an MCP tool for agents, and (from #96) a detector. They return one shape to keep as evidence and
cite: `{value, expected, deviation, deviation_pct, flag, basis, sources}`. `flag` is a short code only
when value and expected disagree enough to act on.

**Calcs (`calcs.py`, the `calc` tool).** The fits that lived inside the plot builders moved here, and
the charts draw from them (every chart on Okan is unchanged): the latest well test against its Arps
decline (`step_down`, `below_decline`), lift gas against the optimum on the well's curve (`more_gas`,
`less_gas`, `below_curve` for a valve failed open), a pump's head against its OEM curve at the same flow
(`head_loss`), a choke's effective Cv against its characteristic, judged on the readings since any
replacement (`eroded`, `restricted`), Weibull or Crow-AMSAA beta (`wear_out`, `early_life`,
`failures_accelerating`), and the fastest CML's wall loss against the item's median with remaining life
(`retire_soon`, `localised`). Each takes `until` so it can run as of a past time. `CHECKS` records who
uses each one.

**Instrument health (`signals.py`, `instrument_health`).** Whether a reading can be trusted, apart from
whether the process is upset. A reading is *frozen* when it doesn't change for 2 h, *stuck* when its
step-to-step jitter falls under a tenth of its usual for 6 h, and *noisy* when the jitter is over 4×
its usual for 1 h. Frozen and stuck only count while the unit runs. "Usual" means the 7 days before the
window. *Out of range* is sitting at or past the range end for 30 min, and *redundant drift* is A / B
transmitters on one unit moving apart from their usual offset for 2 h. Findings within 4 h of each
other merge. On Okan it finds 20-LT-2001 hunting from 02:10 on 4 November to 11:30 on 12 November
2024 (injected 02:00 to 12:30). The pressure transmitter on the same vessel stays clean.

**Change detection (`detect_changes`).** These checks find where a series steps, drifts or changes
its jitter, and when. A *step* is a move between the medians of the windows either side (3 h, or 6
steps) that stands out from how far those windows usually move in that series. A daily swing moves
them every day; a trip moves them once. A *variance* change is a jitter ratio of 4× or more that also
stands out from the series' usual ratios, and isn't next to a step. A *drift* is a straight-line trend
in the window medians between steps that clears the spread left around it (r² ≥ 0.7). On Okan these
find:
- train C's two trips as steps on every machine point;
- the transmitter fault starting and ending;
- each summer's after-cooler fouling as a drift ending at the clean;
- OK-NWP-G3's choke plugging as a gas-rate drift down, then a step at the bean change.

Both read only up to `until`. The replay's `Peek` is a `BaseException`, so a "skip an unreadable
point" handler can't hide a look into the future.

**What changed (`operations.what_changed`).** These are the records whose own time falls between two
times:
- log events and new notifications;
- work orders started and finished;
- downtime started and ended, and incidents;
- equipment installed or removed;
- limits taking or ending effect, and excursions;
- new well tests.

It is the starting snapshot for each SRE run and daily report. Dates without a time (SAP) count on every
window that overlaps their day. SAP notifications carry no closing date, so closed ones aren't listed.

## OEM curves

Pump curves (head, efficiency, NPSHr, power against flow) and choke characteristics (Cv against
opening) are reference data per equipment model, in `equipment_curve`, so every pump or choke of a model
gets its curve through `instance_of`. They arrive two ways:

- **Vendor exports:** CSV rows through a mapping with a `curves` block (`oem_curve.yaml`: of the
  model by `"{manufacturer}|{model}"` as `model_key`, series, x, y, units), plus rating figures (BEP,
  minimum continuous stable flow, preferred / allowable operating regions, max Cv, trim, xT).
- **Datasheets:** `llm_curves.py` sends each current datasheet once, with its page images and any text
  layer, and gets structured output back. Results are cached by file hash and become `curve` review
  items. Only accepted readings (`curve_digitised`, as read or corrected) land on the next rebuild.
  `fuzer eval` scores readings against the fake plant's true curves, per series the sheet shows. Live
  on Okan (Sonnet 5.5, $0.04 for 5 sheets): a text PDF with a table, a scan with a table, and three
  scans with only a drawn curve were all within 3% (curve-only head error 0.2-0.3%, efficiency
  0.5-0.8%).

Charts: `plot_pump_curve` puts PI operating points (head from discharge minus suction pressure) on the
OEM curve, with head against the curve month by month. `plot_choke` computes effective Cv (oil wells:
the test's liquid rate and the pressure drop to the jacket header; gas wells: PI rate against upstream
pressure, scaled on the early points) against the characteristic, with replacements as new series.
Without a curve, the chart says which datasheet is waiting. On Okan the process model follows the same
curves, so the stories read off the charts: export pump A down 8% of head by August 2025 until new wear
rings; OK-J12-02's choke passing 1.6x its curve until replaced on 15 October 2025; OK-NWP-G3's choke
restricted by about 35% in August until the bean change.

## Equipment performance

`equipment.py` adds three checks, each with a chart and listed in the reliability skill:
- **`compressor_performance(stage)`:** a stage's polytropic efficiency,
  ((k − 1)/k) ln(Pd/Ps) / ln(Td/Ts) with k 1.3, and head. The suction side is the scrubber before the
  stage and the cooler before that. Hourly values while running are compared: the last 3 days against
  the window's first week (`efficiency_drop`). There is no flow or OEM map, so no distance to surge, and
  the check says so. On Okan, 20-K-2300A's efficiency falls from 0.81 to 0.67 as its recycle cooler fouls
  through the summer and recovers after the clean; 20-K-2200B's falls with its bearing wear.
- **`cooler_fouling(cooler)`:** daily median outlet temperature against the window's coolest tenth of
  days (clean), the rate since the last drop (a clean), and when it reaches its high limit at that rate.
  20-E-2350A is flagged both summers and is clear after its 19 August clean.
- **`peers(ref)`:** the units of a class or model side by side (failures per year, MTBF, oil lost,
  vibration). Outliers are a robust z of 3 or more; `class_wide_failures` is set when the median unit fails
  1.5 times a year or more.

They are in `calc`, with `plot_compressor_performance`, `plot_cooler_fouling` and `plot_peers`. Pump
degradation is the existing `calc("pump_curve")`.

## Alarms, trips and states

`alarms.py` adds three analyses:
- **`alarm_analysis`:** ISA-18.2 / EEMUA 191 measures. With no alarm journal yet, an alarm is a point
  going past an enabled DCS / AF alarm limit while its unit runs (excursions on the alarm basis). It
  gives:
  - alarms per hour against 6;
  - floods (over 10 in 10 minutes);
  - chattering (3 or more on one point, each within a minute of clearing);
  - standing alarms (24 h or more);
  - the top bad-actor points.
  
  On Okan's week of 4–11 November 2024, it finds floods at both train C trips, the failed gas lift valve's
  low casing pressure standing all week, and the hunting level transmitter chattering.
- **`trip_analysis`:** per unit with a run signal, stops, starts and failed starts (stopped again within
  30 minutes), days between stops and the share of time running. There is no first-out alarm in the data.
- **`states`:** each unit's state spans (running, startup, stopped, transition).

The swimlane chart `plot_state_timeline` shows an upset spreading through a train; `plot_alarms` shows
alarms per 10 minutes against the flood line. MCP: `alarm_analysis`, `trip_analysis`. The troubleshooting
skill uses them for "how did it spread".

## SRE detection

`sre.py` finds upsets in the process data itself, every 6 hours (`sre_scan` in Dagster, `fuzer sre`).
Each detector is a window function, so the replay (`fuzer eval --ops --detector sre`) scores it
exactly as the schedule runs it:

- **excursions:** outside alarm limits, IOWs or the learned normal range while the unit runs.
  A learned-range excursion must last an hour: nobody set that limit, and brief wanders of
  day-night-swinging wellhead temperatures were most of the baseline's false alarms.
- **stops:** a unit with its own run signal going from running to stopped.
- **steps:** `detect_changes` steps on running units, judged against how the point moved over the
  day before the window.
- **drifts** (once a day): a month of what wears and fouls (vibration, bearing, discharge and cooler
  outlet temperatures, differential pressure) at 3-hour steps, still climbing at the end and up 5% or
  more.
- **instruments:** `signals.health`, skipping positions operators set (chokes, valves); this is
  instrument work, not an upset.
- **models** (once a day): pumps against their OEM curve over the last 30 days running, and gas lifted
  wells below their curve (a valve failed open). Optimisation opportunities (more or less lift gas)
  aren't upsets and go to the opportunity scan.

A run covers the time since the last one ended (`sre_run`), less an hour's overlap, in 6-hour windows,
so a missed run catches up. Detections go to `detection`. One still going at the end of its window is
extended by the next run rather than raised again.

**Grouping into upsets.** Detections become one upset when they start within 2 h of the upset's
latest detection and their units are within 4 hops in the plant model. The links counted are:
- `part_of`, `flows_to`, `lift_gas_to` and P&ID piping, both ways;
- the machines and vessels of one compression train (same area, same trailing letter), because the
  register doesn't link a train's stages;
- a tag otherwise on its own, to the manifolds in its area and to its loop family there (20-PT-3003
  with 20-FT-3002), which covers the header pressures and the jackets' gas lift meters.

The earliest detection is the likely first cause; at the same minute, a stop comes before an excursion,
and an excursion before a step. Bad instruments and slow findings (drifts, curves) are upsets of their
own, one per unit and finding. After each scan the recent detections are regrouped. An upset keeps its
id, and any status a person gave it, while most of its detections stay together.

Process upsets are signals in `incidents.py`, on their first-cause unit. Downtime, log events and
notifications on that unit within 3 days join the same incident: it is `detected` until one of them
confirms it, and lists its `upsets`.

On Okan, train C's trip on 5 November 2024 is one upset of about 1,100 detections, across the train,
the lift gas headers, the jackets and the wells. Its first detection is 20-V-2001's high level at 04:10:
the carryover that caused the 05:30 trip. The replay also scores upsets: groups per trip (1 is
right), whether a trip's group starts on the trip or on an upset that led to it, and false upsets a
week. MCP: `get_upsets`, `upset_detail`.

**Hand-off (`sre_handoff.py`).** After each scan, process upsets with 3 or more detections that
haven't been handed off are handled highest priority first:
- **Priority:** each is scored by #100 from its first-cause unit's oil lost per day to downtime,
  criticality and failure rate. One still going is due now; a recovered one is scored as a repeat.
- **Diagnosis:** while the run's cap allows (`ClaudeSettings.max_upset_diagnoses`, 10, and
  `max_sre_usd`, $5), the operations assistant's troubleshooter diagnoses it. It returns likely causes
  with confidence, the records that support each (a cause with none is marked uncited), what would
  confirm or rule each out, and proposed actions for people. A JSON block at the end gives the fields.
- **Work item:** each upset starts the `upset` process, or adds evidence to its open one. The work item
  carries the priority breakdown and the diagnosis for the engineer's triage.

An upset over the cap still gets its work item, and the workflow's troubleshooter step diagnoses it
after triage. When an engineer triages one as a false alarm, the upset is marked and its first
detection's pattern (point, detector, finding) goes in `sre_suppress`, so it isn't handed off again.
`fuzer sre --handoff` does the same by hand.

## Clustering and multivariate anomaly

`clusters.py` adds four analyses:
- **`modes(scope)`:** operating modes in two stages.
  1. The times the unit is stopped (its run signals) are one mode.
  2. The running time is clustered with k-means, on day-long medians of the points that carry its
     regimes: those whose two-week medians hold at least 80% as much of their variance as the slowest
     point's. Fast noise and the daily swing don't make modes; fouling or a new setpoint does.
  
  k is chosen by silhouette. On Okan's train A over 2024, it finds three modes from 20-TT-2350A (normal,
  fouled, stopped), with an adjusted Rand index of 0.77 against the process model's own. Clustering all
  20 points at once scored 0.06: fouling moves only two of them.
- **`entities("wells")`:** peer groups by decline, water cut and its rise, lift gas response and rate.
  **`entities("points")`:** points grouped by correlation distance.
- **`events()`:** SRE upsets grouped by signature (kind of unit and finding, Jaccard). Train C's two
  November trips are one recurring type.
- **`spe()`:** a PCA model of a training week, and stretches of a test window the model can't explain
  (squared prediction error past its 99.9th percentile), with the points contributing most. It is the
  SRE's daily `multivariate` detector, per process area, for 2 h or more. A multivariate finding names its
  largest contributor, not a cause, so it never leads an upset as first cause.

Two SRE fixes:
- A detection is extended only by the window right after the one that last saw it (`seen_until`).
  Before, a scan that skipped days carried an old open detection on and merged two trips.
- Regrouping loads every detection of each upset it touches.

MCP: `cluster_modes`, `cluster_entities`, `cluster_events`, `plot_modes`.

**Stored studies (`study.py`).** A correlation or clustering run is kept as `study:<id>` with:
- its parameters;
- its top results (bulky arrays left out);
- a one-line summary;
- a chart where it has one.

People can re-run a study from its saved parameters, an agent can cite it, and a work item can attach
it. A small study runs at once; a facility scope or a long window queues for the `studies_job`, which
runs every 5 minutes. API: `POST /studies`, `GET /studies[/{id}]`, `POST /studies/{id}/rerun`. MCP:
`start_study`, `get_study`. The Studies page (`#/studies`) starts, lists and opens them.

## Correlation studies

`studies.py` answers what moves with what across hundreds of points, and what moved first.
- **Loading:** points are picked by unit, area, plant path (`flows_to`, `lift_gas_to`), the facility or
  a list, then read through the time-series provider in parallel onto one grid. The matrix is cached
  as Parquet by points, window, step and provider (in the project's cache folder, `.fuzer/cache/<project>`).
- **Cleaning:** drops points that don't move or are mostly missing, fills short gaps from the last
  value, and leaves out rows where under 30% of the units with a run signal were running (unless asked
  to keep them). It can also detrend or difference.
- **`correlate(target, scope)`:** ranks every candidate by the strongest Pearson or Spearman r at any lag
  within ±`max_lag`, each on its overlapping part. A positive lead means the candidate moved first.
  Each candidate is marked as connected or not in the plant model (sre.topology, 4 hops).
  - Points moving with the target at r = 1 are often its siblings (the other wells on a header), so
    `leaders` lists the strongly correlated candidates (|r| ≥ 0.8 of the best) that moved first, earliest
    first.
  - On Okan, ranking the whole facility (1,007 points) against OK-J01-01's casing pressure across train
    C's trip puts the lift gas header flows, train C's speed and vibration, and the header pressures at
    the top of `leaders`, 10 to 20 minutes ahead.
- **`correlation_matrix(scope)`:** all pairs, ordered by average linkage on 1 − |r|, with the strongest
  pairs and whether the plant model connects each.
- **Speed:** 500 points over 90 days at 10 minutes take 4 s for the matrix and 5 s for a lag ranking once
  cached (the target is 60 s).
- **Size cap:** an interactive request is capped at 15 million values; bigger studies go to the
  background job.

`plot_correlation` uses the same `pearson` and `lagged`. Its charts on Okan are unchanged. MCP tools:
`correlate` and `correlation_matrix`, which give agents only the top results.

## Back-pressure

**Process model.** An oil well on a jacket feels the back-pressure.
- **Inflow:** at the wellhead it delivers in proportion to its shut-in pressure less its flowing
  wellhead pressure.
- **Wellhead pressure:** the back-pressure plus the drop across the choke, plus a flowline drop when a
  restriction builds (both ∝ rate²).
- **Solve:** while the choke is subcritical, the rate solves that quadratic against the back-pressure.
  At critical flow (back / wellhead pressure under 0.55) the back-pressure doesn't reach the well.
- **Back-pressure:** the jacket production header, which follows the HP separator's setpoint. After a
  reroute, it is the other header.

The inflow constant is set so each well makes its tested rate against the back-pressure its tests were
taken at, so without events nothing moves. Three new stories:
- a flowline waxing up from 1 October to 18 November 2025, when it is pigged;
- 10-V-2001's setpoint lowered 2 bar on 25 November;
- a well rerouted to another jacket's header on 5 December.

The generator scales the well tests after the first story by the model's rate factor, so test rates
show the response.

**Calcs (`hydraulics.py`).**
- **`pressure_profile`:** pressures down the flow path and each segment's drop.
- **`well_constraint`:** shut in, choke-limited or back-pressure-limited, from the pressure ratio, plus
  lift-limited when the well's gas lift curve says more gas pays. It agrees with the model's mechanism
  for 99% of wells.
- **`backpressure_sensitivity`:** oil per bar for each back-pressure-limited well, two ways. The
  model-based figure differentiates the same equations, using the shut-in pressure seen in PI. The
  data-based figure regresses the well's tests on header pressure, with the decline taken out. Wells
  where the two disagree are flagged.
- **`dp_drift`:** the wellhead-to-header drop rising by at least 0.7 bar and 50% of where it started,
  more than the rate's own wander between tests moves it. It finds the waxing flowline from about three
  weeks into its seven-week build.
- **`headroom`:** each point on a unit against its nearest #101 limit.

`dp_drift` is also the SRE's daily `hydraulics` detector. MCP: `pressure_profile`,
`backpressure_sensitivity`, `capacity_headroom`, and `calc("well_constraint" | "dp_drift")`.

The production separator each jacket follows is its hub's: PP for 10-V-2001, ST1 for 30-V-2002, ST2 for
40-V-2002. `dp_drift` measures a rise on the drift itself, so a step before it (a choke move) doesn't
count, and it ignores a rise while the choke moved.

**Debottleneck options (`debottleneck_options`).** These are ranked by oil gained, using the
model's sensitivities:
- lower a separator to its next limit (an IOW binds before an alarm);
- pig a flowline whose pressure drop is rising;
- reroute a well to a header at least 1 bar lower;
- open a choke throttled under 80%.

Each option comes with its basis, confidence, the next constraint and what would confirm it. On Okan
before 25 November 2025, the top option is lowering 10-V-2001 by 2.1 bar to its IOW low of 9.9 barg.
At 80 Sm3/d of oil per bar, that matches the model's own response to the 2-bar cut it applies that day
(80 per bar). The day-close job stores the options (`debottleneck_option`), #100 scores them as
opportunities, and they appear on the work board.

**Charts:** `plot_pressure_profile`, `plot_backpressure_sensitivity`, `plot_choke_criticality`,
`plot_dp_drift` and `plot_debottleneck`. The production specialist's new `back-pressure` skill works
through these steps: classify the wells, find the drop, what a bar is worth, the next constraint, the
options, and the uncertainty. `constraint-management` points to it.

## Opportunity scan

`opportunities.py` runs daily after the daily report (the day-close job). It looks for gains left on the
table, so the board ranks optimisation against problems:
- **gas lift reallocation per header:** gas moves from wells past their optimum (one more kSm3/d gains
  less than 0.5 Sm3 oil) to wells short of theirs, from each well's curve. The gain is the gas moved times
  the difference in marginal response, and the header's total gas is unchanged;
- **wells below their decline**, and flowing wells with tests older than 90 days;
- **units worth a permanent fix:** 4 or more failures that lost oil;
- **wells shut in by downtime that hasn't ended.**

Back-pressure options come in the same way.

Each opportunity has a stable key in `opportunity` with its gain, basis, confidence, cited evidence and
what would confirm it. It becomes an `opportunity` work item for the engineer role, valued by #100, and
the board shows the scan item and its work item as one card. #100 reads the scan's open opportunities
once it has run; before then, it uses its own simple gas lift moves.

An engineer accepts, rejects (a reason is required) or turns an opportunity into a board project
(`POST /opportunities/{key}/decision`). A decided opportunity isn't raised again unless its evidence (the
tests behind it) changes or its gain moves by half or more. One the data no longer supports is closed,
with its work item. MCP: `opportunities`.

## Troubleshooting brief
One call (`troubleshooting_brief` in MCP, `/entities/{id}/brief`) gathers, for an
incident or a unit's latest incident: the timeline, similar past incidents,
approved lessons for its model or class (and unreviewed drafts, labelled),
manual / guide sections covering its failure modes, documents that apply,
maintenance history, downtime, spares in stock and suggested checks — each with
the id it came from. An MCP prompt (`troubleshoot`) tells agents to start there,
cite everything and never instruct anyone to operate plant directly. Engineers'
ratings go to a `feedback` table.

Process values come through a pluggable time-series provider (the project's `timeseries`):
Fuzer asks for values when needed and never copies them. `pi` reads interpolated
values from PI Web API by point name; `adx` reads them from Azure Data Explorer
through the pointer the series catalog put on each timeseries entity (ADX bins
them); `synthetic` (the default, needs nothing) makes plausible signals per PI
point with the asset's incidents in them. Values around an incident centre on
its first timed signal (log entry or downtime start) and show as small-multiple
trend charts on incident pages, in the MCP `process_values` tool and in the
brief. Each series carries a summary for agents: min / max / mean / last and
`lead_in`, the 12 h before the centre against the hours before that, so "what
moved" needs no chart.

The incident page shows the brief (checks, what moved, lessons, spares) with a
Useful / Not useful rating and comment; the Incidents page shows how many past
incidents have been rated and the next ones to rate (`/feedback/briefs`).

## Scoped knowledge search
`knowledge_index` (kept by every rebuild) holds document sections and incidents
with a weighted full-text vector (headings over body) and the IDs each is linked
to. Searching "for" an entity works out its scope (the entity, the tag or
equipment it's installed at, its model, its classes up the hierarchy, its parent
tags and area) and keeps items linked to anything in it (one GIN array overlap).
Score = text rank x closeness (this tag > model > class > area) x authority (OEM
manual / procedure > site guide > past incident); questions match on any of
their words. Results are cited passages (document, revision, page, section,
snippet, why it applies). Superseded revisions are left out by default.
Sections carry the ISO 14224 failure modes they address: keyword rules in the
standards file (packs can add more), refined by an optional cached Claude reader
that can also narrow a section to one model / class / tag from its candidates.
Searches can boost a failure mode, and each incident gets suggested reading:
sections in scope of its unit that cover its failure modes.
Embeddings (pgvector) can be added beside full text once an embedding model is
chosen (an external service).

## Lessons learned
Per equipment model (or class) and ISO 14224 failure mode, incidents with
corrective work orders draft a lesson: the corrective actions that were done,
how many incidents it rests on, and how often the fix held for 30 days, with the
incidents as evidence. Lessons live in their own table: engineers approve, edit
or reject them on the Lessons page, and rebuilds refresh only a draft's evidence
and numbers. Approved lessons join knowledge search (authority between site
guides and past incidents), scoped to their model or class. Teams thread
summaries will feed the same drafts.

## Spare parts
SAP materials (manufacturer part number, stock, bin) and equipment BOMs: equipment
`uses_part` material. Document sections that name a manufacturer part number
`mentions` that material (one compiled pattern over all part numbers, one pass
over section text). Spares for a tag (via what's installed), equipment or model
list each part with stock (zero flagged), bin, how many units use it and the
manual sections naming it.
