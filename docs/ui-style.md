---
title: UI style guide
description: Shared UI components, page ownership, state, transport and verification conventions.
---

The UI uses a small local component kit in `ui/src/ui/`. Use it for shared page structure and controls; keep specialized visualizations and editor integrations in their own components.
Page and action ownership is defined in [page ownership](page-ownership.md). Navigation presentation
is a separate user decision, not implied by that map. Preserve compatible routes and use contextual links rather than copying
another owner's viewer, editor or action.

## Clean style

Fuzer is used by two people: IOC staff at a desk for a whole shift, and field operators on a phone
outdoors. Both know the plant. Neither needs the app explained on every screen. Every page should
look like a calm operations tool: a title, the work, and the one or two actions that move it on.
These rules come first; the sections below say how the components carry them out. It must read as a
plain, conventional enterprise application for any asset type.

1. **A title needs no tagline.** A page has a title and nothing under it that says what the page is for. `PageHeader`'s
   `detail` is for a fact about the record shown (its description, its dates, its version). How a page works belongs
   in the docs: pass `help="workflow"` for a Help link. Cut any sentence that restates what the screen already shows.
2. **No standing disclaimers.** State a limit where the user acts on it: the confirm step, a disabled
   button's reason, an empty state. Data provenance (synthetic, historical) is one `Badge` in the page
   header or chart title, not a sentence per card. Error text says what failed and what to do next in
   one sentence.
3. **No carets, no adornments.** Don't use `<details>`/`<summary>` at all. Guidance goes in the docs;
   long data (a table, a stack trace, a history) goes in `Disclosure`, a plain text button. Status is a
   word in the state's colour, not a pill. No icons in circles, no tinted boxes, no carousels, no
   decorative symbols. Configuration belongs on Settings, not folded into an operational page.
4. **One component per job.**
    - Pages within an area: Area navigation: underline tabs, one line, scrolls sideways on a phone.
    - Views of one page: `Tabs` (segmented control), at most about six views.
    - Facets with counts: chips, drawn as plain text filters with an underline on the chosen one.
    - A choice: `Select` or `Input` inside `Field`, with a visible label ("Kind", not "every kind").
    - The main action: One `primary` `Button` per page or card; everything else `secondary` or `ghost`.
    - Something the user must act on: `Notice` (`warn` or `danger` only when that is the state).
    - Status: `Badge` with a text label.

    Don't use primary buttons as tabs, link-card grids as a second menu, carousels for content that
    should be read together, or "How to read this" side panels.
5. **Generic chrome.** Shared screens (navigation, search, headers, empty states) never name an asset
   type, a vendor or a site. They say "assets", "records" and "work"; the data supplies "well",
   "compressor" or "separator".
6. **Labels are nouns, values carry units.** "Oil lost (Sm³)", not "Oil lost (Sm³); the last row is loss
   whose records name no equipment". Show display names, never raw keys or ids.
7. **Colour means state.** Accent marks the current place and the main action. `ok`, `warn` and `danger`
   always come with words. Tinted callout boxes are for things to act on, not for introductions.
8. **Empty means one line and at most one action.** "No incidents in this window." plus a link to widen it.
   Use `Empty` from the UI kit for it, never a grey `<p className="muted">`, which the style lint counts.
9. **Phones get the work, not a shrunken desk.** Tap targets at least 44 px, the area tabs on one line,
   and work lists as rows of title, asset and state rather than wide tables.

`npm --prefix ui test` runs `ui/tests/style-lint.test.cjs`, which counts help disclosures, grey
paragraphs, page taglines, carets, standing disclaimers, literal colours, inline styles and raw buttons and
tables in `ui/src`. Counts may only go down: when you remove clutter, lower the number in
`ui/tests/style-baseline.json`; when the test fails, follow its hint rather than raising the number.

## Numbers

`ui/src/format.ts` is the only place a number becomes text: `money`, `percent`, `quantity` and `count`. Pass the
value as the API returned it; never round, scale or abbreviate it in a page. Money is whole dollars from 100 up and
cents below, never "k" or "M", so one amount reads the same on every page. Text written by the server uses
`fuzer.core.wording.money`, which formats the same way.

