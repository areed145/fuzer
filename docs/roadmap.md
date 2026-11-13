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
| R Production readiness | Real data, live historians, cloud, access control | A real asset team runs a shift on it |

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
| 5 Compounding | P7.3, P7.5, P7.6, P5.12, P5.13, P5.14 | P6.3, P3.3 live, P3.4, P7.4 | P6.5, P2.7 if needed | P6.2, P6.4, packs 5 to 8 of P4.2 |
| 6 Standardize | P8.1, P8.3 | P8.2 | P8.4 with the third bundle | Packs proven on the third bundle |
| *Gate C* (passed 2026-11-13) | | | Third asset type live, no code | |

Gate A was signed off on 2026-11-12. One gap is logged for Phase 2: in the loop eval, steward questions and source
and mapping problems are still raised by the loop instead of arriving on their own (#747).

Gate B was signed off on 2026-11-13, with one gap logged: write-back is proven in its own tests but the loop eval's
close step doesn't go through the write-back queue yet (#751). The loop closes every work type on Okan (40), Kestrel
(36) and Harrier (34), and the field leg's jobs pass at 390 px; only Okan's field jobs are rendered on a phone so far
(#963).

Gate C was signed off on 2026-11-13. Harrier, a crude pipeline and terminal with no wells, adopts standard v1 with no
deviations and closes every work type that applies in the loop eval. It carries no code of its own; standing it up
found five platform gaps, each fixed for every asset (#821, #827: far-off dates, packs with no wells, pump trips, adopt
on a new project, and the fake plant's pump vibration). One gap is logged: the deploy test covers only a copy of one
bundle, and Harrier on the Render instance is not yet confirmed (#962).

Landed so far: P1.1 One finding contract, P1.3 Method registry ([Methods](methods.md)) and P1.2 Detector registry
([Detectors](detectors.md)).

## Historian views (P5.12 #967, P5.13 #968)

An IOC engineer looking at a tripped compressor wants its suction and discharge pressures, temperatures, speed and
vibration on one time axis, with the alarms, log entries, cases and work orders beside them, not a grid of one chart
per tag. Two epics in the work and UI lane do this, P5.12 first.

| Epic | Build | Done when |
|---|---|---|
| P5.12 Trend templates by equipment class | A template per equipment class (ISO 14224 code, or well) names groups of measurement roles, the order they read in and which are shown first. Templates ship with the platform; a bundle adds or overrides one in its configuration. A view resolves the entity to its unit, its class, the template and the points holding each role there (P1.4). Points with no role in the template sit in an "Other points" group, off by default. | A compressor on Okan and on Kestrel opens with its pressures, temperatures, speed and vibration grouped from roles, with no tag names in code. Pumps, wells, vessels and heat exchangers have templates too. A role the template wants but the unit lacks is named, not silently dropped. A bundle override changes the view with no code change. |
| P5.13 One trend with event lanes | One large chart with a shared time axis. Each template group is a panel, and traces in a panel share an axis per unit. Beneath them are event lanes: alarms, operator log, cases, work orders and downtime. Every trace and lane has a toggle, the defaults come from the template, and the choice is kept in the link. A crosshair reads every shown trace at one time, and an event marker opens its record. It replaces the per-tag small multiples on the entity page, and the case page shows the same chart over its event window. | Opening the compressor behind an Okan trip case shows the trip's pressures, temperatures, speed and vibration with its alarms, log entries, case and work order in lanes, at the trip time. Each trace and lane toggles, and a shared link restores the choice. Every marker links to its record, every trace names its point, unit and provider, and synthetic values are labelled. It works in light, dark and on a phone. |

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
| R.1 Real data for one area | #9, #10 |
| R.2 Live historians | #15, #16 |
| R.3 Measured on real work | #11, #221 |
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
