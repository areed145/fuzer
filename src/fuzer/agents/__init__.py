"""The operations assistant: a team of Claude agents on Microsoft Agent Framework's harness agent.

    uv run fuzer ask "Why does lift gas keep running short, and what should go in the next turnaround?"

An orchestrator talks to the user, answers simple lookups itself and hands domain work to three
specialists, which it can run at the same time (MAF background agents):

- troubleshooter: one unit or incident: what happened, what the process values did, what to check.
- production: surveillance and optimisation, production efficiency, constraints, production engineering.
- maintenance: reliability and inspection, work orders and backlog, turnarounds.

Each agent gets the Fuzer tools its domain needs (the MCP server's tool functions, called in-process;
all read-only) and its skills (`skills/<agent>/*/SKILL.md`: names and descriptions up front, the
body loaded when the agent needs it).
Nothing here can act on the plant.

Settings: ANTHROPIC_API_KEY (the same key `fuzer llm-link` uses), FUZER_AGENT_MODEL (the orchestrator,
default claude-opus-5-5), FUZER_AGENT_SPECIALIST_MODEL (default: the orchestrator's), FUZER_AGENT_EFFORT
(orchestrator, default high), FUZER_AGENT_SPECIALIST_EFFORT (default medium), FUZER_AGENT_FALLBACKS=0 to
turn off Claude's server-side refusal fallback. Every answer reports its calls, tokens and cost per model.
"""

import asyncio
import functools
import logging
import os
import time
import warnings
from contextvars import ContextVar
from pathlib import Path

SKILLS = Path(__file__).parent / "skills"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_OUTPUT_TOKENS = 16_000  # under the SDK's non-streaming limit; answers are a few thousand at most
CONTEXT_WINDOW = 1_000_000

# What one question did, across every agent working on it (asyncio copies context into the
# specialists' tasks, so their tool calls and model calls land here too).
_trace: ContextVar[list | None] = ContextVar("fuzer_agent_trace", default=None)


def note(kind: str, **data) -> None:
    if (t := _trace.get()) is not None:
        t.append({"kind": kind, "at": round(time.time(), 2), **data})


COMMON = ["search", "get_entity", "link_text", "get_ontology", "documents_for", "search_knowledge"]
TOOLS = {
    "orchestrator": COMMON + ["get_stats", "get_incidents", "trace_flow"],
    "troubleshooter": COMMON + ["troubleshooting_brief", "incident_detail", "get_incidents", "get_events",
                                "process_values", "lessons_learned", "maintenance_history", "equipment_history",
                                "equipment_changes", "spare_parts", "downtime", "trace_flow", "record_history"],
    "production": COMMON + ["production_summary", "deferment_pareto", "production_constraints", "well_performance",
                            "downtime", "get_events", "get_incidents", "process_values", "trace_flow"],
    "maintenance": COMMON + ["bad_actors", "work_order_backlog", "maintenance_compliance", "turnaround_scope",
                             "maintenance_history", "equipment_history", "spare_parts", "lessons_learned",
                             "list_members", "get_incidents", "deferment_pareto"],
}

ORCHESTRATOR = """\
You are the operations assistant for an oil and gas asset. Engineers, supervisors and planners ask you
about the plant: troubleshooting, production, constraints, wells, reliability, maintenance work and
turnarounds. Fuzer is the asset's data graph: SAP, PI, Energy Components, P&IDs, operator logs and
documents, merged and linked. Its tools are your only source of facts.

Answer simple lookups (what is X, where is it, what's connected to it) yourself. Hand domain work to
your specialists, and when a question spans domains, start them together and combine their findings
into one answer:
- troubleshooter: a unit or incident: what happened, what the trends did, causes, checks to make.
- production: production and efficiency, deferment, what limits throughput, well performance.
- maintenance: bad actors and failure modes, inspections and PM compliance, work order backlog,
  turnaround scope and long-lead spares.
Give a specialist a self-contained task: the question, the entities and dates you already know.

Load the `answering` skill before writing your final answer."""