## Times

`when`, `whenDay` and `ago` in `ui/src/format.ts` are the only place a time becomes text: "Nov 14, 2:11 PM", with the
year only when it is not this year. A value with an offset is an instant and reads in the viewer's clock. A value
without one is the plant's own clock: it is shown as written and marked "plant time", never converted. A date alone
("2026-11-17") stays that day in every timezone. Never cut a date string with `slice` in a page.

## API client

`ui/src/api.ts` remains the compatible facade for calls and exported types.
Domain modules in `ui/src/api/` own identity, Explore, investigation, Work,
Sources, Settings and Jobs request shapes. They all use `transport.ts` and the
existing bounded `requestQueue.ts`; do not add per-domain queues, CSRF handling,
retry policies or business rules. Only in-flight read-only requests coalesce.
Paid previews and mutations remain deliberate, unreplayed calls.

## Components

- `PageHeader` is the page title, an optional `detail` (a fact about the record, never fixed text), actions and an optional `help` docs link. Each page has one `PageHeader` and one `h1`; use `h2` for page sections and `h3` inside a section.
- `Button` uses `primary` for the main action, `secondary` for ordinary actions, `ghost` for low emphasis, `link` for inline actions, and `danger` for destructive actions. Use `sm` for compact table actions and `md` elsewhere. Use `ButtonLink` for navigation styled as a button and `ButtonLabel` for labels that open hidden file inputs.
- `Card` (also exported as `Section`) groups related content. Supply `title` and `actions` when the group needs a heading or controls.
- `Table` provides the shared scroll container and table base. Use it for data tables and choose a compact density for dense lists; use specialized classes only for domain-specific layout.
- `Text` sets the text style (`small`, `muted`, `mono`) and `Line` puts things on one line (`spread`, `wrap`). Use them instead of the class names; a style that is missing is added to the kit, not to a page.
- `Checkbox` is a checkbox with its label; `onChange` gets the new state.
- `ErrorText` shows a failure message.
- `Field`, `Input`, `Select` and `Textarea` keep form controls aligned. Widths are `xs`, `sm`, `md`, `lg`, and `full`; prefer `full` for long text and `sm` or `xs` for short values.
- `Tabs` switches between views of one page; `Notice` holds something the user must act on; `HelpLink` links a docs page.
- `Badge` shows a compact status with `ok`, `warn`, `danger`, or `muted` tone.
- `Stat` presents a summary number; `EmptyState` explains an empty result; `Bar` displays a percentage or other bounded value from 0 to 100.
- `LiveFigure` shows a server-built figure with all its wording in a `Card`, so a chart needs no page code. Never set colours or chart text in the UI: series follow the theme colorway by trace position. Put several charts in a grid of cards, not a carousel.
- `FindingEvidence` (`ui/src/Finding.tsx`) is the one way to show what was found: how (rule, agent or another system, with method and standard), when, and the labelled values with units, local times and assets by name. `CaseFindings` wraps it for a case's sources. Never print a finding's fields, or evidence keys, another way.
- `Disclosure` shows long data on request (a table, a stack trace, a history) behind a plain text button. Never use it, or `<details>`, for guidance.
- `Markdown` renders assistant answers. Fenced code is literal, and a `mermaid` fence draws a diagram through `MermaidDiagram`, which loads on demand, uses Mermaid's strict security level and keeps the source readable when it can't draw. A diagram missing its fence is recognized by its opening keyword and direction; one with no line breaks can't be parsed, so it stays as source.
- Citations in text read as names. `Markdown` turns `OK-J09-04 [fz-well-…]` into a linked name, and a citation with no name beside it into a short linked label; the full id stays in the link's tooltip. Stored text keeps its ids, so show entities with `useEntityNames` rather than printing an id.
- `ConfirmAction` presents inline consequences and explicit confirm/cancel controls for shared-state changes.
  `ActionStatus` announces action progress/results with `role="status"` and failures with `role="alert"`.
  Reuse these for source ingestion and review instead of browser dialogs or transient success toasts.
