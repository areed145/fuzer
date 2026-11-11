# Product changes

Read [adapter boundaries](docs/adapter-boundaries.md) and the [UI style guide](docs/ui-style.md)
before changing user journeys. Follow its Clean style rules: one-line subtitles, no standing disclaimers
or help behind carets, one component per job; `ui/tests/style-lint.test.cjs` enforces them. Reuse domain operations, the settings/capability registries, and the
local UI kit; do not add a parallel service, component kit, or source of configuration.
Read [page ownership](docs/page-ownership.md) before adding routes or controls: one owning surface
per action, reusable contextual entry links, compatible URLs, and no duplicated entity/source/job
editors. Navigation grouping is not authorization. Users is a Settings subdestination owning
account/access and workflow administration; Team workload is a contextual read-only operational view.
Keep account approval, application authorization, workflow assignments and Work context distinct.
Work context is presentation focus, never a permission grant, assignment change or impersonation.
Do not add a context selector/registry until it drives meaningful supported behavior.
Home personalization is a future direction, not permission to add a ranking/widget engine.
Keep the user-selected primary destinations Home, Explore, Investigate, Work, Sources, Jobs and
Settings. Put specialist routes in their owner's secondary navigation, preserving URLs and access
rules; do not replace this with a catch-all Manage section or duplicate primary specialist entries.
Shared API requests use the bounded browser queue and coalesce only in-flight read-only calls.
Never automatically retry mutations, paid probes or rejected requests. Verify normal page mounts
and global refresh at default server admission limits, plus explicit overload/recovery behavior.
For source ingestion and review actions, show prerequisites, shared-state consequences and an explicit
confirmation before executing; reuse `ConfirmAction`/`ActionStatus` and existing action services.
Distinguish a saved decision or rebuild request from completed ingestion/rebuild. Link source execution
to the dataset's Runs/Quality and scheduler diagnostics to Jobs, never invent a second run model.
After ambiguous failures, tell users to inspect recorded state before a deliberate retry; do not
silently replay. Keep reader controls explanatory and enforce permissions on the server.
Source loads share `sources.ingest.ingest` selection, validation and result semantics across adapters.
Explicit missing inputs must fail; repeated dataset names execute once. Only bulk/scheduler discovery
may skip optional absent exports, reporting `missing` rather than `loaded`. Preserve held/paused/unchanged
outcomes and failed-run propagation; do not add transport-specific load rules or retry execution.

- Keep business rules in their owning domain, not in API/CLI/MCP adapters or React pages.
- Every guided journey needs an entry, ordered next steps, a useful outcome, and loading, unavailable,
  error/retry and exit paths. Keep progress in route state when it does not need persistence.
- Claims and numeric findings must cite loaded evidence, with units and an explicit time window.
  Distinguish event dates, ingestion observation timestamps, unknown values and live freshness.
  Synthetic and historical data must be labeled; a missing prerequisite is not a successful demo.
- Read-only/keyless journeys must not generate data, rebuild, mutate workflow state or call models.
  Enforce read-only behavior at the server boundary, not just by hiding buttons.
- Every data/execution HTTP router must use `AuthenticatedRoute`; only the shell, health and explicit
  sign-in routes are public. Extend its policy and `test_access_api.py` for new execution surfaces,
  paid GET previews or permission types. Never trust a client-supplied audit actor.
- Keep Microsoft accounts/application permissions separate from workflow routing roles. New accounts
  await owner approval; owners span all human roles, not agent identities. Do not introduce first-visitor
  ownership, email-based ownership, client-only authorization, or an unauthenticated MCP bypass.
  Bind workflow assignments to stable account actors, not display-name matches. Preserve legacy
  roster identities without auto-linking them. Distinguish saved roster assignments from effective
  roles: disabled accounts have none, owners dynamically span all human roles. Reuse the shared
  workflow assignment editor and existing stores; only owners can grant application access.
- Label shared operational state and deliberately shared knowledge. Keep assistant conversations,
  uploads and answers account-scoped on every read/write path. Local attribution is an explicit
  loopback-only development mode, never a deployment fallback.
- Extend the existing components/tokens and use accessible navigation and focus management.
  Do not replace source evidence with hard-coded scenario answers.
- Sources owns connect/test/ingest/freshness/load recovery; Settings owns application configuration,
  defaults and restart requirements. Home summarizes and links to those owners, never duplicates
  their configuration. Reuse existing Sources/Jobs controls, not a parallel dashboard.
  Use effective registry values and provenance, not defaults as
  runtime truth. Distinguish reference presence, recorded tests and observed live behavior.
  Viewing readiness must not test providers, load data, mutate state or incur AI charges.
  Keep secret values in deployment storage, expose references/presence only, and state application
  timing honestly: next use versus affected-process restart, never an unverified "restart complete".

For the Okan investigation, run `uv run pytest tests/api/test_investigation_api.py` and
`npm --prefix ui run build` followed by `npm --prefix ui run test:browser`.
Include `tests/api/test_access_api.py` for permission/session changes and the affected workflow tests.
The API tests use the shared disposable Okan database and verify access and SQL read-only boundaries.
Browser fixtures verify navigation and recovery; they do not prove live Render behavior.