SPECIALISTS = {
    "troubleshooter": ("Troubleshoots one unit or incident: timeline, process values, similar past incidents, "
                       "lessons, manual sections, and checks to make.",
                       "You troubleshoot one unit or incident on an oil and gas asset using Fuzer's tools. Load the "
                       "`troubleshooting` skill first."),
    "production": ("Production surveillance and optimisation, production efficiency and deferment, constraints, "
                   "and production engineering (wells, well tests, gas lift).",
                   "You are a production engineer for an oil and gas asset, working from Fuzer's tools. Load the "
                   "skill that fits the task first: production-surveillance, production-efficiency, "
                   "constraint-management or production-engineering."),
    "maintenance": ("Reliability, maintenance and inspection: bad actors, MTBF, failure modes, PM and inspection "
                    "compliance, work order backlog, turnaround scope and spares.",
                    "You are a maintenance and reliability engineer for an oil and gas asset, working from Fuzer's "
                    "tools. Load the skill that fits the task first: reliability, work-orders or turnarounds."),
}
REPORT = ("Report back to the orchestrator, not the end user: findings first, with numbers, dates and the Fuzer id "
          "or source record behind each fact, then what you could not find. Keep it under 400 words.")


def _tool(name: str, agent: str):
    """A Fuzer MCP tool function as an agent tool: same name, description and parameters, run in a
    thread so specialists working at the same time don't block each other on the database."""
    from agent_framework import tool

    from .. import mcp_server

    fn = getattr(mcp_server, name)

    @functools.wraps(fn)
    async def run(**kwargs):
        note("tool", agent=agent, tool=name, args={k: v for k, v in kwargs.items() if v is not None})
        return await asyncio.to_thread(fn, **kwargs)

    return tool(run, name=name, description=fn.__doc__, approval_mode="never_require")


def _options(effort: str) -> dict:
    # cache_control: automatic prompt caching of tools + system + history, re-sent on every turn
    opts = {"max_tokens": MAX_OUTPUT_TOKENS, "output_config": {"effort": effort}, "cache_control": {"type": "ephemeral"}}
    if os.environ.get("FUZER_AGENT_FALLBACKS", "1") != "0":
        opts["fallbacks"] = "default"  # on a policy decline, the API re-runs the request on a fallback model
    return opts


def _metered(anthropic_client):
    """Record each response's model and token usage in the current question's trace."""
    messages = anthropic_client.beta.messages
    create = messages.create

    async def metered(*args, **kwargs):
        response = await create(*args, **kwargs)
        if not kwargs.get("stream") and (u := getattr(response, "usage", None)):
            note("model", model=response.model, input=u.input_tokens, output=u.output_tokens,
                 cache_read=u.cache_read_input_tokens or 0, cache_write=u.cache_creation_input_tokens or 0,
                 stop=response.stop_reason)
        return response

    messages.create = metered
    return anthropic_client


def usage(trace: list[dict]) -> dict:
    """Token totals and cost for one question, overall and per model (the model the API says served
    each call, so a fallback or a misconfigured model shows up)."""
    from ..pricing import cost

    calls = [t for t in trace if t["kind"] == "model"]
    keys = ("input", "output", "cache_read", "cache_write")
    models: dict[str, dict] = {}
    for c in calls:
        m = models.setdefault(c["model"], {"calls": 0, **{k: 0 for k in keys}, "cost_usd": 0.0})
        m["calls"] += 1
        for k in keys:
            m[k] += c[k]
        usd, known = cost(c["model"], c["input"], c["output"], c["cache_read"], c["cache_write"])
        m["cost_usd"] += usd
        if not known:
            m["unpriced"] = True  # priced as Claude Opus 5.5; add the model to pricing.PRICES
    for m in models.values():
        m["cost_usd"] = round(m["cost_usd"], 4)
    return {"model_calls": len(calls), **{k: sum(c[k] for c in calls) for k in keys},
            "cost_usd": round(sum(m["cost_usd"] for m in models.values()), 4), "models": models}


