# Glossary

This is the dictionary for everything people read in Fuzer. Each concept has one noun and one verb; the words
after "Not" are retired and `ui/tests/glossary.test.cjs` fails on the ones it checks. A new term is added here in
the same change that puts it on screen.

**All work.** Every event and every case in one set of rows (`GET /cases`), shown as the **List** or the **Board**,
each its own menu entry. It is filtered and sorted on any dimension: kind, stage, who holds it, team, source, state,
overdue, the facility, area and asset it is at (from what it is about and what that sits in), the entity it is about
or anything in an entity (an entity page's Work here), the place on the map, words in the title. Each event's **stage** (To dispatch, With
a desk, On the plan, With the field, Back from the field, Closed) is decided once, on the server. A **preset** is a
named set of these filters (Waiting on me, To dispatch, Data questions); presets are added once they prove useful,
and a role can save its own. See [where work goes](work-map.md).

**Asset pack.** Asset-specific tag grammar, entity kinds, limits, source priorities and match thresholds that seed
the source catalog's rules. Okan is the main demo; Kestrel Ridge (a gas plant) and Harrier (a pipeline and
terminal) have their own. See [overview](overview.md).

**Assertion.** One source's value for one field of one entity (re-read from its record; a reviewer's choice is
stored). Golden records are chosen from assertions.

**Back from the field.** A job the field reported Done or Could not do, waiting for the IOC to close it out or send
it round again.

**Board.** All work's rows in columns, by stage (the default), kind, holder, team, source, facility, area or asset, with the same filters as
the List. Sorting by value ranks everything outstanding by what it is worth: backlog, PMs, incidents, opportunities.

**Bundle.** A planned job due near a shutdown of its area that could be done in it. Shown as a flag on Plan.

**Candidate.** What a source record says exists (a tag, a well, a pump), before identity resolution decides which
entity it is.

**Case.** The piece of work that an exception (or several, about one event) opens: who holds it, what it waits on,
its evidence and its history. A case is what is dispatched, worked and closed.

**Checklist.** What a case asks for, item by item, ticked off on the case as each is done or marked not needed
with why. A multi-part ask (a weekly look-ahead: confirm a work order, raise three orders, check the standby pumps)
keeps its parts here. Checklist items are not steps: the workflow does not wait on them, and the closed case keeps
them as the record of what was done. An item for another queue is sent as its own case, part of this one (a
child case); the item is done when that case is.

**Clash.** Two planned jobs on one asset at the same time, a person booked twice, or two jobs whose permits must
not run side by side. Shown on Plan and checked again at dispatch; it is a flag, not a case.

**Close-out.** Closing a case by saying what it was (a real problem, a false alarm, a duplicate, a data problem)
and, for a real problem, what failed, why and what was done. Where another system raised the work, the update for
that system is drafted for a planner to approve. Work someone asked for (a task) is **finished** instead: done as
asked, or a duplicate.

**Capability.** A tool an agent can call, declared once with the agents that use it. See [capabilities](capabilities.md).

**Connection.** How Fuzer reaches a system of record: a folder of exports, PI Web API or ADX, with secrets as
references. A system can have several. See [sources](sources.md).

**Connector.** Reads one source table in one format (CSV, Parquet, Delta, Excel, JSON, GeoJSON, a live system).

**Dataset.** One table or feed a system delivers: where it is, its key, how it loads (snapshot, incremental,
append), its schedule, owner and status (draft, active, paused, retired).

**Dispatch.** The IOC sending a case to a person or a role's queue, with what to check or do. The instruction
travels with the job.

**Detection.** One thing the SRE found in the process data: an excursion, a stop, a step, a drift, a bad instrument.

**Entity.** One real-world thing (a tag, a pump, a well, a work order) with a stable Fuzer ID (`fz-...`), however
many sources describe it. Everything under Browse is an entity, shown with its **kind**; its page is `#/e/<id>`.
Not item, record or asset as a general word.

**Equipment.** The physical item installed at a tag, known by its serial number. Pumps get swapped; tags stay.

**Exception.** Anything that should be worked, raised the same way by every source: one per open condition, with
its evidence and references to the assets it is about. An exception opens a case or joins one.
See [how work is routed](work-routing.md).

**Excursion.** A process value outside its alarm limits, integrity operating window or learned normal range while
its unit runs.

**Field job.** A case as a field operator sees it on My work: the instruction, where to go, what was found, what
happened there lately, and **On site**, **Done** and **Could not do**.

