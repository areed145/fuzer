# Every source of work

Fuzer takes in work from many systems, matches it to assets through the register, and routes it to one place. This
page lists every source Fuzer takes work from, as the IOC and the field see it. For each one it answers five
questions:

1. **Where does it come from?** The system or check that raises it.
2. **What is it matched to?** The references it carries, matched to entities in the register.
3. **Who decides what happens?** The routing table's answer: the IOC, a role's queue, or a workflow.
4. **Can it go to the field?** Whether a field operator can be sent, and how.
5. **How does it close?** By itself when the condition clears, at the IOC's close-out, or once the system that raised it hears back.

[How work is routed](work-routing.md) explains the machinery and the List the IOC works from.
[Where work goes](work-map.md) names the page for each stage. [Work types](work-types.md) is generated from the code
and lists every type with its queue.

## What every source shares

Every source on this page follows these rules unless its row says otherwise.

- **One exception per open condition.** A source raises an *exception* with its evidence and references. While the
  condition still holds, the source adds evidence to that exception and does not raise a second one. When the
  condition clears, the exception clears, and the case working it closes with it.
- **Matched through the register.** Each reference (a Fuzer ID, a tag, an SAP equipment number, a well name, a PI
  point, an alias a steward recorded) is matched to an entity. A reference that matches nothing is kept: the row shows
  it as *unmatched*, anyone can match it from the row, and **Master data > Unmatched on open work** lists it for the data
  steward. Matching it as an alias also fixes the next exception that carries it.
- **One event, one case.** A type marked *joins* adds itself to the open case about the same asset (or the unit or
  machine it belongs to) if that case was raised within `exceptions.join_hours` (48 by default), so the IOC works one
  event once. All work shows one row with each source under it, and **More > Split out** separates a source that
  is a different event.
- **Planned work holds back what it explains.** For a type marked *held back*, a finding on an asset that is down for
  a shutdown or planned job is counted under that plan and opens no case. Safety work is never held back or shelved.
- **One routing table.** Each type starts a workflow, goes to a role's queue (the standard one-step queue workflow,
  with the role's SLA and escalation), or waits on **To dispatch** for the IOC. **Settings > Routing** shows the
  table and can dry-run an intake.
- **Always on the List.** Whatever road it takes, open work is a row on **Work > List** (All work) and a card on the
  **Board**, counted once.

## At a glance

