# Overview and principles

What Fuzer is, the decisions it rests on and how the parts fit.

Fuzer pulls asset data
from many systems, works out which records describe the same real thing, links
them into one graph, and serves that graph to apps, AI agents and engineers.

The current reference project is the Okan field demo. Asset-specific behavior is
isolated in an **asset pack**. Okan is the main demo; Kestrel Ridge, a small second facility, boots from
its own seed bundle to show nothing is tied to Okan.

## Decisions

| Topic | Decision |
|---|---|
| Hosting | Azure, next to ADX. Login with Entra ID later. |
| Architecture | Postgres-first modular monolith. Seams left so we can add a lakehouse / graph DB later. |
| Equipment master | SAP S/4 is the long-term master. JDE and Maximo are legacy sources matched against it. |
| P&IDs | Mix of smart files, text PDFs and scanned images; each handled differently. |
| Time series | Stays in PI and ADX. Fuzer stores only metadata and pointers. |
| Matching | Sure matches merge on their own; unsure ones go to a review queue. Reviewer answers are pinned and survive reruns. |
| Source conflicts | Per-field priority rules, plus human review for fields we care about (manufacturer, model). |
| History | Links have `valid_from` / `valid_to`, so "what was true on date X" works. |
| AI | Claude, for messy text (operator logs, documents) and hard P&ID images, and the operations assistant. |
| Access | Everyone signed in sees everything. |
| Users | API for apps/dashboards, MCP server for AI agents, web UI for engineers. |
| Languages | Python backend, TypeScript/React web UI. |

## Big picture

```mermaid
flowchart LR
  subgraph SRC["Sources (fake exports today)"]
    direction TB
    ERP["SAP S/4, JDE, Maximo<br/>assets, notifications, work orders, materials"]
    PI["PI points, PI AF"]
    EC["Energy Components<br/>network, downtime, DAY001"]
    PID["P&ID sheets<br/>smart, text PDF, scans"]
    TXT["Operator logs"]
    DOC["Manuals, guides, procedures"]
  end

  subgraph PG["Postgres"]
    direction TB
    RAW[("source_record<br/>+ history")]
    LLM[("llm_extraction<br/>cached Claude readings")]
    REG[("entity, crosswalk, golden,<br/>edge, knowledge_index, lesson")]
    REV[("review_item, pinned_link,<br/>feedback")]
  end

  subgraph RB["Rebuild (no model calls)"]
    direction TB
    EXT["Extract<br/>pack grammars"] --> RES["Resolve<br/>match, stable IDs"]
    RES --> GOLD["Golden records"] --> TAX["ISO 14224 classes<br/>and models"]
    TAX --> GR["Graph links"] --> INC["Incidents"]
    INC --> KN["Knowledge index<br/>and lessons"]
  end

  SRC -->|"ingest (COPY)"| RAW
  RAW --> EXT
  LLM --> EXT
  KN --> REG
  REV <--> RES

  CL["Claude readers<br/>logs, comments, P&IDs, sections"]
  RAW -.->|"fuzer llm-link"| CL -.-> LLM

  TS["Time-series provider<br/>PI Web API, ADX or synthetic"]

  subgraph OUT["Users"]
    direction TB
    API["FastAPI"] --> UI["React web UI"]
    MCP["MCP server<br/>AI agents"]
  end
  REG --> API & MCP
  REV <--> API
  TS --> API & MCP

  DAG["Dagster<br/>sensor, schedules, checks"] -.->|"runs"| SRC
  DAG -.-> RB
  DAG -.-> CL
  PACK["Asset pack (YAML)<br/>+ ISO 14224 standard"] -.-> RB
```

The pipeline is: **land → extract → resolve → golden → taxonomy → graph →
incidents → knowledge → serve**.
Every step after landing can be rerun from scratch at any time. That is the
point: we can change matching rules and rerun without losing anything, because
raw data and reviewer decisions are kept separately.

## One graph, many views
All entities share one node table. Each "ontology" is a set of edge types:

| View | Edges | From |
|---|---|---|
| Physical | `part_of`, `installed_at`, `sidetrack_of` | SAP, PI AF |
| Flow / allocation | `flows_to` (dated) | Energy Components, later P&IDs |
| Data | `has_timeseries` | PI AF attributes, PI point names |
| Documents / text | `mentions` | P&IDs, operator logs |

Edges have `valid_from` / `valid_to`, so you can trace "which wells fed the
production separator on 2024-01-01".

## Asset packs
Everything asset-specific lives in a pack: the tag grammar (function codes, the
area format, loop length, or a whole tag pattern), well names, phrase aliases, and
matching thresholds and survivorship rules that seed the source catalog's rules
(edited there afterwards). Okan's is `src/fuzer/packs/okan.yaml`.

A **seed bundle** (#376) holds what a fresh database needs to serve one facility:
`bundle.yaml`, the pack and, optionally, its exports. `fuzer init --bundle <folder>`
stores the project and its pack in the database and names it in the setting
`project.active`; it doesn't ingest or rebuild. The API, MCP server, jobs and CLI
then resolve the pack from the database they are connected to when they use it,
not when they start, so a database holds one facility and two databases serve two.
`FUZER_PROJECT` still picks a project for one process, and a database that names
none falls back to `FUZER_PACK` or Okan while the move to bundles finishes.

One database can hold several projects. Accounts, access, the project list and
settings live in the shared `fuzer` schema; the first project's register is in
`public`, and `fuzer init --bundle` for another project adds it in its own schema
(`proj_<name>`). Code runs for one project at a time (`projects.use(conn, name)`, or
`fuzer --project <name>`): its connections search that project's schema, then
`fuzer`, so no project sees another's register.

In the application, the sidebar's project picker lists the projects a person may
open: every project for an owner, otherwise those they hold a role in (an owner
grants roles per project). The browser sends the pick as the `X-Fuzer-Project`
header (links it opens itself, such as a document, carry `fuzer_project=`); the
server checks the grant on every request and runs it in that project's schema.
Dagster starts each schedule and sensor run once per project, tagged
`fuzer/project`. On Render, each deploy makes Okan the first project and adds every
other bundle in `fakeplant/bundles` (Kestrel) beside it, loaded and rebuilt, with
its own agent worker.

```sh
fuzer init --bundle fakeplant/bundles/kestrel        # a fresh database: Kestrel Ridge (KRGP)
FUZER_DATA_DIR=fakeplant/bundles/kestrel/exports fuzer ingest && fuzer rebuild
```

Kestrel Ridge's pack shows the grammar is the pack's: train areas `A1`/`B1`/`C1`
and four-digit loops (`A1-PT-1001`, written `KRGP:A1.PT1001.PV` in PI), against
Okan's two-digit areas (`20-PT-3003`).
