# Roadmap

Fuzer is one router of work from many sources, each matched to an asset through the register, and judged from the
IOC seat and the field seat: how work arrives, is triaged, routed, dispatched and closed out. This page is the short
version of the platform roadmap. Status lives in the GitHub issues it links.

## The loop

Every epic makes one step of this loop better for a work type, on any asset: work **arrives** from a source or a
detector, is **matched** to an asset, **routed** to a queue and role, **triaged**, **dispatched**, **done** in the
field on a phone, **closed** back to the source, **counted**, and **learned** from.

## Phases

Phase 1 gates Phases 2, 3 and 4. Phase 5 needs only Phase 0 and runs beside it.

| Phase | Goal | Exit gate |
|---|---|---|
| 0 Land and baseline | Finish what is in flight on one clean surface | Work has four entries, every page passes the Clean style lint, no agent step waits silently |
| 1 Platform spine | One contract each for findings, detectors, methods, measurement roles, source packs and bundles | **Gate A:** the loop eval runs arrive to close on Okan and Kestrel in CI, with registered detectors and bundle configuration only |
| 2 Context and data quality | Context builds itself, and data problems are routed work | A new source added from the UI is matched and linked with under 10% needing a steward |
| 3 Detection | Deterministic coverage by equipment class, plus an agentic watch | Every detector has a replay score on both bundles; the watch has run in shadow with measured precision |
| 4 Specialists | Standards-backed specialists that pre-read every case | Six specialist packs pass their evals; every new case has an agent brief |
| 5 Work and field | One pane for everyone, a phone-first field, a workflow designer | **Gate B:** every work type closes end to end, write-back included, with the field leg on a phone |
| 6 Learning | Closed work improves agents, detectors and the register, under review | A confirmed false alarm changes a detector and a reviewed case changes a skill, each through an eval gate |
| 7 Analytics | One set of measures for plant, process, detectors and agents | An asset manager answers "are we keeping up, and did it stick" from one page |
| 8 Standards | One operating model across assets | **Gate C:** a third bundle of a different asset type goes live without code changes |
| R Production readiness | Cloud, access control, OT security and historian connectors, all proven without a customer | The deployment passes its preflight, a restore is rehearsed, access tests pass, and the PI and ADX connectors pass on recorded responses |

## Build order

Six waves across four lanes, so sessions can work at once without touching the same code. Within a cell, epics are
built left to right.

| Wave | Work and UI | Detection and platform | Context and sources | Specialists |
|---|---|---|---|---|
| 1 Baseline | P0.1, P0.2, P0.3 | P0.4, P1.1, P1.7 | P1.6 | P1.3, P4.6 |
| 2 Spine and rails | P5.10, P5.7, P5.2 | P1.2, P1.4 | P1.5, P2.2 | P4.4, R.7 design note |
| *Gate A* (passed 2026-11-12) | | Loop eval green on Okan and Kestrel | | |
| 3 First value | P5.3, P5.1, P6.1, P7.2 | P3.5, P3.8, P3.2, P3.9 | P2.3, P2.4, P2.5 | P4.1, P4.3, first pack of P4.2 |
| 4 Depth | P5.5, P5.11, P5.4, P5.6, P5.8, P5.9 | P3.1, P3.10, P3.6, P3.3 in shadow, P3.7 | P2.1, P2.6, P7.1 | Packs 2 to 4 of P4.2, P4.5 |
| *Gate B* (passed 2026-11-13) | Every work type closes end to end | | | |
| 5 Compounding | P7.3, P7.5, P7.6, P5.12, P5.13, P5.14, P5.15, P5.16 | P6.3, P3.4, P7.4, P3.11 | P6.5, P2.7 if needed | P6.2, P6.4, packs 5 to 8 of P4.2 |
| 6 Standardize | P8.1, P8.3 | P8.2 | P8.4 with the third bundle | Packs proven on the third bundle |
| *Gate C* (passed 2026-11-13) | | | Third asset type live, no code | |

