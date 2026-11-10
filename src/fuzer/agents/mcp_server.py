"""MCP server: lets AI agents query the Fuzer asset graph.

All tools are read-only except the work tools (my_work, claim_work, complete_work, hand_off,
send_evidence), which move agent steps through the work engine (#97) on the same paths as the API.
Review decisions and requiresHuman steps stay with people in the web UI.
Run with `fuzer mcp` (stdio) or `fuzer mcp --http` (streamable HTTP).
"""

import json
from datetime import date
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..core import access, db
from ..core.pack import Pack, current_pack
from ..graph import queries
from ..workflow import bpmn
from ..workflow import engine as work
from . import capabilities

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Work tools change Fuzer's own work items, nothing in the plant or outside systems.
WORK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
AgentRole = Literal[tuple(sorted(bpmn.AGENT_ROLES))]

INSTRUCTIONS = """\
Fuzer is the asset data graph for {facility} (oil & gas). It merges records from SAP, JDE,
Maximo, PI / PI AF, Energy Components (allocation network), P&IDs and operator logs into one entity per
real-world thing, and links them.

Entity kinds: facility, area, tag (a functional position such as 20-P-1001B), equipment (the physical
item installed at a tag, identified by serial number; pumps get swapped, tags stay), equipment_model
(manufacturer + model), equipment_class (ISO 14224 category / class / type, e.g. ISO14224:PU.CE =
centrifugal pumps), well (A-07, sidetracks as "A-07 ST1"), timeseries (a PI point), allocation_node,
document (P&ID sheet), log_entry, event, notification and work_order (SAP PM, with ISO 14224 failure
mode / mechanism and maintenance activity), downtime (EC deferment: affects a well or the facility,
caused_by tags, lost oil), production_day (EC DAY001: a day's allocated production), incident (an upset on one unit: about the unit,
includes its events, downtime and notifications), document (P&ID sheet, OEM manual, troubleshooting
guide or procedure revision; applies_to models / classes / tags; supersedes older revisions),
document_section (a section of a document, part_of it, with heading, page and text).

Links: part_of, installed_at (equipment -> tag, dated), has_timeseries, flows_to (production / allocation
flow, dated), lift_gas_to (gas lift network: compression -> lift gas headers -> jacket gas lift manifolds,
sometimes through another jacket's manifold -> gas lifted wells, dated), located_at (manifolds, wells ->
the jacket or platform they are on), mentions (document or log -> entity), sidetrack_of, classified_as (tag / equipment ->
class), instance_of (equipment -> model), is_a (model -> class, class -> parent), reports (log entry -> event) and
involves (event -> tag / well, with a role). Events (trips, starts, changeovers, shut-ins, choke changes,
alarms, leaks, instrument faults, ...) were read from operator logs by Claude; each cites its log entry. Dated links have from/to dates; pass
as_of=YYYY-MM-DD to see what was true on a day.
Kinds and link types added for this asset (from new datasets) are described by `get_ontology`, with
what each means and which kinds each link type joins.

Measurements that aren't in PI (well tests, lab results at sample points, wall thickness at CMLs) are
landed and kept: `measurements(ref)` returns them per quantity and location next to process values.
Operating limits per point (alarm limits from the DCS and PI AF, integrity operating windows, normal ranges
learned from history) and excursions outside them, counted only while the unit runs: `limits(ref)`.

Diagnostics return numbers to cite as evidence, {value, expected, deviation, flag, basis, sources}:
`calc(check, ref)` (decline, gas lift optimum, pump and choke against OEM curves, Weibull / Crow-AMSAA, wall
loss), `instrument_health(ref)` (frozen, stuck, noisy, out of range, redundant drift), `detect_changes(ref)`
(steps, drifts, jitter changes with their start times) and `what_changed(since)` (what the records say
happened since a time).

Charts: the plot_* tools build interactive charts from Fuzer data and return a figure id, a
`[[figure:<id>]]` marker and a summary of what the chart shows (never the plotted arrays); the chart
opens at the returned link. `look_at_figure` shows one as an image when the summary isn't enough.

Work: processes (upsets, daily reports, ...) move between agents and people. Act as your agent role:
`my_work(agent)` lists your steps, `claim_work` takes one, `complete_work` records your result (it moves
the process on), `hand_off` gives a step to a person, a role's queue (role:<role>) or another agent, and
`send_evidence` adds what you found to an open process by its correlation key (incident id). Agents
propose; steps that need a person (approvals, anything with an outside effect) refuse agents.

Any tool taking `ref` accepts a Fuzer ID (fz-...), a tag in any style (20-PT-1001A, 20PT1001A), a well
name, a PI point name or a source record key. Every value carries the source it came from; cite sources
when you answer. If a ref is ambiguous the tool returns candidates; pick one or ask the user.
"""



