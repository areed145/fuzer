# Adapter boundaries

FastAPI routes, CLI commands and MCP tools are transport adapters. They translate transport-specific input
into domain calls, invoke the operation, and shape its result for the caller. Business rules and decisions
that should be consistent across entry points belong in domain modules under `src/fuzer`.

## What belongs where

| Adapter | Adapter responsibilities | Domain responsibilities |
|---|---|---|
| FastAPI (`fuzer.api`) | Parse and validate HTTP input, resolve dependencies, map domain errors to HTTP status codes, and return response shapes. | Catalog, graph, workflow, analysis and operations behavior belongs in the relevant domain package. |
| CLI (`fuzer.cli`) | Parse flags and positional arguments, handle command-specific files and environment, and format terminal output or exit errors. | Commands call the same domain operations as other entry points rather than reimplementing their rules. |
| MCP (`fuzer.agents.capabilities`, `fuzer.agents.mcp_server`) | Declare tool schemas and descriptions, translate references and agent-facing errors, and trim or serialize results for MCP clients. | Tool behavior belongs in domain operations shared with the API, CLI or jobs. |

Adapters may retain transport validation and translation, protocol-specific error and response formats,
authentication and dependency wiring, and presentation-only shaping. File reads and writes that are intrinsic
to a CLI workflow, such as importing or exporting catalog files, also stay at that boundary. These exceptions
must not introduce a second implementation of domain policy.

## Shared operations

For example, the API dataset detail route and `fuzer sources show` both need mapping versions for every
record type a dataset produces. `sources.catalog.versions_for_dataset` owns that selection, including nested
readers that produce multiple record types. Similarly, API and MCP source-health views call
`sources.quality.health`; neither adapter should define its own freshness or check rules.

When adding or changing an entry point, prefer an existing domain operation. If matching behavior would
otherwise require repeating a rule, extract that rule into the domain module and test the entry points for
equivalent results. Keep only the input/output translation specific to each transport in the adapter.

## Source ingestion contract

`sources.ingest.ingest` owns dataset selection, status/availability checks, fingerprint and watermark
handling, schema/quality holds, staged execution, history, run recording and load outcomes.
HTTP dataset Run, the compatible mapping-and-load route, Sources onboarding, CLI ingestion and
Dagster assets all call it. MCP exposes source health, not an ingestion mutation; do not invent a
second execution surface to achieve adapter symmetry.

Explicit names must exist; repeated names execute once. A missing active input is an error for
deliberate UI/API/CLI loads, not a successful zero-row result. Paused/retired inputs remain explicit
`paused` no-ops. Bulk discovery skips optional absent exports and reports them in `missing`.
Dagster's catalog-wide asset discovery deliberately opts into `allow_missing=True`: built-in optional
exports may be absent, and their absence must not prevent other sources and the register from refreshing.
It reports `missing=true` and `loaded=false`, not a completed source load; a Dagster materialization
is scheduler bookkeeping, not proof of ingested records. Connector/read failures still propagate.

Keep the engine's result fields intact across HTTP/CLI and nested onboarding results, including
`loaded`, `unchanged`, `held`, `paused`, `missing` and profile drift. Jobs translates these lists to
per-asset boolean metadata, retaining trigger and connector/path diagnostics. HTTP translates known
selection/connection errors to status codes; CLI formats them as exit errors. Authorization, CSRF and
trusted actor resolution remain transport responsibilities. No adapter retries a failed load.

Preflight rejection creates no source run. Once row execution starts, failure rolls back staged data
and records failed source runs before propagating the error. Recovery is an explicit inspection of
Sources Runs/Quality and the connection, followed by a deliberate retry; ingestion does not rebuild
the graph. Jobs owns scheduler diagnostics, not a parallel source-run model.
