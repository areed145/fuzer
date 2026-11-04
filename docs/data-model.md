# Data model and ontology

The entity kinds, how they link and the tables that hold them.

## Tags are not equipment
A **tag** (functional location, e.g. `20-P-1001B`) is a position in the process.
**Equipment** is the physical item installed there (the pump with serial
`SN40123`). Pumps get swapped; the tag stays. Fuzer keeps them as separate
entities joined by an `installed_at` edge with dates. Mixing these up is the
most common cause of bad merges.

## Equipment classes and models (ISO 14224, CFIHOS)
`standards/iso14224.yaml` holds the ISO 14224 categories, equipment classes and
types, each with its CFIHOS class name (CFIHOS IDs to be filled from the RDL).
The asset pack's `classification` rules map local signals onto it: ISA function
letters, PI AF templates and description words to a class or type, plus
manufacturer spellings to one name. Packs can add classes (e.g. fired heaters
downstream) or point at another taxonomy file, without code changes.

After golden values are chosen, each tag is `classified_as` a class; equipment
takes the class of the position it's installed at and is an `instance_of` an
`equipment_model` (manufacturer + model), which `is_a` class. Facility, area,
tag and equipment carry their ISO 14224 taxonomy level and ISA-95 level. Models
and classes are where manuals, procedures and failure history attach.

Entity pages and agent tools return at most 100 (50 for agents) links per
relationship with the true total, so hubs like "Pumps" stay fast; members of a
class (including subtypes) or a model are listed with paging.

## Maintenance history (ISO 14224 reliability codes)
SAP PM notifications and work orders become `notification` and `work_order`
entities that `concerns` the equipment (by SAP equipment number) and the tag;
work orders link `for_notification`. The standards file holds ISO 14224 failure
modes, failure mechanisms, failure causes and maintenance activities; the pack's
`maintenance_codes` maps the site's SAP catalog codes onto them. A notification on
a tag within 3 days of a logged event on that tag is linked `related_to` it (one
set-based statement). Failure history rolls up from a tag or piece of equipment
to every instance of its model and every member of its class.

## Downtime and daily reports (Energy Components)
EC downtime / deferment records become `downtime` entities that `affects` a well
or the facility and are `caused_by` the tags named in their comment (rules, then
Claude), with duration and lost oil / gas. The pack maps EC reason codes to a
name and category; a record whose comment names equipment but whose reason is in
another category is flagged (`reason_check`, a Dagster check, a list in the UI).
Lost oil is ranked by causing tag, by the class of the causing equipment and by
reason. DAY001 daily reports become `daily_report` entities with their figures,
`reports_on` the facility and `mentions` what their comments name. Claude's text
reader covers operator logs, daily report comments and downtime comments, each
cached per record.

## Incidents
Logged events (`involves`), downtime (`caused_by`) and notifications (`concerns`)
are signals on a tag; instrument tags roll up to the equipment tag they're
`part_of`. Signals on a unit within 3 days of each other form a group, and a
group with downtime or at least two kinds of signal becomes an `incident`
(`about` the unit, `includes` each signal) with lost oil, failure modes and
event types. Similar past incidents come from the same unit or class, ranked by
shared failure modes and event types. Teams threads will join as another
signal kind.

## Documents
An EDMS-style export (manifest + files) is read into one `document` entity per
revision (number, revision, type, status, effective date) and one
`document_section` per section (heading, page, text), split on Markdown headings
or numbered PDF headings with page numbers kept; PDFs without a text layer are
flagged for OCR. Revisions link `supersedes`. EDMS metadata gives the first
links: an OEM manual `applies_to` its equipment model (manufacturer spellings
folded as for equipment), a procedure to its class, a guide to the tags it
references; sections `mentions` the tags in their text. A SharePoint / EDMS
connector replaces the folder later.

## Gas lift network
Energy Components network edges carry a `service`. Lift gas edges become `lift_gas_to` links, separate
from production `flows_to`, so a production trace never wanders into the lift gas system. The mapping
language's `when` / `unless` can test a column's value for this. Network nodes with a `site` are
`located_at` it (a jacket's production and gas lift manifolds sit on the jacket).
- **Okan's layout.** GGCP compression feeds the GGCP lift gas header, which feeds the ST1 and ST2
  headers and NWP. Each jacket belongs to its nearest hub. It is piped through a neighbouring jacket
  when that saves a lot of pipe, so its lift gas and oil pass through that jacket's manifolds. Each gas
  lift manifold feeds its jacket's gas lifted wells. Well tests follow a gas lift performance curve
  per well, so each well's response to lift gas can be measured.
- **What sees it.** `trace_flow(network="lift_gas")` and the Network page's Gas lift view.
  `gas_lift_network` (operations.py, MCP) gives:
  - per header: jackets, wells, lift gas and oil
  - per well: supply path, oil per lift gas, and response to more gas (slope of rate on injection,
    decline removed)
  - least and most responsive wells, and wells lifting mostly water
- **Agents.** The production specialist's `gas-lift` skill explains the method; the troubleshooting
  skill traces a dead well's lift gas supply.

## Data model (Postgres)

| Table | What it holds |
|---|---|
| `source_system`, `source_connection`, `source_dataset` | The source catalog: systems of record, how to reach them, each dataset and its load state ([sources](sources.md)) |
| `mapping_version` | What each source table's columns mean, versioned; the latest approved one is in use |
| `source_run`, `source_profile`, `source_check`, `source_check_result` | Load history, a profile per load, data-quality checks and their results |
| `source_match_stat`, `source_rule`, `source_audit` | Per-source coverage and match statistics after rebuilds; survivorship and match rules; every catalog change |
| `source_record` | Raw records as received (JSON; a system's number and date formats read), keyed by source + type + key |
| `source_record_history` | Previous versions of changed or removed source records |
| `entity` | One row per Fuzer ID, with kind and `merged_into` |
| `crosswalk` | Source record → Fuzer ID, with how it was matched |
| `entity_key` | Canonical keys (tag key, serial, well key) → Fuzer ID |
| `assertion` | Reviewer choices (per-source values are re-derived from records on demand) |
| `golden` | The chosen value per entity and field, and why |
| `edge` | Typed links with dates, sources and confidence |
| `measurement` | Landed readings (well tests, lab results, wall thickness) on the entity they were taken on, in stored units |
| `point_limit` | Alarm limits and IOWs per PI point, dated, from their source record, in stored units |
| `point_normal` | Normal ranges learned per point and operating mode of its unit |
| `excursion` | Times a point was outside a limit while its unit ran, and any work item opened for them |
| `review_item` | Open/decided match and conflict questions |
| `pinned_link` | Reviewer must-link / cannot-link decisions |
| `id_event` | Mint / merge / split history |
