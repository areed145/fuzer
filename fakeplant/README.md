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
synthetic, driven by the hand-written `exports/process_model.json` (its climate, air-cooled aftercooler, units,
PI points and yesterday's compressor trip), so its charts have values without anything running.

Kestrel has the same kinds of sources as Okan, each loaded by the same built-in mapping: SAP floc, equipment,
notifications, work orders (with turnaround KR-TAR-2027), maintenance plans, BOM and materials; Maximo; PI points
and AF; operator logs, EC downtime, well tests and network; DCS alarms, IOW register, inspection and LIMS
readings; OEM curves; smart P&IDs; GIS facilities, pipelines and wells; and documents. Its dated records are
written relative to the day they are read (`today-1d 03:10`, `fuzer.sources.relative_dates`), as are the process
model's events, so the demo never goes stale: yesterday's B1-K-2001 trip is on the charts, in the log and
downtime, and in the IOC's queue as an open SAP notification, an IOW breach on the inlet separator and an SRE
upset. `uv run fakeplant historian --data fakeplant/bundles/kestrel/exports` serves it as PI Web API and ADX.

| Module | What it makes |
|---|---|
| `okan.py` | The Okan field (NMA South Offshore): PP, GGCP trains A/B/C, ST1, ST2, NWP, 24 jackets, gas lift, EGP export, GIS, troubleshooting stories |
| `registers.py` | Legacy JDE and Maximo copies of SAP equipment, with planted matching cases and `answer_keys/matches.json` |
| `pid.py` | P&ID drawings (clean exports and scans) and their answer keys |
| `docs.py` | Manuals, troubleshooting guides and procedures |
| `incoming.py` | Tables with messy headers and no mapping, for suggested mappings |
| `lake.py` | Tables as lake-style Parquet folders |