**Golden record.** An entity's best value for each field, chosen from its assertions by source priority (the
systems' rank, or the field's own rule).

**Handover.** At each shift change, what the incoming ops lead needs to know, drafted from live work with every
line citing its case. The incoming lead acknowledges it.

**Instrument-health summary.** A week's bad instruments in one upset of kind `instrument`, listed by area.

**IOW.** Integrity operating window: a limit that protects equipment integrity rather than the process.

**Kind.** What sort of entity something is (a well, a tag, a work order, a document). The ontology names and labels
every kind; a page shows that label, or the more specific word a record carries (a compressor's template, a
drawing's document type).

**Mapping.** YAML that says what one source table's rows mean: entities, fields, links and measurements. Kept as
versions in the source catalog (the latest approved one is in use); the files in `src/fuzer/mappings/` seed it.

**MCP.** Model Context Protocol: how Claude, in the app or in another client, calls Fuzer's tools.

**Plan.** The one timeline of every planned job from every source: SAP orders and maintenance plans, inspections,
schedules, and work the IOC planned for a day. A bar on it is a **plan item**.

**Profile.** What a load of a dataset looked like: per column its type, fill, distinct and top values. Compared
with the one before to catch a source changing shape.

**Project.** One asset's configuration: its pack, data folder, timezone and time-series source.

**Queue.** Where work for a role waits until one of its holders takes it. A step in a queue has the role's SLA and
escalates along the role's chain.

**Record.** One row from a source system, before identity resolution decides which entity it describes. Not a word
for the entity itself.

**Register.** The golden records and the links between them: what the work is matched to.

**Replay.** Running the detectors over history one window at a time, without letting them see later data, and
scoring them against what really happened. See [evals](evals.md).

**Review item.** A question for a person: an uncertain match, a held source (a changed schema or a failing
check), a suggested mapping, a reading by Claude to check.

**Resource.** Something planned work shares and can run out of: a crew, a crane, beds, helicopter seats, vessel
support, operator rounds, production capacity. Plan > Resources sets demand against its capacity.

**Round.** A checklist of readings and checks an operator walks each shift on the phone. A reading outside its
limits, or a failed check, raises a round finding.

**Routing table.** One row per type of work: whether it joins an open case, what it starts (a workflow, a queue, or
nothing until the IOC dispatches it) and when it is due. Shown on Settings > Routing.

**Run.** One load of a dataset that changed something: when, what triggered it, rows read, new, changed and
removed, and the mapping versions used.

**Schedule.** A turnaround, project or campaign whose activities have durations, links and resource needs, shown
as a Gantt under Plan > Schedules. A planning system such as Primavera P6 can deliver one.

**Shelve.** Take a standing condition that can wait off the list until a date, with a reason. It comes back by
itself. Safety work cannot be shelved.

**Source catalog.** The systems of record, their connections, datasets, mapping versions, runs, profiles,
checks and rules, as rows edited on the Sources page with history. See [sources](sources.md).

**Setting.** A configurable value (a threshold, a model, a schedule) stored in the database, global or per
project. See [settings](settings.md).

**SRE.** The scheduled loop that watches the process data: detect, group into upsets, diagnose, hand off.

**Standing.** Alarms already on when scanning began. They join an upset that starts near them but never open one.

**Step.** One task in a case's workflow, held by a person, a queue or an agent. A step is worked on its case's page:
**Take it**, **Give it back to the queue**, **Complete**, or **Send to** someone else.

**Suppression.** Planned work holding back the detections it explains: a trip on a machine that is down for a
shutdown is counted under the shutdown and opens no case.

**Tag.** A functional position in the plant, such as `20-P-1001B`. Equipment is installed at a tag.

**Tower.** The first page (`#/`): the field map with what needs you over it, the control tower view. Not Home.

**Upset.** Detections close in time and connected in the plant model, grouped into one. The earliest is its likely
first cause.

**Watch.** An agent that reads one process area at a time and proposes what looks unusual that no detector raised.
Its proposals are kept and scored; they are raised as work only in live mode, which is off by default.

**Work item.** The engine's name for a case, kept in code and the API only. On screen it is a case.

**Workflow.** A BPMN process: lanes for roles, steps for people and agents, timers and escalation. Listed under
Settings > Workflows (formerly Processes).

**Write-back.** A change Fuzer proposes to a source system. It is only ever sent after a named person approves it.
