# Sources and ingestion

How records come in: connectors, shapes, mappings and text.

## Sources as connectors, shapes and mappings
Today each export has a hand-written loader and each source / record type a
hand-written extractor, so every new source is new code. Landing is already
generic (every record is source + type + key + JSON payload). The plan is to
describe every dataset by three independent things, so a new source is mostly
configuration:

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

Built so far: CSV, Parquet (file or folder of part files) and Delta
connectors behind one interface; `dataset_state` keeps each dataset's
fingerprint, columns and watermark, so unchanged datasets are skipped (large
asset re-ingest 2.4 s -> 0.4 s), added columns load, removed or retyped columns
hold the dataset behind a `schema` review item, and watermarked lake tables read
only new rows. Nested JSON manifests and the live AF tree have no columns; their
JSON shape (typed field paths, with tag-keyed maps as one path) gets the same
check. Medium asset from Parquet: same records and eval scores as CSV;
large first load 3.6 s either way.

Mappings: every source record type is a YAML file read by one compiled
extractor (`mappings.py`). A table mapping may say where the table is
(`location`, relative to the data folder or absolute; `book.xlsx#Sheet` for a
sheet) and which reader to use (`connector`); by default it's `<dataset>` in the
data folder, read by its file type; `extract.py` keeps only named value transforms
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
never in rebuilds). Drafts are `mapping` review items; accepted versions live
in `mapping_version` and load after the built-in files. On the Sources page a
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
| Reference data / code lists | Pack maintenance codes, ISO 14224 | Site catalogs, units of measure, EC reason codes, CFIHOS reference data library |

Measurements differ from PI data: they are few, manual and not in a historian,
so they are landed and kept. Drawings and documents are not shapes but
**extractions**: a reader (Claude, OCR, a DEXPI parser) turns a sheet or file into
derived records / network / references that flow through the shapes above,
each carrying a link back to the sheet, tile or page it came from. 3D plant
models and laser scans fit later as records with plant coordinates and a
viewer link.

**Mapping: what each column means.** A YAML file per dataset names the shape,
the Fuzer kind or ISO 14224 class, and which column is key, tag, description,
parent, from / to, timestamp, unit, value or geometry. Today's extractors
become mapping files plus a few named hooks for logic that truly is
source-specific. **Schema detection proposes the mapping**: profile columns
(types, uniqueness, distinct values, nulls), match values against the pack's
tag / well / FLOC grammars, spot dates, units, coordinates and from / to pairs,
and let Claude name the rest from headers and sample rows (structured output
limited to Fuzer's field meanings). A person approves it in the review queue;
the approved mapping is versioned, and rebuilds never call Claude.

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
| EC DAY001 daily reports | (planned) | Comments read by Claude into links and events |
| EC downtime / deferment | (planned) | `downtime` events with reason codes, lost volume, comments |
| Teams channels | (planned) | Selected channels via Microsoft Graph; thread summaries and links |
| Manuals, guides, procedures | (planned) | SharePoint / EDMS; sections linked to models, classes, tags |

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
