# Page ownership and navigation

Organize by user goals, not by the adapter, database table or feature's implementation history.
These are ownership boundaries, not a requirement for eight top-level navigation entries.
The seven operational primary destinations remain **Home, Explore, Investigate, Work,
Sources, Jobs and Settings**. An optional **Demo** tab introduces first-time users and is enabled
by default; administrators hide or enable it application-wide with the global `demo.enabled` setting.
Specialist pages appear in secondary area navigation using their existing URLs.

Primary landings reuse Home, Browse (Explore), Downtime (Investigate), Work board
(Work), Sources, Jobs and Settings. Demo owns onboarding, not a new generic investigation subsystem.
Direct links select their owning primary destination.

| Owner | Existing routes and surfaces | Boundary |
|---|---|---|
| Home | `#/`, legacy `#/?q=…` search entry | Orientation, current priorities and brief readiness links. No embedded configuration. Search reuses Browse. |
| Demo | `#/demo?step=…`, compatible `#/investigate/okan?step=…&evidence=…` | Guided orientation, Okan evidence walkthrough, registered agent capabilities and contextual entries to shared Work. No copied assistant, workflow editor, seeding or automatic execution. |
| Explore | `#/browse`, `#/e/:id`, `#/map`, `#/network`, `#/ontology` | Assets and connected evidence. All entity links reuse the existing entity viewer. Ontology is configure-protected. |
| Investigate | `#/ask`, `#/incidents`, `#/downtime`, `#/analytics`, `#/reports`, `#/studies`, `#/lessons` | Evidence, analysis, findings and conclusions. Keep existing specialist tools. Paid Ask remains AI-permission gated. |
| Work | `#/board`, `#/inbox`, `#/inbox/task/:id`, contextual `#/teams`, `#/cases`, `#/cases/:id`, `#/review`, `#/processes`, `#/processes/:key`, `#/processes/forms/:key` | Human priorities, read-only team workload, review decisions, tasks, cases and process definitions. Data review decides; Sources owns the subsequent load/recovery action. |
| Sources | `#/sources` with existing dataset/system/rule/add query views | Connect, test, map, ingest, monitor freshness and recover failed loads. Reuse its wizard, connection editor and dataset actions. |
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

Explore secondary pages are Browse, Map, Network and configure-protected Ontology.
Demo contains the guided tour and compatible Okan walkthrough. Investigate contains AI-gated Ask, Downtime, Incidents, Daily reports,
Analytics, Lessons and Studies. Work contains Work board, Inbox, Cases, Data review and Processes.
Settings contains Application settings, configure-visible Users and external Docs. Sources retains
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
view within Work/Inbox, with contextual links to the matching Users routing section and existing cases/processes.
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
