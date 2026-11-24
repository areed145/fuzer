# Page ownership and navigation

Organize by user goals, not by the adapter, database table or feature's implementation history.
These are ownership boundaries, not a requirement for a fixed number of top-level navigation entries.
The operational primary destinations are **Tower, Work, Plan, Explore, Ops IQ, Reports,
Sources, Jobs and Settings**, in that order (Ops IQ, the operations agent, only for accounts with AI access; its spark sits beside search on every page). Work's two pages, List and Board, are all the work, today's shift (triage, dispatch,
close out), as rows or as columns; Plan is the days and weeks ahead, so they sit together. Demo is not in the menu; `#/demo` still opens the guided tour.
Specialist pages appear in secondary area navigation using their existing URLs.

Primary landings reuse Home, All work (List), the Board (Board), Plan (Plan), Explore (Explore), Downtime (Reports),
Sources, Jobs and Settings. Demo owns onboarding, not a new generic investigation subsystem.
Direct links select their owning primary destination.

| Owner | Existing routes and surfaces | Boundary |
|---|---|---|
| Tower (was Home) | `#/`, legacy `#/?q=…` search entry, compatible `#/analytics` redirect | The field map as the page, a control tower view (#1208). Layers over the map: open work gathered at the located places it is about (a place opens its top items and All work filtered to it, `at=`, so dispatch stays on All work), crews, incidents at the place each is about, the network, and the production charts. Figures over the map's top link to the view that shows the same number (All work's views for the ops lead, Open work for every seat but a field operator, Master data's questions for the steward) and the seat's registered metrics with their trend, each opening Metrics. **Needs you** is the live list of what waits on the signed-in person (My work's rows across their fields, work to dispatch for the ops lead, held or failed loads for stewards and configurers, failed scheduled runs for configurers) and what others did on their work since they last looked; each item opens its record, and the map rings the place it is about while it is pointed at and for a few seconds when it arrives. No actions or embedded configuration. Home has no search box of its own; app search (the magnifying glass at the top left) serves every page. |
| Demo | `#/demo?step=…`, compatible `#/investigate/okan?step=…&evidence=…` | Guided orientation, Okan evidence walkthrough, registered agent capabilities and contextual entries to shared Work. No copied assistant, workflow editor, seeding or automatic execution. |
| Explore | `#/browse`, `#/e/:id`, compatible `#/map`, `#/network`, `#/3d` | Every entity in the register, assets first, each with what it sits in. An entity's page owns everything about it and everything beneath it: the chain above it, Beneath (two levels of what sits in it, with open work and down counts rolled up), and its tabs. All entity links reuse the entity viewer. An asset's kind links to it in the Ontology for configurers. |
| Ops IQ | `#/ask`, `#/skills` (`?skill=…&tab=playbook\|versions\|short&v=…`), `#/knowledge` (`?tab=facts\|lessons\|skills\|decided`) | The operations agent that answers from the register, the work and the readings. Paid, AI-permission gated. An entity's "Ask about this" opens it with that subject. Skills is what its agents follow, for the engineers who own them: each skill's live playbook, its versions against the live one, and where it fell short. Editing saves a draft; publishing (behind the eval gate, AI access) and rolling back happen there and nowhere else. A skill change case links to its draft. Knowledge is the one queue for what the agents propose: learned facts (approved or rejected there and nowhere else), suggested lessons (decided there; Lessons learned keeps the approved and rejected) and skill drafts (each opening its draft on Skills). Reading, drafting and deciding need no AI access, so the Ops IQ menu entry opens Skills for someone without it. |
| Reports | `#/incidents`, `#/downtime`, `#/reports`, `#/metrics`, `#/studies`, `#/lessons` | Evidence, analysis, findings and conclusions. Keep existing specialist tools. |
| List and Board | `#/list` and `#/board`: all the work (`GET /cases`), filtered and sorted on any dimension in the URL (`kind`, `stage`, `holder`, `queue`, `source`, `facility`, `area`, `asset`, `state`, `overdue`, `waiting_on`, `subject`, `within`, `at`, `q`, `sort`; the Board adds `group`). Old links land on the same work: `#/exceptions?list=…` and `#/cases?state=…` redirect to `#/list` with the filters the old view meant. Presets (Waiting on me, To dispatch, Back from the field, Overdue, Data questions) and views saved for a role only set filters. `#/inbox` (a field operator's jobs with their report buttons, kept for no signal; `#/field` redirects into it), `#/masterdata` (its Questions tab answers what `#/review` did), contextual `#/teams` and `#/handover`, and `#/cases/:id` keep their URLs and light Work. | Human priorities, dispatch, review decisions, tasks and cases. Workflows (`#/workflows`, `#/workflows/:key`, `#/workflows/forms/:key`; the older `#/processes…` URLs open the same pages) are in Settings. |
| Plan | `#/plan` (with `#/turnaround` redirecting into it), `#/plan?schedules=1` (Schedules), `#/plan?schedule=CODE`, `#/plan?resources=1` (Resources), `#/plan?logistics=1` (Logistics) | The days and weeks ahead: every source's planned work on one timeline, schedules, resources and their clashes. Data review decides; Sources owns the subsequent load/recovery action. |
| Sources | `#/sources` with existing dataset/system/rule/add query views, configure-protected `#/ontology` (`?kind=…` opens on a kind) | Connect, test, map, ingest, monitor freshness and recover failed loads. Reuse its wizard, connection editor and dataset actions. |
| Jobs | `#/jobs`, `#/jobs/run/:id` | Technical schedules, runs and diagnostics. Sources links here for scheduler failures, without copying job controls. |
| Users (Settings subdestination) | `#/users`, `#/users?view=workflow` | Unified account approval, application roles and human workflow assignments/routing. Only owners grant application access; configure administrators manage routing. Work's contextual Team workload links to this administration owner. |
| Settings | `#/settings`, existing configuration/filter/project queries | Application-wide defaults, optional AI/resource policies, deployment references and restart requirements. Browser Appearance remains available to readers. Secret values remain deployment-managed. |

## Contextual entries, not duplicate implementations

Home links into Sources and Settings. Sources links to Jobs for technical diagnostics and Data review
for human decisions. Data review links back to Sources for source recovery. Jobs links to Sources
for dataset actions and Settings for application policies. Investigation citations and asset links
reuse evidence/entity views. A page may link into another owner; it must not copy that owner's editor
or action implementation.

All existing route URLs remain compatible. The legacy `#/work`, `#/work/item/:id`,
`#/work/processes/:key`, `#/work/forms/:key` and `#/work?task=:id` redirects remain in the shell.
Navigation grouping is presentation, not authorization: keep server permission checks and existing
owner-only/configure/AI visibility rules.

Explore has one page, the entity list; the plant-wide Map, Network and 3D keep their links and light Explore.
App search (`ui/src/AppSearch.tsx`, the magnifying glass at the top left of every screen, or `/`) finds pages from
the menu, the tabs pages declare in `ui/src/pageTabs.ts`, and records through the same `/entities` search Browse
uses; a question (a question word, a question mark or four words or more) puts Ops IQ first and opens it with the question, asked once; any other search keeps Ask Ops IQ in view at the foot of the results. It is a way in, not an owner: each result opens its owning page. Sources secondary pages are Sources and configure-protected
Ontology, the model each source maps onto.
Demo is one route outside the menu, the guided tour; it starts the Okan walkthrough, which keeps `#/investigate/okan`. Reports opens on the report gallery and contains Downtime, Incidents,
Metrics, Regulatory, Lessons and Studies; the daily report is the narrative on the Daily operations report. Work has two pages, List and Board: all the work as a table or as columns, with the same filters.
My work and Master data's questions are presets on it, not menu entries. Workflows (formerly Processes; "process" means the plant in oil and gas) sit in Settings beside Routing:
they are the playbooks the work follows; a case or task links to the workflow it
is following. Plan contains Plan (the timeline), Schedules, Resources and Logistics (read-only: each day's crews and jobs, travel, crews aboard between jobs, seats, beds, lifts and materials to send, read from the plan and the work orders' reservations; changes are made on a schedule or on Resources). A work item planned for a day links to its
bar (`#/plan?item=ID&from=DATE` opens it), and a bar planned from work links back to it in All work. Shift handover is
reached from My work, which shows a banner from shift change until the incoming lead acknowledges it; `#/handover`
still opens it. Turnaround is a window on Plan: `#/plan?revision=…` shades the revision's window on the timeline and
opens its scope beside it, and `#/turnaround?revision=…` redirects there. A schedule is another view of Plan:
`#/plan?schedule=…` opens its activities, logic, critical path and resource histograms for editing (P5.18), and `#/plan?resources=1` (optionally `&resource=CODE`) is the resource owners' view:
each shared resource's demand from every source against its capacity, its overloads with the move that clears each,
and its capacity windows. Data review is the Questions tab of Master data
(P2.3, #505): `#/review?kind=…` redirects to `#/masterdata?tab=questions&kind=…`, and each question is also a case in
the data steward's queue that links to it. Routing stays under Settings and
links to each type's workflow.
All work (#502, the simplification review) is one set of rows: every event and every case, from `GET /cases`. With
no filter it shows all the open work. One filter bar (kind, stage, held by, team, source, state, sort, overdue, words,
and the place, entity or person a link names) is shared by the List and the Board and kept in the URL, so switching
between them keeps it; each picker shows how many match each value. The views it once had (To dispatch, With the
field, Back from the field, All open, In progress, Ranked by value, Closed) are filters, and their old links redirect
to them.
My work (#503, `#/inbox`) is one person's list: the field jobs sent to them and waiting in their queues (the former
My jobs page; `#/field` redirects here), their assigned steps and their queues' unclaimed steps. The steps are one list,
escalated first, with chips for All, Assigned to you, Unclaimed in your roles and Escalated (`?show=`). What changed on
their work since they last looked is Home's Needs you, not a second list here; a field operator still reads it beside their jobs. The rest of the consolidation is planned in #501.
Settings contains Application settings, configure-visible Users, Routing and external Docs. Sources retains
its datasets/systems/rules/wizard navigation; Jobs retains run detail links. Users is deliberately
a Settings subdestination, not a ninth primary destination.

## Unified Users and compatibility

Users owns account approval/disablement, owner and application-role grants, workflow assignments,
coverage and routing defaults. Its Accounts & access view is owner-only; configuration administrators
may use Workflow assignments & routing without reading the owner-only account catalog or granting
application access. Effective access cites saved application roles or owner status. Account identity
uses Microsoft's validated tenant/object IDs and the stable actor, never display-name inference.
Legacy roster names remain explicit; they are not automatically merged with accounts.

`#/access` redirects to `#/users`; `#/settings?view=people` redirects to the workflow view.
The old embedded People editor is retired. `#/teams?role=...` remains a read-only Team workload
view within Work/My work, with contextual links to the matching Users routing section and existing cases/processes.
There is one shared assignment editor for account and roster contexts, not two role stores.

Saved workflow assignments differ from effective roles: disabled accounts retain assignments
for deliberate handoff but have no effective roles; owners span every human role dynamically
without acquiring agent identities. Removing a roster entry returns claimed work to its queues;
disabling an account revokes sessions but preserves assignments/work for deliberate handoff.

Work context is presentation focus, not authentication, permissions, workflow assignment or
impersonation. Workflow roles may suggest defaults; a user's context choice may override the default
without changing roles or administrative access. No editable persona registry, selector without
observable behavior or Home personalization engine is introduced.

## Home direction, not implemented in this pass

Future Home should combine role-based defaults and useful current-work context with personal
pin/hide/reorder choices. Personal choices should take precedence over presentation defaults.
Roles still determine authorization, not a forced layout. Do not add a personalization engine,
automatic relevance ranking or widget configuration until the owning workflows are coherent.

## Contributor checks

Before adding a page or action, identify its owner here and link to an existing surface when one
already owns it. Update this ownership map and navigation together when responsibility changes.
Keep these operational destinations; do not substitute a catch-all Manage group.
Preserve old URLs or add
explicit tested redirects. Verify active navigation, contextual links, direct reader
access and desktop/mobile navigation. Use the existing API/domain boundaries and shared UI kit.

Demo uses `GET /demo/config` for reader-safe effective visibility and the existing
`GET /assistant/agents` catalog. Neither request runs a provider probe. Disabled onboarding URLs
offer an exit and an explicit visibility recheck; the underlying authenticated tools remain available.
`#/ask?demo=agents&q=…` prepares a question without sending it and confirms paid calls in Ask.
Shared-memory learning and conversation deletion are outside the tour; normal Ask retains them.
`#/cases/:id?demo=workflow` shows the existing case and its waiting step (`#/inbox/task/:id?demo=workflow` opens it
there), and confirms deliberate shared take/give back/handoff/completion actions,
preserving server authorization and workflow roles. Contextual return links retain the tour step.
No private sandbox, new run model, automatic task assignment or completion certification is implied.
