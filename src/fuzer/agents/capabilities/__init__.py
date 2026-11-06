"""Capability and agent registry: tools, domain agents, persona coverage and skills have one catalog.

    @capability("production-surveillance", kind="plot")
    def plot_choke(ref: str, ...) -> dict: ...

"all" gives a tool to every agent. A tool with no agents is MCP-only (the work tools agents call as
themselves through the workflow engine). Legacy tool declarations are mapped during migration.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class AgentDefinition:
    id: str
    label: str
    description: str
    instructions: str
    personas: tuple[str, ...]
    skill_paths: tuple[str, ...]
    data_requirements: tuple[str, ...]
    outputs: tuple[str, ...]
    constraints: tuple[str, ...]
    human_decisions: tuple[str, ...]


AGENT_CATALOG = (
    AgentDefinition(
        "orchestrator", "Orchestrator", "Routes questions to read-only domain specialists and synthesizes cited findings.",
        "You coordinate domain specialists, answer simple lookups from tools, and synthesize only supported findings.",
        (), ("orchestrator/answering",),
        ("Fuzer entity graph and whichever linked records a question requires.",),
        ("A concise, cross-domain answer with citations, dates, units, gaps, and next steps.",),
        ("Never present synthetic or historical data as live; state when evidence or prerequisites are missing.",),
        ("Never make operating, maintenance, HSE, emergency, or schedule decisions.",),
    ),
    AgentDefinition(
        "production-surveillance", "Production & Surveillance",
        "Production trends, deferment, constraints, gas lift, and production-efficiency evidence.",
        "You analyze production and surveillance evidence. Load the relevant production skill first.",
        ("Production Surveillance", "Production / Operations Engineer", "Optimization Engineer"),
        ("production/back-pressure", "production/constraint-management", "production/gas-lift",
         "production/production-efficiency", "production/production-engineering",
         "production/production-surveillance"),
        ("Allocated production, potential, well tests, events, and linked facility records; dates and units must be available.",),
        ("Dated production trends, ranked losses or constraints, and evidence-backed opportunities with uncertainty.",),
        ("Do not infer reservoir causes or upside where the underlying evidence is absent.",),
        ("Operators and accountable engineers approve operating changes and optimization actions.",),
    ),
    AgentDefinition(
        "wells-subsurface", "Wells & Subsurface",
        "Well performance, well-test trends, and linked well/intervention context where records exist.",
        "You interpret well and subsurface evidence conservatively. Load production-engineering or surveillance skills as relevant.",
        ("Subsurface Engineer", "Drilling, Completions & Intervention Specialist"),
        ("production/production-engineering", "production/production-surveillance", "production/gas-lift"),
        ("Linked well identity, dated well tests or production records, and intervention history when present.",),
        ("Well-level evidence summaries and candidate questions for specialist review.",),
        ("Reservoir state, completion design, and intervention outcomes are unknown unless recorded by an authoritative source.",),
        ("Subsurface and well intervention decisions remain with accountable engineers.",),
    ),
    AgentDefinition(
        "field-operations-facilities", "Field Operations & Facilities",
        "Read-only facility, event, alarm, process-value, and operating-constraint evidence.",
        "You summarize facility and field evidence from linked records. Load troubleshooting or constraint skills as relevant.",
        ("Field Operator", "Surface / Facilities Engineer", "Instrumentation, Controls & OT Specialist"),
        ("troubleshooter/troubleshooting", "production/constraint-management", "production/back-pressure"),
        ("Linked equipment and event records; process values require a configured time-series source.",),
        ("Dated event timelines, observed signal changes, and questions for field verification.",),
        ("Synthetic process values are not plant evidence; no control or operating-envelope validation is implied.",),
        ("No control changes or field actions; operators and control-system owners make those decisions.",),
    ),
    AgentDefinition(
        "maintenance-integrity", "Maintenance & Integrity",
        "Reliability, failure history, inspection/compliance, condition evidence, and integrity plots.",
        "You summarize available maintenance, reliability, inspection, and integrity records. Load reliability as relevant.",
        ("Maintenance Technician / Coordinator", "Reliability Engineer", "Inspection Specialist", "Asset Integrity Engineer"),
        ("maintenance/reliability",),
        ("Linked work orders, incidents, PM/inspection records, and measured condition data where available.",),
        ("Ranked evidence summaries with data dates, units, and explicit coverage gaps.",),
        ("OEM pump curves, choke characteristics, and vibration sources are not currently available; no inferred inspection status.",),
        ("Maintenance execution, deferral, integrity acceptance, and shutdown decisions require accountable human approval.",),
    ),
    AgentDefinition(
        "work-management-supply-chain", "Work Management & Supply Chain",
        "Work backlog, turnaround scope, work readiness, and recorded spares evidence.",
        "You are the work management and supply chain specialist. Assess recorded work and materials evidence. "
        "Load work-orders or turnarounds as relevant.",
        ("Work Planner", "Work Scheduler", "Supply Chain Specialist", "Logistics Coordinator"),
        ("maintenance/work-orders", "maintenance/turnarounds"),
        ("Work orders, turnaround scope, and spares stock records when linked; vendor and transport data may be absent.",),
        ("Readiness and backlog findings with explicit missing-resource information.",),
        ("A recorded stock count is not a reservation; no vendor, transport, access, or schedule status is inferred.",),
        ("People approve work priorities, schedules, resource commitments, and logistics.",),
    ),
    AgentDefinition(
        "hse-incident-management", "HSE & Incident Management",
        "Evidence-only incident timelines and corrective-action context; no regulatory or emergency authority.",
        "You organize recorded incident evidence for accountable HSE review. Load troubleshooting when useful and state unavailable HSE sources.",
        ("HSE Specialist", "Environmental / Regulatory Specialist", "Incident Investigator", "Emergency Response Lead"),
        ("hse-incident-management/incident-evidence",),
        ("Recorded incidents, event logs, and linked corrective work only; environmental and regulatory sources are not configured.",),
        ("Source-linked incident timelines and clearly scoped evidence gaps for human review.",),
        ("No compliance determination, hazard/barrier assurance, emergency procedure, or response advice without authoritative data.",),
        ("Escalate safety-critical judgments and emergency response to accountable people and approved procedures.",),
    ),
    AgentDefinition(
        "asset-performance-reporting", "Asset Performance & Reporting",
        "Cited cross-domain operational summaries based only on the linked evidence available to Fuzer.",
        "You prepare dated operational summaries from available production and maintenance evidence. Load matching domain skills.",
        ("Asset Manager", "Operations Reporter", "Commercial / Production Accounting Specialist"),
        ("asset-performance-reporting/operations-summary",),
        ("Current operational measures and provenance; authoritative allocation, cost, and accounting data are required for reconciliation.",),
        ("Cross-domain KPI summaries with units, time windows, provenance, and explicit unknowns.",),
        ("Commercial and accounting reconciliation is unavailable without authoritative accounting and cost sources.",),
        ("Asset trade-offs and external/commercial reporting require human validation and approval.",),
    ),
)
AGENTS = tuple(agent.id for agent in AGENT_CATALOG)
AGENT_BY_ID = {agent.id: agent for agent in AGENT_CATALOG}
SHARED_SKILL_PATHS = ("shared/charts",)
PERSONA_DISPOSITION = (
    ("Data Scientist / Operations Research Analyst",
     "Shared analytical methods in domain agents; no separate agent",
     "Use only supported calculations and plots; state assumptions, data sufficiency, and uncertainty."),
    ("Data Steward / Platform Administrator",
     "Platform administration, Sources/Settings/Jobs, and governance; outside the operational-agent roster",
     "Do not grant operational agents platform administration or data-governance authority."),
)

# Existing decorators remain a narrow compatibility layer while capabilities are assigned to domains.
LEGACY_AGENT_OWNERS = {
    "troubleshooter": ("field-operations-facilities",),
    "production": ("production-surveillance", "wells-subsurface"),
    "maintenance": ("maintenance-integrity",),
}
ADDITIONAL_AGENT_OWNERS = {
    "backpressure_sensitivity": ("field-operations-facilities",),
    "capacity_headroom": ("field-operations-facilities",),
    "debottleneck_options": ("field-operations-facilities",),
    "plot_backpressure_sensitivity": ("field-operations-facilities",),
    "plot_choke_criticality": ("field-operations-facilities",),
    "plot_debottleneck": ("field-operations-facilities",),
    "production_constraints": ("field-operations-facilities", "asset-performance-reporting"),
    "get_incidents": ("hse-incident-management",),
    "incident_detail": ("hse-incident-management",),
    "get_events": ("hse-incident-management",),
    "get_upsets": ("hse-incident-management",),
    "upset_detail": ("hse-incident-management",),
    "similar_cases": ("hse-incident-management",),
    "bad_actors": ("work-management-supply-chain", "asset-performance-reporting"),
    "work_order_backlog": ("work-management-supply-chain", "asset-performance-reporting"),
    "maintenance_compliance": ("work-management-supply-chain", "asset-performance-reporting"),
    "turnaround_scope": ("work-management-supply-chain", "asset-performance-reporting"),
    "maintenance_history": ("work-management-supply-chain",),
    "spare_parts": ("work-management-supply-chain",),
    "deferment_pareto": ("work-management-supply-chain", "asset-performance-reporting"),
    "production_summary": ("asset-performance-reporting",),
    "well_performance": ("asset-performance-reporting",),
    "value_score": ("asset-performance-reporting",),
}
KINDS = ("query", "calc", "plot", "work")


@dataclass(frozen=True)
class Capability:
    name: str
    agents: tuple[str, ...]
    kind: str
    fn: Callable


REGISTRY: dict[str, Capability] = {}


def registrar(server, annotations: dict[str, Any]) -> Callable:
    """The `capability` decorator for one MCP server; `annotations` gives the MCP tool annotations by kind."""

    def capability(*agents: str, kind: str = "query") -> Callable:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
        if bad := set(agents) - {*AGENTS, *LEGACY_AGENT_OWNERS, "all"}:
            raise ValueError(f"no agent {', '.join(sorted(bad))}")
        declared = set(AGENTS) if "all" in agents else {
            owner for name in agents for owner in LEGACY_AGENT_OWNERS.get(name, (name,))
        }
        owners = tuple(a for a in AGENTS if a in declared)

        def register(fn: Callable) -> Callable:
            if fn.__name__ in REGISTRY:
                raise ValueError(f"tool {fn.__name__} is declared twice")
            resolved_owners = tuple(a for a in AGENTS if a in {*owners, *ADDITIONAL_AGENT_OWNERS.get(fn.__name__, ())})
            REGISTRY[fn.__name__] = Capability(fn.__name__, resolved_owners, kind, fn)
            server.tool(annotations=annotations[kind])(fn)
            return fn

        return register

    return capability


def tools_by_agent() -> dict[str, list[str]]:
    """Each agent's tool names, in the order they are declared."""
    from .. import mcp_server  # noqa: F401  (declaring the tools fills the registry)

    return {a: [c.name for c in REGISTRY.values() if a in c.agents] for a in AGENTS}


