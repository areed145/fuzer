# Workflow and scheduled jobs

The work board, daily reports and the jobs that run on a schedule.

## The work board

`board.py` keeps all outstanding work in one ranked list (`board_card`), for people (`GET /board`) and
agents (MCP `board`, `next_work`). Cards come from what already exists:
- the #100 items: incidents, open work orders, PM and inspection plans, opportunities;
- SRE upsets with a priority;
- open workflow items: an IOW breach is valued at the #100 IOW compliance rate for each day outside;
- one card per open review queue (not valued);
- projects added on the board (`POST /board/projects`).

Records joined by an explicit link are one card, led by the highest-valued record. The links are an
incident listing an upset, an upset's work item, and a work order raised for a
notification an incident includes.

Cards are ranked by value, highest first, with unvalued cards last. A person can pin a card's rank or
value with a reason (`POST /board/pins`). Pins are kept by record key in `board_pin`, so they survive
refreshes and register rebuilds. Each refresh records rank changes in `board_history` and says why a
card moved ("up 4 places: value 12,000 -> 40,000", "pinned", "new"). The board refreshes after every
register build and every SRE run, and with `fuzer board refresh`. Pages are read in rank order from
indexes on rank, area and unit.

**Board page.** `#/board` in the UI has five views: ranked (the default), by owner (the person or
role its work item is with), by area, by type, and new since the viewer's last visit. The views come from
facet counts under the same filters. A viewer can save named filters and filter by words in the title.
Each card shows its rank, why it moved, its value and owner, and any pin with its reason. "Why" opens:
- the #100 breakdown (production, risk, compliance, urgency, effort, what isn't known);
- its records, linked to their entities and work items;
- its rank history;
- a form to pin its rank or value (a reason is required) or unpin it.

Projects can be added from the page. Saved filters and the last visit are kept in the viewer's browser.

## Daily operations report

`daily_report.py` writes what happened on a production day and what it means, with every line citing its
records.
- **Facts:** tools work out the day's facts, never Claude's arithmetic, in a fixed outline:
  1. headline: production, the biggest loss with its status, the top board item;
  2. production and deferment, against yesterday and the 7-day average (DAY001, EC downtime);
  3. upsets and events (SRE upsets, incidents, log events);
  4. wells (tests 10% or more from the well's previous test);
  5. maintenance and integrity (notifications, work orders started and finished, IOW excursions,
     plans that went overdue);
  6. look-ahead and asks (plans due tomorrow, the top of the board).
  
  Each fact is a sentence with its numbers and the records it cites.
- **Draft:** `render` writes the facts as cited lines. Claude rewrites them as a report (`redraft`, or the
  reporter agent's `draft` step in the `daily_report` process), keeping every citation and using only
  the facts' numbers. The ops lead's comments on a draft sent back go with the next draft. The cost and
  time of each draft are kept.
- **Checks:** `check` lists lines with no citation, citations not in the day's facts, and numbers not
  in them. The reviewer sees them, and the eval counts them.
- **Versions:** `close_day` stores a version only when the day's facts changed, with what changed by
  section, and starts or updates the day's work item for the ops lead. The `day_close` job (07:00, or the
  `daily_report.close_cron` setting) closes the latest day and re-checks the six before it for late records.

Readers: the Reports page (`#/reports`: days, versions, checks, what changed), MCP
`get_daily_ops_report(day)`, `GET /reports/daily/{day}`, and `fuzer report --day [--claude]`. On Okan, the
day of train C's trip (5 November 2024) leads with "Biggest loss: Process trip, 146 Sm3 oil, back at
10:30" and the trip's downtime record.

## Scheduled jobs

Dagster runs everything on a schedule (`src/fuzer/ops/jobs.py`; `fuzer jobs`, or alongside the UI with `fuzer up`).
Times are in the project's timezone.

| Job or sensor | When | What |
|---|---|---|
| Source assets | on demand, by the jobs below | One per dataset in the source catalog; each loads only itself, as its load mode says |
| `new_exports` sensor | every minute | A changed export file in the data folder loads that dataset, then rebuilds |
| `dataset_schedules` sensor | every minute | A dataset with its own cron (`source_dataset.schedule`) loads when it is due, then rebuilds |
| `refresh_logs` | hourly | Operator logs, then rebuild |
| `refresh_all` | 02:00 | Every dataset (live PI and ADX ones included), then rebuild |
| `claude_reading` | 03:00, off by default | Claude reads new log entries, drawings and documents (Batches API, capped) |
| `work_tick` | every 5 minutes | Work-engine timers and SLA escalations |
| `sre_scan` | every 6 hours | Detect, group into upsets, prioritise, diagnose (capped) and hand off |
| `day_close` | `daily_report.close_cron` (07:00) | The daily operations report, debottleneck options, the opportunity scan |
| `studies_job` | every 5 minutes | Queued correlation and clustering studies |
| `source_health` | every 30 minutes | Stale, failed or held sources and failing checks become work items for their owners |
| `alert_on_failure` sensor | on any failed run | Posts it to the alert webhook (`alerts.webhook`) |

Asset checks: on the register (review backlog, PI points attached, tags in SAP, downtime reason codes, document
revisions, flow against piping) and on each source asset (its dataset's checks and health).
