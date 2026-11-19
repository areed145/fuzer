# A tour of the app

What each part of the web UI is for, shown on the Okan demo. The screenshots were taken from a local
`fuzer up --demo okan` with no AI provider configured, so nothing on them was written by a model. Okan is a
fictional field and its data is synthetic; its sample crew works the queue on today's clock, so the work on the
screens is live, and the people are not real.

The menu holds eight destinations, in this order: **Home**, **Work**, **Plan**, **Explore**, **Investigate**,
**Sources**, **Jobs** and **Settings**. Each opens a row of tabs for its pages. On a phone the bottom bar keeps
Home, My work, Explore and Investigate in thumb reach and **Menu** opens the rest. The panel at the foot of the
sidebar names the facility and says whether its data checks and jobs are healthy.

```mermaid
flowchart TB
  home["Home<br><small>where attention is needed</small>"]
  home --> work["Work<br><small>All work, My work,<br>Workflows, Master data</small>"]
  home --> plan["Plan<br><small>Plan, Schedules,<br>Resources</small>"]
  home --> explore["Explore<br><small>Browse, Map,<br>Network, 3D</small>"]
  home --> investigate["Investigate<br><small>Downtime, Ask, Incidents,<br>Daily reports, Metrics,<br>Lessons learned, Studies</small>"]
  home --> sources["Sources<br><small>Sources, Ontology</small>"]
  home --> jobs["Jobs"]
  home --> settings["Settings<br><small>Application settings,<br>Users, Routing</small>"]
  work <-.->|"plan for a day,<br>planned work holds back findings"| plan
  work -.->|"the asset a case is about"| explore
  investigate -.->|"draft work"| work
  sources -.->|"loads and rebuilds"| explore
```

[Where work goes](work-map.md) follows a piece of work through these pages from the system that raised it to its
close. The [guides by role](guides/index.md) do each role's jobs step by step.

## Home

**Home** shows where attention is needed now: open work gathered at the places it is about, on the field map and
as a ranked list, with filters for high priority, overdue and unassigned. Choosing a place opens **All work**
filtered to it. Register housekeeping for the data steward is counted, not placed, since it is nowhere to send
anyone. With an empty register, Home points to the Sources setup wizard instead.

![Home: open work at the places it is about](assets/screens/home.png){ loading=lazy }

## Work

**All work** is the IOC's one list, one row per event however many systems reported it. Its views follow work
through its life: **To dispatch**, **With the field**, **Back from the field**, **All open**, **In progress**,
**Ranked by value**, **Closed** and **Board**. One filter bar is shared by every view and kept in the link.
**How the work is running** opens the performance view: how fast work is triaged and closed, what each detector's
cases turned out to be, and rounds done and missed per shift.

![All work: everything open, one row per event](assets/screens/all-work.png){ loading=lazy }

A row's title opens its **case**: what it is waiting on and who holds it, what each source found, the asset's
trend with the event marked, the alarms and log entries around the event, how the case was routed, and its
history.

