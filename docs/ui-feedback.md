# UI review: November 2026

!!! info "A snapshot, tracked as issues"
    This page records the review as it was on 8 November 2026 and is not updated as items are fixed. Each
    note is now a GitHub issue (or a comment on an existing page issue), linked at the end of the note and
    tracked in the checklist on the UI polish umbrella,
    [#290](https://github.com/areed145/fuzer/issues/290). Check the issue for current status.

Notes from walking every page of the web UI on the Okan demo while capturing the screenshots for these
docs. The goal is a backlog of concrete improvements, so each note names the page, what was seen, and a
suggested change.

**How this was gathered.** `FUZER_AUTH_MODE=local fuzer up --no-jobs --no-worker` on main at `022951a`,
no AI provider configured, then `limits learn`, `limits scan`, `sre --handoff --max-diagnoses 0`, `report`
and `board refresh` by hand. Three people were added on Teams & routing (an ops lead and engineer, a
maintenance planner, an engineer and production engineer). Pages were opened with Playwright in Chromium
at 1440×900 in light and dark mode and as an iPhone 13. No page overflowed horizontally on the phone, and
no page logged a rendering error. Base map tiles could not load from the sandbox, so map notes leave that
out.

Severity: **High** gets in the way of the demo story or reads as wrong; **Medium** costs time or trust;
**Low** is polish.

## Cross-cutting

- **High · Synthetic time and wall-clock time are mixed.** The scenario's data ends on 2025-12-30, but
  SLAs ("due in 4 h"), case "Opened" dates and the entity page's **Process values** window use today's
  date (Nov 2026), and the synthetic historian fills values up to now. The upset on Inbox was *detected*
  for 2025-12-24 but is *due* tonight. Measurements on the same entity page stop at 2025-12-03 while process
  values run to 2026-11-09. *Suggest:* anchor the demo's "now" to the data's latest day (the daily report
  already does), or label each time as "event time" vs "recorded time" consistently, and default the
  Process values window to the data's last day. ([#389](https://github.com/areed145/fuzer/issues/389))
- **Medium · Headings show a focus ring after direct navigation.** `main.tsx` focuses the page `h1`
  (`tabIndex=-1`) on route change. After a mouse click there's no ring, but on a reload, a pasted link or
  Back, the heading (and on Inbox and Ontology the whole `main`) draws a thick blue/black outline that looks
  like a selected text box. *Suggest:* `[tabindex="-1"]:focus:not(:focus-visible) { outline: none; }`,
  keeping the focus move for screen readers. ([#391](https://github.com/areed145/fuzer/issues/391))
- **Medium · Raw keys and citation markers leak into the UI.** Examples: daily report lines end with
  `[opportunity:gas_lift:OK-J07-05→OK-J12-09]` and `[maintenance_plan:INSP-30000038]`; entity field labels
  are title-cased keys ("Ec Code", "Isa95 Level", "Iso14224 Level"); a well shows its GeoJSON geometry as a
  value next to latitude and longitude; task evidence lists `start` and `units` raw. *Suggest:* render
  citations as links only, add display labels for known fields (ISA-95 level, ISO 14224 level, EC code),
  and hide geometry when lat/long are shown. ([#392](https://github.com/areed145/fuzer/issues/392))
- **Medium · Agent-dependent work looks stuck when no worker runs.** The daily report case sits in
  "Draft report / reporter queue" and the report page says "the reporter agent is drafting it" when no
  agent worker was started (no provider). *Suggest:* say "waiting for the agent worker, which isn't
  running" with a link to Settings, as Jobs already does for Dagster. ([#393](https://github.com/areed145/fuzer/issues/393))
- **Low · Secondary navigation is inconsistent.** Sources and Jobs have no tab row, so their heading
  sits higher than on every other page, and Sources has a second, lower tab row (Datasets, Systems &
  connections, Rules) under a long intro. Settings has "Application configuration" and "Configuration"
  tabs whose difference isn't clear. ([#394](https://github.com/areed145/fuzer/issues/394))
- **Low · Sidebar background stops at the first screen.** On full-page captures of long pages (dataset
  Mapping, Add a source step 4) the white sidebar ends at 900 px and the page background shows below it.
  *Suggest:* `min-height: 100vh` with `position: sticky` on the sidebar, or stretch it to the page. ([#395](https://github.com/areed145/fuzer/issues/395))
- **Low · Timestamps carry milliseconds.** Sources and dataset versions show
  `2026-11-08 21:01:53.039 UTC`. Minutes are enough in tables; keep the full value in a tooltip. ([#396](https://github.com/areed145/fuzer/issues/396))

## Home

- **Medium · The production efficiency panel says little.** Weighted efficiency 100%, allocated 104,354
  vs dynamic maximum 104,355.3 Sm³, and the two lines are drawn on top of each other on a y-axis from 0 to
  8,000, so neither the gap nor day-to-day change is visible. In dark mode the blue line disappears entirely
  behind the orange one. *Suggest:* plot the gap (or efficiency %) with a fitted y-axis, or lead with the
  loss in Sm³ and its biggest cause. ([comment on #307](https://github.com/areed145/fuzer/issues/307))
- **Low · Work badge changes between loads** (14, then 15) without anything on the page explaining the
  count. A tooltip ("1 task, 14 data reviews") would help. ([#397](https://github.com/areed145/fuzer/issues/397))

## Explore

- **Medium · Network opens unreadable.** The allocation graph renders as a tall, narrow column of tiny
  nodes with unreadable labels; **Fit** doesn't help much at 1440×900. *Suggest:* a left-to-right layered
  layout (wells → manifolds → separators → meters → sales), and start zoomed to one platform. ([comment on #300](https://github.com/areed145/fuzer/issues/300))
- **Medium · Map fit includes the export line,** so the field (where almost everything is) occupies a
  small corner while the gas and oil export lines run off to the terminal. *Suggest:* fit to wells and
  platforms by default, with the export lines a toggle away. With no tiles the background is plain grey
  with no message; a "base map unavailable" note would explain it. ([#398](https://github.com/areed145/fuzer/issues/398))
- **Low · Ontology edge labels overlap** heavily in the centre (Tag, Well, Downtime) and **Add link
  type** wraps onto two lines. ([#399](https://github.com/areed145/fuzer/issues/399))
- **Low · Entity page links section is long.** A well lists all 24 well tests and every log entry
  inline. Collapse each relation over ~5 items with "show all". ([#400](https://github.com/areed145/fuzer/issues/400))
- **Low · PI AF keys look escaped** on the entity page (`OKAN\20 OK_GGCP\20-K-2300A`). If that's the AF
  path, show it with separators that read as a path. ([#401](https://github.com/areed145/fuzer/issues/401))

## Investigate

- **High · Daily report numbers read as values when they are differences.** "0.0 Sm3 against
  yesterday's 7,472 Sm3" and "20 Sm3 against the 7-day average of 7,452 Sm3" read as if production was 0 and
  20 Sm³. *Suggest:* "7,472 Sm3, unchanged from yesterday" and "20 Sm3 above the 7-day average". ([#390](https://github.com/areed145/fuzer/issues/390))
- **Medium · Incidents lists one event twice.** The 2024-11-05 train C trip appears as two incidents
  (on 20-V-2001 and 20-V-2101C) with the same title; 20-V-2101C appears again on 2024-11-09. If grouping is
  per unit by design, say so, or group by the shared downtime record. ([#402](https://github.com/areed145/fuzer/issues/402))
- **Medium · Downtime totals don't reconcile.** The header says 69,751 Sm³ lost; "By equipment" adds up
  to about 2,100 Sm³ because planned maintenance (65,600 Sm³) names no equipment. *Suggest:* an "unattributed"
  row in the equipment and class cards. "Hydrates 0" is listed as a reason with no loss; hide zero rows. ([#403](https://github.com/areed145/fuzer/issues/403))
- **Low · Ask doesn't say up front that no provider is configured.** The suggestions and prompt look
  ready; the limitation is only clear on Home's card text. A notice above the prompt would set expectations. ([#404](https://github.com/areed145/fuzer/issues/404))

## Work

- **Medium · Upset titles are machine text.** "Upset: Detected alarm:hihi on 30PT2002.PV at
  2025-12-24 00:00, reaching 90 units" and "priority 0.00". *Suggest:* "High-high alarm on 30-PT-2002
  (value 90 barg)" with the unit from the point, and hide priority until it's scored. ([#405](https://github.com/areed145/fuzer/issues/405))
- **Medium · Process diagrams are too small to read** on the case and task pages: task labels render at
  about 6 px. *Suggest:* fit to width with a minimum zoom, or show the current step's neighbourhood with a
  "full diagram" toggle. The "waiting now / done path taken" legend under the diagram runs into itself. ([#406](https://github.com/areed145/fuzer/issues/406))
- **Low · Task page shows the form-js "BPMN.io" watermark** inside the task form, and **Complete** is
  enabled before the task is claimed. ([#407](https://github.com/areed145/fuzer/issues/407))
- **Low · Board cards are tall for what they hold.** Each card is one line of content in ~70 px; a
  denser row would show twice as many. Owners read "unassigned" on every card on the demo. ([#408](https://github.com/areed145/fuzer/issues/408))
- **Low · Cases filter has an unlabelled "choose…" select** next to "every kind". ([#409](https://github.com/areed145/fuzer/issues/409))
- **Low · Data review match reasons are terse.** "same tag 76-HCV-1003; manufacturer unknown,
  description 1.00" and an unlabelled 0.70 bar. Spell out "description similarity 1.00, manufacturer
  missing on one side, match score 0.70 (review between 0.60 and 0.90)". ([#410](https://github.com/areed145/fuzer/issues/410))

## Sources

- **Medium · Add a source suggests the wrong record kind for an equipment list.** For
  `incoming/contractor_equipment_list.csv` (tag, description, manufacturer, model, serial, install date,
  weight) the suggestion is "Each record is: Tag", keyed on `Tag No.`. Rows with serial numbers and
  manufacturers are equipment; suggesting equipment keyed on serial, blocked by tag, would match the SAP
  copy straight away. `Install Date` is detected as a timestamp in the sample header but offered as `text`. ([#411](https://github.com/areed145/fuzer/issues/411))
- **Medium · "Everything this connection holds is already a dataset" is a dead end.** On the default
  `files` connection the wizard doesn't look in subfolders (the demo's `incoming/` holds six new tables) and
  doesn't suggest adding a connection for another folder. Offer "look in a subfolder" or link to step 2. ([#412](https://github.com/areed145/fuzer/issues/412))
- **Low · Sample header runs the column and its meaning together** ("Tag No. tag", "Mfr manufacturer").
  Put the meaning on its own line or as a chip. ([#413](https://github.com/areed145/fuzer/issues/413))
- **Low · The record key picker is a cramped multi-select** (four rows visible, the fifth cut off). A
  checkbox list or chips would be clearer. ([#414](https://github.com/areed145/fuzer/issues/414))
- **Low · Sources readiness copy pushes the table below the fold.** On a phone, the datasets start
  about two screens down. Collapse the four-step readiness explanation once a deployment has datasets. ([#415](https://github.com/areed145/fuzer/issues/415))

## Jobs

- **Low · The sidebar and the page disagree.** The sidebar says "Jobs: no history yet"; the page says
  scheduler history is unavailable. When Dagster isn't running, say how to start it (`fuzer up` without
  `--no-jobs`, or `fuzer jobs`). ([comment on #325](https://github.com/areed145/fuzer/issues/325))

## Command line (found while writing the guide)

- **Low · Missing data folder errors don't say how to fix them.** Run outside a project,
  `fuzer sources preview` fails with "no table … in data" and `run` with "unavailable through its
  connection". Name the folder it looked in and suggest `--project`, `--data` or `FUZER_DATA_DIR`. ([#416](https://github.com/areed145/fuzer/issues/416))
- **Low · `fuzer sources add-dataset` silently uses the system's first connection,** which is the
  automatic `files` connection, not one just added with `add-connection`. Accept `--set connection=<name>`,
  or say which connection was chosen. ([#417](https://github.com/areed145/fuzer/issues/417))
