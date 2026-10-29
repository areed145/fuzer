# Fuzer

Home-built asset data fusion for oil & gas: entity resolution, entity linking,
and a dated asset graph (physical, flow/allocation, data, documents) across
SAP S/4, JDE, Maximo, PI / PI AF, ADX, Energy Components, P&IDs and operator
logs.

Design and decisions: [docs/design.md](docs/design.md).

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and Docker.

```sh
docker compose up -d       # Postgres 16 on localhost:5432
export FUZER_DATABASE_URL=postgresql://fuzer:fuzer@localhost:5432/fuzer
uv sync
uv run fuzer demo          # fake data -> Postgres -> resolve -> golden -> graph
uv run fuzer serve         # API on http://127.0.0.1:8000 (docs at /docs)
uv run pytest              # always uses a throwaway embedded Postgres
```

Without Docker, skip the first two lines and run `uv sync --extra local`;
Fuzer then starts an embedded Postgres under `.fuzer/pgdata`.

### Bigger fake data

```sh
uv run fuzer demo --size medium   # ~1/10 of a real offshore asset: 60 areas, ~7.9k tags,
                                  # ~17k PI points, ~2k equipment, 40 wells, 480 P&IDs, 3k log entries
uv run fuzer generate --size medium --areas 90 --trains 10 --logs 10000   # or tune any part
```

`small` (the default) is the hand-checked asset the tests use. A full rebuild of
`medium` takes about 4 s. Review answers return at once: a chosen value is
applied immediately, and match answers queue one background rebuild (the UI
shows "Updating…").

### Claude reads operator logs and scanned P&IDs

```sh
export ANTHROPIC_API_KEY=...       # or `ant auth login`
uv run fuzer llm-link --dry-run    # how many entries / requests / tokens, calls nothing
uv run fuzer llm-link --limit 40   # try a small sample first
uv run fuzer llm-link --batch      # everything, via the Batches API at half price (results within ~1 h)
uv run fuzer llm-link --source pids --limit 5   # just scanned drawings (or --source logs)
uv run fuzer pid-score             # fake data only: found / missed / wrong tags vs. the answer key
```

For each log entry Claude gets a numbered list of candidates from the asset
register (ambiguous short tags in every area, equipment in any area named in
the text) and picks from it, or says it can't tell. It also pulls out events
(trip, start, changeover, shut-in, choke change, alarm, leak, instrument fault,
...) which become `event` entities linked to the log entry and to the tags and
wells involved. Results are cached per entry; rebuilds never call the model,
and an entry is only re-read when its text changes (`--force` re-reads all).
Scanned P&IDs (the fake generator draws them as PNGs, with an answer key in
`pid_truth.json` that is never ingested) are sent as overlapping tiles. Claude
transcribes every tag as drawn; Fuzer turns each reading into a tag key with the
asset pack's grammar and checks it against the register. Readings that aren't
tags, tags missing from the register, and title blocks that disagree with the
document register are kept as findings rather than links. Each drawing is cached
by the SHA-256 of its image, so it's only re-read when re-scanned. The drawing
shows on the P&ID's page in the UI.

Model defaults to `claude-opus-5-5` (`--model` or `FUZER_LLM_MODEL` to change),
with server-side refusal fallback on.

Ingest treats each export file as a full snapshot: records no longer in it are
removed and their entities retired (IDs are kept and revived if they return).

### Scheduled jobs (Dagster)

```sh
uv sync --extra jobs
export FUZER_DATA_DIR=data/fake    # the folder the exports land in
uv run fuzer jobs                  # Dagster UI on http://127.0.0.1:3000 (Ctrl+C to stop)
```

- One asset per source export (each loads only its own file, as a full
  snapshot), then `register` (the rebuild), then the Claude readers.
- **`new_exports` sensor** (on): drop a changed export into the data folder and
  that source is loaded and the register rebuilt, usually within a minute.
