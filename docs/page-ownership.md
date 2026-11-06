# Page ownership and navigation

Organize by user goals, not by the adapter, database table or feature's implementation history.
These are ownership boundaries, not a requirement for eight top-level navigation entries.
Sidebar presentation is undecided. The prior Explore, Operations, Knowledge, Workflows and Admin
groups/labels remain unchanged pending a user decision; they do not define domain ownership.

| Owner | Existing routes and surfaces | Boundary |
|---|---|---|
| Home | `#/`, legacy `#/?q=…` search entry | Orientation, current priorities and brief readiness links. No embedded configuration. Search reuses Browse. |
| Explore | `#/browse`, `#/e/:id`, `#/map`, `#/network`, `#/ontology` | Assets and connected evidence. All entity links reuse the existing entity viewer. Ontology is configure-protected. |
| Investigate | `#/investigate/okan`, `#/ask`, `#/incidents`, `#/downtime`, `#/analytics`, `#/reports`, `#/studies`, `#/lessons` | Evidence, analysis, findings and conclusions. Keep existing specialist tools; the Okan walkthrough is an existing journey, not a new generic subsystem. Paid Ask remains AI-permission gated. |
| Work | `#/board`, `#/inbox`, `#/inbox/task/:id`, `#/cases`, `#/cases/:id`, `#/review`, `#/processes`, `#/processes/:key`, `#/processes/forms/:key` | Human priorities, review decisions, tasks, cases and process definitions. Data review decides; Sources owns the subsequent load/recovery action. |
| Sources | `#/sources` with existing dataset/system/rule/add query views | Connect, test, map, ingest, monitor freshness and recover failed loads. Reuse its wizard, connection editor and dataset actions. |
| Jobs | `#/jobs`, `#/jobs/run/:id` | Technical schedules, runs and diagnostics. Sources links here for scheduler failures, without copying job controls. |
| Users (deferred #279) | Existing `#/access`, `#/teams`, People editor in Settings | Accounts, access, application roles and human/team routing have a future shared owner. Keep current surfaces and authorization until that separate change; do not silently consolidate or conflate app and workflow roles. |
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

## Home direction, not implemented in this pass

Future Home should combine role-based defaults and useful current-work context with personal
pin/hide/reorder choices. Personal choices should take precedence over presentation defaults.
Roles still determine authorization, not a forced layout. Do not add a personalization engine,
automatic relevance ranking or widget configuration until the owning workflows are coherent.

## Contributor checks

Before adding a page or action, identify its owner here and link to an existing surface when one
already owns it. Update this ownership map when responsibility changes. Do not infer sidebar grouping or label
approval from conceptual ownership boundaries. Preserve old URLs or add
explicit tested redirects. Verify active navigation/breadcrumbs, contextual links, direct reader
access and desktop/mobile navigation. Use the existing API/domain boundaries and shared UI kit.