def instructions(pack: Pack | None) -> str:
    return INSTRUCTIONS.replace("{facility}", f"facility {pack.facility}" if pack else "the facility this database holds")


# main() names the facility from the database it serves (#376); importing the module doesn't read it.
server = MCPServer(name="fuzer", title="fuzer asset graph", version="0.1.0", instructions=instructions(None))
# Each tool declares the agents that use it and its kind (#133). Charts are read-only like queries: a chart
# is kept (assistant_figure) so the answer can show it, but no plant or register data changes, and clients
# shouldn't ask for approval per chart.
capability = capabilities.registrar(server, {"query": READ_ONLY, "calc": READ_ONLY, "plot": READ_ONLY, "work": WORK})

REL_LABELS = {
    ("part_of", "out"): "part_of", ("part_of", "in"): "contains",
    ("installed_at", "out"): "installed_at", ("installed_at", "in"): "equipment_installed_here",
    ("has_timeseries", "out"): "timeseries", ("has_timeseries", "in"): "measures",
    ("flows_to", "out"): "flows_to", ("flows_to", "in"): "fed_by",
    ("mentions", "out"): "mentions", ("mentions", "in"): "mentioned_in",
    ("sidetrack_of", "out"): "sidetrack_of", ("sidetrack_of", "in"): "sidetracks",
    ("involves", "out"): "involves", ("involves", "in"): "events",
    ("reports", "out"): "reports_event", ("reports", "in"): "reported_in",
    ("classified_as", "out"): "class", ("classified_as", "in"): "members",
    ("instance_of", "out"): "model", ("instance_of", "in"): "instances",
    ("is_a", "out"): "is_a", ("is_a", "in"): "subtypes",
    ("concerns", "out"): "concerns", ("concerns", "in"): "maintenance",
    ("for_notification", "out"): "notification", ("for_notification", "in"): "work_orders",
    ("related_to", "out"): "related_events", ("related_to", "in"): "related_notifications",
    ("affects", "out"): "affects", ("affects", "in"): "downtime",
    ("caused_by", "out"): "caused_by", ("caused_by", "in"): "downtime_caused",
    ("reports_on", "out"): "reports_on", ("reports_on", "in"): "production_days",
    ("about", "out"): "about", ("about", "in"): "incidents",
    ("includes", "out"): "includes", ("includes", "in"): "in_incident",
    ("applies_to", "out"): "applies_to", ("applies_to", "in"): "documents",
    ("supersedes", "out"): "supersedes", ("supersedes", "in"): "superseded_by",
    ("uses_part", "out"): "spare_parts", ("uses_part", "in"): "used_by",
    ("pipes_to", "out"): "pipes_to", ("pipes_to", "in"): "piped_from",
    ("tests", "out"): "tests", ("tests", "in"): "well_tests",
    ("lift_gas_to", "out"): "supplies_lift_gas_to", ("lift_gas_to", "in"): "lift_gas_from",
}


def _plain(obj: Any) -> Any:
    """Dates and other DB types -> JSON-safe values."""
    return json.loads(json.dumps(obj, default=str))


