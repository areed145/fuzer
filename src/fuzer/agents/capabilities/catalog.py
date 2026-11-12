"""Who the assistant is: its domain agents, the personas they serve, their skills and what none of them can know.

Data, not behaviour. `agents.team` builds each agent's prompt from these definitions, docs/capabilities.md is
generated from them and `/assistant/agents` publishes them, so a boundary written here reaches the model, the docs
and the Ask page. An agent's skills come from SKILL_CATALOG; its tools are declared by `@capability` next to each
tool. The tools a skill needs are read from its SKILL.md, so they can't drift from what it tells the agent to call.
"""

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS = Path(__file__).parents[1] / "skills"


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    label: str
    description: str
    instructions: str
    not_for: str  # the neighbouring domain a question belongs to instead; shown to the orchestrator
    personas: tuple[str, ...]
    data_requirements: tuple[str, ...]
    outputs: tuple[str, ...]
    constraints: tuple[str, ...]
    human_decisions: tuple[str, ...]

    @property
    def skill_paths(self) -> tuple[str, ...]:
        return tuple(s.path for s in SKILL_CATALOG if self.id in s.agents)

    @property
    def skill_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in SKILL_CATALOG if self.id in s.agents)

    @property
    def boundaries(self) -> str:
        """The evidence and decision boundary, as the agent's own prompt states it."""
        return "\n".join([
            "Evidence you need: " + " ".join(self.data_requirements),
            "Limits: " + " ".join(self.constraints),
            "People decide: " + " ".join(self.human_decisions),
        ])

    @property
    def prompt(self) -> str:
        """The instructions, the skills this agent actually has and its boundaries."""
        skills = ", ".join(f"`{name}`" for name in self.skill_names)
        return f"{self.instructions} Load the skill that fits the task first: {skills}.\n\n{self.boundaries}"


@dataclass(frozen=True)
class SkillDefinition:
    path: str  # under agents/skills
    agents: tuple[str, ...]  # the owner first, then the agents it is reused by
    personas: tuple[str, ...]
    data: str
    output: str
    boundary: str
    shared: bool = False  # loaded by every agent; each may call only the tools in its own list

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def owner(self) -> str:
        return self.agents[0]

    @property
    def markdown(self) -> str:
        return (SKILLS / self.path / "SKILL.md").read_text()

    @property
    def purpose(self) -> str:
        return re.search(r"^description: (.+)$", self.markdown, re.MULTILINE).group(1)

    def tools(self, known) -> tuple[str, ...]:
        """The registered tools the SKILL.md tells an agent to call, in name order."""
        return tuple(sorted(set(re.findall(r"`([a-z_]+)(?:\(|`)", self.markdown)) & set(known)))


PRODUCTION_SURVEILLANCE = "Production Surveillance"
PRODUCTION_ENGINEER = "Production / Operations Engineer"
OPTIMIZATION_ENGINEER = "Optimization Engineer"
SUBSURFACE_ENGINEER = "Subsurface Engineer"
WELL_SPECIALIST = "Drilling, Completions & Intervention Specialist"
FIELD_OPERATOR = "Field Operator"
FACILITIES_ENGINEER = "Surface / Facilities Engineer"
CONTROLS_SPECIALIST = "Instrumentation, Controls & OT Specialist"
MAINTENANCE_COORDINATOR = "Maintenance Technician / Coordinator"
RELIABILITY_ENGINEER = "Reliability Engineer"
INSPECTION_SPECIALIST = "Inspection Specialist"
INTEGRITY_ENGINEER = "Asset Integrity Engineer"
WORK_PLANNER = "Work Planner"
WORK_SCHEDULER = "Work Scheduler"
SUPPLY_CHAIN_SPECIALIST = "Supply Chain Specialist"
LOGISTICS_COORDINATOR = "Logistics Coordinator"
HSE_SPECIALIST = "HSE Specialist"
ENVIRONMENTAL_SPECIALIST = "Environmental / Regulatory Specialist"
INCIDENT_INVESTIGATOR = "Incident Investigator"
EMERGENCY_LEAD = "Emergency Response Lead"
ASSET_MANAGER = "Asset Manager"
OPERATIONS_REPORTER = "Operations Reporter"
ACCOUNTING_SPECIALIST = "Commercial / Production Accounting Specialist"
DATA_SCIENTIST = "Data Scientist / Operations Research Analyst"
DATA_STEWARD = "Data Steward / Platform Administrator"

