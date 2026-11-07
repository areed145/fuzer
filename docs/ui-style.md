# UI style guide

The UI uses a small local component kit in `ui/src/ui/`. Use it for shared page structure and controls; keep specialized visualizations and editor integrations in their own components.
Page and action ownership is defined in [page ownership](page-ownership.md). Navigation presentation
is a separate user decision, not implied by that map. Preserve compatible routes and use contextual links rather than copying
another owner's viewer, editor or action.

## Components

- `PageHeader` is the page title and optional subtitle/actions. Each page has one `PageHeader` and one `h1`; use `h2` for page sections and `h3` inside a section.
- `Button` uses `primary` for the main action, `secondary` for ordinary actions, `ghost` for low emphasis, `link` for inline actions, and `danger` for destructive actions. Use `sm` for compact table actions and `md` elsewhere. Use `ButtonLink` for navigation styled as a button and `ButtonLabel` for labels that open hidden file inputs.
- `Card` (also exported as `Section`) groups related content. Supply `title` and `actions` when the group needs a heading or controls.
- `Table` provides the shared scroll container and table base. Use it for data tables and choose a compact density for dense lists; use specialized classes only for domain-specific layout.
- `Field`, `Input`, and `Select` keep form controls aligned. Widths are `xs`, `sm`, `md`, `lg`, and `full`; prefer `full` for long text and `sm` or `xs` for short values.
- `Badge` shows a compact status with `ok`, `warn`, `danger`, or `muted` tone.
- `Stat` presents a summary number; `EmptyState` explains an empty result; `Bar` displays a percentage or other bounded value from 0 to 100.
- `ChartShow` (`ui/src/ChartShow.tsx`) carries several charts in one card, moved by swipe, arrows, dots or arrow keys. Each slide mounts when first reached. Slides take any content; `LiveFigure` shows a server-built figure with all its wording, so a chart needs no page code. `PlotlyChart` leaves touch drags to the show. Never set colours or chart text in the UI: series follow the theme colorway by trace position.
- `Markdown` renders assistant answers. Fenced code is literal, and a `mermaid` fence draws a diagram through `MermaidDiagram`, which loads on demand, uses Mermaid's strict security level and keeps the source readable when it can't draw.
- Citations in text read as names. `Markdown` turns `OK-J09-04 [fz-well-…]` into a linked name, and a citation with no name beside it into a short linked label; the full id stays in the link's tooltip. Stored text keeps its ids, so show entities with `useEntityNames` rather than printing an id.
- `ConfirmAction` presents inline consequences and explicit confirm/cancel controls for shared-state changes.
  `ActionStatus` announces action progress/results with `role="status"` and failures with `role="alert"`.
  Reuse these for source ingestion and review instead of browser dialogs or transient success toasts.

## Action lifecycle

Source loads first expose known prerequisites (active dataset, enabled connection and resolving secret
references); these do not substitute for server validation of mappings, credentials or quality checks.
Confirm the load mode and effects on shared records before sending a request. Disable competing actions
while executing and show progress, then distinguish loaded, unchanged, paused, held and failed outcomes.
Held loads link to the dataset's Quality/Mapping and Runs; scheduler execution belongs in Jobs, not a
new parallel task system. Existing source runs remain the source of recorded ingestion details.

Review decisions require operate access and applicable workflow roles. Confirm the shared decision;
acknowledging a held-load notice does not repair or retry ingestion. A saved decision or queued rebuild
is not proof of completed downstream work. Preserve the existing global rebuild/health feedback.
Failures must remain visible and explain how to inspect recorded state before a deliberate retry:
an HTTP/network failure may occur after a mutation committed. Never automatically replay actions.
Viewing pages must not execute loads, rebuilds, connection tests or paid probes.

Users is a Settings subdestination with owner-only account/application-access controls and
configuration-admin workflow routing controls. Reuse its shared workflow assignment editor for
account and legacy-roster contexts. Always explain saved assignments versus effective access.
Work context is presentation focus, not a workflow role or permission. No context selector is
shown until supported presentation behavior exists; do not persist a cosmetic persona choice.

## Layout and tokens

Use the existing spacing rhythm: 4px for tight inline gaps, 8px for control groups, 12–16px between related blocks, and 20–24px between major page areas. Keep page content within the app's existing 1200px reading width.

Use theme variables for color: `--bg`, `--panel`, `--text`, `--muted`, `--line`, `--accent`, `--accent-soft`, `--ok`, `--warn`, and `--danger`. Entity graph colors use `--kind-*`; map services and points use `--map-*`. Do not add literal colors to TSX.

