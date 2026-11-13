# Time series

Process values from PI and ADX, operating limits and excursions.

## PI Web API and ADX connectors
On a `pi_web_api` connection in the source catalog, the `pi_points` and `pi_af` datasets load from
PI Web API instead of files (`pi_web_api.py`): the point list paged, point and
element attributes through `/batch`, yielding the same rows as the file exports,
so mappings don't change. On an `adx` connection, `adx_series` reads the series
catalog (`adx.py`) and the `adx_series.yaml` mapping merges cluster, database,
table and series id onto the PI point's timeseries entity. Live datasets have no
cheap change marker, so the file sensor skips them and the daily `refresh_all`
loads them; an unchanged server is still skipped by content hash. Setting `sources.pi.url` or `sources.adx.url`
(or the old `FUZER_PI_WEB_API_URL`, `FUZER_ADX_URL`) makes the connection on the next load and moves the datasets
onto it; `fuzer sources test <id>` checks it step by step. The time-series providers read values through the first
enabled connection of their type.

`uv run fakeplant historian` serves a fake PI Web API and ADX (a small KQL subset)
over a fake plant's points, with upsets taken from the plant's own downtime and
log records, for development and tests.

Coupled synthetic values: a fake plant can ship a process model
(`process_model.json`). It lists its units (compressor trains, compressors,
coolers, vessels, lift gas headers, jackets, wells, pumps, meters, valves), which
PI point measures which variable, and its stories as timed events taken from the
same downtime and log records. `process_model.py` evaluates it with numpy on a
fixed 5-minute grid in absolute time: inputs are stateless, lags are truncated
convolutions with 12 h of warm-up, and values are interpolated to the requested
times. A timestamp therefore reads the same value from any window or step, and
the fake PI / ADX serve the same numbers. Relations: compressor discharge
pressure and temperature by stage, heat from a failing bearing after a lag, a
trip taking a train's capacity out of the lift gas header, header pressure
setting wells' injection rate and casing pressure, oil from lift gas by each
well's performance curve, wellhead pressure through an equal-percentage choke,
pump head and power from a quadratic curve, a hunting level transmitter, and
liquid carryover between vessels. On Okan, 998 points are modelled; the rest,
and the Okan asset, keep the per-point signals. A month of 12 points reads
in about 20 ms.