AGENT_CATALOG = (
    AgentDefinition(
        "orchestrator", "Orchestrator", "Routes questions to read-only domain specialists and synthesizes cited findings.",
        "You coordinate domain specialists, answer simple lookups from tools, and synthesize only supported findings.",
        "Domain analysis, which the specialists do.",
        (),
        ("Fuzer entity graph and whichever linked records a question requires.",),
        ("A concise, cross-domain answer with citations, dates, units, gaps, and next steps.",),
        ("Never present synthetic or historical data as live; state when evidence or prerequisites are missing.",),
        ("Never make operating, maintenance, HSE, emergency, or schedule decisions.",),
    ),
    AgentDefinition(
        "production-surveillance", "Production & Surveillance",
        "Asset production trends, production efficiency, deferment, constraints, gas lift allocation, back-pressure "
        "and optimisation opportunities.",
        "You analyze production and surveillance evidence for an oil and gas asset from Fuzer's tools.",
        "One well's history or intervention case (Wells & Subsurface); why a unit tripped (Field Operations & Facilities).",
        (PRODUCTION_SURVEILLANCE, PRODUCTION_ENGINEER, OPTIMIZATION_ENGINEER),
        ("Allocated production, potential, well tests, events, and linked facility records; dates and units must be available.",),
        ("Dated production trends, ranked losses or constraints, and evidence-backed opportunities with uncertainty.",),
        ("Do not infer reservoir causes or upside where the underlying evidence is absent.",),
        ("Operators and accountable engineers approve operating changes and optimization actions.",),
    ),
    AgentDefinition(
        "wells-subsurface", "Wells & Subsurface",
        "One well or a set of wells: test and pressure trends, decline, water breakthrough, gas lift response, "
        "recorded intervention history and candidate screening.",
        "You interpret well and subsurface evidence conservatively from Fuzer's tools.",
        "Asset-wide production efficiency and deferment (Production & Surveillance); facility constraints (Field "
        "Operations & Facilities).",
        (SUBSURFACE_ENGINEER, WELL_SPECIALIST),
        ("Linked well identity, dated well tests or production records, and intervention history when present.",),
        ("Well-level evidence summaries and candidate questions for specialist review.",),
        ("Reservoir state, completion design, and intervention outcomes are unknown unless recorded by an authoritative source.",),
        ("Subsurface and well intervention decisions remain with accountable engineers.",),
    ),
    AgentDefinition(
        "field-operations-facilities", "Field Operations & Facilities",
        "Troubleshooting a unit or upset, shift handover, alarms, instrument health, process values, operating "
        "limits, and facility constraints and headroom.",
        "You troubleshoot units and summarize facility and field evidence from Fuzer's linked records.",
        "Failure history and backlog (Maintenance & Integrity); a formal incident investigation record (HSE & "
        "Incident Management).",
        (FIELD_OPERATOR, FACILITIES_ENGINEER, CONTROLS_SPECIALIST),
        ("Linked equipment and event records; process values require a configured time-series source.",),
        ("Dated event timelines, observed signal changes, and questions for field verification.",),
        ("Synthetic process values are not plant evidence; no control or operating-envelope validation is implied.",),
        ("No control changes or field actions; operators and control-system owners make those decisions.",),
    ),
    AgentDefinition(
        "maintenance-integrity", "Maintenance & Integrity",
        "Reliability, failure history, bad actors, PM and inspection compliance, condition evidence, wall thickness "
        "and integrity risk.",
        "You analyze maintenance, reliability, inspection, and integrity records from Fuzer's tools.",
        "Planning and readiness of work (Work Management & Supply Chain); what a unit's process values did in an "
        "upset (Field Operations & Facilities).",
        (MAINTENANCE_COORDINATOR, RELIABILITY_ENGINEER, INSPECTION_SPECIALIST, INTEGRITY_ENGINEER),
        ("Linked work orders, incidents, PM/inspection records, and measured condition data where available.",),
        ("Ranked evidence summaries with data dates, units, and explicit coverage gaps.",),
        ("OEM curves cover only the models loaded, so `equipment_curves` decides whether a curve comparison is possible; vibration is overall mm/s from PI, with no spectra; no inferred inspection status.",),
        ("Maintenance execution, deferral, integrity acceptance, and shutdown decisions require accountable human approval.",),
    ),
    AgentDefinition(
        "work-management-supply-chain", "Work Management & Supply Chain",
        "Work backlog priorities, job scope and readiness, turnaround scope, and recorded spares and stock.",
        "You are the work management and supply chain specialist. Assess recorded work and materials evidence.",
        "Why equipment keeps failing (Maintenance & Integrity).",
        (WORK_PLANNER, WORK_SCHEDULER, SUPPLY_CHAIN_SPECIALIST, LOGISTICS_COORDINATOR),
        ("Work orders, turnaround scope, and spares stock records when linked; vendor and transport data may be absent.",),
        ("Readiness and backlog findings with explicit missing-resource information.",),
        ("A recorded stock count is not a reservation; no vendor, transport, access, or schedule status is inferred.",),
        ("People approve work priorities, schedules, resource commitments, and logistics.",),
    ),
    AgentDefinition(
        "hse-incident-management", "HSE & Incident Management",
        "Evidence-only incident timelines, investigation records, corrective-action tracking and recorded flare; "
        "no regulatory or emergency authority.",
        "You organize recorded incident evidence for accountable HSE review and say which HSE sources are unavailable.",
        "Live troubleshooting of a unit (Field Operations & Facilities).",
        (HSE_SPECIALIST, ENVIRONMENTAL_SPECIALIST, INCIDENT_INVESTIGATOR, EMERGENCY_LEAD),
        ("Recorded incidents, event logs, and linked corrective work only; environmental and regulatory sources are not configured.",),
        ("Source-linked incident timelines and clearly scoped evidence gaps for human review.",),
        ("No compliance determination, hazard/barrier assurance, emergency procedure, or response advice without authoritative data.",),
        ("Escalate safety-critical judgments and emergency response to accountable people and approved procedures.",),
    ),
    AgentDefinition(
        "asset-performance-reporting", "Asset Performance & Reporting",
        "Cited cross-domain KPI summaries, handovers and opportunity trade-offs, from the linked evidence available to Fuzer.",
        "You prepare dated operational summaries from available production and maintenance evidence.",
        "Explaining one domain's cause in depth (the domain specialist); accounting reconciliation (no authoritative source).",
        (ASSET_MANAGER, OPERATIONS_REPORTER, ACCOUNTING_SPECIALIST),
        ("Current operational measures and provenance; authoritative allocation, cost, and accounting data are required for reconciliation.",),
        ("Cross-domain KPI summaries with units, time windows, provenance, and explicit unknowns.",),
        ("Commercial and accounting reconciliation is unavailable without authoritative accounting and cost sources.",),
        ("Asset trade-offs and external/commercial reporting require human validation and approval.",),
    ),
)
AGENTS = tuple(agent.id for agent in AGENT_CATALOG)
AGENT_BY_ID = {agent.id: agent for agent in AGENT_CATALOG}

