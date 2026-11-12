"""The operations assistant: a team of Claude agents on Microsoft Agent Framework's harness agent.

    uv run fuzer ask "Why does lift gas keep running short, and what should go in the next turnaround?"

An orchestrator talks to the user, answers simple lookups itself and routes domain work to seven
specialists, which can run at the same time (MAF background agents). The shared capability registry
defines their persona coverage, skills, prerequisites, constraints and human-decision boundaries.

Each agent gets its registered Fuzer tools (the MCP server's tool functions, called in-process; all
read-only), the skills assigned to it (names and descriptions up front, body loaded when needed) and a
prompt built from its catalog entry, so its evidence limits and human decisions reach the model.
Nothing here can act on the plant.

The agent provider, models and efforts are settings (#131):
agents.model and agents.effort for the orchestrator, agents.specialist_model (empty: the orchestrator's) and
agents.specialist_effort, and agents.fallbacks for Claude's server-side refusal fallback. Every answer reports
its calls, tokens and cost per model.
"""

import asyncio
import functools
import logging
import os
import time
import warnings
from contextvars import ContextVar
from pathlib import Path

from ..core import llm, models, settings
from .capabilities import AGENT_BY_ID, AGENT_CATALOG

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
about the plant. Fuzer is the asset's data graph; its linked records and tools are your only source of
facts. Route domain questions to the relevant specialists below, and start independent specialists
together when a question spans domains. Each specialist is read-only and has a narrow evidence boundary.

Domain routing:
[SPECIALIST_ROUTING]

