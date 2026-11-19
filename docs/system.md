# The system at a glance

How Fuzer is put together and what runs where. For the package-by-package view generated from the code, see
[architecture](architecture.md); for the design decisions behind it, [overview and principles](overview.md).

## Layers

Fuzer is a Postgres-first modular monolith. Each layer uses only the ones below it, and the layer contract is
checked by the tests, so a business rule lives in exactly one place and every adapter (web UI, API, MCP, command
line) reaches it the same way.

```mermaid
flowchart TB
  subgraph A["Adapters"]
    direction LR
    ui["Web UI<br><small>React, installable<br>on a phone</small>"]
    api["HTTP API<br><small>FastAPI</small>"]
    mcp["MCP server<br><small>for AI agents</small>"]
    cli["Command line<br><small>fuzer ...</small>"]
  end
  subgraph D["Domains"]
    direction LR
    agents["Agents<br><small>assistant, watch,<br>specialists</small>"]
    wf["Work engine<br><small>BPMN, queues,<br>SLAs, escalation</small>"]
    ops["Operations<br><small>routing, intake, plan,<br>handover, close-out</small>"]
    ana["Analyses<br><small>decline, limits,<br>upsets, value</small>"]
  end
  subgraph R["The register"]
    direction LR
    gr["Graph and<br>golden records"]
    src["Sources and<br>ingestion"]
  end
  core["Core: settings, capabilities, exceptions, events, the Work port"]
  ui --> api
  A --> D
  D --> R
  R --> core
  D --> core
```

The operations layer never imports the work engine directly. It opens and feeds cases through the Work port in
`fuzer.core.work`, and `fuzer.ops.routing` is the one module that opens a case for an exception.

## What runs where

One container image runs everything. On Render it is one web service with a persistent disk and a managed Postgres;
it can be split into a web service and a background worker against the same database
([deployment](deployment.md#jobs-on-their-own-service-optional)).

```mermaid
flowchart LR
  user(["IOC, field, planners,<br>data stewards"]) -->|"HTTPS, Microsoft sign-in"| web
  agent(["AI agents"]) -->|MCP| web
  subgraph svc["Fuzer service (one image)"]
    direction TB
    web["API and web UI"]
    daemon["Dagster daemon<br><small>schedules, sensors,<br>data checks</small>"]
    worker["Agent worker<br><small>workflow agent steps</small>"]
  end
  subgraph pg["Postgres"]
    direction TB
    reg[("Register<br><small>records, entities,<br>golden values, links</small>")]
    work[("Work<br><small>exceptions, cases,<br>tasks, plan</small>")]
    out[("Event outbox")]
    dag[("Dagster storage")]
  end
  web <--> pg
  daemon <--> pg
  worker <--> pg
  ts["Historian<br><small>PI Web API, ADX,<br>or synthetic</small>"] --> web
  srcs["Source systems<br><small>SAP, EC, PI AF,<br>P&IDs, logs</small>"] -->|"connect, ingest"| daemon
  llm["AI provider<br><small>Claude or<br>OpenAI-compatible</small>"] <-.->|"Ask, readers,<br>watch"| worker
  out -.->|"source.update"| srcs
```

Time series stay in the historian: Fuzer keeps only metadata and pointers, and reads values on request. Every model
call is optional and metered against a monthly budget set in **Settings**; with no provider configured, everything
except reading messy text and the assistant still runs.

## The scheduled loop

The Dagster daemon runs the loop that keeps the register and the work current. The **Jobs** page shows each job's
schedule, recent runs and data checks.

```mermaid
flowchart TB
  load["Load changed<br>source data"] --> rebuild["Rebuild the<br>register"]
  rebuild --> intake["Intake<br><small>SAP notifications</small>"]
  rebuild --> plan["Plan sync<br><small>SAP orders, PM plans,<br>P6 schedules</small>"]
  tiers["Detectors, by tier<br><small>5 minutes: trips<br>hourly: machine health<br>6 hours: upsets<br>daily: inspection, parts, permits,<br>alarms, wells, emissions</small>"] --> exc["Exceptions"]
  intake --> exc
  watch["Agentic watch<br><small>daily, per area;<br>kept and scored unless live</small>"] -.-> exc
  exc --> route["Routing"]
  plan --> pl[("The plan")]
  pl -.->|"holds back what<br>planned work explains"| exc
  tick["Work tick, every 5 minutes<br><small>SLAs, escalation, shelving expiry,<br>shift handover, rounds</small>"] --> route
  report["Daily report"] --> rep[("Reports")]
```

Each detector runs in a tier matched to how fast its condition matters, and resumes from where its last good run
ended, so a missed run catches up and a condition is raised once. [Detectors](detectors.md) lists every one with
its tier; [work types](work-types.md) lists what each raises and where that goes.

## One register, many projects

Everything specific to a facility lives in its **asset pack** (YAML): naming grammars, source priorities, the
taxonomy, and the units and areas. Okan (an offshore oil field) is the main demo; Kestrel Ridge (a gas plant) and
Harrier (a crude pipeline and terminal with no wells) boot from their own seed bundles with no code of their own.
[Extend Fuzer](extend.md) shows how to add one.