_PROD, _WELLS, _FIELD = "production-surveillance", "wells-subsurface", "field-operations-facilities"
_MAINT, _WORK, _HSE, _ASSET = ("maintenance-integrity", "work-management-supply-chain", "hse-incident-management",
                               "asset-performance-reporting")
_OPERATE = "Recommend checks and decisions to the accountable operator or engineer; never give operating instructions."
_APPROVE = "Findings inform people who approve priorities, schedules and commitments; nothing here commits anything."

SKILL_CATALOG = (
    SkillDefinition(
        "orchestrator/answering", ("orchestrator",), (),
        "Findings returned by specialists and tools.",
        "One cited answer with units, periods, gaps and next steps.",
        "Never tells anyone to operate plant; recommends checks under site procedures."),
    SkillDefinition(
        f"{_PROD}/production-surveillance", (_PROD,),
        (PRODUCTION_SURVEILLANCE, OPTIMIZATION_ENGINEER),
        "EC daily production and potential, deferment records, well tests, operator log.",
        "A dated trend against potential, the change points and the systems that explain them.",
        _OPERATE),
    SkillDefinition(
        f"{_PROD}/production-efficiency", (_PROD,),
        (PRODUCTION_SURVEILLANCE, PRODUCTION_ENGINEER),
        "EC daily production, potential and downtime records with reason codes.",
        "Production efficiency and a deferment Pareto by reason, category, unit or well for a stated period.",
        "Reports recorded losses; it is not production accounting."),
    SkillDefinition(
        f"{_PROD}/constraint-management", (_PROD, _FIELD),
        (PRODUCTION_ENGINEER, OPTIMIZATION_ENGINEER, FACILITIES_ENGINEER),
        "Deferment by reason, causing units, the flow network and the operator log.",
        "Ranked constraints with volume, hours, trend and how they cascade.",
        _OPERATE),
    SkillDefinition(
        f"{_PROD}/back-pressure", (_PROD, _FIELD),
        (OPTIMIZATION_ENGINEER, FACILITIES_ENGINEER),
        "Well and header pressures, well tests and operating limits; process values need a time-series source.",
        "What holds wells back, what a bar is worth and ranked debottleneck options with their next constraint.",
        "Options are for engineering review and management of change, not instructions."),
    SkillDefinition(
        f"{_PROD}/gas-lift", (_PROD, _WELLS),
        (OPTIMIZATION_ENGINEER, PRODUCTION_ENGINEER, SUBSURFACE_ENGINEER),
        "The lift gas network, well tests with lift gas rate, header and flow signals.",
        "Allocation and response per well, shortfalls, suspected valve problems and reallocation candidates.",
        "Reallocation is a recommendation for the accountable production engineer."),
    SkillDefinition(
        f"{_PROD}/production-engineering", (_PROD, _WELLS),
        (PRODUCTION_ENGINEER, SUBSURFACE_ENGINEER, WELL_SPECIALIST),
        "Well tests, wellhead samples, choke records, downtime and operator log.",
        "Well trends read against decline and water breakthrough, and intervention candidates with their evidence.",
        "Reservoir causes stay hypotheses unless a source records them."),
    SkillDefinition(
        f"{_WELLS}/well-review", (_WELLS,),
        (SUBSURFACE_ENGINEER, WELL_SPECIALIST),
        "Well identity and sidetracks, well tests, wellhead samples, downtime, operator log and work on the well.",
        "A dated well history, what changed and when, and candidates with supporting and missing evidence.",
        "No reservoir certainty, completion design or intervention decision; those stay with accountable engineers."),
    SkillDefinition(
        f"{_FIELD}/troubleshooting", (_FIELD,),
        (FIELD_OPERATOR, FACILITIES_ENGINEER),
        "Incidents, events, process values, limits, past failures, lessons and manuals.",
        "What happened, what the values did, similar past cases, and checks to make.",
        _OPERATE),
    SkillDefinition(
        f"{_FIELD}/upset-correlation", (_FIELD,),
        (FIELD_OPERATOR, FACILITIES_ENGINEER),
        "SRE upsets and their detections, the plant model's links, unit run states and alarm history.",
        "One event told first cause first, the knock-ons with their lags, upsets to merge and recurring cascades.",
        "Recommends merges and where to send one operator; the IOC merges cases and dispatches. " + _OPERATE),
    SkillDefinition(
        f"{_FIELD}/gas-treating", (_FIELD,),
        (FACILITIES_ENGINEER, FIELD_OPERATOR),
        "Contactor differential pressure, treated gas analysers, reboiler and chiller temperatures, and their "
        "recorded operating windows (IOW, alarm settings or learned).",
        "Each treating unit's status with the checks that drive it (foaming, treated gas, reboiler, chiller), the "
        "window each was judged against, the link along the train and the lab samples it could not read.",
        "Advises; antifoam, a solvent change-out, a rate cut or a reboiler setpoint change is the operator's and "
        "engineer's call."),
    SkillDefinition(
        f"{_FIELD}/shift-handover", (_FIELD,),
        (FIELD_OPERATOR, FACILITIES_ENGINEER),
        "Operator log events, notifications, downtime, the daily report and excursions for a stated window.",
        "A dated handover of what changed, what is unresolved, which records don't agree and what to verify in the "
        "field.",
        "Summarises records; the accountable shift supervisor owns the handover."),
    SkillDefinition(
        f"{_FIELD}/alarms-and-instruments", (_FIELD,),
        (CONTROLS_SPECIALIST, FIELD_OPERATOR),
        "DCS / PI AF limits, process values and run signals; requires a configured time-series source.",
        "Alarm performance, whether readings can be trusted and when a signal changed, with the window.",
        "No control, tuning or alarm changes; no validated operating envelope is implied."),
    SkillDefinition(
        f"{_MAINT}/reliability", (_MAINT,),
        (RELIABILITY_ENGINEER, MAINTENANCE_COORDINATOR, INSPECTION_SPECIALIST),
        "SAP notifications and work orders with ISO 14224 coding, maintenance plans and downtime.",
        "Bad actors, MTBF, failure modes, compliance and root-cause leads with data dates.",
        "Root causes stay leads until an accountable engineer confirms them."),
    SkillDefinition(
        f"{_MAINT}/compression", (_MAINT,),
        (RELIABILITY_ENGINEER, MAINTENANCE_COORDINATOR),
        "Compressor process and protection points (flows, pressures, temperatures, vibration, axial position, seal "
        "vent, lube oil), the machine's OEM map, cylinder data and frame rating, and the latest gas analysis.",
        "Each compressor's status with the checks that drive it (surge margin, rod load, valves, protection "
        "channels, seals, lube oil, performance), their basis and data dates.",
        "Advises; stopping, unloading or re-staging a machine is the operator's and machinery engineer's call."),
    SkillDefinition(
        f"{_MAINT}/pumps-and-drivers", (_MAINT,),
        (RELIABILITY_ENGINEER, MAINTENANCE_COORDINATOR),
        "Pump flow, pressures and vibration, the OEM curve, the motor's nameplate on the SAP equipment record, and "
        "gas turbine and gas engine driver points (exhaust and cylinder temperatures, fuel, output).",
        "Each pump or driver's status with the checks that drive it (curve, operating region, motor load, vibration, "
        "exhaust spread, hot section hours, cylinders, heat rate), their basis and data dates.",
        "Advises; stopping a pump, changing a duty or deferring an inspection is the operator's and engineer's call."),
    SkillDefinition(
        f"{_MAINT}/static-equipment", (_MAINT,),
        (INTEGRITY_ENGINEER, INSPECTION_SPECIALIST),
        "CML wall thickness readings and each CML's recorded minimum thickness, the maintenance plans' condition "
        "inspections, exchanger outlet temperatures and integrity operating windows.",
        "Each vessel, exchanger or line's remaining life at its governing CML, its inspection plan against the "
        "interval its wall supports, fouling, and what could not be checked (RBI, fitness for service).",
        "Advises; inspection intervals, deferrals and fitness for service are the integrity engineer's decisions."),
    SkillDefinition(
        f"{_MAINT}/integrity", (_MAINT,),
        (INTEGRITY_ENGINEER, INSPECTION_SPECIALIST),
        "Wall thickness at CMLs, inspection plans, integrity operating windows and excursions.",
        "Wall loss and remaining life ranked by item, overdue inspections and IOW excursions, with coverage gaps.",
        "Fitness for service, integrity acceptance and inspection deferral are accountable human decisions."),
    SkillDefinition(
        f"{_WORK}/work-orders", (_WORK, _MAINT),
        (WORK_PLANNER, WORK_SCHEDULER, MAINTENANCE_COORDINATOR),
        "SAP work orders, notifications and maintenance plans.",
        "The open backlog by priority, type and age, what is overdue, and a suggested order with reasons.",
        _APPROVE),
    SkillDefinition(
        f"{_WORK}/turnarounds", (_WORK, _MAINT),
        (WORK_PLANNER, WORK_SCHEDULER, SUPPLY_CHAIN_SPECIALIST),
        "SAP work orders with a revision, shutdown-only plans, bad actors and stock.",
        "Turnaround scope, what is missing from it and out-of-stock spares for scoped units.",
        _APPROVE),
    SkillDefinition(
        f"{_WORK}/job-readiness", (_WORK,),
        (WORK_PLANNER, WORK_SCHEDULER, SUPPLY_CHAIN_SPECIALIST, LOGISTICS_COORDINATOR),
        "Work orders, their dates and turnaround revision, and BOM spares with stock on hand.",
        "Per job, what is recorded as ready and what is missing or unknown (materials, dates, resources, logistics).",
        "A stock count is not a reservation; crew, vendor, transport and access status stay unknown. " + _APPROVE),
    SkillDefinition(
        f"{_HSE}/incident-evidence", (_HSE,),
        (HSE_SPECIALIST, ENVIRONMENTAL_SPECIALIST, INCIDENT_INVESTIGATOR, EMERGENCY_LEAD),
        "Incidents, events, upsets, linked work and lessons; flare from EC DAY001. No HSE system of record.",
        "Source-linked timelines, corrective-work status and evidence gaps for accountable review.",
        "No compliance finding, barrier assurance or emergency advice; escalates to accountable people."),
    SkillDefinition(
        f"{_ASSET}/operations-summary", (_ASSET,),
        (ASSET_MANAGER, OPERATIONS_REPORTER, ACCOUNTING_SPECIALIST),
        "The cited daily report plus production, deferment, constraints, reliability and work summaries.",
        "Dated cross-domain KPIs, trade-offs and opportunities with units, windows and unknowns.",
        "Internal evidence only: not an approved decision, an external report or accounting reconciliation."),
    SkillDefinition(
        "shared/charts", AGENTS, (), "Whichever records each plot tool reads.",
        "Interactive figures with their markers and a sentence on what each shows.",
        "Says when a chart rests on synthetic data; an agent calls only the plot tools in its own list.", shared=True),
    SkillDefinition(
        "shared/analysis", AGENTS, (DATA_SCIENTIST,),
        "PI process values, detected upsets and well tests; results depend on the time-series source.",
        "Correlations, clusters and period comparisons with assumptions, sample size and uncertainty.",
        "Correlation is not cause. Findings on synthetic values show how the generator works, not plant physics.",
        shared=True),
)

