# Page ownership and navigation

Organize by user goals, not by the adapter, database table or feature's implementation history.
These are ownership boundaries, not a requirement for eight top-level navigation entries.
The seven operational primary destinations remain **Home, Explore, Investigate, Work,
Sources, Jobs and Settings**. An optional **Demo** tab introduces first-time users and is enabled
by default; administrators hide or enable it application-wide with the global `demo.enabled` setting.
Specialist pages appear in secondary area navigation using their existing URLs.

Primary landings reuse Home, Browse (Explore), Downtime (Investigate), All work
(Work), Sources, Jobs and Settings. Demo owns onboarding, not a new generic investigation subsystem.
Direct links select their owning primary destination.

| Owner | Existing routes and surfaces | Boundary |
|---|---|---|
| Home | `#/`, legacy `#/?q=…` search entry, compatible `#/analytics` redirect | Orientation, the production Chart Show and brief readiness links. Work is summarized, not copied (#556): counts for the person's roles (My work for everyone, All work's views for the ops lead, Master data for the steward and configurers), each linking to the view that shows the same number, and the next few titles. Where attention is needed: open exceptions gathered at the located places they are about, on the field map (the Map page's `MapView`) and as a ranked list; a place opens All work filtered to it (`at=`), so dispatch stays on All work. No work rows, actions or embedded configuration. Search reuses Browse. |
| Demo | `#/demo?step=…`, compatible `#/investigate/okan?step=…&evidence=…` | Guided orientation, Okan evidence walkthrough, registered agent capabilities and contextual entries to shared Work. No copied assistant, workflow editor, seeding or automatic execution. |
| Explore | `#/browse`, `#/e/:id`, `#/map`, `#/network` | Assets and connected evidence. All entity links reuse the existing entity viewer. An asset's kind links to it in the Ontology for configurers. |
| Investigate | `#/ask`, `#/incidents`, `#/downtime`, `#/reports`, `#/studies`, `#/lessons` | Evidence, analysis, findings and conclusions. Keep existing specialist tools. Paid Ask remains AI-permission gated. |
| Work | `#/exceptions` (All work, with compatible `#/board` and `#/cases` redirects into its views), `#/inbox` (My work, with `#/field` redirecting into it), `#/inbox/task/:id`, contextual `#/teams` and `#/handover`, `#/plan` (with `#/turnaround` redirecting into it), `#/masterdata` (its Questions tab answers what `#/review` did, which redirects there), `#/cases/:id`, `#/processes`, `#/processes/:key`, `#/processes/forms/:key` (listed under Settings) | Human priorities, read-only team workload, review decisions, tasks, cases and process definitions. Data review decides; Sources owns the subsequent load/recovery action. |
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

Explore secondary pages are Browse, Map and Network. Sources secondary pages are Sources and configure-protected
Ontology, the model each source maps onto.
Demo is one entry, the guided tour; it starts the Okan walkthrough, which keeps `#/investigate/okan`. Investigate contains AI-gated Ask, Downtime, Incidents, Daily reports,
Lessons and Studies. Work contains four entries: All work, My work, Plan and Master data (#554). Shift handover is
reached from My work, which shows a banner from shift change until the incoming lead acknowledges it; `#/handover`
still opens it. Turnaround is a window on Plan: `#/plan?revision=…` shades the revision's window on the timeline and
opens its scope beside it, and `#/turnaround?revision=…` redirects there. Data review is the Questions tab of Master data
(P2.3, #505): `#/review?kind=…` redirects to `#/masterdata?tab=questions&kind=…`, and each question is also a case in
the data steward's queue that links to it. Processes sits beside Routing
under Settings, keeping its `#/processes` URLs.
All work (#502) is the IOC's one list: its views are To dispatch, With the field, Back from the field, All open
(exceptions), In progress and Closed (the former Cases list), Ranked by value (the former Work board) and Board
(the open work by stage, one column per view).
`#/board?view=…` redirects to `#/exceptions?list=ranked&view=…`, and `#/cases?state=…` to the In progress or
Closed view with its filters; case and task pages keep their URLs. One filter bar (kind of work, held by, words in
the title) is shared by every view and kept in the URL, so switching views keeps it.
My work (#503, `#/inbox`) is one person's list: the field jobs sent to them and waiting in their queues (the former
My jobs page; `#/field` redirects here), their assigned steps and their queues' unclaimed steps. The rest of the consolidation is planned in #501.
Settings contains Application settings, configure-visible Users, Routing, Processes and external Docs. Sources retains
its datasets/systems/rules/wizard navigation; Jobs retains run detail links. Users is deliberately
a Settings subdestination, not an eighth primary destination.

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
Keep the seven operational destinations plus optional Demo; do not substitute a catch-all Manage group.
Preserve old URLs or add
explicit tested redirects. Verify active navigation/breadcrumbs, contextual links, direct reader
access and desktop/mobile navigation. Use the existing API/domain boundaries and shared UI kit.

Demo uses `GET /demo/config` for reader-safe effective visibility and the existing
`GET /assistant/agents` catalog. Neither request runs a provider probe. Disabled onboarding URLs
offer an exit and an explicit visibility recheck; the underlying authenticated tools remain available.
`#/ask?demo=agents&q=…` prepares a question without sending it and confirms paid calls in Ask.
Shared-memory learning and conversation deletion are outside the tour; normal Ask retains them.
`#/cases/:id?demo=workflow` inspects the existing case read-only and links to its tasks.
`#/inbox/task/:id?demo=workflow` confirms deliberate shared claim/release/handoff/completion actions,
preserving server authorization and workflow roles. Contextual return links retain the tour step.
No private sandbox, new run model, automatic task assignment or completion certification is implied.