def _summary(fn: Callable) -> str:
    doc = " ".join((fn.__doc__ or "").split())
    return (doc.split(". ")[0].rstrip(".") + ".") if doc else ""


def _params(fn: Callable) -> str:
    import inspect

    return ", ".join(p if v.default is inspect.Parameter.empty else f"{p}?"
                     for p, v in inspect.signature(fn).parameters.items())


def catalogue() -> str:
    """docs/capabilities.md: personas, skills, prerequisites, boundaries and tools, all from the registry
    (`uv run python -m fuzer.agents.capabilities > docs/capabilities.md`; a test keeps it current)."""
    from ...analysis import calcs

    by_agent = tools_by_agent()
    shared = [c for c in REGISTRY.values() if len([a for a in c.agents if a != "orchestrator"]) > 1]
    rows = lambda cs: ["| Tool | Kind | Parameters | What it does |", "|---|---|---|---|"] + [  # noqa: E731
        f"| `{c.name}` | {c.kind} | {_params(c.fn)} | {_summary(c.fn)} |" for c in cs]
    out = ["# Capabilities and agent catalog", "",
           "Generated from the capability registry (`src/fuzer/agents/capabilities/`, #133); don't edit by hand. "
           "Each tool is declared once with the agents that use it. `?` marks an optional parameter.", "",
           "## Domain agents and persona coverage", "",
           "Tool availability is not evidence that every source exists. Each specialist stays within its listed "
           "data prerequisites and human-decision boundary; unsupported inputs remain explicit unknowns. Each "
           "domain row's skills are available to its listed personas; the agent selects only relevant methods.", "",
           "| Agent | Personas | Skills | Required data | Expected output | Limits | Human decision |",
           "|---|---|---|---|---|---|---|"]
    for agent in AGENT_CATALOG:
        out.append(f"| {agent.label} | {'; '.join(agent.personas) or 'Cross-domain routing'} "
                   f"| {'; '.join(agent.skill_paths)} | {'; '.join(agent.data_requirements)} "
                   f"| {'; '.join(agent.outputs)} | {'; '.join(agent.constraints)} "
                   f"| {'; '.join(agent.human_decisions)} |")
    for persona, disposition, boundary in PERSONA_DISPOSITION:
        out.append(f"| {disposition} | {persona} | Shared methods / platform workflows | "
                   f"Existing domain evidence only | Reusable analysis or platform workflow output | "
                   f"{boundary} | Human platform administration remains outside the agent roster. |")
    out += ["", "### Shared skills", "",
            "`shared/charts` is loaded by the orchestrator and every specialist as a chart-reference catalog. "
            "An agent may call only chart tools present in its own tool list; the skill does not grant capabilities.", ""]
    out += ["", "## Tool coverage", "", "| Agent | Tools |", "|---|---|"] + [
        f"| {AGENT_BY_ID[a].label} | {len(t)} |" for a, t in by_agent.items()]
    out += ["", f"## Shared ({len(shared)})", "", "Used by every agent or by several specialists.", "", *rows(shared)]
    for a in AGENTS:
        own = [c for c in REGISTRY.values() if c not in shared and c.agents and
               ([x for x in c.agents if x != "orchestrator"] or ["orchestrator"])[0] == a]
        if own:
            out += ["", f"## {AGENT_BY_ID[a].label} ({len(own)})", "", *rows(own)]
    mcp_only = [c for c in REGISTRY.values() if not c.agents]
    out += ["", f"## MCP only ({len(mcp_only)})", "", "Called by agents as themselves through the workflow engine.", "",
            *rows(mcp_only)]
    out += ["", "## Calc checks", "", "`calc(check, ref)` runs one; each returns {value, expected, deviation, flag, "
            "basis, sources}.", "", "| Check | On | Also used by |", "|---|---|---|"]
    label = {"person": "chart", "detector": "SRE detector"}
    out += [f"| `{k}` | {v['ref']} | " + ", ".join(f"{label[w]} `{x}`" for w, x in v["used_by"].items() if x and w in label)
            + " |" for k, v in calcs.CHECKS.items()]
    return "\n".join(out) + "\n"