def client(model: str | None = None, anthropic_client=None):
    """MAF's Anthropic chat client on the same key as the rest of Fuzer."""
    from agent_framework.anthropic import AnthropicClient
    from anthropic import AsyncAnthropic

    anthropic_client = _metered(anthropic_client or AsyncAnthropic())
    betas = ["server-side-fallback-2026-07-01"] if os.environ.get("FUZER_AGENT_FALLBACKS", "1") != "0" else []
    return AnthropicClient(model=model or os.environ.get("FUZER_AGENT_MODEL", DEFAULT_MODEL),
                           anthropic_client=anthropic_client, additional_beta_flags=betas)


def _agent(chat, name: str, instructions: str, effort: str, description: str | None = None, **kw):
    from agent_framework import SkillsProvider, create_harness_agent

    # Our own markdown skills (no scripts), so loading them needs no approval.
    skills = SkillsProvider.from_paths([SKILLS / name], script_extensions=(), disable_load_skill_approval=True,
                                       disable_read_skill_resource_approval=True)
    return create_harness_agent(
        chat, name=name, description=description, agent_instructions=instructions,
        tools=[_tool(t, name) for t in TOOLS[name]], skills_provider=skills,
        max_context_window_tokens=CONTEXT_WINDOW, max_output_tokens=MAX_OUTPUT_TOKENS,
        disable_web_search=True, disable_file_memory=True, disable_todo=True, disable_mode=True,
        default_options=_options(effort), **kw)


def team(chat=None, effort: str | None = None, specialist_effort: str | None = None, specialist_chat=None):
    """The orchestrator agent, with the specialists as its background agents. The specialists use their
    own model when FUZER_AGENT_SPECIALIST_MODEL is set (or specialist_chat is given)."""
    from agent_framework._feature_stage import ExperimentalWarning

    warnings.filterwarnings("ignore", category=ExperimentalWarning)  # background agents: pinned MAF version
    for name in ("agent_framework", "httpx", "httpx2"):  # every tool call and request is logged at INFO
        logging.getLogger(name).setLevel(logging.WARNING)
    chat = chat or client()
    specialist_model = os.environ.get("FUZER_AGENT_SPECIALIST_MODEL")
    specialist_chat = specialist_chat or (client(specialist_model) if specialist_model else chat)
    effort = effort or os.environ.get("FUZER_AGENT_EFFORT", "high")
    specialist_effort = specialist_effort or os.environ.get("FUZER_AGENT_SPECIALIST_EFFORT", "medium")
    specialists = [_agent(specialist_chat, name, f"{instructions}\n\n{REPORT}", specialist_effort, description)
                   for name, (description, instructions) in SPECIALISTS.items()]
    return _agent(chat, "orchestrator", ORCHESTRATOR, effort, "Fuzer operations assistant",
                  background_agents=specialists, background_agents_wait_timeout_seconds=600)


def final_text(response) -> str:
    """The answer: the orchestrator's text after its last tool call (not its narration along the way)."""
    msgs = response.messages
    tooled = [i for i, m in enumerate(msgs)
              if any(getattr(c, "type", None) in ("function_call", "function_result") for c in m.contents)]
    tail = msgs[(tooled[-1] + 1) if tooled else 0:]
    return "\n\n".join(m.text for m in tail if str(m.role) == "assistant" and m.text).strip() or response.text


async def ask(question: str, agent=None, session=None, trace: list | None = None) -> dict:
    """One question to the team: the answer, what each agent did (tools, specialist tasks, model calls)
    and the tokens and cost across all of them. Pass `session` to continue a conversation and `trace`
    (a list) to watch progress while it runs."""
    agent = agent or team()
    session = session or agent.create_session()
    trace = [] if trace is None else trace
    token = _trace.set(trace)
    started = time.time()
    try:
        response = await agent.run(question, session=session)
    finally:
        _trace.reset(token)
    return {"answer": final_text(response), "seconds": round(time.time() - started, 1), "usage": usage(trace),
            "specialists": sorted({t["agent"] for t in trace if t["kind"] == "tool" and t["agent"] != "orchestrator"}),
            "tools": [{k: t[k] for k in ("agent", "tool", "args")} for t in trace if t["kind"] == "tool"],
            "session": session}
