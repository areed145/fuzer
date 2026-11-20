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

## Shared modules

Each repeated pattern has one module. Use it as it is; don't copy it into a page or write a local variant. If it
doesn't cover a case, extend the module, with a test, so every page gets the change.

| Pattern | Use | Enforced by |
| --- | --- | --- |
| Money, percentages, quantities, counts | `ui/src/format.ts` in the UI; `fuzer.core.wording.money` in Python. Pass the number the API returned. | `style-lint` rule `local-number-format`; the two formatters share test cases |
| Times and dates | `when`, `whenDay` and `ago` in `ui/src/format.ts`. An instant reads in the viewer's clock; a value without an offset is the plant's clock, shown as written and marked "plant time"; a date alone stays that day. | `style-lint` rules `local-time-format`, `local-count-format` |
| A button's async action (busy, failure, refresh) | `useAction` in `ui/src/lib.tsx`; `useReasonedAction` when the change needs a reason for the history; `errorMessage` for a caught error | `style-lint` rule `local-action-state` |
| Asking before a change, asking for one value | `confirmDialog` and `promptDialog` from `ui/src/ui` (awaitable, queued); `ConfirmAction` for an inline confirm step | `style-lint` rule `raw-control` (no `confirm`, `prompt`, `alert`, `<dialog>`) |
| Controls and tables | `Button`, `Input`, `Select`, `Textarea`, `Field`, `Table` and the rest of `ui/src/ui` | `style-lint` rule `raw-control` |
| A figure worked out from API data (a total across kinds) | `ui/src/figures.ts`, or a field on the API. A page never adds up a list itself. | `style-lint` rule `computed-total` |
| The state of a read (loading, failed, empty) | `<Status of={read} />` from `ui/src/lib.tsx`, given the `useAsync` result; a failed read always offers Try again | `style-lint` rule `read-state` |
| Text styles and one-line layouts | `Text` (`small`, `muted`, `mono`) and `Line` (`spread`, `wrap`) from `ui/src/ui`, not class names | `style-lint` rule `raw-text-class` |
| A checkbox | `Checkbox` from `ui/src/ui` | `style-lint` rule `raw-checkbox` |
| A failure message | `ErrorText` from `ui/src/ui`; `ActionStatus` for a button's action | `style-lint` rule `raw-error` |
| Loading data | `useAsync` in `ui/src/lib.tsx` over the `api` facade; never `fetch` in a page. A read shows the tab's last answer at once while it asks again, and Refresh reads in place (`ui/src/snapshot.ts`) | `style-lint` rule `raw-fetch` |

### The kit is used, not edited

`ui/src/ui` is the component kit. Pages import from `ui/src/ui` only and take the components as they come.

- A page stylesheet does not restyle a kit class (`.ui-*`); `style-lint` rule `kit-override` counts these and the
  count only goes down.
- A component that is missing a variant gets one in the kit, for every page, not a one-off class in a page.
- Changing how an existing kit component looks or behaves changes every page: say so in the PR and check phone,
  iPad and desktop.

