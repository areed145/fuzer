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
Okan exports and the packaged Okan pack; `bundles/kestrel` is a second
facility (Kestrel Ridge gas plant, `KRGP`) with its own pack and hand-written
exports, initialised with `fuzer init --bundle fakeplant/bundles/kestrel`. Kestrel's historian is
synthetic, driven by the hand-written `exports/process_model.json` (its climate, air coolers, units,
PI points and yesterday's compressor trip), so its charts have values without anything running.

Kestrel is a 50 MMscfd (1420 ksm3/d) rich gas plant with a little CO2 and H2S, fed by two Pad 12 wells, with eight
trains in the order the gas goes through them: A1 inlet (pig receiver, inlet separator, filter coalescer,
condensate pumps), B1 compression (scrubbers, the Ariel compressor, aftercooler), D1 amine treating for CO2 and H2S
(contactor, flash tank, regenerator, hot oil reboiler, lean amine pumps), C1 TEG dehydration and regeneration
(contactor, flash tank, filters, fired reboiler), E1 hydrocarbon dew point control (gas/gas exchanger, propane
chiller and refrigeration compressor, low temperature separator), F1 condensate stabilization and storage for
trucking, G1 utilities (hot oil, fuel gas, instrument air, standby generator, methanol, produced water) and H1
flare and the sales gas custody meter H1-FT-8010. C1-FT-3005 is the dry gas check meter. Beside yesterday's trip
(which flares the inlet gas and foams the amine on restart), the plant carries slower stories the IOC triages: the
TEG reboiler's burner fouling (C1-TT-3008 falling, wet sales gas at C1-AT-3011 and in the lab), the inlet filter
coalescer loading up with too few elements in stock, and an overdue standby generator load test.

Each bundle's `exports/dcs_alarm_journal.csv` is written from its process model by
`uv run fakeplant journal fakeplant/bundles/<name>` (the last 100 days, in relative dates); Okan's covers September to
December 2025 and is written by `fakeplant generate`. Rerun the command after changing a bundle's process model or
alarm export.

Kestrel has the same kinds of sources as Okan, each loaded by the same built-in mapping: SAP floc, equipment,
notifications, work orders (with turnaround KR-TAR-2027), maintenance plans, BOM and materials; Maximo; PI points
and AF; operator logs, EC downtime, well tests and network; DCS alarms, IOW register, inspection and LIMS
readings; OEM curves; smart P&IDs; GIS facilities, pipelines and wells; and documents. Its dated records are
written relative to the day they are read (`today-1d 03:10`, `fuzer.sources.relative_dates`), as are the process
model's events, so the demo never goes stale: yesterday's B1-K-2001 trip is on the charts, in the log and
downtime, and in the IOC's queue as an open SAP notification, an IOW breach on the inlet separator and an SRE
upset. `uv run fakeplant historian --data fakeplant/bundles/kestrel/exports` serves it as PI Web API and ADX.

Its gas wells report gas and condensate rates in their well tests, not an oil rate and a GOR. And one Kestrel
system has no built-in mapping on purpose: `bundles/kestrel/measurement/daily_volumes.csv`, a gas measurement
system's daily volume, energy and heating value per meter, naming plant meters (C1FT3005, H1FT8010) and wellhead meters
(KR12-03H) in one column. Add it from the Sources page (Add a source, a folder connection to that path): the
suggested mapping keys it on meter and day and lands the readings on each tag and well.

| Module | What it makes |
|---|---|
| `okan.py` | The Okan field (NMA South Offshore): PP, GGCP trains A/B/C, ST1, ST2, NWP, 24 jackets, gas lift, EGP export, GIS, troubleshooting stories |
| `registers.py` | Legacy JDE and Maximo copies of SAP equipment, with planted matching cases and `answer_keys/matches.json` |
| `pid.py` | P&ID drawings (clean exports and scans) and their answer keys |
| `docs.py` | Manuals, troubleshooting guides and procedures |
| `incoming.py` | Tables with messy headers and no mapping, for suggested mappings |
| `lake.py` | Tables as lake-style Parquet folders |
| `journal.py` | The DCS alarm and event journal (#859): ALARM, ACK, RTN, SHELVE, SUPPRESS and STATE events from the process model against the DCS alarm export, with chattering, shelving, state-based suppression and first-out alarms on trips |
| `instruments.py` | Instrument faults and interface outages over the process model (#868): frozen, stuck, noisy, pinned, drifting and bad readings and RTU outages, written as `instrument_faults.json`, which is also their answer key |