Gate A was signed off on 2026-11-12. One gap is logged for Phase 2: in the loop eval, steward questions and source
and mapping problems are still raised by the loop instead of arriving on their own (#747).

Gate B was signed off on 2026-11-13 with write-back outside the loop eval. Both of its gaps are now closed (#976): the
loop's write_back step sends the field's close-out through the write-back queue to a stand-in SAP PM service, a named
planner approves it and SAP's confirm releases the case (#751), and every bundle's field jobs render at 390 px (#963).
The loop closes every work type on Okan (40), Kestrel (36) and Harrier (34).

Gate C was signed off on 2026-11-13. Harrier, a crude pipeline and terminal with no wells, adopts standard v1 with no
deviations and closes every work type that applies in the loop eval. It carries no code of its own; standing it up
found five platform gaps, each fixed for every asset (#821, #827: far-off dates, packs with no wells, pump trips, adopt
on a new project, and the fake plant's pump vibration). Its gap is closed (#962): the deploy test registers every
shipped bundle, and Harrier runs on the Render instance as its own project, with its detectors and work on schedule.

Landed so far: P1.1 One finding contract, P1.3 Method registry ([Methods](methods.md)) and P1.2 Detector registry
([Detectors](detectors.md)).

## The agentic watch (P3.3 #931, P3.11 #1016)

P3.3 closed on 2026-11-13 with the watch running in shadow and its precision measured: on one Okan window, 1 of 3
proposals matched an upset no rule caught. That meets Phase 3's exit, and it is too small a sample to let the watch
raise work. So live mode is out of the demo and is its own long-term epic, P3.11: a statistical drift detector the
watch leans on for slow drifts, at least five scoring windows on each of the three bundles, and live mode behind a
per-asset setting only once half its proposals are right across all of them.

## Pilot track

Three things can't be finished by building: they need a real asset team, its exports and its historian. They sit in
their own track, outside the roadmap's percentage, so the build can reach 100% and the pilot is counted on its own.
They were R-track items 1 to 3 until 2026-11-13.

| Pilot item | Needs from the site | Done when | Ready now |
|---|---|---|---|
| PL.1 Real data for one area | Exports for one area: tags, equipment, work orders, logs, drawings | The area loads, and the tag grammar and asset pack are tuned on its real names (#9, #10) | Bundles, source packs and the realistic fake plants (#858) |
| PL.2 Live historians at the site | Read-only access to the site's PI Web API or ADX | PI AF and points load without files and match a file export of the same server; the series catalog refreshes on a schedule (#15, #16) | The connectors, proven on recorded responses under R.2 |
| PL.3 Measured on real work | Engineers' time, and past incidents with known causes | Reader accuracy is measured on the site's logs and drawings, and engineers rate briefs on real past incidents (#11, #221) | The evals and answer-key format, scored on three fake plants |

The Production gate, a real asset team running a shift on Fuzer, is the pilot's exit, not Phase R's.

R.2 (#1017) stays in Phase R as the part that can be built today: the PI Web API and ADX connectors run against responses
recorded from a real server where one is public, or taken from the vendor's reference where none is, instead of only
against Fuzer's own fake historian.

## Historian views (P5.12 #967, P5.13 #968, P5.15 #1012)

An IOC engineer looking at a tripped compressor wants its suction and discharge pressures, temperatures, speed and
vibration on one time axis, with the alarms, log entries, cases and work orders beside them, not a grid of one chart
per tag. Two epics in the work and UI lane do this, P5.12 first.

| Epic | Build | Done when |
|---|---|---|
| P5.12 Trend templates by equipment class | A template per equipment class (ISO 14224 code, or well) names groups of measurement roles, the order they read in and which are shown first. Templates ship with the platform; a bundle adds or overrides one in its configuration. A view resolves the entity to its unit, its class, the template and the points holding each role there (P1.4). Points with no role in the template sit in an "Other points" group, off by default. | A compressor on Okan and on Kestrel opens with its pressures, temperatures, speed and vibration grouped from roles, with no tag names in code. Pumps, wells, vessels and heat exchangers have templates too. A role the template wants but the unit lacks is named, not silently dropped. A bundle override changes the view with no code change. |
| P5.13 One trend with event lanes | One large chart with a shared time axis. Each template group is a panel, and traces in a panel share an axis per unit. Beneath them are event lanes: alarms, operator log, cases, work orders and downtime. Every trace and lane has a toggle, the defaults come from the template, and the choice is kept in the link. A crosshair reads every shown trace at one time, and an event marker opens its record. It replaces the per-tag small multiples on the entity page, and the case page shows the same chart over its event window. | Opening the compressor behind an Okan trip case shows the trip's pressures, temperatures, speed and vibration with its alarms, log entries, case and work order in lanes, at the trip time. Each trace and lane toggles, and a shared link restores the choice. Every marker links to its record, every trace names its point, unit and provider, and synthetic values are labelled. It works in light, dark and on a phone. |
| P5.15 A Timeline viewer that reads at a glance | An audit of the P5.13 chart from the IOC desk and a phone found it works but makes the reader do too much. Its chips take a screen of space above the chart. A trace is matched to its chip only by colour, far above it. A panel can stack four value axes. Sixteen alarms collapse into one bar. Plotly's floating toolbar covers the top of the chart. The slices: (a) declutter: no floating toolbar, a Reset zoom of our own that shows only after a zoom, one control row, and the heading names the unit; (b) direct labels: each trace named at its right edge in its colour with its last value, at most two value axes a panel (a third unit gets its own panel), and the shaded lead-in labelled in the chart; (c) lanes that read: alarms one row per point by priority, crowded markers counted, and a clicked event opens a detail card under the chart instead of leaving the page; (d) a pinned cursor: click to pin a time, read every trace there, and pin a second time for before and after; (e) zoom reads detail: zooming in re-reads values at a finer step, and the step is shown; (f) phone: one control row, shorter panels, swipe to move the window. | On the Okan 20-K-2200B trip, every trace is named on the chart without the chips. No panel has more than two value axes. Each alarming point has its own row. A pinned before and after reads the trip's drop in every value. No toolbar covers the chart. On a 390 px phone the controls are one row and the chart starts in the first screen. Zooming to an hour shows values at the provider's finest step. Each slice has a browser test. |

## The installed app on a phone (P5.14 #984)

A field operator dispatched to a job has the phone in one hand, maybe gloves on, maybe one bar of signal. Added to the
home screen, Fuzer opens without the browser's back, reload and address bar, and iOS discards it in the background, so
the way back to the job is lost. One epic in the work and UI lane makes the installed app easy to get around.

| Epic | Build | Done when |
|---|---|---|
| P5.14 The installed app on a phone | A web manifest and icons so it installs standalone. An in-app Back that walks the app's own history and, with nothing behind it, goes up to the page's owner, never out of the app; a cold launch resumes the last page. A bottom bar within thumb reach on a phone. Going back keeps scroll, tab and filters. The shell and the kept jobs open without signal, links land on the right page, and a page can be shared. | Installed on iOS and Android it opens standalone. Back returns through the pages visited and never leaves the app; a cold launch resumes. Home, My work, Explore and Investigate are one tap away at the bottom with targets of 44 px or more. Back to a list restores its place. With no signal it opens to My work with the kept jobs. A browser test walks a dispatched job at 390 px in standalone display and back again. |

Drawings on a phone (P5.14f): a P&ID opens on the whole screen from its card (or a tap on the sheet), with pinch and
wheel zoom, drag to pan, double-tap, Fit, and Find for the ringed item. A tap on a tag says what it is and opens it. An
off-page connector, or a reading that names another sheet, goes through to that sheet in the viewer, and Back returns
to the one before. New readings place each off-page connector on the sheet; older ones take the place where the other
sheet's number was read. The home-screen icon is in the page itself, since iOS saves a linked icon by fetching it
again and got a letter tile.

## Schematic 3D views (P5.16 #1032)

An IOC engineer with a compressor trip on the board wants one picture of what that train feeds and what is behind it,
and a field operator wants to see which jacket and which piece of kit a job is on before the boat lands. One epic in
the work and UI lane draws a facility from generic shapes, laid out from the links Fuzer already holds. It is a
schematic for reading flow and scenarios, not a digital twin and not to scale; where a customer has a real 3D model,
#871 links out to their viewer.

| Epic | Build | Done when |
|---|---|---|
| P5.16 Schematic 3D views of flow and scenarios | A scene read on the server: each item a generic shape by ISO 14224 class (a table a bundle can override), areas as plates and jackets as lattice frames, links as pipe runs, and a deterministic layout from flow order where there is no geometry. A 3D tab in Explore, loaded on demand, with a card that opens the asset page. Then status on every shape, lit flow paths, trip and reroute scenarios, the field by latitude and longitude, and a 2D fallback. | Okan, Kestrel and Harrier each draw from the register alone. Every shape shows open work, alarms and downtime and opens its asset page. Upstream and downstream of a tripped compressor light up, a trip names what goes down with it, and a rerouted well shows old and new paths without writing anything. It holds 30 frames a second on a phone, and draws in 2D where 3D is not available. |

P5.16a has landed the scene, the layout and the 3D view. Slices b to f add status, paths, scenarios, the field and the
phone and 2D work, in that order.

## How the work is tracked

| Thing | How |
|---|---|
| Epic | One issue titled with its id and name, for example "P1.2 Detector registry", holding Build, the "done when" list and Builds on. Filed when a session starts it, so the backlog holds no stale epics. |
| Slice | One sub-issue per pull request under its epic, written when the epic starts. |
| Labels | `epic`, `phase:0` to `phase:8` and `track:R`, `lane:work`, `lane:platform`, `lane:context`, `lane:specialists`, `model:fable` or `model:opus`, `size:S`, `size:M`, `size:L`. |
| Pull request | Its body says "Part of" its epic and "Closes" its slice. One behavior per pull request, merged before the next starts. |
| Closing an epic | Only when every "done when" box links the test that shows it, and the loop eval is green. |
| Existing issues | Each becomes a sub-issue of its epic when that epic is filed, using the map below, or is closed with a reason. |

[Open epics](https://github.com/areed145/fuzer/issues?q=is%3Aissue+is%3Aopen+label%3Aepic) ·
[Closed epics](https://github.com/areed145/fuzer/issues?q=is%3Aissue+is%3Aclosed+label%3Aepic)

## Where the earlier issues go

Each issue open before the roadmap belongs to the epic that will deliver it. When a session files that epic, it
attaches these as sub-issues.

| Epic | Issues |
|---|---|
| P0.1 Finish screen rationalization | #501, #504, #506, #508 |
| P0.2 Clean style on every page | #290 and its page polish issues #296, #297, #298, #299, #300, #301, #303, #305, #306, #308, #309, #321, #322, #323, #324 |
| P1.6 Bundles complete | #376 |
| P2.1 A new source contextualizes itself | #747 (Gate A follow-up: steward, source and mapping work arrives on its own in the loop eval) |
| P2.2 Calibrated match confidence | #94 |
| P2.3 Data quality as routed work | #505, #316 |
| P2.7 Richer plant structure | #61, #63, #205 |
| P3.1 Deterministic library build-out | #339, #341, #342, #343, #345, #347, #351, #354 |
| P3.7 Source packs that bring new work | #369, #371 |
| P4.2 Specialist packs | #333 (umbrella), #338, #355, #356, #357, #358, #359, #363, #364, #365, #366, #367, #368 |
| P4.6 Shared analysis foundation | #334 |
| P5.5 Ready to dispatch | #349, #361, #362, #370 |
| P5.6 Workflow designer | #317, #318, #319 |
| P5.8 Work that starts in conversation | #22 |
| P5.9 Plan by window | #337, #360 |
| P7.2 How the work is running | #348, #350, #373 |
| P7.3 How the plant is running | #336, #352, #353 |
| P7.5 Across assets, and value realized | #335, #340, #372 |
| PL.1 Real data for one area (pilot) | #9, #10 |
| PL.2 Live historians at the site (pilot) | #15, #16 |
| PL.3 Measured on real work (pilot) | #11, #221 |
| R.4 Cloud deployment | #12, #14 |
| R.5 Access, audit and retention | #29 |

## Principles every epic is judged against

- **Agents read and propose; people decide.** Anything that changes plant, maintenance, schedule or commercial state
  is approved by a person, and nothing is written to a source system without a named person's approval.
- **Nothing ships unmeasured.** Every detector, reader, matcher and specialist skill has an answer key and a score in
  CI. Learning changes pass the same gate.
- **Asset-type agnostic.** Facility names, tag patterns and asset types do not appear in `src/fuzer/`. Every behavior
  is proven on Okan and on Kestrel.
- **Extend, never fork.** Reuse the owning domain, registry and UI kit. A new page, table or service states why an
  existing one cannot serve.
