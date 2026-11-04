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

ANTHROPIC_API_KEY is the same key `fuzer llm-link` uses. The models and efforts are settings (#131):
agents.model and agents.effort for the orchestrator, agents.specialist_model (empty: the orchestrator's) and
agents.specialist_effort, and agents.fallbacks for Claude's server-side refusal fallback. Every answer reports
its calls, tokens and cost per model.
"""

import asyncio
import functools
import logging
import time
import warnings
from contextvars import ContextVar
from pathlib import Path

from ..core import models, settings

SKILLS = Path(__file__).parent / "skills"
DEFAULT_MODEL = models.DEFAULT_MODEL
MAX_OUTPUT_TOKENS = 16_000  # under the SDK's non-streaming limit; answers are a few thousand at most
CONTEXT_WINDOW = 1_000_000

# What one question did, across every agent working on it (asyncio copies context into the
# specialists' tasks, so their tool calls and model calls land here too).
_trace: ContextVar[list | None] = ContextVar("fuzer_agent_trace", default=None)


def note(kind: str, **data) -> dict:
    """Add an entry to the current question's trace (if any); returned so the caller can add to it."""
    entry = {"kind": kind, "at": round(time.time(), 2), **data}
    if (t := _trace.get()) is not None:
        t.append(entry)
    return entry


def __getattr__(name: str):
    """TOOLS (each agent's tool names) comes from the capability registry (#133): each tool declares its
    agents where it is defined in mcp_server."""
    if name == "TOOLS":
        from .capabilities import tools_by_agent

        return tools_by_agent()
    raise AttributeError(name)


ORCHESTRATOR = """\
You are the operations assistant for an oil and gas asset. Engineers, supervisors and planners ask you
about the plant: troubleshooting, production, constraints, wells, reliability, maintenance work and
turnarounds. Fuzer is the asset's data graph: SAP, PI, Energy Components, P&IDs, operator logs and
documents, merged and linked. Its tools are your only source of facts.

Answer simple lookups (what is X, where is it, what's connected to it) yourself. Hand domain work to
your specialists, and when a question spans domains, start them together and combine their findings
into one answer:
- troubleshooter: a unit or incident: what happened, what the trends did, causes, checks to make.
- production: production and efficiency, deferment, what limits throughput, well performance, gas lift
  (the lift gas network, allocation between wells, shortfalls).
- maintenance: bad actors and failure modes, inspections and PM compliance, work order backlog,
  turnaround scope and long-lead spares.
Give a specialist a self-contained task: the question, the entities and dates you already know.

Specialists can make interactive charts and return their `[[figure:<id>]]` markers. Keep the markers
that help the reader in your answer, each on its own line next to the text about it; drop the rest.
Ask for a chart when one would answer faster than words (a decline, a trend, a ranking).

The user may attach images (a PI Vision screenshot, a photo of a vibration spectrum, a datasheet page).
Read them yourself and pass what matters to a specialist in words. Values read off an image are
approximate: say which ones came from the image, and check them against Fuzer's data where it has any.

A question may come with facts "Known from earlier conversations": things engineers and the assistant
established before, about the entities the question names. Use them, check them against the data, and
cite each one you rely on as `fact:<id>` (learned from a conversation), never as a source record. Call
`recall` for what was learned about other entities that come up.

Load the `answering` skill before writing your final answer."""

SPECIALISTS = {
    "troubleshooter": ("Troubleshoots one unit or incident: timeline, process values, similar past incidents, "
                       "lessons, manual sections, and checks to make.",
                       "You troubleshoot one unit or incident on an oil and gas asset using Fuzer's tools. Load the "
                       "`troubleshooting` skill first."),
    "production": ("Production surveillance and optimisation, production efficiency and deferment, constraints, "
                   "production engineering (wells, well tests) and gas lift (network, allocation, shortfalls).",
                   "You are a production engineer for an oil and gas asset, working from Fuzer's tools. Load the "
                   "skill that fits the task first: production-surveillance, production-efficiency, "
                   "constraint-management, back-pressure, production-engineering or gas-lift."),
    "maintenance": ("Reliability, maintenance and inspection: bad actors, MTBF, failure modes, PM and inspection "
                    "compliance, work order backlog, turnaround scope and spares.",
                    "You are a maintenance and reliability engineer for an oil and gas asset, working from Fuzer's "
                    "tools. Load the skill that fits the task first: reliability, work-orders or turnarounds."),
}
REPORT = ("Report back to the orchestrator, not the end user: findings first, with numbers, dates and the Fuzer id "
          "or source record behind each fact, then what you could not find. Keep it under 400 words. If you made "
          "charts, include each `[[figure:<id>]]` marker on its own line with one sentence on what it shows.")


def _tool(name: str, agent: str):
    """A Fuzer MCP tool function as an agent tool: same name, description and parameters, run in a
    thread so specialists working at the same time don't block each other on the database."""
    from agent_framework import tool

    from . import mcp_server

    fn = getattr(mcp_server, name)

    @functools.wraps(fn)
    async def run(**kwargs):
        entry = note("tool", agent=agent, tool=name, args={k: v for k, v in kwargs.items() if v is not None})
        started = time.perf_counter()
        try:
            return _content(await asyncio.to_thread(fn, **kwargs))
        finally:
            entry["seconds"] = round(time.perf_counter() - started, 2)

    return tool(run, name=name, description=fn.__doc__, approval_mode="never_require")


def _content(result):
    """MCP images (look_at_figure) as image content Claude sees; anything else as it is."""
    from agent_framework import Content
    from mcp.server.mcpserver.utilities.types import Image

    if isinstance(result, list) and any(isinstance(x, Image) for x in result):
        return [Content.from_data(x.data, x._mime_type) if isinstance(x, Image) else Content.from_text(str(x))
                for x in result]
    return result


def _options(effort: str) -> dict:
    # cache_control: automatic prompt caching of tools + system + history, re-sent on every turn
    opts = {"max_tokens": MAX_OUTPUT_TOKENS, "output_config": {"effort": effort}, "cache_control": {"type": "ephemeral"}}
    if settings.get(None, "agents.fallbacks"):
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
    from ..core.pricing import cost

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
    betas = ["server-side-fallback-2026-07-01"] if settings.get(None, "agents.fallbacks") else []
    return AnthropicClient(model=model or settings.get(None, "agents.model"),
                           anthropic_client=anthropic_client, additional_beta_flags=betas)


def _agent(chat, name: str, instructions: str, effort: str, description: str | None = None, **kw):
    from agent_framework import SkillsProvider, create_harness_agent

    from .capabilities import tools_by_agent

    # Our own markdown skills (no scripts), so loading them needs no approval.
    skills = SkillsProvider.from_paths([SKILLS / name, SKILLS / "shared"], script_extensions=(),
                                       disable_load_skill_approval=True,
                                       disable_read_skill_resource_approval=True)
    return create_harness_agent(
        chat, name=name, description=description, agent_instructions=instructions,
        tools=[_tool(t, name) for t in tools_by_agent()[name]], skills_provider=skills,
        max_context_window_tokens=CONTEXT_WINDOW, max_output_tokens=MAX_OUTPUT_TOKENS,
        disable_web_search=True, disable_file_memory=True, disable_todo=True, disable_mode=True,
        default_options=_options(effort), **kw)


def team(chat=None, effort: str | None = None, specialist_effort: str | None = None, specialist_chat=None,
         history=None):
    """The orchestrator agent, with the specialists as its background agents. The specialists use their
    own model when FUZER_AGENT_SPECIALIST_MODEL is set (or specialist_chat is given). `history` (a MAF
    HistoryProvider, e.g. history.PostgresHistory) keeps the orchestrator's conversations; without one they
    live in the session. The specialists' tasks are one question long, so their history stays in memory."""
    from agent_framework._feature_stage import ExperimentalWarning

    warnings.filterwarnings("ignore", category=ExperimentalWarning)  # background agents: pinned MAF version
    for name in ("agent_framework", "httpx", "httpx2"):  # every tool call and request is logged at INFO
        logging.getLogger(name).setLevel(logging.WARNING)
    chat = chat or client()
    specialist_model = settings.get(None, "agents.specialist_model")
    specialist_chat = specialist_chat or (client(specialist_model) if specialist_model else chat)
    effort = effort or settings.get(None, "agents.effort")
    specialist_effort = specialist_effort or settings.get(None, "agents.specialist_effort")
    specialists = [_agent(specialist_chat, name, f"{instructions}\n\n{REPORT}", specialist_effort, description)
                   for name, (description, instructions) in SPECIALISTS.items()]
    return _agent(chat, "orchestrator", ORCHESTRATOR, effort, "Fuzer operations assistant",
                  background_agents=specialists, background_agents_wait_timeout_seconds=600,
                  **({"history_provider": history} if history is not None else {}))


def final_text(response) -> str:
    """The answer: the orchestrator's text after its last tool call (not its narration along the way)."""
    msgs = response.messages
    tooled = [i for i, m in enumerate(msgs)
              if any(getattr(c, "type", None) in ("function_call", "function_result") for c in m.contents)]
    tail = msgs[(tooled[-1] + 1) if tooled else 0:]
    return "\n\n".join(m.text for m in tail if str(m.role) == "assistant" and m.text).strip() or response.text


async def _forget_tasks(agent, session) -> None:
    """Drop the specialists' tasks once a question is answered (their reports are already in the history).
    MAF otherwise lists them in a status message it puts ahead of the history on the next turn, and Claude
    rejects the history's thinking blocks once anything comes before them."""
    from agent_framework import BackgroundAgentsProvider

    for p in agent.context_providers:
        if isinstance(p, BackgroundAgentsProvider):
            await p.release_session(session)
            if state := session.state.get(p.source_id):
                state["tasks"] = []


def ask_sync(question: str, **kw) -> dict:
    """`ask` from synchronous code (the SRE hand-off, the workflow worker, the eval): one event loop per call,
    with the team built inside it and its HTTP clients closed before the loop ends. A client left open is
    closed later by garbage collection on a dead loop ("Event loop is closed"), and one reused across
    loops holds connections from the old one."""
    from contextlib import AsyncExitStack

    from anthropic import AsyncAnthropic

    async def go():
        async with AsyncExitStack() as stack:
            def chat(model=None):
                ac = AsyncAnthropic()
                stack.push_async_callback(ac.close)
                return client(model, anthropic_client=ac)

            spec = settings.get(None, "agents.specialist_model")
            agent = team(chat=chat(), specialist_chat=chat(spec) if spec else None)
            return await ask(question, agent=agent, **kw)

    return asyncio.run(go())


def _remembered(question: str) -> dict | None:
    """The facts learned in earlier conversations about the entities this question names (memory.context)."""
    from ..core import db
    from ..graph import queries
    from . import memory, mcp_server

    try:
        with db.connect() as conn:
            ids = [m["fuzer_id"] for m in queries.link_text(conn, mcp_server.PACK, question) if m["fuzer_id"]]
            return memory.context(conn, list(dict.fromkeys(ids))) if ids else None
    except Exception as e:  # memory is a help, never a reason not to answer
        logging.getLogger(__name__).warning("recall failed: %s", e)
        return None


async def ask(question: str, agent=None, session=None, trace: list | None = None,
              images: list[tuple[str, bytes]] | None = None, recall: bool = True) -> dict:
    """One question to the team: the answer, what each agent did (tools, specialist tasks, model calls)
    and the tokens and cost across all of them. Pass `session` to continue a conversation and `trace`
    (a list) to watch progress while it runs; `images` (media type, bytes) go with the question. With
    `recall`, facts learned in earlier conversations about the entities the question names go with it."""
    from agent_framework import Content, Message

    agent = agent or team()
    session = session or agent.create_session()
    trace = [] if trace is None else trace
    token = _trace.set(trace)
    started = time.time()
    remembered = await asyncio.to_thread(_remembered, question) if recall else None
    try:
        # The remembered facts go in the user's turn, after the history, so the prefix Claude's thinking blocks
        # are bound to never changes. Images: a screenshot, a photo of a spectrum, a datasheet page.
        if images or remembered:
            question = Message(role="user", contents=[Content.from_text(question), *(
                Content.from_data(data, media_type) for media_type, data in images or [])] + (
                [Content.from_text(remembered["text"])] if remembered else []))
        response = await agent.run(question, session=session)
    finally:
        _trace.reset(token)
        await _forget_tasks(agent, session)
    return {"answer": final_text(response), "seconds": round(time.time() - started, 1), "usage": usage(trace),
            "specialists": sorted({t["agent"] for t in trace if t["kind"] == "tool" and t["agent"] != "orchestrator"}),
            "tools": [{k: t[k] for k in ("agent", "tool", "args", "seconds") if k in t} for t in trace if t["kind"] == "tool"],
            "recalled": remembered["ids"] if remembered else [], "session": session}