| Family | Sources | Routed to | Field | Closes |
|---|---|---|---|---|
| [Maintenance and materials](#maintenance-and-materials) | SAP notifications, parts not ready, routines due | IOC (To dispatch); maintenance planner | SAP notifications | IOC close-out, then SAP hears back; parts by themselves |
| [Process data](#process-data-fuzers-detectors) | Upsets, IOW breaches, trips, machine health, loops, gas lift, well tests, lab quality, gas balance, alarms, flare | Upset and IOW workflows; engineer, production engineer, HSE queues | Upsets, IOW breaches, trips, machine health | Last workflow step, desk close-out, or the condition clears |
| [Other detection tools](#other-detection-tools) | Vendor condition monitoring, predictive health, leak detection | The type the tool raises, after its trust level | If its type allows | As its type; the close-out can go back to the tool |
| [Integrity and inspection](#integrity-and-inspection) | Inspection or wall loss, pig run anomalies, repeat leaks | Engineer queue | Sent from the case | By themselves when a later survey or screening clears |
| [Safety, permits and change](#safety-permits-and-change) | Lapsed permits, bypasses, proof tests, safeguard demands, temporary MOCs, unrecorded changes, deviations, records gaps | Ops lead and engineer queues | Lapsed permits | By themselves when the register shows it done |
| [The field](#the-field) | Round findings, field reports, crew handovers | Ops lead queue | Yes | IOC close-out |
| [Messages](#messages) | The IOC mailbox, radio calls, Teams posts, field notes from the phone | Ops lead queue; data steward queue | Yes | IOC close-out; the steward's answer |
| [Regulatory and reporting](#regulatory-and-reporting) | Reports coming due, reports to review, the daily report | HSE queue; daily report workflow | No | When submitted or published |
| [The register and sources](#the-register-and-sources) | Questions, held loads, source problems, unmatched records, gaps, corrections | Data steward queue; source owners | Field checks from corrections | When the question is answered or the load recovers |
| [Agents](#agents) | The watch, detector tuning, skill changes, agent steps | Engineer queue | Watch findings, from the case | Desk close-out |
| [People asking for work](#people-asking-for-work) | New case, assistant drafts, opportunities, checklist items | The queue it was asked of | Sent from the case | Done as asked |
| [Fuzer following up its own work](#fuzer-following-up-its-own-work) | Did the fix hold, updates back to a system, failed write-backs, shift handover | Whoever closed it; approver queues; ops lead | No | When answered, approved or acknowledged |
| [Any other system](#any-other-system) | Rules on loaded records, webhooks, `POST /exceptions` | Wherever its type says | If its type allows | As its type; the update goes back if its system is set up |
| [Planned work](#planned-work) | SAP orders and plans, P6, turnaround scope, Plan for a day | Plan, not the routing table | Booked crews, on My work > Coming up | Done on the plan |

## Maintenance and materials

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **SAP notification** `sap_notification` | Each register refresh: open notifications (status OSNO or NOPR) with no work order, created in the last `intake.sap_notifications.max_age_days` (60) of data. Off with `intake.sap_notifications`. | The equipment and functional location the notification concerns | **To dispatch** for the IOC, due by SAP priority (`intake.sap_notifications.due_hours`) once dispatched. Joins the open event on the same asset. | Yes: Options, Send to, or Plan for a day | When an order is raised or SAP completes it, it clears. If the field does it, the IOC closes it out, the SAP completion (or a follow-up notification for Could not do) is drafted for the maintenance planner to approve, and the notification is held until SAP's status changes. |
| **Parts not ready for planned work** `parts_not_ready` | After each refresh: a planned work order whose reserved part won't arrive by the job's day | The work order and the equipment it concerns | Maintenance planner queue | No | By itself when stock, or an order arriving in time, covers every line. The planner expedites or moves the job on Plan. |
| **Routine due** `routine_due` | After each refresh: an SAP maintenance or inspection plan's next due task, or a routine kept in Fuzer, inside its lead time | The equipment the routine covers | Maintenance planner queue (or the routine's owner role) | No | By itself when its job on Plan is done or cancelled; a routine kept in Fuzer then rolls to its next date. The planner plans, books and releases the job. |

SAP work orders and maintenance plans are planned work, not routed work: see [Planned work](#planned-work).

## Process data: Fuzer's detectors

The SRE loop reads the historian, finds detections, and groups them into upsets by time and plant topology. Other
analyses read the same data for slower or narrower problems. [Detectors](detectors.md) lists every detector and
what it reads.

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Process upset** `upset` | Excursions, stops, steps, drifts, bad instruments, model residuals, hydraulics, multivariate, peer drifts and promoted rules, grouped by the SRE | The first unit it reached and its PI points | The **upset** workflow: Triage (engineer), Field check (field operator) if not sure, Diagnose (agent), Approve (engineer), Draft SAP notification (agent), Raise notification (maintenance planner), re-check in 6 hours, Confirm resolved (engineer). Joins; held back by planned work. | Yes: the Field check step, or Options from To dispatch | When the last step is done, or Triage calls it a false alarm |
| **IOW breach** `iow_breach` | An integrity operating window held past its allowed time | The point and the unit it protects | The **iow_breach** workflow: Review, then Raise notification (engineer). Joins; never held back or shelved. | Yes | When the last step is done |
| **Compressor trip** `machine_trip` | A compression train or pump trip, with what moved first | The machine and the likely culprit | Engineer queue. Joins; held back by planned work. | Yes | When the case is worked, or when the condition clears |
| **Machine health** `machine_health` | The rotating fleet screen: vibration zone C or D, surge, motor overload, head or efficiency loss | The machine | Engineer queue. Joins; held back by planned work. | Yes | As above |
| **Loop cycling** `loop_candidate` | A measurement cycling as a hunting loop or sticking valve does | The point and its asset | Engineer queue | Sent from the case | When it stops cycling, or at desk close-out |
| **Product or water quality drift** `quality_drift` | Lab samples off their own recent level | The sample point's subject | Engineer queue | Sent from the case | As above |
| **Gas balance not closing** `gas_balance_gap` | The daily gas balance open past its threshold for days running | No asset: the days it stayed open | Engineer queue | No | When the balance closes |
| **Alarm flood** `alarm_flood` | More than 10 alarms in 10 minutes (EEMUA 191) | Up to 20 points that drove it | Engineer queue | No | Desk close-out |
| **Alarm bad actor** `alarm_bad_actor` | An alarm standing a day or more, or chattering | The point | Engineer queue | No | When it stops, or at desk close-out |
| **Gas lift heading** `gas_lift_instability` | A gas-lifted well whose casing pressure surges and falls back | The well | Production engineer queue | Sent from the case | When it settles |
| **Well test to review** `well_test_review` | A test overdue, or one that moved water cut or GOR past recent tests | The well | Production engineer queue | Sent from the case | When reviewed, or a new test settles it |
| **Flare event** `flare_event` | A flare above its baseline, with the downtime, log entries and detections around it | The flare | HSE specialist queue | No | Desk close-out |

Alarms and operator log entries do not open cases themselves. They show in the case's **Around the event** card.

## Other detection tools

A site's own tools (vendor condition monitoring, an equipment-specific predictive model, leak detection, corrosion
or well surveillance) send alerts through a source pack's `detection_providers`. Alerts arrive by webhook
(`POST /hooks/{system}/alerts`) or are read every 15 minutes from a table the system writes. See
[sources](sources.md).

- **Matched to:** the asset references the provider maps, such as a tag or a serial number. An alert whose asset
  matches nothing goes to the data steward's queue with its references kept, to be matched.
- **Routed:** as the work type the provider names. It joins a Fuzer detection already open on the same asset, so
  the IOC sees one event with both sources. Each provider has a trust level per equipment class:
  - **Route directly**: the case goes to the role its type names.
  - **Triage first**: a person in `providers.triage_role` looks at it before it goes on.
  - **Muted**: alerts are kept and counted, and no case is opened.
- **Closes:** as its type does. If the provider takes feedback, the case's close-out (real, false alarm, duplicate or
  data problem, with the failure mode) is offered back to it as a write-back for a person to approve.

## Integrity and inspection

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Inspection or wall loss finding** `inspection_due` | Thickness and inspection records: a wall running out, or an inspection overdue for its wall loss or plan | The line or vessel | Engineer queue | Sent from the case | By itself when a later survey clears it; usually worked by planning an inspection on Plan |
| **Pig run anomaly over the screen** `ili_anomaly` | An in-line inspection anomaly over the strength or depth screen now, or within the horizon at its growth rate | The anomaly and its line | Engineer queue | Sent from the case | When reassessed or repaired |
| **Leak repair not holding** `repeat_leak` | LDAR: a component leaking in consecutive surveys and at its latest screening | The component | Engineer queue | Sent from the case | When a screening shows it holding |

## Safety, permits and change

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Permit lapsed with plant still isolated** `permit_lapsed` | Permit to work records: past valid-to, not closed, isolations still in place | The permit, its location and its isolation points | Ops lead queue | Yes: Options, or Send to the field from the case | By itself when the permit is closed or back in date, or nothing on it is still isolated |
| **Bypass overdue** `bypass_overdue` | A safeguard still bypassed past its authorised expiry | The bypass, the safeguard, what it protects and acts on | Ops lead queue | Sent from the case | When the register shows it removed. Active bypasses are on every handover. |
| **Proof test overdue** `proof_test_overdue` | A safeguard past its proof test interval, or with none on file | The safeguard and what it protects | Engineer queue | Sent from the case | When a test is recorded |
| **Safeguard demand to review** `safeguard_demand` | A safeguard that failed on demand, or was demanded more often than its risk assessment assumed | The safeguard and what it protects | Engineer queue | No | Desk close-out |
| **Temporary MOC overdue** `temporary_moc_overdue` | A temporary change open past its approved end date | The change and the plant it touches | Engineer queue | No | When the change is closed or extended |
| **Change with no record** `unrecorded_change` | An alarm or IOW limit that changed with no change record around it | The point | Engineer queue | No | Desk close-out |
| **Deviation review due** `deviation_review_due` | A named deviation from the operating model past its review date | The deviation | Ops lead queue | No | When reviewed |
| **Records don't agree** `records_gap` | Downtime with no log entry or notification, or a log entry saying something stopped with no downtime record | The record and the plant it names | Ops lead queue | No | When the records agree, or at desk close-out |

## The field

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Round finding** `round_finding` | An operator's round on the phone: a reading outside its limits, or a check marked **Found something** and sent to the IOC | The asset the round point is on | Ops lead queue. Joins the open event on the same asset. | Yes: Options, or Send to | IOC close-out after the field reports back |
| **Field report** `field_report` | A type a site defines (on **Settings > Routing**, or in a source pack). The demo's sample crew raises them, labelled *sample crew*. | The tag it names | Ops lead queue, in the demo | Yes | IOC close-out |
| **Crew handover of a job** | A field operator handing a job in progress to the incoming crew at shift change (what was done, what is left, anything unsafe to leave) | The job's asset | Stays with the job: the incoming operator accepts it on their phone | It is a field job | Back with the incoming operator until they report Done or Could not do |

What the field reports on a job (On site, Done, Could not do, a note, a photo, a reading) is evidence on the same
case, not new work. Done or Could not do sends the job back to whoever dispatched it, under **Back from the field**.

## Messages

Work that arrives as free text with no system behind it: mail to the IOC mailbox, radio calls the control room types
in, posts in the operations Teams channels (one `messages` export: sender, channel, subject, text), and a field
operator's **Note to the IOC** from **My work** on the phone. `fuzer.ops.messages` reads each one once, after each
register build or as the note is sent, with the operator log reader's matching (`core.nel`: tags, wells, the pack's
aliases and the ones a steward recorded, checked against the register). It calls no model.

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Reported in a message** `message_report` | A message that names an entity for certain: every tag, well or alias it names is in the register, in one area, and at least `messages.min_confidence` (0.8) sure | What it names, the first named first | To dispatch, as its own case, for the ops lead to send: a message never joins an older case, where its news would read as a second report under that case's title | Yes | IOC close-out, or **Log only, no action** for one that only said what was done |
| **Message to match** `message_to_match` | A message that names nothing for certain: a short tag in several areas, a tag the register lacks, only a place, or no tag at all | The entities it might be about | Data steward queue | No | When the steward chooses the entity on **Work > Master data > Questions > Messages**, from the candidates or a register search that offers only what work can be about (tags, equipment, wells and lines, each with its description and place; anything else is refused), tapping one row or several (a message about three drivers is about all three), and its case opens as above, or says no work is needed; the steward's case closes as **Matched** or **No work needed** |

A safety report (gas, a smell, fire or smoke, a leak, someone hurt) that names nothing for certain does not wait for
the steward: it is also a **Reported in a message** case at once, about nothing yet, at To dispatch for the ops lead.
The steward's answer then says what that same case is about, as **Change the entity** does, so it keeps its age and
whoever has it; no work needed leaves it for the IOC to close (#1408).

A message that asks for nothing ("thanks", an out-of-office reply), or only says what was done with nothing wrong or
asked in it ("Swapped 10-P-1001A to C, A is isolated and drained"), opens no case and is kept as handled, the second as
a log entry with what it names. On All work a message's case shows the channel and sender as its source and a short
summary of its text as the title; the case opens with the channel, sender and time it was received, then the whole
message. The phone's confirmation and the steward's answer link the case they opened, and the answer links the
entities it is about too; answered on its case, the confirmation stays after the case closes. A message case matched
to the wrong entity, by the reader or a steward, is redone on the case: **Change the entity** on the message card
searches the same way and replaces what the case is about, and the case's history says what it was about before
(`POST /exceptions/{id}/message/entities`), naming equipment by the tag it is installed at, never its serial number;
the case's Options are read again for the new entity, and the old entity's leave the page while they are.
The register search taps rows rather than a dropdown; a single result is the pick (a line and **Use it**, no box to
untick). It says "12 of N" when it found more than it shows, and reads a trailing separator ("20-P-") as part
of a tag still being typed. Searching what work can be about lists each thing once, in the plant: an installed
equipment as the tag it is installed at, and no equipment installed nowhere (a scrapped pump a second system still
lists). An answer closes its own steward case at once and comes back with the case as it now stands, so **No work
needed** answered on the case shows it Done without a reload. The reader's precision on Okan's recorded messages is in [eval results](eval-results.md#message-reader)
(`fuzer eval --messages`).

## Regulatory and reporting

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Regulatory report coming due** `report_due` | A report whose due date is near with no draft, or past due and not submitted | No asset: the report and its period | HSE specialist queue | No | When drafted and submitted |
| **Regulatory report to review** `regulatory_report` | A regulatory or emissions report drafted from its records for one period | No asset | HSE specialist queue; reviewed under **Reports > Regulatory** | No | When submitted |
| **Daily operations report** | Each production day closing | No asset | The **daily_report** workflow: Draft report (agent), Review report (ops lead) | No | When published |

## The register and sources

The data steward's queue holds the register's questions and source problems. They also show on **Master data >
Questions** and under the **Data questions** preset on the List. See the [data steward's guide](guides/data-steward.md).

| Work | Where it comes from | Routed to | Closes |
|---|---|---|---|
| **Possible match to confirm** `unmatched_record` | Identity resolution: two systems' records may be one entity | Data steward queue | When confirmed or kept apart; the answer is kept on every rebuild |
| **Sources disagree** `conflicting_sources` | Systems give one entity different values for a field | Data steward queue | When a value is chosen. If a system was wrong, a correction goes back to it (below). |
| **Records that match nothing** `unmatched_records` | A source's records matched nothing the register should have | Data steward queue | When each is said to be new or matched |
| **Links that name nothing** `unresolved_links` | A source's rows name a parent or location the register lacks | Data steward queue | When each value is an alias or the source is fixed |
| **Source problem** `source_issue` | A dataset failed, is held, fails its checks, changed shape or is stale | Data steward queue | When the load recovers |
| **Source quality dropped** `source_quality_drop` | The quality scorecard: completeness, validity, timeliness or agreement fell | Data steward queue | When quality recovers |
| **Load failed a quality check** `failed_quality_check` | A blocking check held a load | Data steward queue | When the export is fixed and loads |
| **Export changed its columns** `mapping_drift` | A dataset's columns changed and its load is held | Data steward queue | When the change is accepted or the load stays held on purpose |
| **Suggested mapping to approve** `mapping_to_approve` | A suggested mapping for a new dataset | Data steward queue | When approved or left unmapped |
| **Drawing disagrees with a system** `drawing_disagreement` | A P&ID against the DCS, AF or SAP | Data steward queue | When the side that needs changing is recorded |
| **Drawing line to confirm** `drawing_line_to_confirm` | A line read from a P&ID joining two entities | Data steward queue | When confirmed |
| **Datasheet curve to confirm** `curve_to_confirm` | A curve read from a datasheet | Data steward queue | When confirmed |
| **Measurement role to confirm** `measurement_role_to_confirm` | What a historian point measures is uncertain | Data steward queue | When confirmed |
| **Asset context missing** `context_gap` | An asset lacks context its class usually has, so a check can't run | Data steward queue | When filled |
| **Criticality not assessed** `criticality_not_assessed` | An asset with no criticality, so its work can't be ranked | Data steward queue | When rated |

**Corrections go back to the source.** When a steward's answer says a source is wrong, Fuzer drafts the fix for
whoever owns it:

- **master_data**: a change to the system's record, through the approved write-back queue, in its approver's queue.
- **drawing_correction**: a redline for the drawing's owner.
- **correction**: a case for the owner of something no write-back reaches, such as the configured alarms.
- **field_check**: a case in the operator queue to go and look at the plant. This is the one register source that
  goes to the field.

The question stays answered while the correction is out. It comes back to the steward if the owner rejects it, the
source changes to something else, or the field check reports back.

## Agents

| Work | Where it comes from | Matched to | Routed to | Field | Closes |
|---|---|---|---|---|---|
| **Proposed by the watch** `watch_finding` | The agentic watch reading a process area for what no detector raised. Off by default: in shadow it keeps and scores proposals and routes nothing. It routes only with `watch.live` on and once it has passed its bar. | The area and the entities it cites | Engineer queue | Sent from the case | Desk close-out. After three confirmed proposals of one pattern, the watch drafts a rule for it. |
| **Detector tuning to review** `detector_tuning` | A detector whose cases on one class keep closing as false alarms or duplicates, with a quieter value replayed before and after | No asset | Engineer queue | No | When accepted or declined |
| **Skill change to review** `skill_change` | An agent's drafted change to a skill's playbook, from briefs people did not follow | No asset | Engineer queue | No | When accepted or declined |

A workflow step held by an agent (Diagnose, Draft SAP notification, Draft report) that no worker picks up does not
wait silently. The case offers **Take it over**, and after 30 minutes the step goes to a person's queue.

## People asking for work

All of these run on the one-step **task** workflow, in the queue they were asked of, with that role's SLA.

| Work | Where it comes from | Routed to | Field | Closes |
|---|---|---|---|---|
| **New case** | **New case** on the List: a title, a queue and an optional checklist (or `POST /work/items` from another tool) | The chosen queue | Sent from the case | Done as asked, at its close-out |
| **A task the assistant drafted** | Ask drafts a task with its steps, and a person confirms it. The steps become the case's checklist. | The queue it was asked of | Sent from the case | Done as asked |
| **Opportunity** | The opportunity scan: a gain in barrels a day, valued | Engineer queue, ranked by value | No | Desk close-out. It is not raised again unless the data changes. |
| **Checklist item sent to another queue** | A checklist item on a case (a permit, a spares decision) sent as its own case, marked *Part of* the first | That queue, or a named person | Sent from the case | When it is done, the item on the parent case reads as done |

## Fuzer following up its own work

| Work | Where it comes from | Routed to | Closes |
|---|---|---|---|
| **Did the fix hold?** | `closeout.recheck_days` (30) after a case closed as a real problem | Whoever closed it | Yes closes it. No reopens the first case as a repeat. A new case with the same failure mode on the same asset inside the window does the same. |
| **Follow-up** `follow_up` | A close-out as **Contained, follow-up planned** or **Not fixed** that named a field operator or the operator queue (one named to a desk is a task in that queue) | Dispatched to them at once: a Field check asking the close-out's note, on the closed case's assets | As field work: the field's report comes back to the IOC to close out |
| **Update to the source system** `source_closeout` | The field reporting Done or Could not do on work another system raised | The **source_closeout** workflow, in that system's close-out role (the maintenance planner for SAP) | When the planner approves it and the system confirms it |
| **Write-back to approve** | Any drafted update to another system: a close-out, a correction, detection feedback | The system's approver role | When approved and confirmed, or rejected |
| **Write-back failed** | A system refused an approved update | The approver role again, with the system's reason. It is never retried on its own. | When approved again or rejected |
| **Shift handover** | Each shift change | The **shift_handover** workflow: a banner on the ops lead's **Waiting on me** | When the incoming lead acknowledges it |

## Any other system

Another system brings its own work in three ways, each declared in its source pack or on the Sources page. See
[sources](sources.md) and [extend](extend.md).

- **Rules on its loaded records** (`exception_rules`). After every load, a record that meets a rule's `when` raises
  its exception, and one that meets `clear_when` (or no longer meets `when`) clears it. Its `refs` say what it is
  matched to.
- **A webhook** (`POST /hooks/{system}` with `X-Fuzer-Secret`). A SCADA, LIMS or inspection system sends exceptions
  without a Fuzer client.
- **`POST /exceptions`** for any type already defined.

Each type is defined with a label, a queue or workflow (or neither, to wait for the IOC), whether the field sees it,
whether it joins open events, and a due policy. It then follows the same table as Fuzer's own types. When the field
finishes one, its system hears back the way its `closeout` setting says: `none`, `draft` (a case for its close-out
role to approve) or `send`.

## Planned work

Work with a date rather than a deadline goes on the plan. It does not pass through the routing table.

| Work | Where it comes from | Matched to | Where it goes | Closes |
|---|---|---|---|---|
| **SAP work order** | SAP orders, after every register build | The equipment and location on the order | **Plan**, by area; its work centre's crew counts on **Plan > Resources** | When the crew says Done or SAP closes the order |
| **SAP maintenance and inspection plan** | Each plan's next due date | The objects on the plan | **Plan** | As above |
| **P6 or other schedule** | A planning system's export | The assets each activity names | **Plan > Schedules** as a Gantt | As each activity is progressed in its export |
| **Turnaround scope** | A turnaround revision's scope | The assets in scope | **Plan**, shaded for the revision's window | As the schedule progresses |
| **Plan for a day** | The IOC planning routed work from its row (**More > Plan for a day**) or from **Options > Ride along** | The case's assets | **Plan**, linked to its case. On its day it comes back to **To dispatch**. | With its case |

A person or role booked as crew sees the job on **My work > Coming up** on the phone and marks it **Started** and
**Done**. Planned and routed work meet in three ways: planned work holds back what it explains, parts not ready is
raised for the planner, and clashes are checked again at dispatch.
