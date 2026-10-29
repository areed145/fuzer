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

### Claude reads the operator logs

```sh
export ANTHROPIC_API_KEY=...       # or `ant auth login`
uv run fuzer llm-link --dry-run    # how many entries / requests / tokens, calls nothing
uv run fuzer llm-link --limit 40   # try a small sample first
uv run fuzer llm-link --batch      # everything, via the Batches API at half price (results within ~1 h)
```

For each log entry Claude gets a numbered list of candidates from the asset
register (ambiguous short tags in every area, equipment in any area named in
the text) and picks from it, or says it can't tell. It also pulls out events
(trip, start, changeover, shut-in, choke change, alarm, leak, instrument fault,
...) which become `event` entities linked to the log entry and to the tags and
wells involved. Results are cached per entry; rebuilds never call the model,
and an entry is only re-read when its text changes (`--force` re-reads all).
Model defaults to `claude-opus-5-5` (`--model` or `FUZER_LLM_MODEL` to change),
with server-side refusal fallback on.

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
| `ui/` | React + TypeScript web UI (Vite) |
