---
title: Approved stack
description: The libraries Fuzer is built on, what each is for, and the rule for adding one.
---

# Approved stack

Fuzer is built on a fixed set of libraries, one per job. The list is [`stack.json`](https://github.com/areed145/fuzer/blob/main/stack.json)
at the repository root: every dependency in `ui/package.json`, `pyproject.toml` and `fakeplant/pyproject.toml`,
with what it is for. Two tests hold the manifests to it (`ui/tests/stack.test.cjs` and
`tests/architecture/test_stack.py`): a dependency the list doesn't name fails the build, and so does a listed one
that nothing uses.

## Adding a dependency

Reaching for a well-kept library is better than writing a poor copy of one. But ask first.

1. Check the tables below: the job may already have a library, or a shared module that wraps one.
2. If it doesn't, say what the dependency is, what it replaces or makes possible, and its size and licence, and get
   Adam's go-ahead before installing it.
3. Once agreed, add it to the manifest and to `stack.json` with its purpose in the same change, and wrap it in a
   shared module if more than one page or router will use it.

Editing `stack.json` to make the test pass without that go-ahead is not allowed. The same rule covers swapping a
library for another and adding a second library for a job that already has one.

## By kind of work

| Kind of work | Use | Not |
| --- | --- | --- |
| Web UI | React 19 function components, TypeScript (strict), Vite, the local UI kit in `ui/src/ui`, hooks and helpers in `ui/src/lib.tsx` | A second component kit, a CSS-in-JS library, a state-management library, a router library (routes are hash routes through `useRoute`) |
| UI styling | Theme tokens and the kit's classes; Tailwind 4 supplies the base | Utility-class strings in pages, literal colours, inline styles |
| Charts | Server-built Plotly figures through `LiveFigure` and `PlotlyChart` | Chart text or colours set in the UI; another chart library |
| Maps, graphs, diagrams, documents, 3D | Leaflet or MapKit behind `ui/src/map/engine.ts`; Cytoscape through `graph.tsx`; bpmn-js; Mermaid through `MermaidDiagram`; pdf.js through the viewers; three.js in `Scene3D` | Importing these libraries directly in a page |
| Workflow forms | form-js through `TaskForm` and `FormEditor` | Hand-built forms for a workflow task |
| HTTP API | FastAPI routers with `AuthenticatedRoute`, served by uvicorn | Another web framework; business rules in a router |
| Database | Postgres through psycopg and the existing stores | An ORM, SQLite, a second database |
| CLI | `argparse` in `fuzer.cli`, calling domain operations | A CLI framework; logic in the command |
| Scheduled work | Dagster | cron files, a second scheduler, a second run model |
| Agents and models | Microsoft Agent Framework on the Anthropic SDK; MCP for tools served to other clients | Calling a model outside the agent layer; another agent framework |
| Outbound HTTP | httpx | requests; `urllib` in new code (four older webhook and probe calls still use it) |
| Data files | pyarrow, deltalake, openpyxl, pypdf, pillow | pandas (not in the stack) |
| Numerics and analysis | numpy, scipy, scikit-learn; matplotlib only for images the assistant reads | |
| Tests | pytest (unit, integration, e2e tiers); `node --test` for UI logic; Playwright for the browser | Jest, Vitest, a second browser driver |
| Lint and structure | ruff, import-linter, `tsc --noEmit`, `ui/tests/style-lint.test.cjs` | |
| Docs | MkDocs Material | |

The purpose of each individual package is in `stack.json`.