![A case: what it waits on, what was found, and the asset's trend](assets/screens/case.png){ loading=lazy }

**My work** is what waits on you: jobs the IOC sent you, steps assigned to you, what is unclaimed in your roles'
queues, this shift's rounds and planned work you are booked on. A field operator reports **On site**, **Done** or
**Could not do** from a job's card here. A step opens as a page with its form, its evidence and the case history.

![My work for the ops lead](assets/screens/my-work.png){ loading=lazy }

![A step: triage of an upset the detectors found](assets/screens/task.png){ loading=lazy }

**Workflows** lists each BPMN workflow with its health, and opens Overview, Running, Model, Monitor and SLAs &
routing. Task forms are edited here too.

![Workflows: each workflow with its health](assets/screens/workflows.png){ loading=lazy }

**Master data** holds the register's questions for a person: possible matches, conflicting values, schema changes,
held loads, references on open work that matched nothing, and gaps. Each answer is kept and applied on every
rebuild.

![Master data: the register's questions](assets/screens/master-data.png){ loading=lazy }

## Plan

**Plan** puts every planned activity from every source on one timeline by area: SAP work orders and maintenance
plans, inspections, turnaround and project schedules, and work the IOC chose to plan for a day. Flags mark a clash,
a chance to bundle a job into a shutdown, and parts that are short.

![Plan: one timeline by area](assets/screens/plan.png){ loading=lazy }

**Schedules** lists the turnarounds, projects and campaigns, including the ones a planning system such as
Primavera P6 delivers. Each opens as a Gantt board with its logic and critical path, and the other planned work in
its span underneath. **Resources** sets every shared resource's demand against its capacity, day by day.

![A schedule as a Gantt board, with the other planned work in its span](assets/screens/schedule.png){ loading=lazy }

![Resources: demand against capacity](assets/screens/guides/resources-desktop.png){ loading=lazy }

## Explore

**Browse** searches tags, wells, PI points, serials and descriptions, with counts per kind. Every result opens an
**asset page**: its status now, the work open on it, the golden value of each field with the system it came from
(**why?** traces it to the record, mapping version and rule), its trends, documents and drawings, and where it
sits in the flow.

![The asset page for compressor 20-K-2300A](assets/screens/entity.png){ loading=lazy }

![Measurements on well OK-J12-02: two years of well tests with trends](assets/screens/well-measurements.png){ loading=lazy }

**Map** shows located wells, platforms and pipelines. **Network** shows Energy Components allocation, gas lift and
P&ID piping as of a date. **3D** draws a facility or an area as generic shapes (vessels as cylinders, machines as
blocks, jackets on lattice frames) with the links as pipe runs. It is a schematic laid out from flow order, not a
model of the plant. Shapes are coloured by open work, alarms sounding and downtime; a picked item lights its path
upstream or downstream, and "If this trips" estimates what goes down with it and the production at risk. Where a
device cannot draw 3D, the same scene is drawn flat.

![Network: allocation and piping as of a date](assets/screens/network.png){ loading=lazy }

![3D: an area drawn as a schematic, coloured by status](assets/screens/scene-3d.png){ loading=lazy }

## Investigate

**Downtime** ranks lost production by equipment, class and reason. **Incidents** groups downtime records, log
events and notifications on the same plant within a few days into one upset.

![Downtime ranked by equipment, class and reason](assets/screens/downtime.png){ loading=lazy }

![Incidents, newest first](assets/screens/incidents.png){ loading=lazy }

**Ask** is the operations assistant: suggested questions per domain, the specialists and what they cannot do, and
a prompt. It answers only from loaded evidence, cites every fact and never gives operating instructions. It needs
an AI provider configured by the owner, and each question is a deliberate, metered call.

![The Ask page](assets/screens/ask.png){ loading=lazy }

**Daily reports** shows each production day's report: fixed sections built from the day's records, every line
citing them, with checks for uncited lines and numbers that are not in the facts. Regulatory reports are reviewed
on the same page. **Metrics** lists every KPI, defined once, with its value now and its drill-down. **Lessons
learned** and **Studies** keep what closed cases and deeper analyses found.

![The daily report for a production day](assets/screens/daily-report.png){ loading=lazy }

![Metrics: each KPI defined once](assets/screens/metrics.png){ loading=lazy }

## Sources, Jobs and Settings

**Sources** owns connecting, testing, loading and recovering source data: every dataset with its health, last run,
owner and schedule, and **+ Add a source** ([walkthrough](extend.md#from-the-ui)). **Ontology**, beside it, shows
the data model the sources map onto, with circles sized by how many entities of each kind are loaded.

![The Sources page](assets/screens/sources.png){ loading=lazy }

![The Ontology graph](assets/screens/ontology.png){ loading=lazy }

**Jobs** shows each scheduled job's schedule or sensor, recent runs and data checks, read from Dagster.

![Jobs: each scheduled job and its recent runs](assets/screens/jobs.png){ loading=lazy }

**Settings** holds your appearance preference (saved in your browser) and the application configuration, with
**Detection** for switching and tuning each detector. **Users** is where owners approve accounts and assign
workflow roles. **Routing** shows the routing table and dry-runs an intake.

![Settings > Detection: every detector, what it needs and what it raised](assets/screens/detection.png){ loading=lazy }

![Settings > Routing: the routing table](assets/screens/routing.png){ loading=lazy }

## The guided introduction

The guided tour and the Okan investigation are no longer in the menu. They still open at `#/demo` and
`#/investigate/okan`: a five-step introduction, and one train B compressor issue followed from the asset to its
source records and a cited finding. Neither generates data, calls a model or changes work when a page loads.

![The Okan investigation, step 1 of 4](assets/screens/investigation.png){ loading=lazy }
