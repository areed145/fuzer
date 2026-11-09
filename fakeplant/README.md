# fakeplant

Fake plants for Fuzer, kept out of the `fuzer` package: messy exports of every
source (SAP, JDE, Maximo, PI / AF, Energy Components, operator logs), P&ID
drawings, manuals and procedures, tables with no mapping yet, and the answer
keys `fuzer eval` scores against. Fuzer never imports it; its tests do.

All fake output lives in `fakeplant/data/okan` (git-ignored); point Fuzer at it
with `FUZER_DATA_DIR=fakeplant/data/okan` in `.env`.

```sh
uv run fakeplant generate                   # write Okan exports to fakeplant/data/okan
uv run fakeplant demo                       # reset the database, generate, ingest, rebuild
```

Seed bundles (#376) live in `bundles/`: `bundles/okan` points at the generated
Okan exports and the packaged Okan pack; `bundles/kestrel` is a second, small
facility (Kestrel Ridge gas plant, `KRGP`) with its own pack and hand-written
exports, initialised with `fuzer init --bundle fakeplant/bundles/kestrel`. Kestrel's historian is
synthetic, driven by the hand-written `exports/process_model.json` (its climate, units, PI points and a
compressor trip on 2026-11-06), so its charts have values without anything running.

| Module | What it makes |
|---|---|
| `okan.py` | The Okan field (NMA South Offshore): PP, GGCP trains A/B/C, ST1, ST2, NWP, 24 jackets, gas lift, EGP export, GIS, troubleshooting stories |
| `registers.py` | Legacy JDE and Maximo copies of SAP equipment, with planted matching cases and `answer_keys/matches.json` |
| `pid.py` | P&ID drawings (clean exports and scans) and their answer keys |
| `docs.py` | Manuals, troubleshooting guides and procedures |
| `incoming.py` | Tables with messy headers and no mapping, for suggested mappings |
| `lake.py` | Tables as lake-style Parquet folders |