Instruments and the historian (#868): real readings also carry what the instruments and the historian did
to them, so a fake plant can list planted faults in `instrument_faults.json`, which is also their answer
key (`sources/instruments.py`). A reading can freeze at its last value, stick (following the process at
2% of its true change), turn noisy, pin at the top of its range, drift for days and be recalibrated, or
be stored as a digital state (Bad Input, Comm Fail) instead of a value. An interface outage stores
I/O Timeout on every point of one jacket's RTU for an hour or more. Faults apply in absolute time like
the model. The synthetic provider leaves bad values out, as the PI reader does, and the analyses line
values up by time, not by order. The fake PI serves the states as PI system digital states (`Good`
false), and `recorded` returns the archive after swinging-door compression (the point's `compdev`, else
0.2% of its level). Okan plants about 25 faults and 3 outages from June 2024 to December 2025, away from
the quarter its normal ranges are learned on. They go on transmitters with no alarm or IOW that aren't a
run signal, so limits, excursions and operating modes are unchanged. Instrument health finds every
frozen, stuck, noisy and pinned fault. Drift needs a redundant twin to find, and a gap is only a gap.

Real well upsets (#904): `evals/3w/` keeps eight real instances from Petrobras's 3W Dataset (CC BY 4.0,
its licence and attribution beside it): two of normal operation, and an abrupt rise in water cut, two
spurious DHSV closures, a rapid productivity loss and two quick choke restrictions, each a few hours at
1 Hz with its onset labelled. `fuzer.evals.wells3w` replays one onto a fake Okan well (`Overlay`, a source
over any other): tree pressure as WHP, tree temperature as WHT and annulus pressure as CASP, scaled to the
well's level, the last value held for three hours after the record ends. `score(src)` runs change
detection as the SRE's steps detector reads (10-minute steps, 180-minute windows) and instrument health
over each. Today it finds 5 of the 6 upsets (it misses the water cut, a slow fall over two hours), and dates
two of them too early at that coarse step, which count as false alarms. At 1-minute steps and 60-minute windows
it finds all 6, each within 45 minutes of its onset. Health finds all three stretches where a 3W reading is frozen in the source (one well's annulus pressure, for hours), and
raises one noisy reading as that pressure starts moving again.

A real rig's faults (#904): `evals/pronto/` keeps the three labelled days of the PRONTO benchmark from
Cranfield's three-phase flow facility (CC BY 4.0, its licence and attribution beside it): an air blockage,
three air leakages, two diverted flows and slugging, one row a second with the rig's alarms, 17 process
readings and a label. `fuzer.evals.pronto` runs change detection over every reading but the air and water
flow in, which the operators set by hand: a change in normal running within 15 minutes after they move
one is explained. The tests last minutes, so it reads 1-minute medians in 20-minute windows. Today it
finds 6 of the 8 fault onsets, a median 6 minutes in, with no unexplained change in 3.3 hours of normal
running. It misses the air blockage, which starts 6 minutes into its day, and the second bout of slugging.

## Trend templates (P5.12)

An entity's trend reads by its equipment class, not one chart per tag. `core/trend_templates.yaml` holds a template
per ISO 14224 class: groups of measurement roles (P1.4), each group a panel, in the order an engineer reads them, some
switched off at first. A trace is a role on the unit, or on a neighbour: its `driver`, what it has `driven`, what is
`upstream` or `downstream` of it. A compressor's suction pressure can come from its scrubber and its speed from its gas
turbine. `GET /entities/{id}/trend-view` (`analysis/trend_view.py`) returns the groups with each trace's values,
point, units and shared axis, the unit's other points (switched off), and the roles the template expects but the unit
has no point for. A class with no template is grouped by what each point measures. Values come through
`values.around`, so the window, limits and provider match the rest of the entity page.

## Operating limits, IOWs and excursions

An excursion needs a limit, and "high" depends on what the unit is doing. Limits are data, per PI
point, dated and with their source record, in `point_limit`:

- **Alarm limits** (HiHi / Hi / Lo / LoLo): PI AF limit traits on an attribute (`pi_af_element.yaml`,
  a `limits` block over each attribute) and DCS / SCADA alarm exports (`dcs_alarm.yaml`: one row per
  alarm, the DCS's names for the types mapped, enabled flag, priority, last change). When both give
  one, the first source in the pack's `limits.priority` counts (Okan: the DCS, which actually alarms).
  Where the DCS also exports its alarm and event journal (`dcs_alarm_journal.yaml`, #859: ALARM, ACK, RTN,
  SHELVE, SUPPRESS and STATE events to the second), the alarm analyses count the alarms it logged over the
  windows it covers, matched to points through these limits, instead of deriving them from readings.
- **Integrity operating windows** (API RP 584, `iow_window.yaml`): class (critical / standard /
  informational), low and high limit, time allowed outside ("2 h", "24 hours"), damage mechanism and
  response, effective and superseded dates. A register written against an instrument tag counts for
  that tag's one point.
- **Learned normal ranges** (`point_normal`, `fuzer limits learn`): the 1st, 50th and 99th percentile
  of each point per operating mode of its unit over a window (default 90 days). A point with no
  source limits uses the running range, with a margin, labelled learned. Positions operators set
  (chokes, valves) are left out (`limits.not_learned`).

**Operating mode.** A point's unit is the equipment tag its instrument belongs to, or the well. A unit
runs when its run signal, the first of its points matching the pack's `limits.run_signals` (Okan:
motor power, turbine speed, vibration, choke opening), is above a level; it is in `startup` for
`settle_minutes` after it starts. Units without a signal (scrubbers, coolers) follow the units in the
same area and train (the train `graph.structure` reads from piping and drives), else the area. Excursions count only while
running, so a stopped pump's low flow, or a standby pump, raises nothing.

**Checks** (`limits.issues`, `fuzer limits issues`, `/limits/issues`): sources disagreeing (AF not
updated after an MOC), out of order (Hi above HiHi), disabled, standing (inside the learned normal
range), implausible (many times the normal value: often psi typed against barg), and missing (no
alarm limits where most points measuring the same thing have some), and drawing: a P&ID's alarm and
trip table (landed as `design` limits, source `pid`, that never count themselves) differs from the alarm
that counts, or shows settings no source configures. Limits flagged standing,
implausible or out of order aren't used for excursions.

**Excursions** (`limits.scan`, `fuzer limits scan`, the `excursion` table): stretches outside a
limit while running, with start, end (open while still outside), peak and minutes outside. An
excursion ends only once the value is back inside by a 1% deadband, as an alarm's does, and back
outside within 30 minutes is the same one. A scan extends an excursion still open from the last one.
An IOW breach past its allowed time (critical or standard) starts an `iow_breach` work item (engineer
review, `requiresHuman`, then a notification if inspection or repair is needed), one per IOW while open;
later breaches go to it as evidence. The SRE will run the scan every 6 hours and group what it
finds.

Limits show on trends (dashed lines, IOWs solid, learned grey; off-scale ones named), on each series
from `/entities/{id}/values`, in the troubleshooting brief, and through the `limits(ref)` MCP tool.

Okan's limits are set from its process model run without the stories (choke moves kept), so the
stories cross them: train A's fouled 3rd stage after-cooler breaches IOW-001 (critical, 144 degC, 2 h)
on hot afternoons in summer 2025 and IOW-004 (standard) for weeks; the inlet separator's high level in
November 2024. Planted faults (answer key `limits.json`): AF Hi typed in psi, DCS limits moved without
AF, alarms disabled, Hi above HiHi, a Lo inside the normal range, points with no limits, and an IOW on
a tag that doesn't exist. The P&IDs' alarm and trip tables show the limits as designed in 2019, so
every alarm moved since, or never configured, also disagrees with its drawing. The checks find all of
them and nothing else (learning on a quiet quarter).
