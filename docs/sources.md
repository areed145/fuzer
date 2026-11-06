# Sources and ingestion

How records come in: the source catalog, connections, shapes, mappings and text.

## The source catalog

Every system of record, how Fuzer reaches it, each dataset it delivers and what each column means are rows in
the database (#225), so a deployed Fuzer is set up and changed from the Sources page, the API or `fuzer sources`,
with history, and nothing about a source is read from repository files at runtime.

| Table | What it holds |
|---|---|
| `source_system` | One per system of record (`sap`, `pi`, `ec`, ...): kind, owner, steward, how it writes numbers and dates (decimal, date format, timezone, encoding), its survivorship rank |
| `source_connection` | How to reach a system: `folder` (the project's data folder, or a path), `pi_web_api`, `adx`. Config plus secrets as references (`env:NAME`), never values. A system can have several |
| `source_dataset` | One table or feed: its connection, location, key, watermark, load mode (`snapshot`, `incremental`, `append`), schema-drift policy (`hold_breaking`, `hold_any`, `ignore`), status (`draft`, `active`, `paused`, `retired`), schedule (cron), expected delivery (hours), owner, and its state at the last load |
| `mapping_version` | What the columns mean, per source and record type. The latest *approved* version is in use; drafts wait; a rollback approves a copy of an older one |
| `source_run` | One row per load that changed something: trigger, status (`done`, `held`, `failed`), rows read / new / changed / unchanged / removed, the mapping versions used |
| `source_profile` | A profile of each load: per column type, fill %, distinct values, top values, samples; key duplicates; what changed since the profile before |
| `source_check`, `source_check_result` | Expectations per dataset (`key_unique`, `not_null`, `range`, `accepted`, `row_count`, `freshness`, `coverage`) and their results |
| `source_match_stat` | Per source after each rebuild: link coverage, how its records matched, entities shared with other sources, golden fields won, open reviews |
| `source_rule` | Field survivorship rules and match thresholds per entity kind (the default priority is the systems' rank) |
| `source_audit` | Every change above: who, when, old and new, why |

**Seeding.** The mapping files in `src/fuzer/mappings/` and the nested-export readers (PI AF tree, EC network,
P&ID sheets, document manifests) are seed data. `fuzer sources seed` imports them, idempotently; an empty catalog
seeds itself the first time it is read, and `fuzer ingest` and `fuzer up` seed first. A seed never overwrites
what a person changed: a built-in mapping that moved ahead of an edited one becomes a draft and is reported as
`ahead`. The old `sources.pi.*` and `sources.adx.*` settings seed a connection the first time a URL is set.
`$FUZER_MAPPINGS` folders still seed, with a deprecation warning. `fuzer sources export` writes the catalog back
to YAML (the mapping files as written, plus a `catalog:` block with how each loads, and `systems.yaml`) for code
review and other environments; `fuzer sources import` makes a mapping file a draft.

**Loading.** `ingest` loads each active dataset through its connection as its load mode says. An unchanged
dataset is skipped without reading a row. Before a load is applied, the staged rows are profiled and checked:
duplicate keys, or a failing *blocking* check, hold the dataset (its records stay as they were, its run is
`held`, a `check` review item says why); a schema change holds it as its drift policy says (a `schema` review
item). Each system's number and date formats are read before mapping (`formats.py`: a decimal comma, a date
format, a timezone moved to the project's), and CSVs in its encoding.

**Connections** (`connections.py`) share one contract: `connector` (the dataset's reader), `test` (reach,
authenticate, list, read a few rows, each step timed and kept on the connection) and `discover` (what it holds
that no dataset covers yet). A new live system plugs in as a new type.

**Quality and matching.** `fuzer sources health` (and the `source_health` MCP tool) gives each dataset's state:
stale (no delivery within its expected hours), held, failed, checks failing, profile drift; with its row trend,
coverage and open reviews. A problem opens one `source_issue` work item for the dataset's owner (else the
system's owner, else `role:data_steward`) and posts to the alert webhook; the item closes when the problem
clears. Rules (`rules.py`): survivorship priority and match thresholds come from the catalog at each rebuild,
seeded from the pack (a rule nobody edited follows the pack).

**Before and after a change.** `fuzer sources impact` runs a proposed mapping beside the one in use over the
stored records and diffs what they make: entities and links added or removed, fields changed, golden values that
would flip. `fuzer sources why <id> <field>` (the entity page's *why?*, the `why_value` MCP tool) traces a value to
the records that say it, the mapping version that read each one, the run that delivered it and the rule that chose
it. A change takes effect on the next rebuild (`POST /sources/rematch`).

**Scheduling.** Dagster has a source asset and a `source_checks` asset check per dataset; a dataset with its own
cron loads then (`dataset_schedules` sensor); `source_health` runs every 30 minutes.

**On the Sources page**, **+ Add a source** walks through five steps: the system the data comes from (or a new
one), where it is (one of its connections, or a new one: a folder anywhere, PI Web API, ADX; tested on the spot),
which table (what the connection holds, new ones first), what its rows mean (a sample, a date-format hint for the
system, and a mapping suggested for the system, by rules or Claude, to edit), and how it loads (load mode,
schedule, expected delivery, owner). Save and load creates the dataset and its approved mapping and loads it; a
held first load says why (duplicate keys with examples, a failing check, changed columns) and where to fix it.
**Datasets** lists every dataset with its health, last run, owner and schedule (filter, problems only; datasets whose
data isn't in this project are hidden unless asked for). Each dataset has its own page: a header with its health
and, when its last load was held or failed, why and **Load again**; then **Overview** (connection, how and when it
loads, owner, records, open work items and review items, quick actions), **Settings** (how it loads, what to do
when columns change, schedule, expected delivery, owner; connection, key, location, reader and watermark under
Advanced; pause, retire or delete), **Mapping** (edited column by column: what each column is kept as, with a name and
type, and what it links the record to (a tag, a well, equipment, a functional location, a tag or a well), the record
key and what records are matched on; the YAML stays under Advanced, and anything the form doesn't show (hooks,
measurements, links of other shapes) is kept as it is. An edit becomes a draft; a draft or an older version is shown
as a line diff against the one in use with what it would do to the register, then approved, discarded or rolled back
to), **Runs**,
**Quality** (checks suggested from the profile, one click to add; checks and their results; the profile),
**Matching** and **History**. A banner on the Sources pages says when loads, mapping versions or rule changes aren't
in the register yet, with **Rebuild now**. **Systems & connections** edits each system and its connections (shown in
words: the project data folder, a path, a server) and lists what each connection holds, with an *add it* link per
new table; a system added here gets no connection until it needs one. **Rules** edits ranks and rules. Held loads
also show on the Data review page (**Held loads**), linked to their dataset.
The API behind Add a source: `GET /sources/connections/{id}/tables`,
`GET /sources/connections/{id}/tables/{table}` (preview and suggestion) and `POST` to the same path (save and load).

## Connectors, shapes and mappings

Every dataset is described by three independent things, so a new source is configuration, not code.

**Connector: how data arrives.** Each yields records plus a schema fingerprint;
a changed fingerprint (column added, renamed, retyped) sends the mapping back
for review instead of failing quietly.

| Connector | Examples | Reads |
|---|---|---|
| Lake table | Parquet / Delta in ADLS (SAP, EC, Maximo extracts) | Streamed with column projection; incremental by file, table version or watermark column |
| API | SAP OData, PI Web API, GIS feature services | Paged; incremental by change date |
| Time-series store | PI, ADX, OPC UA | Metadata only (AF templates / elements / attributes, ADX schemas and tag columns); values stay put and are read through `timeseries.Provider` |
| File / document store | EDMS, SharePoint, P&ID scans, inspection photos | Listed and fingerprinted; only changed files re-read |
| Messages | Teams, operator logs | Incremental by timestamp |
| Standard exchange file | DEXPI (smart P&IDs), CFIHOS handover, WITSML / PRODML, PODS, GeoPackage | Parsed by a standard reader into the shapes below, no Claude needed |

Built: CSV, Parquet (file or folder of part files), Delta, Excel, JSON and GeoJSON readers on folder
connections, and PI Web API and ADX live connections. Each dataset keeps its fingerprint, columns and watermark
on `source_dataset`, so unchanged datasets are skipped (large asset re-ingest 2.4 s -> 0.4 s), added columns
load, removed or retyped columns hold the dataset behind a `schema` review item (or as its drift policy says),
and watermarked lake tables read only new rows. Nested JSON manifests and the live AF tree have no columns;
their JSON shape (typed field paths, with tag-keyed maps as one path) gets the same check. Medium asset from
Parquet: same records and eval scores as CSV; large first load 3.6 s either way.

Mappings: every source record type is a YAML mapping (a `mapping_version` row) read by one compiled
extractor (`mappings.py`). Its dataset says where the table is (`location`, relative to its connection's
folder or absolute; `book.xlsx#Sheet` for a sheet) and which reader to use (`connector`); by default it's
`<dataset>` in the folder, read by its file type; `extract.py` keeps only named value transforms
(tag, well, serial, number), resolvers that read kind and key together (FLOC,
AF path, EC node) and six hooks (ISO 14224 codes, sidetracks, EC downtime,
document applicability, section failure modes, P&ID formats). All 77,585
records of the small, medium and onshore assets give identical candidates and
links to the old extractors; medium eval and large entity / link counts are
unchanged; large extraction 1.0 s -> 1.4 s.

Suggested mappings (`suggest.py`): profile a sample per column; rules name
meanings from headers, value patterns (pack grammars, dates, units, known
manufacturers) and values that are keys of records already held (ties between
overlapping number ranges go to the target whose key column has the same
name); Claude names columns rules could only call `code` or `other` (cached,
never in rebuilds). Drafts are `mapping` review items; an accepted one is approved as a
new mapping version and its dataset is added to the catalog. On the Sources page (New tables) a
new table is added in one flow: the draft from rules (or Claude), edited, saved
as a mapping version and loaded, without going through the queue. Eval on 18 fake tables
(12 built-in, 6 with messy headers): key 1.0, references 1.0, columns 0.99
(rules), 1.0 with Claude on the opaque table.

Geospatial: GeoJSON connector and `geospatial` shape; `geo_index` (box,
centre, geometry per located entity, rebuilt with the register) serves the map
and nearby queries without PostGIS (100k features: field-sized box 5 ms, wider
box 36 ms, entity map 12 ms). `spatial_links` rules in the pack link what GIS
didn't carry as IDs by distance (wells to pads / jackets, legacy pipeline ends
to facilities), scored by distance; eval on the Okan and onshore plants:
precision 1.0, recall 1.0. PostGIS waits until polygons or exact geometry
operations are needed (Azure Database for PostgreSQL supports it).

Measurements: a `measurements` block in a mapping names what was measured
(a list of endpoints tried in order: tag, well, pipeline or the record itself),
when, how and where on it (a CML, a sample point), and one or more quantities
per row (a well test gives five; a LIMS row names its analyte). The rebuild lands
them in `measurement` against the resolved entity, after the graph. Units go
through reference data (`units.yaml`: stored unit, spellings, conversions), so a
2019 survey in inches is stored in mm with what the source said kept; "<5" is
kept as a below-detection-limit qualifier. They appear on the entity page next
to process values, in the troubleshooting brief and through the `measurements`
MCP tool, one series per quantity and location with the change per year (wall
loss, decline). Large asset: 89,710 readings on 1,190 entities; a well's test
history 0.6 ms (4 ms cold).

A `reference` mapping names each row's list, code, name, category and optional
standard code. These rows load with other datasets but do not create entities.
Maintenance and downtime hooks use the loaded entries first and retain the
asset pack's catalogs as defaults; codes found in neither are reported by the
`unmapped_site_codes` data-quality check.

**Shape: what kind of data it is.** One generic extractor per shape:

| Shape | Today | Anticipated |
|---|---|---|
| Records (things with attributes) | SAP equipment, JDE, Maximo, materials | Line lists, instrument index, cable schedules, vendors, contracts |
| Hierarchy | FLOCs, PI AF | ISA-95 site / area / unit, ISO 14224 trees, org units |
| Network (nodes + edges) | EC allocation, P&ID piping | Pipeline and gathering networks, electrical single-lines, cause & effect matrices and trip logic (input -> trips -> output) |
| Time-series metadata | PI points, AF attributes | ADX tables, SCADA, OPC UA nodes |
| Measurements (sparse, landed) | Well tests, LIMS results, inspection thickness readings (CMLs) | Vibration routes, emissions / LDAR surveys |
| Limits (per point, dated) | PI AF limit traits, DCS alarm exports, IOW registers | Safe operating limits, trip setpoints from cause & effect, equipment design envelopes |
| Events (time + text + references) | Logs, downtime, notifications, DAY001 | Teams threads, alarm journals, trips |
| Cases (records with a workflow) | Notifications, work orders | Management of change, permits to work, isolations, HAZOP / LOPA actions, inspection findings |
| Documents | Manuals, guides, procedures | Any PDF / Office file, datasheets, inspection reports |
| Geospatial features | none | GIS wells, pads, pipelines, rights-of-way, facility outlines |
| Positions along a line | none | Pipeline stationing and in-line inspection anomalies (PODS); well trajectories, completions and depth logs by MD / TVD (WITSML, LAS) |
| Reference data / code lists | SAP maintenance catalogs and EC downtime reasons; pack values are defaults | Site catalogs, units of measure, CFIHOS reference data library |

Measurements differ from PI data: they are few, manual and not in a historian,
so they are landed and kept. Drawings and documents are not shapes but
**extractions**: a reader (Claude, OCR, a DEXPI parser) turns a sheet or file into
derived records / network / references that flow through the shapes above,
each carrying a link back to the sheet, tile or page it came from. 3D plant
models and laser scans fit later as records with plant coordinates and a
viewer link.

**Mapping: what each column means.** A YAML mapping per source and record type names the shape, the Fuzer
kind or ISO 14224 class, and which column is key, tag, description, parent, from / to, timestamp, unit, value
or geometry; logic that truly is source-specific is a named hook. **Schema detection proposes the mapping**:
profile columns (types, uniqueness, distinct values, nulls), match values against the pack's tag / well / FLOC
grammars, spot dates, units, coordinates and from / to pairs, and let Claude name the rest from headers and
sample rows (structured output limited to Fuzer's field meanings). A person approves it; the mapping is
versioned in the catalog, and rebuilds never call Claude.

New linking methods come with new shapes: **spatial** (a well within 50 m of a
pad, a pipeline segment crossing a road) on PostGIS, which keeps the
Postgres-first decision, and **along-line** (an anomaly at station 12+340 on
segment X). Both produce scored candidate links like every other matcher;
identities, history, review and the graph stay as they are.

## Source plan

| Source | First version | Later |
|---|---|---|
| SAP S/4 | CSV export of FLOCs and equipment | OData / CDS views |
| JDE, Maximo | CSV export | Scheduled DB extracts until retired |
| PI AF | JSON export of element tree + attributes, or PI Web API | |
| PI points | CSV point list, or PI Web API; ADX series catalog | |
| Energy Components | JSON of network nodes and dated edges | EC database views |
| P&IDs | Smart: tag list. Text PDF: extracted text. Image: flagged `needs_ocr` | DEXPI parsing, OCR (Azure Document Intelligence), Claude vision, line tracing |
| Operator logs | CSV of entries | Claude for hard mentions and event extraction |
| EC DAY001 daily reports | CSV of DAY001 rows | Comments read by Claude into links and events |
| EC downtime / deferment | CSV: `downtime` with reason codes, lost oil, comments | EC database views |
| DCS alarms, IOW registers | CSV exports landed as point limits | DCS and AF limit traits read live |
| Lab, well tests, inspection | LIMS results, EC well tests, CML readings as measurements | |
| GIS | GeoJSON wells, facilities, pipelines | Feature services |
| Manuals, guides, procedures | EDMS manifest (JSON) of PDFs; sections linked to models, classes, tags | SharePoint / EDMS live |
| Teams channels | (planned) | Selected channels via Microsoft Graph; thread summaries and links |

## Piping from P&IDs
Smart P&IDs carry piping (DEXPI-style from / to / line number) and the Claude
drawing reader returns connections read off scans; both become directional
`pipes_to` links between tags, kept apart from Energy Components' `flows_to`
(allocation). The Network page switches between the two, flow traces can
follow either, and a comparison lists EC flows with no piping path on the P&IDs
and piping between EC nodes with no EC flow path (API and a Dagster check).

## Entity linking in text
Operator logs and P&ID text are scanned for:
- tags (tag grammar; area taken from context when missing),
- wells (well grammar),
- known phrases from the pack (`"test sep"` → `20-V-2002`).

A short tag like `PT-2001` exists in several areas; if it can't be pinned to one
it is left unlinked rather than guessed. Claude will later handle the hard
cases (pronouns, "the B pump", context from earlier entries).