def _resolve(conn, ref: str) -> tuple[str | None, dict | None]:
    """Fuzer ID for ref, or an error payload for the agent."""
    try:
        fid = queries.resolve_ref(conn, current_pack(conn), ref)
    except queries.AmbiguousRef as e:
        return None, {"error": f"{ref!r} is ambiguous", "candidates": _plain(e.candidates)}
    if fid is None:
        return None, {"error": f"nothing found for {ref!r}; try the search tool"}
    return fid, None


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@server.prompt(title="Troubleshoot an upset")
def troubleshoot(what: str) -> str:
    """Guided troubleshooting of an event, incident or piece of equipment."""
    return f"""Help an operations engineer troubleshoot: {what}

1. Call troubleshooting_brief with the tag, equipment or incident. If it's ambiguous, ask which one.
2. Summarise what happened from the timeline, then what has worked before: approved lessons first, then
   similar past incidents and how they ended.
3. Give a short, ordered list of checks. Back each with a citation (lesson, document and page, incident or
   work order). Say plainly where Fuzer has no evidence.
4. Mention spares that are out of stock and recent repeat failures.
5. Never instruct anyone to operate plant equipment directly; recommend checks for the responsible
   operator or engineer, and follow site procedures (permit to work, isolation)."""


# --- diagnostics (calcs.py, signals.py): numbers with their basis, to cite as evidence -----------------


def _resolve_point(conn, ref: str) -> tuple[str | None, dict | None]:
    """A PI point: when a ref also matches a well or tag, the timeseries with exactly that name."""
    fid, err = _resolve(conn, ref)
    if err:
        exact = [c for c in err.get("candidates", []) if c["kind"] == "timeseries" and c["label"] == ref]
        if len(exact) == 1:
            return exact[0]["fuzer_id"], None
    return fid, err


def _scope(conn, scope: str | list[str]) -> tuple[Any, dict | None]:
    if scope == "facility":
        return scope, None
    out = []
    for r in scope if isinstance(scope, list) else [scope]:
        fid, err = _resolve(conn, r)
        if err:
            return None, err
        out.append(fid)
    return (out if isinstance(scope, list) else out[0]), None


def _trim(r):
    if not isinstance(r, dict):
        return r
    return {k: (v[:15] if isinstance(v, list) else v) for k, v in r.items()}




# --- work (#97): agents use the same engine paths as the API and the inbox -----------------------

def _work(fn, *args, **kwargs) -> dict[str, Any]:
    with db.connect() as conn:
        try:
            return _plain(fn(conn, *args, **kwargs))
        except work.WorkError as e:
            return {"error": str(e)}


# --- charts (plots/): built here from Fuzer data; the agent gets an id, a marker and a summary -------


def _chart(build, params: dict, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve refs, build and store a chart; the result is what the agent sees (never the arrays)."""
    from .. import plots as charts
    with db.connect() as conn:
        args = dict(params)
        for k, ref in (refs or {}).items():
            if ref is None:
                args[k] = None
                continue
            ids = []
            for r in ref if isinstance(ref, list) else [ref]:
                fid, err = _resolve(conn, r)
                if err:
                    return err
                ids.append(fid)
            args[k] = ids if isinstance(ref, list) else ids[0]
        try:
            fig = build(conn, **args)
        except charts.ChartError as e:
            return {"error": str(e)}
        return _plain(charts.store(conn, fig, _plain({"chart": fig.kind, **params, **(refs or {})})))


def main(http: bool = False, host: str = "127.0.0.1", port: int = 8001) -> None:
    if http and (access.mode() != "local" or host not in ("127.0.0.1", "::1", "localhost")):
        raise access.AccessError("HTTP MCP is available only in explicit local mode on loopback. "
                                 "Use trusted local stdio or the authenticated web API; remote MCP login is not implemented.")
    db.database_url()  # start embedded Postgres up front if it's in use
    with db.connect() as conn:
        db.init_schema(conn)  # an older database gets any newer tables
        server._lowlevel_server.instructions = instructions(current_pack(conn))
    if http:
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()


# The tools live with the agents that use them (agents/capabilities/<group>.py, #133). Importing them
# declares them on the server; mcp_server.<tool> still finds each one.
from .capabilities import (  # noqa: F401
    graph,
    maintenance,
    operations,
    plots,
    production,
    sources,
    studies,
    troubleshooting,
)
from .capabilities import work as work_tools  # noqa: F401


def __getattr__(name: str):
    if name == "PACK":  # mcp_server.PACK: the pack of the database's project, read at runtime (#376)
        return current_pack()
    if c := capabilities.REGISTRY.get(name):
        return c.fn
    raise AttributeError(name)
