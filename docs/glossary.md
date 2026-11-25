# Glossary

This is the dictionary for everything people read in Fuzer. Each concept has one noun and one verb; the words
after "Not" are retired and `ui/tests/glossary.test.cjs` fails on the ones it checks. A new term is added here in
the same change that puts it on screen.

**All work.** Every event and every case in one set of rows (`GET /cases`), shown as the **List** (a table) or the **Board**,
the two pages under **Work** in the menu. It is filtered and sorted on any dimension: kind, stage, who holds it, team, source, state,
overdue, the facility, area and asset it is at (from what it is about and what that sits in), the entity it is about
or anything in an entity (an entity page's Work here), the place on the map, words in the title. Each event's **stage** (To dispatch, With
a desk, On the plan, With the field, Back from the field, Closed) is decided once, on the server. A **preset** is a
named set of these filters (To dispatch, Data questions; what waits on you is My work, its own page); presets are added once they prove useful,
and a role can save its own. See [where work goes](work-map.md).

**AI note.** What a report's panels show for one scope, window and day, in a headline and a few points, written by
Claude from those panels only and kept for everyone (the `summary` panel). Each point cites what it rests on; the daily
report's checks flag a number or a citation that is not on the page. Not "insight" or "AI summary". See
[reports](reports.md).

**Asset pack.** Asset-specific tag grammar, entity kinds, limits, source priorities and match thresholds that seed
the source catalog's rules. Okan is the main demo; Kestrel Ridge (a gas plant) and Harrier (a pipeline and
terminal) have their own. See [overview](overview.md).

**Assertion.** One source's value for one field of one entity (re-read from its record; a reviewer's choice is
stored). Golden records are chosen from assertions.

**Back from the field.** A job the field reported Done or Could not do, waiting for the IOC to close it out or send
it round again.

**Board.** All work's rows in columns, by stage (the default), kind, holder, team, source, facility, area or asset, with the same filters as
the List. Sorting by value ranks everything outstanding by what it is worth: backlog, PMs, incidents, opportunities.

**Booking.** When a job is planned to happen and with which resources: its dates, crew and the quantity it takes of
each resource. Shown as the crew and dates on a bar on Plan. See [the work model](work-model.md).

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

**Close-out.** Closing a case by saying what it was (a real problem, a false alarm, a duplicate, a data problem, or
for a message that only said what was done, log only with no action) and, for a real problem, what failed, why and what was done. Where another system raised the work, the update for
that system is drafted for a planner to approve. Work someone asked for (a task) is **finished** instead: done as
asked, or a duplicate.

**Capability.** A tool an agent can call, declared once with the agents that use it. See [capabilities](capabilities.md).

**Container.** A group of cases with a shared window, budget or goal, and a parent: a business plan, program,
project, turnaround or package. Shown by its kind, never as "container". Its own case runs its gates.

**Connection.** How Fuzer reaches a system of record: a folder of exports, PI Web API or ADX, with secrets as
references. A system can have several. See [sources](sources.md).

**Connector.** Reads one source table in one format (CSV, Parquet, Delta, Excel, JSON, GeoJSON, a live system).

**Couldn't determine.** Something readiness, an option, a brief or the assistant could not settle, kept with the
reason and what would have answered it (a source, a tool or a skill). Shown as "unknown" with the reason where the
value would be, and ranked on How the work is running under **What Fuzer couldn't determine**. Not a guess, not 0.
See [agents](agents.md).

**Custom report.** A report a person made by copying one or starting blank, listed under My reports. Its owner changes
it on the page and shares it with everyone or a workflow role; each save is a new version, and an earlier one can be
restored. See [reports](reports.md).

**Dataset.** One table or feed a system delivers: where it is, its key, how it loads (snapshot, incremental,
append), its schedule, owner and status (draft, active, paused, retired).

**Dispatch.** The IOC sending a case to a person or a role's queue, with what to check or do. The instruction
travels with the job.

**Detection.** One thing the SRE found in the process data: an excursion, a stop, a step, a drift, a bad instrument.

**Entity.** One real-world thing (a tag, a pump, a well, a work order) with a stable Fuzer ID (`fz-...`), however
many sources describe it. Everything under Explore is an entity, shown with its **kind**; its page is `#/e/<id>`.
Not item, record or asset as a general word.

**Beneath.** What sits in an entity, at any depth, along the register's containment (part of, installed at,
located at): a facility's areas and their wells, machines and tags. An entity page's tabs and counts roll up
everything beneath it. The chain the other way is where it sits.

**Equipment.** The physical item installed at a tag, known by its serial number. Pumps get swapped; tags stay.

