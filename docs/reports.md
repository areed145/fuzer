# Reports

A report is a stored definition: a scope, a window and rows of panels. Layouts are free; data is not. Every panel
reads the application's own data model (the register, cases, the metric registry, trend specs) and never computes a
number of its own, so a figure on a report is the figure on the entity page, in Work and in Ask. There is no reporting
model, warehouse or second definition of a number.

This page is the contract the report engine, the measures and the commentary build against.

## Definition

Standard reports live in `src/fuzer/core/reports/*.yaml`, one file each. A pack adds or replaces one under
`reports:` in its `pack.yaml`, in the same shape, keyed by report key (like `trend_templates`). Custom reports, later,
keep the same shape in the database. A definition is checked when it is read: an unknown panel type, measure, grain,
kind or row set, a duplicate panel id or a size outside 1 to 4 is refused with the reason.

```yaml
schema: 1                 # the definition format; bumps when this shape changes
key: work                 # the URL: #/reports/work
title: Work and dispatch
version: 1                # this definition's own version; bumps on every saved change
for: [IOC lead, maintenance supervisor, field supervisor]   # who reads it, shown in the gallery
scope:
  kinds: [facility, area]         # ontology kinds the scope picker offers; the first entity of the first kind is the default
window:
  default: 7d
  options: [1d, 7d, 30d, 90d]     # optional; every named window when left out
rows:
  - id: headline                  # optional; r0, r1, ... when left out
    panels:
      - {id: arrivals, type: stat, size: 1, measure: work.arrivals}
      - {id: closed,   type: stat, size: 1, measure: work.closed}
      - {id: backlog,  type: stat, size: 1, measure: work.backlog}
      - {id: late,     type: stat, size: 1, measure: work.sla_misses}
  - panels:
      - {id: flow, type: trend, size: 2, title: Opened, closed and open, measures: [work.arrivals, work.closed, work.backlog]}
      - type: column              # a stack of panels beside the others, one level deep
        size: 2
        panels:
          - {id: by-queue, type: figure, measure: work.backlog, grain: queue, chart: bar}
          - {id: note, type: note}
  - id: per-area
    repeat: {kind: area, limit: 8}  # drawn once for each entity of that kind beneath the scope
    panels:
      - {id: area-backlog, type: stat, size: 1, measure: work.backlog}
  - panels:
      - {id: overdue, type: cases, size: 4, title: Overdue cases, filter: {overdue: true}, limit: 10, phone: false}
```