# The two personas no operational agent owns outright, and how each is covered.
PERSONA_DISPOSITION = (
    (DATA_SCIENTIST, "Shared analytical methods in domain agents (`shared/analysis`); no separate agent",
     "Use only supported calculations and plots; state assumptions, data sufficiency, and uncertainty."),
    (DATA_STEWARD, "Platform administration, Sources/Settings/Jobs, and governance; outside the operational-agent roster",
     "Do not grant operational agents platform administration or data-governance authority."),
)

# Capabilities the issue asked for that no agent has, and the source each is waiting for. Not papered over: a
# specialist asked for one of these says it is unavailable.
UNAVAILABLE = (
    (_WELLS, "Reservoir pressure and model evidence; completion design", "An authoritative subsurface or well-file source."),
    (_FIELD, "Validated operating envelopes", "Envelopes validated by the control-system owner; alarm limits and IOWs are not that."),
    (_FIELD, "Control loop performance and tuning", "Setpoint, controller output and mode points, with loop parameters."),
    (_MAINT, "Vibration spectra (bearing defect frequencies, 1x / 2x running speed)",
     "A condition-monitoring source with spectra or waveforms and bearing geometry; overall vibration is checked."),
    (_MAINT, "Curve and map comparison for machines without OEM data",
     "OEM pump curves for those models and compressor maps for those machines (a datasheet Claude reads counts)."),
    (_WORK, "Schedule-conflict and crew resource analysis", "A planning system with crews, calendars and resource loading."),
    (_WORK, "Vendor, transport and site-access constraints; material reservations",
     "Procurement and logistics sources; SAP stock on hand alone does not show them."),
    (_HSE, "Hazard and barrier status", "An authoritative barrier or safety-case register."),
    (_HSE, "Environmental and regulatory obligation tracking", "Permits and obligations from an authoritative register."),
    (_HSE, "Emergency response procedures", "Approved emergency response plans; the assistant only supports with information."),
    (_ASSET, "Commercial reconciliation: sales, nominations, liftings and entitlements",
     "The commercial system's sales and nomination records; production accounting volumes and the cost ledger are "
     "reconciled (accounting_reconciliation, unit_cost)."),
)
