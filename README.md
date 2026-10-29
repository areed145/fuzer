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

### Web UI

Needs Node 20+.

```sh
cd ui && npm install && npm run build   # then `fuzer serve` shows the UI at /
npm run dev                             # or: live-reload UI at :5173 (keep `fuzer serve` running)
```

Three screens: **Browse** (search everything), **entity detail** (chosen
values and what each source says, source records, dated links, flow trace with
an "as of" date) and **Review** (yes/no on possible matches, pick the right
value when sources disagree).

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
| `src/fuzer/api.py` | REST API (also serves the built UI) |
| `ui/` | React + TypeScript web UI (Vite) |