Every panel has an `id` (unique in the report, kept across versions so notes and links stay attached), a `type`, a
`size` (1 to 4 quarters of the desktop width, default 4 for table and cases, 1 for stat, 2 otherwise), an optional
`title` (the measure's label when left out) and `phone: false` to leave it out on phones.

| Type | Fields | Draws | Owner |
|---|---|---|---|
| `stat` | `measure` | The value, its target, the change against the window before and a sparkline | engine |
| `trend` | `measures` (one or more) | The measures over the window on the one trend chart, from `trend_spec.from_measures` | engine |
| `figure` | `measure`, `grain`, `chart` (`bar`, `pareto` or `share`), `limit` | The measure cut by one of its grains | engine |
| `table` | `rows` (a row set) or `measure` + `grain`, `limit` | Rows, each opening its case or entity | engine |
| `cases` | `filter` (the Work list's filters: `state`, `kind`, `stage`, `holder`, `queue`, `source`, `overdue`, `q`, `sort`), `limit` | Live case rows, the same rows as Work (`ops/worklist.rows`, scoped with `within`), with a link to that list | engine |
| `note` | `text` (fixed markdown, optional) | Written commentary for the report and period, with live values | commentary |
| `summary` | none | The AI note for this report, scope, window and day | commentary |

### Layout

A row wraps. On desktop (above 1100 px) a row has four columns and a panel takes `size` of them; on iPad (601 to
1100 px) two, so size 1 is half and 2 to 4 are full; on a phone (600 px and below) one, except stats, which go two
across. The order on a phone is the definition's order, so the answer goes first. Rows size to their content; charts
take a fixed height per width. There are no separate phone, iPad or desktop layouts to keep in step.

### Scope and window

The scope is an entity from the register; every measure and row set answers for it and rolls up what is beneath it.
The window is named: `1d`, `7d`, `30d`, `90d`, `qtd`, `ytd`, ending on `at` (a day, today when left out). Both live in
the URL, so a link opens what the sender saw: `#/reports/work?scope=fz-...&window=30d&at=2026-11-23`.

`core/windows.resolve(window, at)` turns a name and a day into dates, the one place that does:

```python
{"key": "7d", "at": "2026-11-24", "start": "2026-11-18", "end": "2026-11-24", "days": 7,
 "label": "7 days to 24 Nov 2026", "previous": {"start": "2026-11-11", "end": "2026-11-17"}}
```

## The two signatures measures provide

```python
metrics.value(conn, pack, key, scope=None, window=None, at=None, grain=None, history=False) -> dict
trend_spec.from_measures(conn, pack, keys, scope, window, at) -> TrendSpec
```

`metrics.value` answers one measure for a scope (an entity id; None is the whole register) and a window (a name; None is
the measure's own period) ending `at`. Its result keeps today's fields (`value`, `rows`, `refs`, `as_of`, `note`,
`history`, the declaration) and adds `scope` and `window`: what it was actually computed for. A measure that can't yet
be cut by scope returns `scope: None`, and a report says the figure is for the whole register rather than pretend.

`trend_spec.from_measures` draws measures over a window as a trend spec (`analysis/trend_spec.py`), one series per
measure with its label and unit, so the one chart (`ui/src/trends/TrendChart.tsx`) draws it.

## The engine's Python API

The definitions and their checks are in `core/reports` (no data); reading a report for a scope and window is in
`ops/report_pages.py`, which reads the register, the work and the measures.

```python
reports.load(pack) -> dict[str, dict]       # core: every definition, the platform's with the pack's on top, checked
reports.get(pack, key) -> dict              # core: one definition; ReportError when there is none
reports.check(definition) -> dict           # core: the definition with defaults filled, or ReportError with the reason
reports.panels(definition) -> list[dict]    # core: every panel, flattened out of columns, each with its row id

report_pages.view(conn, pack, key, scope, window, at) -> dict              # the definition, scope, window, repeats
report_pages.panel_data(conn, pack, key, panel_id, scope, window, at) -> dict   # one panel's result
report_pages.panel_type(name)               # decorator: registers fn(conn, pack, report, panel, scope, window) -> dict
```

`panel_type` functions get the definition, the panel, the scope as `{id, label, kind}` and the resolved window, and
return their type's own fields; `panel_data` wraps them in the envelope below. `report_pages.DECLARED_IN` names the
modules that register types (like the metric registry): the commentary adds its module there and registers `note`
and `summary`; the AI note takes `panel_data` of every other panel as its facts.

## API

| Route | Answers |
|---|---|
| `GET /reports` | Every report: key, title, for, scope kinds, default window, source (platform, bundle) |
| `GET /reports/{key}?scope&window&at` | The definition, the resolved scope and window, and the entities each repeat row draws for |
| `GET /reports/{key}/panels/{id}?scope&window&at` | One panel's result, so a slow panel costs only itself |

Every panel result carries the same envelope, then its type's own fields:

```python
{"id", "type", "title", "scope": {"id", "label", "kind"}, "window": {...as above},
 "as_of", "note",      # the day the value is for; why it is empty or what it leaves out
 "scoped",             # False when the data is for the whole register, not the scope asked for
 "refs",               # what it is made of: entity ids or "case:<id>"
 "href",               # where it drills: the metric, the Work list with the same filter, the row set's page
 "error"}              # only when it could not be computed
```

The types are in `ui/src/reports/spec.ts`.

## Commentary

Two kinds of text sit beside the figures: the AI note says what the numbers show, and a person's note says what people
know that the numbers do not. Both are kept per report, scope, window and the day the window ends, and both are read
as panel results, so reading a report never calls a model.

**AI note (`summary` panel, `agents/report_summary.py`).** The facts are the report's own panel results for that scope,
window and day (`report_pages.panel_data` of every panel except the commentary and the repeat rows), made small enough
for one short call on `agents.summary_model` at low effort. It writes a headline and two to five points; each point
cites the measure keys, entity ids or `case:<n>` it rests on. The daily report's checks run on it and are kept with it:
a number not in the facts, a citation not in them or a point with no citation is flagged on the note, not hidden.
Notes are kept in `report_summary`, shared with everyone. The panel's result says `due` when none is kept for the
report's default window ending today: the first visit with AI access to the report's own view writes it. Any other
scope, window or day shows "Write the note" to someone with AI access and nothing to write for anyone else.

**People's notes (`note` panel, `ops/report_notes.py`).** Markdown with live values bound to measures, resolved by the
server for the note's own scope and period: `{production.oil}`, `{work.backlog@area}` (the area the scope sits in, or
any kind), `{work.backlog@fz-...}` (that entity). A note is pinned to one period; a report shows its own period's notes
first, then those pinned to shorter periods inside it with their dates (Tuesday's line shows on that week). Only the
author edits or removes a note, and a removed one is kept. A `note` panel with `text` in the definition is a fixed line
with the same live values, such as a section's heading.

| Route | Does |
|---|---|
| `POST /reports/{key}/summary/generate?scope&window&at&rewrite` | Writes the AI note, or returns the kept one (AI access) |
| `POST /reports/{key}/notes` | Pins a note: `{body, scope, window, at}` |
| `PATCH /reports/{key}/notes/{id}` | Changes your own note: `{body}` |
| `DELETE /reports/{key}/notes/{id}` | Takes down your own note |
