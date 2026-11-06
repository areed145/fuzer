# Deployment and settings

Running Fuzer, its configuration and how it performs at size.

## Configuration

`.env` holds only the database, the project and secrets. Everything else is a setting: change it on the Settings page or with `fuzer settings set <key> <value>`. A project's value beats the global one, which beats the default. [Settings](settings.md) lists them all.

Reference: [command line](cli.md), [database schema](schema.md).

## Render

The repository includes a Render Blueprint (`render.yaml`) that creates the
Docker-based web service and a managed Postgres database. In Render, create a
new Blueprint from this repository and deploy it. The web service builds the
React UI and serves it with the FastAPI app; Render injects the database's
connection string as `FUZER_DATABASE_URL`.

The Blueprint selects small paid plans for both resources so the web service
stays available and the database persists. Review or change those plans in
`render.yaml` before deploying if you prefer different resource sizes.

The service initializes the database schema on startup, but it does not load
source exports or demo data. Import your asset data after deployment. If the
application needs Claude features, set `ANTHROPIC_API_KEY` in the Render
service's environment; the API and UI can start without it.

## Performance at full size
`fakeplant generate --size large` makes an asset close to a real offshore one: 58k
tags, 163k PI points, 3.9k pieces of equipment, 15k notifications, 26k work
orders, 20k log entries (~300k source records, 289k entities, 510k links).

- **Rebuilds** sync instead of rewrite: rows are staged in a temporary table
  and only the differences are deleted, updated or inserted (hash joins, with
  `work_mem` raised for the rebuild transaction). Only reviewer choices are
  stored as assertions; per-source values are re-derived from an entity's own
  records when its page is opened. No per-row foreign-key checks on tables the
  rebuild owns. Labels are a table kept by the rebuild, and the ontology summary
  is computed once per rebuild. Large: ingest 5 s, first rebuild ~37 s, later
  rebuilds ~25 s, ~1.1 GB peak memory. Medium (1/10): ~7 s.
- **Reads** (large, `fuzer bench`): search 13 ms, tag page 6 ms, hub page
  190 ms (links capped per relationship), 2-step neighbourhood 14 ms, flow trace
  1 ms, ontology 1 ms, incidents 16 ms, review list 7 ms, class members 116 ms,
  class-wide maintenance history 490 ms.
- **Work board** (large, with a year of open work orders: 3,731 cards): a full
  refresh 6 s; `GET /board` 15 to 25 ms for a page with any filter, text search
  or facets, 52 ms for the last page; a card's history 14 ms.
- **Graph database**: not needed. Traversals Fuzer does (flow traces,
  neighbourhoods, class hierarchies) are milliseconds in Postgres at full size;
  revisit only if multi-asset graphs or deep path queries change that.