Pick by what is being asked, not by which words appear. "Not for" names where a neighbouring question belongs.
Answer simple lookups (what is X, where is it, what's connected to it) yourself. Hand domain work to
your specialists, and combine their findings into one answer. A specialist may report missing evidence;
do not route a question as if the catalog implies the required source or model exists.

You and your specialists cannot create, assign or change work. When someone asks for work to be created
or sent to a queue, gather what the case needs, then call draft_work_item and put its marker on a line of
its own. Say plainly that it is only a draft: nothing exists until the person confirms it on the page.
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
`recall` for what was learned about other entities that come up. Facts that have ended are history: never
cite them; if one matters, say what replaced it and cite that.

Load the `answering` skill before writing your final answer.

[BOUNDARIES]"""

SPECIALISTS = {agent.id: (agent.description, agent.prompt) for agent in AGENT_CATALOG if agent.id != "orchestrator"}
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


def _options(effort: str, model: str | None = None) -> dict:
    # cache_control: automatic prompt caching of tools + system + history, re-sent on every turn
    selected = settings.get(None, "agents.provider")
    if selected != "anthropic":
        opts = {"max_tokens": MAX_OUTPUT_TOKENS}
        model = (model or settings.get(None, "agents.model")).lower()
        if any(n in model for n in ("o1", "o3", "o4", "gpt-5")):
            opts["reasoning_effort"] = effort if effort in ("low", "medium", "high") else "high"
        return opts
    opts = {"max_tokens": MAX_OUTPUT_TOKENS, "output_config": {"effort": effort},
            "cache_control": {"type": "ephemeral"}}
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
                 stop=response.stop_reason, provider="anthropic")
        return response

    messages.create = metered
    return anthropic_client


def _metered_openai(openai_client):
    """Record OpenAI-compatible chat-completion usage in the current question's trace."""
    completions = openai_client.chat.completions
    create = completions.create

    async def metered(*args, **kwargs):
        response = await create(*args, **kwargs)
        if (u := getattr(response, "usage", None)):
            prompt = getattr(u, "prompt_tokens", 0) or 0
            details = getattr(u, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) or 0
            note("model", model=response.model, input=max(0, prompt - cached),
                 output=getattr(u, "completion_tokens", 0) or 0,
                 cache_read=cached, cache_write=0, stop="end_turn",
                 provider=settings.get(None, "agents.provider"))
        return response

    completions.create = metered
    return openai_client


def usage(trace: list[dict]) -> dict:
    """Token totals and cost for one question, overall and per model (the model the API says served
    each call, so a fallback or a misconfigured model shows up)."""
    from ..core.pricing import cost

    calls = [t for t in trace if t["kind"] == "model"]
    keys = ("input", "output", "cache_read", "cache_write")
    models: dict[str, dict] = {}
    for c in calls:
        provider = c.get("provider", "anthropic")
        key = c["model"] if provider == "anthropic" else f"{provider}:{c['model']}"
        m = models.setdefault(key, {"provider": provider, "calls": 0, **{k: 0 for k in keys}, "cost_usd": 0.0})
        m["calls"] += 1
        for k in keys:
            m[k] += c[k]
        usd, known = cost(c["model"], c["input"], c["output"], c["cache_read"], c["cache_write"], provider)
        m["cost_usd"] += usd
        if not known:
            m["unpriced"] = True
    for m in models.values():
        m["cost_usd"] = round(m["cost_usd"], 4)
    return {"model_calls": len(calls), **{k: sum(c[k] for c in calls) for k in keys},
            "cost_usd": round(sum(m["cost_usd"] for m in models.values()), 4), "models": models}


def client(model: str | None = None, anthropic_client=None, openai_client=None):
    """MAF chat client selected by the agents.provider setting."""
    selected, endpoint, key = llm._config("agents")
    model = model or settings.get(None, "agents.model")
    if selected == "anthropic":
        from agent_framework.anthropic import AnthropicClient

        anthropic_client = _metered(anthropic_client or llm.async_client("agents"))
        betas = ["server-side-fallback-2026-07-01"] if settings.get(None, "agents.fallbacks") else []
        chat = AnthropicClient(model=model, anthropic_client=anthropic_client, additional_beta_flags=betas)
        chat._fuzer_model = model
        return chat
    from agent_framework_openai import OpenAIChatCompletionClient

    openai_client = _metered_openai(openai_client or llm.async_client("agents"))
    kwargs = {"model": model, "async_client": openai_client}
    if key:
        kwargs["api_key"] = key
    elif selected == "ollama":
        kwargs["api_key"] = "ollama"
    if endpoint:
        if selected == "azure_openai" and "/openai/v1" not in endpoint:
            kwargs["azure_endpoint"] = endpoint
            kwargs["api_version"] = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        else:
            kwargs["base_url"] = endpoint
    chat = OpenAIChatCompletionClient(**kwargs)
    chat._fuzer_model = model
    return chat


def _agent(chat, name: str, instructions: str, effort: str, description: str | None = None,
           model: str | None = None, **kw):
    from agent_framework import SkillsProvider, create_harness_agent

    from .capabilities import tools_by_agent

    # Our own markdown skills (no scripts), so loading them needs no approval.
    # A skill's live published version (P6.4b) loads in place of the shipped folder.
    from .skill_versions import roots

    live = roots()
    skills = SkillsProvider.from_paths([live.get(path, SKILLS / path) for path in AGENT_BY_ID[name].skill_paths],
                                       script_extensions=(), disable_load_skill_approval=True,
                                       disable_read_skill_resource_approval=True)
    return create_harness_agent(
        chat, name=name, description=description, agent_instructions=instructions,
        tools=[_tool(t, name) for t in tools_by_agent()[name]], skills_provider=skills,
        max_context_window_tokens=CONTEXT_WINDOW, max_output_tokens=MAX_OUTPUT_TOKENS,
        disable_web_search=True, disable_file_memory=True, disable_todo=True, disable_mode=True,
        default_options=_options(effort, model), **kw)


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
    orchestrator_model = getattr(chat, "_fuzer_model", settings.get(None, "agents.model"))
    specialist_model = settings.get(None, "agents.specialist_model")
    specialist_chat = specialist_chat or (client(specialist_model) if specialist_model else chat)
    specialist_chat_model = getattr(specialist_chat, "_fuzer_model", specialist_model or orchestrator_model)
    effort = effort or settings.get(None, "agents.effort")
    specialist_effort = specialist_effort or settings.get(None, "agents.specialist_effort")
    specialists = [_agent(specialist_chat, name, f"{instructions}\n\n{REPORT}", specialist_effort, description,
                          model=specialist_chat_model)
                   for name, (description, instructions) in SPECIALISTS.items()]
    routing = "\n".join(f"- {agent.id}: {agent.description} Not for: {agent.not_for}"
                        for agent in AGENT_CATALOG if agent.id != "orchestrator")
    prompt = ORCHESTRATOR.replace("[SPECIALIST_ROUTING]", routing).replace(
        "[BOUNDARIES]", AGENT_BY_ID["orchestrator"].boundaries)
    return _agent(chat, "orchestrator", prompt, effort, "Fuzer operations assistant",
                  model=orchestrator_model, background_agents=specialists,
                  background_agents_wait_timeout_seconds=600,
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

    async def go():
        async with AsyncExitStack() as stack:
            def chat(model=None):
                from ..core import llm

                ac = llm.async_client("agents")
                stack.push_async_callback(ac.close)
                return (client(model, anthropic_client=ac) if settings.get(None, "agents.provider") == "anthropic"
                        else client(model, openai_client=ac))

            spec = settings.get(None, "agents.specialist_model")
            agent = team(chat=chat(), specialist_chat=chat(spec) if spec else None)
            return await ask(question, agent=agent, **kw)

    return asyncio.run(go())


def _remembered(question: str) -> dict | None:
    """The facts learned in earlier conversations about the entities this question names (memory.context)."""
    from ..core import db
    from ..graph import queries
    from . import mcp_server, memory

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