- `confirmDialog(message)` and `promptDialog(label)` are the app's one modal: await the answer in a handler
  (`if (!(await confirmDialog("Delete kind pump?"))) return;`). Questions asked while one is open wait their turn.
  Never call the browser's `confirm`, `prompt` or `alert`.
- `useAction` (`ui/src/lib.tsx`) runs a button's async action: `busy` names the action in flight, `error` holds the
  failure for `ActionStatus`, and a success refreshes every view. `useReasonedAction` is the same for a change that
  needs a reason. Don't keep busy and error state by hand.

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

Users account search and enabled/owner filters live in the URL. Account search starts in the first
viewport on a phone; guidance about accounts lives in the docs, reached from the page's Help link. Counts and owner
recovery guidance come from the loaded account catalog, never filtered results.
Typing replaces the current search URL so Back does not walk through each character.
Account edits remain unsaved until confirmation; displayed effective access
continues to cite saved roles. Workflow person cards distinguish saved
assignments, effective roles, disabled accounts and dynamic owner access.
After an ambiguous assignment failure, reload recorded assignments before a
deliberate retry. Reloading is a read, not a replay of the failed mutation.

Each sign-in state has its own heading (checking, check failed, session ended,
not configured, sign-in failed or unreachable, first-owner setup, awaiting approval,
no read access) and names who acts next. Check buttons show that a check is in progress,
and a changed state moves focus to its heading. Sign-in distinguishes a not-enabled account (awaiting approval or deliberately
disabled) from an enabled account without read permission. Both explain the
owner's next action without requesting protected data before access is granted.
Neither signing in nor workflow assignment grants application authorization.

## Layout and tokens

Use the existing spacing rhythm: 4px for tight inline gaps, 8px for control groups, 12–16px between related blocks, and 20–24px between major page areas. Keep page content within the app's existing 1200px reading width.

Use theme variables for color: `--bg`, `--panel`, `--text`, `--muted`, `--line`, `--accent`, `--accent-soft`, `--ok`, `--warn`, `--danger` and `--info`. One palette for things and flows, used by every graph, the 3D view, maps and their legends: entity types use `--kind-*` (`kindColor`), what a line carries uses `--product-*` (`productColor`), and link types use `--rel-*` (`relColor`; a link type without a token takes a chart colour picked from its name). A drawn line takes its product's colour when that is known, else its link type's (`linkColor`, in `ui/src/kinds.ts`). The `--map-*` service tokens alias the product tokens. How urgently a place or item needs someone is one attention scale, `--attn-high`, `--attn-medium` and `--attn-low` (danger, warn and the blue `--info`), used by Home's hotspots, the 3D view and its 2D twin, and the High, Medium and Low priority words (`attentionColor` and `ATTENTION_TONE` in `kinds.ts`): high is down now or high-priority work, medium an alarm sounding or medium-priority work, low low-priority work. Text on a filled status colour uses its `--on-*` ink, white in light and near-black in dark. A picked thing is the accent on every view, never a status colour. Do not add literal colors to TSX.

Charts, graphs, maps, drawings and the 3D view open on the whole screen from their own button: use `useFullscreen` and `FullscreenButton` from the UI kit (a Plotly chart also passes its layout through `fullscreenLayout`), never a page's own full screen code.

Tailwind v4 utilities and colors mapped from these variables are available throughout the UI. Its Preflight reset is intentionally omitted so existing pages remain stable while they move onto the shared components.

Keep `ui/src/styles.css` limited to theme tokens and app-wide base styles. Put shared primitive styles in `ui/src/styles/ui-kit.css`; keep page and visualization styles in `ui/src/styles/` and import them from the component that owns the markup.

## One design on every screen