**Eval.** How well a skill answers its eval questions (the facts a good answer must name, the right specialist) or
how sound what the agents wrote is against its facts, run on Ops IQ, Evals and kept with its history. Its **grade** is
a model's reading of each result from 1 to 5, which waits while no model may be called.

**Exception.** Anything that should be worked, raised the same way by every source: one per open condition, with
its evidence and references to the assets it is about. An exception opens a case or joins one.
See [how work is routed](work-routing.md).

**Excursion.** A process value outside its alarm limits, integrity operating window or learned normal range while
its unit runs.

**Fact.** A short, dated statement about an entity that Ops IQ learned from a conversation, cited as `fact:<id>`.
A new fact is **proposed** until a person **approves** it on Ops IQ, Knowledge (only then does an answer recall it,
and it ends the facts it replaces) or **rejects** it. Not "memory item". See [agents](agents.md#knowledge-review).

**Field job.** A case as a field operator sees it on My work: the instruction, where to go, what was found, what
happened there lately, and **On site**, **Done** and **Could not do**.

**Golden record.** An entity's best value for each field, chosen from its assertions by source priority (the
systems' rank, or the field's own rule).

**Handover.** At each shift change, what the incoming ops lead needs to know, drafted from live work with every
line citing its case. The incoming lead acknowledges it.

**Instrument-health summary.** A week's bad instruments in one upset of kind `instrument`, listed by area.

**IOW.** Integrity operating window: a limit that protects equipment integrity rather than the process.

**Job.** One piece of execution inside a case: done at a place, by a kind of crew, in a window. A case has none (desk
work) or several. On screen a **field job** or a **desk job**; the **Jobs** page is the technical schedules and runs.