- **Schedules**: `refresh_logs` hourly and `refresh_all` daily at 02:00 (on);
  `claude_reading` nightly at 03:00 (**off**: it costs money; turn it on in the
  UI). Scheduled Claude runs use the Batches API and cap each run at 2,000 log
  entries and 200 drawings. Times are in `FUZER_TIMEZONE` (default UTC).
- **Checks on the register** (warnings): review backlog under 5% of tags and
  equipment, PI points attached to a tag or well, every tag present in SAP.
- **Alerts**: failed runs are posted to `FUZER_ALERT_WEBHOOK` (Teams, Slack or any
  JSON webhook) when it's set.
- Run history lives in `.dagster/` locally; in Azure, Dagster's own storage
  would go in Postgres (`dagster-postgres`).

### Web UI

Needs Node 20+.

```sh
cd ui && npm install && npm run build   # then `fuzer serve` shows the UI at /
npm run dev                             # or: live-reload UI at :5173 (keep `fuzer serve` running)
```

Screens:

- **Browse**: search everything.
- **Entity detail**: chosen values and what each source says, source records,
  dated links, flow trace, all with an "as of" date. Its **Graph** tab draws
  the neighbourhood (click a node for details, double-click to expand, filter
  by link type, choose a layout).
- **Ontology**: the data model as a diagram (kinds sized by count, link types
  with counts) with physical / flow / data / documents views; click a kind for
  its fields, field coverage and feeding sources.
- **Network**: the whole production / allocation flow, left to right, as of
  any date.
- **Review**: yes/no on possible matches, pick the right value when sources
  disagree.

### MCP server (AI agents)

```sh
uv run fuzer mcp           # stdio, for Claude Code / Claude Desktop
uv run fuzer mcp --http    # streamable HTTP on http://127.0.0.1:8001/mcp
```

Claude Code picks it up from `.mcp.json` in this folder (approve it once when
asked). Read-only tools: `get_ontology`, `search`, `get_entity`, `get_events`, `trace_flow`,
`equipment_history`, `equipment_changes`, `link_text`, `list_reviews`,
`get_stats`. Anything that takes a `ref` accepts a tag in any style, a well,
a PI point, a source key or a Fuzer ID. Try asking: *"What fed the production
separator on 2024-01-01, and what changed after?"* or *"Which pumps were
swapped, and what replaced them?"*

### API

Try:

```sh
curl "localhost:8000/entities?q=OSB.20PT1001A.PV"
curl "localhost:8000/lookup?text=Lined%20up%20A-03%20to%20test%20sep"
curl "localhost:8000/review"
curl "localhost:8000/entities/<fuzer_id>/trace?direction=up&as_of=2024-01-01"
```

## Layout

| Path | What |
|---|---|
| `src/fuzer/packs/*.yaml` | Asset packs: tag grammar, aliases, thresholds, survivorship rules |
| `src/fuzer/fakedata.py` | Messy fake offshore asset for all sources |
| `src/fuzer/ingest.py` | Land source exports into `source_record` |
| `src/fuzer/extract.py` | Per-source extractors → candidates and edges |
| `src/fuzer/tags.py`, `nel.py` | Tag / well grammars, text entity linking |
| `src/fuzer/resolve.py` | Matching, clustering, stable IDs, review queue |
| `src/fuzer/golden.py` | Per-field survivorship and conflict review |
| `src/fuzer/graph.py` | Dated edges and flow tracing |
| `src/fuzer/queries.py` | Read queries shared by the API and MCP server |
| `src/fuzer/api.py` | REST API (also serves the built UI) |
| `src/fuzer/mcp_server.py` | MCP server for AI agents |
| `src/fuzer/llm_links.py` | Claude reads operator logs: links and events |
| `src/fuzer/llm_pids.py`, `fakepid.py` | Claude reads scanned P&IDs; fake drawing generator |
| `src/fuzer/jobs.py` | Dagster assets, schedules, sensors, checks and alerts |
| `ui/` | React + TypeScript web UI (Vite) |