The app follows iOS on every screen. When in doubt, do what iOS does. `ui/src/ios.ts` puts the `ios` class on the page on every device, so iPhone, iPad and desktop share one design: the same type ramp, controls, cards and colours. Only the navigation changes shape: a bottom tab bar on a phone, a sidebar on an iPad or desktop, as Apple does on larger screens. The gestures (edge-swipe Back, pull to refresh) stay phone-only. `?ios=0` turns the look off for one tab, to compare against the old desktop look. All the styles sit under `:root.ios` in `ui/src/styles/ios.css`. The account is the round badge with the initials at the top right on every screen: a sheet from the bottom on a phone, a popover under the badge on an iPad or desktop. The sidebar carries navigation and the site's health only, and there are no breadcrumbs: Back, the sidebar and the section tabs say where you are. Fuzer's colour tokens stay the same; iOS brings the type, shapes and gestures. The look is **flat, not skeuomorphic**: Liquid Glass's shapes (capsules, round bar buttons, a floating tab bar) drawn with plain or simply translucent fills. Don't add highlights, rims, bevels, gradients, glows or drop shadows:

- The system font (SF Pro). Large page titles fold into the top bar as the page scrolls.
- No solid top bar: Back and other bar buttons are round translucent buttons. The tab bar is a floating capsule of icons with no labels (each tab keeps its name for VoiceOver), and the current tab sits in a lighter pill. A tab is lit anywhere in its group, so Work is lit on All work, Plan and Master data as well as My work. Menu is never highlighted.
- Inset grouped cards and lists with chevrons. The Menu is a sheet with one grouped list and plain line icons in the tint colour.
- A row of segments that fits the screen is a segmented control: a capsule track, the chosen segment a lighter pill with the label in the primary colour and the others grey, and the segments share the width equally. A single-choice chip row (`.chips.segmented`) gets the same look. The section tabs (`.area-navigation`, the second level of navigation) are always tabs: a row too wide for a phone scrolls sideways and opens with the current tab in view, and it never folds into a menu. A page's own sections (Settings, Sources) are section tabs too, so mark them `area-navigation`. On a phone a list table that sits straight on the page is its own inset card, and a list with no headings stacks each cell on its own line. A `Disclosure` label made of several parts reads as one wrapped line. Say "select", not "click", for anything a finger can do. A status badge in a page header wraps; it never runs off the page. A "show more" inside a table cell is a link in its line. On a desktop, text, date and select fields and the filled button beside them are 34 px high. A page's own view picker (any `.tabs`, the Board stage row, Home's filter row) behaves the same way: every choice stays visible as a segment and a row too wide scrolls sideways. Nothing folds into a pull-down menu.
- Work cards swipe left to bring up their actions. Keyboard focus and VoiceOver reach the same buttons.
- Buttons are 44 points tall (34 for small ones) everywhere outside field mode. Selects are 36-point pop-up buttons with a small up/down chevron. Rows in a card form an inset grouped list: one leading edge, a hairline between rows, a semibold title, and a grey line of detail.
- Type follows one ramp, Apple's Dynamic Type sizes, as `--t-*` tokens in `ios.css`: Large Title (34) for the page, Title 3 (20) for a section, Headline (17 semibold) for card and row titles, Subheadline (15) for running text, Footnote (13) for detail and meta, and Caption (12) for badges. Use a token, never a new pixel size.
- Tables share one look: grey Footnote headings as written, 44-point rows with hairlines, and numbers right-aligned in tabular figures (`Table` marks numeric cells). On a phone every `default`, `results` or `list` table with headings stacks into labelled list rows. A stacked row reads as its title, then grey Footnote detail lines with each label in line with its value; a cell that is blank or only a dash is left out, and the row keeps the card's 16-point inset.
- On/off checkboxes look like iOS switches. Sheets float with deep corners.
- A `Notice` is an inset card like the rest of the page (a warning keeps its colour as a hairline), a text area is the same filled field as an input, and the map's layer chips are the same 32-point chips as every other filter row. Floating surfaces (the base map picker, sheets, the Menu) carry no drop shadow.
- An empty list says so in one grey line (`Empty`, or `Status` with `empty`). Standing alone on a page the line sits in an inset card; inside a card it is just the line. Don't centre it or use `p.small.muted` for it.
- A case or a task is titled by the work, never by the step ("Do the task"): the step's name sits under the title. A case that works a steward's question shows the question's own card on the case, with the drawing it is about, so the answer is given where the case is read; giving the case to someone else and changing its priority are one tap away, under the next step. A due time shows once. A list title is short ("20-PT-2201B: drawing and SAP disagree on where it sits"); the full question is in the card. The one-step standard queue is not drawn as a process diagram unless asked for, and a question about records shows no trend.
- Nothing on screen is a raw key: a duration reads "1 day" (not `P1D`), a role "data steward", a source system "SAP", an agent "opportunity scan agent". A long list of values shows its first four and folds the rest. A signed-in account is never asked to type its name.
- Dates use `stamp` and `dayStamp` from `wording.ts`: "Nov 14, 2:11 PM", with the year only when it is not this year. A card or a list row has at most one filled button, and a chosen range or view is a segmented control, never a filled button.
- A record's header actions (its kind, Watch, Ask, Earlier date) are one line of small controls in one text size, and a work row's text starts at the card's own inset.
- Tools on a canvas are one stack: the map, Network and 3D use the same 44-point rounded squares in the panel colour down the right edge, Full screen and zoom included. The base map picker is a segmented control at the top left. Apple Maps offers Standard and Satellite; the free tiles add Terrain.
- The account sheet names the account in its heading with Done at the trailing edge, and Sign out is a plain red row. In the installed app Share sits beside the account badge.
- Help is a round info button on every screen. On an iPhone a large title stops at two lines and the subtitle gives way. A table with headings and the `phone-rows` class stacks each row as a list row: the first column is its title and every other cell is labelled with its heading by `Table`, so give wide tables that class rather than phone-only markup.
- Filter chips (`.chips` > `.chip`) are compact 32-point chips. On an iPhone a row of them scrolls sideways on one line instead of wrapping; a row that holds a disclosure still wraps.
- An iPad in landscape keeps the same look, with the sidebar as a floating panel. In portrait, Home's map and list sit side by side down to 768 px.
- In the installed app, where Safari's own gestures are missing, you can swipe in from the left edge to go Back (this uses the app's Back button), and you can pull down to refresh.

