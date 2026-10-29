# fakeplant

Fake plants for Fuzer, kept out of the `fuzer` package: messy exports of every
source (SAP, JDE, Maximo, PI / AF, Energy Components, operator logs), P&ID
drawings, manuals and procedures, tables with no mapping yet, and the answer
keys `fuzer eval` scores against. Fuzer never imports it; its tests do.

All fake output lives in `fakeplant/data/<asset>` (git-ignored); point Fuzer at it
with `FUZER_DATA_DIR=fakeplant/data/offshore` in `.env`.

```sh
uv run fakeplant generate --size medium     # write exports to fakeplant/data/offshore
uv run fakeplant demo                       # reset the database, generate, ingest, rebuild
uv run fakeplant demo --asset onshore       # fakeplant/data/onshore, onshore_demo pack
```

| Module | What it makes |
|---|---|
| `offshore.py` | The offshore platform: every source, sizes small / medium / large |
| `onshore.py` | An onshore field with ESPs, for the onshore_demo pack |
| `pid.py` | P&ID drawings (clean exports and scans) and their answer keys |
| `docs.py` | Manuals, troubleshooting guides and procedures |
| `incoming.py` | Tables with messy headers and no mapping, for suggested mappings |
| `lake.py` | Tables as lake-style Parquet folders |