Tailwind v4 utilities and colors mapped from these variables are available throughout the UI. Its Preflight reset is intentionally omitted so existing pages remain stable while they move onto the shared components.

Keep `ui/src/styles.css` limited to theme tokens and app-wide base styles. Put shared primitive styles in `ui/src/styles/ui-kit.css`; keep page and visualization styles in `ui/src/styles/` and import them from the component that owns the markup.

## Preferences, state and guided use

Settings -> Appearance offers System, Light and Dark, persisted per browser.
Use `useTheme` or the `THEME_CHANGED` event from `theme.tsx` when a visualization
needs to repaint; never listen to OS appearance directly in individual views.
Theme-specific CSS uses `:root[data-theme="dark"]`. Drawing and BPMN canvases
intentionally retain their light backgrounds so source drawings stay legible.

Identity lives in `identityStore.ts` (re-exported from `work.tsx` for existing callers).
Use `usePerson` for reactive UI and `loadPerson` at action time; do not read or
write the reviewer storage key in pages. This is audit attribution, not
authentication. Shared API refresh/rebuild polling belongs in `useAppData`;
route selection and shell presentation belong in `main.tsx`.
`useAsync` retains data during a refresh of the same resource but hides it
immediately when dependencies change. This prevents a previous person's inbox
or an old search result being presented as the newly selected resource.

Use visible labels or accessible names on controls, actionable empty states and
`Status` for loading/errors. Preserve keyboard links in tables. The mobile
navigation contains focus and returns it to the menu on close; a skip link
allows keyboard users to bypass navigation. Home introduces the purpose and
starting actions; empty registers link directly to the source setup wizard.

For read failures on core screens, pass the resource's `reload` to `Status.onRetry` and provide an
exit to the owning list. This retries only an explicit read; mutation recovery must still inspect
recorded state first. Missing data is unknown/unavailable, never a zero count or a passed check.
Use existing `Badge` tones plus textual labels; color alone is not a status explanation.
Dataset and job-run table navigation uses real links, not click-only rows.

Label Home's latest report by its recorded report date, not "Today". Detections without an end
date do not prove a current outage. Source health concerns ingestion and configured expectations,
not the age of historical plant events. Scheduler availability is distinct from loaded-data
availability. Missing scheduler history needs deployment-operator guidance, not an assertion that
the user should run a local CLI command.

Use `lib.timestamp` for ingestion/run/observation timestamps: offset-bearing values are shown in
UTC, missing/invalid values are unknown, and timezone-free values remain explicitly unzoned.
Never apply this normalization to source event dates with no supplied timezone. Retain the
Okan synthetic/historical labels and distinguish event time from ingestion observation time.
Do not infer percent units from a numeric value below one; scheduler metadata keeps its recorded
field name and numeric value. A scheduler asset result does not by itself establish ingestion.

Demo's Okan entry leads to the compatible `#/investigate/okan`: a four-step, read-only journey
(issue, asset, evidence, finding). Step and citation selection live in the URL so
Back, reload and deep links work. Use ordinary links for steps, `aria-current="step"`,
and focus the step heading after navigation. Findings cite the same evidence shown
in the journey; numeric claims are assembled in `graph/investigation.py`, never
hard-coded in React. The API enforces a read-only SQL transaction and requires no
model calls. This boundary is limited to the journey, not an application-wide
public-demo access policy.

The optional Demo primary tab is enabled by default and controlled application-wide by the
global `demo.enabled` registry setting in Settings. Home no longer embeds the Okan callout.
The broader `#/demo?step=…` tour introduces evidence, registered agents and loaded workflows;
progress remains in URLs. Agent prompts open Ask for review and explicit paid-call confirmation,
never automatic submission. Contextual case views are read-only; task entries confirm shared
assignment/completion changes using `ConfirmAction` and report failures using `ActionStatus`.
Readers can inspect capabilities and work without acquiring AI access or workflow roles.

Run `npm --prefix ui test` for preference/state regression tests and
`npm --prefix ui run build` for type-checking and production assets.
For browser regressions, install Chromium with `cd ui && npx playwright install chromium`,
then run `npm --prefix ui run test:browser` after building. These tests use an
isolated preview on port 5174 with API fixtures; they do not need a database or
provider key. CI runs all three checks. Confirm representative workflows
against a disposable seeded asset as well before claiming live data validation.