**Kind.** What sort of entity something is (a well, a tag, a work order, a document). The ontology names and labels
every kind; a page shows that label, or the more specific word a record carries (a compressor's template, a
drawing's document type).

**Knowledge.** Ops IQ's review queue: the facts, lessons and skill drafts the agents propose, waiting for a person to
decide. Not "inbox" or "approvals". See [agents](agents.md#knowledge-review).

**Logistics.** What the plan asks to be sent each day: each crew's jobs, the crews that travel out and back, the
seats and beds they take (a visiting crew keeps its beds aboard between its jobs until it flies back), the cranes,
vessels and deck space booked, and the materials to send: the parts reserved on each work order whose first planned
job starts that day, with whether stock or an order on record covers them. Plan > Logistics reads it from the plan's
dates, resource needs and the work orders' reservations.

**Mapping.** YAML that says what one source table's rows mean: entities, fields, links and measurements. Kept as
versions in the source catalog (the latest approved one is in use); the files in `src/fuzer/mappings/` seed it.

**MCP.** Model Context Protocol: how Claude, in the app or in another client, calls Fuzer's tools.

**Message.** Free text sent to the IOC with no system behind it: mail to its mailbox, a radio call the control room
typed in, a Teams post, or a field operator's **Note to the IOC** from My work. Read once into a case about the entity
it names (**Reported in a message**), a question for the data steward (**Message to match**), or nothing to do.

**Need.** Why work should happen: a condition seen, an obligation falling due, or an intent to change something. A
need opens a case or joins an open one. In code; on screen it is shown by its type (a notification, a due date).

**Note.** What a person writes on a report, pinned to its scope and period, kept with who wrote it and when (the `note`
panel). A note may carry live values, `{production.oil}`, that read the measure for the note's own period.

**Ops IQ.** The operations agent: it answers a question from the register, the work and the readings, citing what
it used. Its sign is its gradient, Fuzer's teal running into indigo: the search button wears it (search and asking
are one way in), and so does every way to ask it ("Ask Ops IQ about this asset") and its row in app search; nothing
else in the app does. Its page is `#/ask`. To ask is the verb ("Ask Ops IQ"); Ops IQ is the name, never "the assistant" or "Ask" as a page.

**Options.** The choices for a piece of field work, side by side on a case in To dispatch, With a desk or Back from
the field (and from its row on All work): **Dispatch now**, **Ride along with the next visit** (a scheduled plan
item on the same area or assets), **Shelve** (until the area's turnaround, or for at most the longest shelving) and
**Escalate** (to the engineer, or past the engineer to the ops lead). Each says when it would be done, the
production deferred until then in bbl and $, the risk of waiting, and whether it is ready; one is **recommended**
with its reason. Choosing another needs a reason; the choice is kept on the case and counted as the recommendation
**taken** or not, per work type, on Work performance. Not "proposal" (that is an agent's suggested change).

**Plan.** The one timeline of every planned job from every source: SAP orders and maintenance plans, inspections,
schedules, and work the IOC planned for a day. A bar on it is a **plan item**.

**Plan line.** One line of the business plan: the work a program means to do on a facility or area over a period,
with the hours and cost it estimates, read against the cases matched to it (plan against actual). Written by a planner or
brought by the business plan export. Its **variance** is the actual less the estimate; above 0 is over the plan.

**Panel.** One figure, chart, list or note on a report, sized in quarters of the width. It reads a registered
measure, a trend or the Work list's rows; it never adds up a number of its own. See [reports](reports.md).

**Prerequisite.** Something a job needs before it can be released: parts covered, crew within capacity, a permit,
isolations, seats, beds and lifts, the unit down. A job is ready when every prerequisite is satisfied.

**Profile.** What a load of a dataset looked like: per column its type, fill, distinct and top values. Compared
with the one before to catch a source changing shape.

**Project.** One asset's configuration: its pack, data folder, timezone and time-series source.

**Queue.** Where work for a role waits until one of its holders takes it. A step in a queue has the role's SLA; when
it is overdue it is marked escalated there, or moves on where a site has set the role's chain.

**Record.** One row from a source system, before identity resolution decides which entity it describes. Not a word
for the entity itself.

**Register.** The golden records and the links between them: what the work is matched to.

**Release.** The IOC sending a planned field job to the field, from its bar on Plan: the one way planned field work
reaches it. Its prerequisites are checked first; one still open needs a reason, kept on the case. Moving a released
job (Reschedule, with a reason) sends it back to wait for release.

**Replay.** Running the detectors over history one window at a time, without letting them see later data, and
scoring them against what really happened. See [evals](evals.md).

**Review item.** A question for a person: an uncertain match, a held source (a changed schema or a failing
check), a suggested mapping, a reading by Claude to check.

**Report.** A page of panels in rows for a **scope** (an entity in the register and what is beneath it) and a
**window** (7 days, 30 days, quarter to date, ending on a day), both kept in its link. Reports are listed on the
Reports page; a daily report is the day's written narrative. See [reports](reports.md).

**Resource.** Something planned work shares and can run out of: a crew, a crane, beds, helicopter seats, vessel
support, operator rounds, production capacity. Plan > Resources sets demand against its capacity. Production
capacity is the most the system can handle online at once, in BOE/d: the wells at their latest test rates, less
what a job has down, plus what a job brings on when it finishes.

**Round.** A checklist of readings and checks an operator walks each shift on the phone. A reading outside its
limits, or a failed check, raises a round finding.

**Routine.** A standing rule that raises work each time it falls due: an SAP maintenance plan, a round, a routine
duty, a report. It says when, on what, the job it makes and who owns it.

**Routing table.** One row per type of work: whether it joins an open case, what it starts (a workflow, a queue, or
nothing until the IOC dispatches it) and when it is due. Shown on Settings > Routing.

**Run.** One load of a dataset that changed something: when, what triggered it, rows read, new, changed and
removed, and the mapping versions used.

**Schedule.** A turnaround, project or campaign whose activities have durations, links and resource needs, shown
as a Gantt under Plan > Schedules. A planning system such as Primavera P6 can deliver one.

**Shelve.** Take a standing condition that can wait off the list until a date, with a reason. It comes back by
itself. Safety work cannot be shelved.

**Skill.** A playbook (its SKILL.md) an agent follows for one kind of work, with the tools it may call and the eval
questions it is held to. A skill's owner changes it on Ops IQ, Skills: a change is a **draft**, and a person
**publishes** it behind the skill's eval (it goes live only if it scores no lower than the live version) or **sets it
aside**; **roll back** makes the version before it live again. Not "prompt" or "pack" on screen. See [agents](agents.md).

**Source catalog.** The systems of record, their connections, datasets, mapping versions, runs, profiles,
checks and rules, as rows edited on the Sources page with history. See [sources](sources.md).

**Setting.** A configurable value (a threshold, a model, a schedule) stored in the database, global or per
project. See [settings](settings.md).

**SRE.** The scheduled loop that watches the process data: detect, group into upsets, diagnose, hand off.

**Standing.** Alarms already on when scanning began. They join an upset that starts near them but never open one.

**Step.** One task in a case's workflow, held by a person, a queue or an agent. A step is worked on its case's page:
**Take it**, **Give it back to the queue**, **Complete**, or **Send to** someone else.

**Status mapping.** A system's statuses (SAP's system and user statuses, JD Edwards codes, Maximo statuses) and the
Fuzer state each means, with the status sent back for each Fuzer move. Edited on Sources; a status with no row is
flagged, not guessed.

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