Add a new control by extending the shared components rather than adding iOS-only markup. Check it at iPhone size with `?ios=1` in both light and dark.

## Preferences, state and guided use

Settings -> Appearance offers System, Light and Dark, persisted per browser.
Use `useTheme` or the `THEME_CHANGED` event from `theme.tsx` when a visualization
needs to repaint; never listen to OS appearance directly in individual views.
Theme-specific CSS uses `:root[data-theme="dark"]`. Drawing and BPMN canvases
intentionally retain their light backgrounds so source drawings stay legible.

Field mode (P5.17g, #1096) is a second, per-device Appearance choice for gloves and sunlight, not a role: the class
`field-mode` on `<html>` (`setFieldMode`, `useFieldMode` in `theme.tsx`, styles in `styles/field-mode.css`). It sets
targets to at least 56 px with 8 px gaps, body text to 17 px, colours to 7:1 contrast in light and dark, removes
translucency, underlines links in the page, and keeps a job's main actions at the bottom of the screen within thumb
reach on a phone. The installed app on a phone offers it once on My work. A new control must still meet 56 px and 7:1
under `field-mode`.

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

Home's start guide lists reader destinations before administration (Sources, Settings). With no
records loaded, Home keeps only the setup prompt, Needs you and administrator health; report,
priority and incident cards would only be empty. Data and scheduler health is shown only to
administrators. Each card reports a failed read with its own retry, never as an empty result, and
labels its times (incident start, detection start, run start) with the shared `timestamp` format.
A detection links to its case when one exists.

Use `lib.timestamp` for ingestion/run/observation timestamps: offset-bearing values are shown in
UTC, missing/invalid values are unknown, and timezone-free values remain explicitly unzoned (shown as
written, to the minute, labelled "plant time").
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

Demo is not in the menu. The guided tour still opens at `#/demo` while the global `demo.enabled`
registry setting is on, and Home no longer embeds the Okan callout.
The `#/demo?step=…` tour introduces evidence, registered agents and loaded workflows;
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
