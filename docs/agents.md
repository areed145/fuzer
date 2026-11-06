# Agents and capabilities

The operations assistant, its domain specialists, persona coverage, skills and tools.

## Operations assistant
A team of agents built with `create_harness_agent` from Microsoft Agent Framework (MAF), using the
provider selected by `agents.provider` (`anthropic` by default; `openai`, `azure_openai` and `ollama`
are also supported):

- **Orchestrator.** Answers simple lookups and routes domain work to specialists as MAF
  *background agents*, which may run at the same time. It synthesizes cited findings using its
  `answering` skill and never gives plant-operating instructions.
- **Seven domain specialists.** Production & Surveillance; Wells & Subsurface; Field Operations &
  Facilities; Maintenance & Integrity; Work Management & Supply Chain; HSE & Incident Management;
  Asset Performance & Reporting. The complete persona-to-domain, skill ownership, tool, evidence
  prerequisite, output, limitation and human-decision matrix is generated in
  [capabilities](capabilities.md) from the central registry.

  Skills are reused across roles when their evidence and methods fit; a role does not imply its
  sources are configured. All specialists are read-only and report unknown or unsupported inputs.
- **Tools** are the MCP server's functions called in-process, so outside MCP clients see the same
  tool surface. They run in threads so concurrent specialists don't block each other. Asset-wide
  questions use `operations.py`: production summary and PE, deferment Pareto, constraints with
  trend and cascade causes, well-test performance, bad actors and MTBF, work order backlog, PM and
  inspection compliance, and turnaround scope. All are read-only, compact, and dated from the data's
  latest daily report.
- **Provider settings.** `agents.model` selects the orchestrator model and
  `agents.specialist_model` for the specialists; effort is `high` for the orchestrator and `medium`
  for the specialists. Each answer reports calls, tokens and cost per model, as the API reports the
  serving model; a model missing from `pricing.py` is flagged as unpriced. Anthropic prompt caching
  and server-side refusal fallback are provider-specific; `agents.fallbacks` controls the latter.
  Install `uv sync --extra openai` to use OpenAI, Azure OpenAI or Ollama. `agents.base_url` sets a
  custom endpoint and `agents.api_key` stores an environment-variable reference, not a key value.
  Ollama defaults to `http://localhost:11434/v1` and needs no API key. Anthropic's adapter uses its
  default adaptive thinking; supported OpenAI reasoning models receive the configured effort. Web search and file memory are
  off. The agent framework is a core dependency. `agent-framework-anthropic` still declares `anthropic<2`, so a uv override keeps Fuzer's 2.x
  SDK; the tests run the adapter against it.
- **Surfaces.** `fuzer ask` / `fuzer chat`, and the Ask page. The page uses `/assistant/ask`, which
  starts a background job, and polls `/assistant/jobs/{id}` to show which agent is calling which
  tool. Every answer is stored in `assistant_answer` with its tools, tokens and cost, and engineers
  rate it through `feedback` (`answer:<id>`).
- **Conversations** are kept in Postgres (`agents/history.py`, #93), so they survive a restart and a
  follow-up can carry on after one. A MAF history provider writes each message to `assistant_message`
  as MAF serialises it, in a `json` column (not `jsonb`) so provider-specific history comes back byte for
  byte, which a follow-up needs. Attached images are stored once per conversation in `assistant_image`
  and referenced from the messages. `assistant_conversation` holds each conversation's asker, title and
  dates. The Ask page lists your recent conversations and reopens one (`/assistant/conversations`).
  The setting `assistant.retention_days` (0 keeps everything) removes conversations idle that long,
  with their messages, images and answers.
- **Memory** (`agents/memory.py`, #93) follows Graphiti / Zep (arXiv 2501.13956) in Postgres. Answers in
  `assistant_answer` are the episodes. When an answer is rated useful (after the rating's response), or an
  engineer marks a statement "Remember this", one low-effort Claude call extracts at most eight short facts
  into `assistant_fact`. Each fact has its entities, the period it holds in the plant (`valid_from` /
  `valid_to`, often open), `learned_at`, and its sources (the answer and the records it cited). The same call
  sees the current facts on the answer's entities and names the ones each new fact replaces. A replaced fact
  that shares an entity with the new one is ended (`status` 'ended', `valid_to`, `invalidated_by`), never
  deleted. Facts that read as reusable lessons also go to the lessons review queue as suggestions. Rating
  an answer not useful drops the facts learned from it that no engineer has touched.
- **Recall.** For the entities a question names (`link_text`), the current facts about them and their
  units go in a "Known from earlier conversations" block. The block is added to the user's turn after the
  question, so it never changes the history prefix that Claude's thinking blocks are bound to. Answers
  cite facts as `fact:<id>`, learned from a conversation. `recall(ref)` gives more, and
  `assistant_answer.recalled` records which facts each question got. The entity page's "What the
  assistant remembers" lets engineers correct, end or delete facts (`/assistant/facts`). Everyone
  sees everything for now; group access follows #29.
- **Quality.** `fuzer eval --agents` asks the questions in `answer_keys/assistant.json` (one per
  domain plus one across domains, on Okan). It scores the facts each answer must name, citations,
  absence of operating instructions, and routing to specialists, with cost and time per answer.
  Tests drive the whole team with a scripted fake SDK client (no network).
- **Charts.** Each chart is a tool (`plot_*`, `src/fuzer/plots/`): the server reads the data
  (measurements, the time-series provider, operations queries), runs any fit (Arps decline after the
  last step change, the gas lift curve with decline taken out and failed-valve tests left out, CML
  loss rates, Weibull by median ranks, Crow-AMSAA, Pearson and lagged correlation) and stores a Plotly
  figure in `assistant_figure`. The agent gets an id, a `[[figure:<id>]]` marker and a summary of a few
  hundred tokens with the exact numbers, never the arrays; specialists pass markers up and the
  orchestrator keeps the useful ones. Specs come only from server code, from allow-listed trace types,
  and are checked again before storing (no HTML beyond plain formatting, no images, scripts or links;
  20k points at most). The browser loads plotly.js (cartesian bundle, its own 1.4 MB chunk) only when
  an answer has a chart and applies the page theme. `plot_custom` takes small inline data the agent
  worked out, labelled as assistant-entered. `look_at_figure` renders a stored figure to PNG
  (matplotlib, no browser) as an image in the tool result, only when the summary isn't enough; the eval
  counts how often it's used. Users can attach up to four images to a question; values read off them
  are reported as approximate. Not yet: pump curves (no OEM curve data), choke Cv (no choke
  characteristics), vibration spectra (no condition-monitoring source), and process values that move
  together in the synthetic historian, so correlation and XY charts on the demo show how the
  generator shapes upsets rather than plant physics.

## Migration and coverage boundaries

The registry in `src/fuzer/agents/capabilities/__init__.py` owns the domain IDs, descriptions,
persona mapping, assigned skills, source prerequisites, expected outputs, constraints and
human-decision boundaries. Legacy capability declarations are kept behind a compatibility map:
former `troubleshooter` tools stay with Field Operations & Facilities, with selected incident evidence
also available to HSE; `production` tools stay with Production & Surveillance and Wells & Subsurface,
with selected constraint tools shared to Field Operations & Facilities and high-level summaries to
Asset Performance & Reporting; `maintenance` tools stay with Maintenance & Integrity, with selected
backlog/readiness and summary tools shared to Work Management & Supply Chain and Asset Performance &
Reporting. No new data source or operational authority is implied by these assignments.

The old persisted specialist IDs (`troubleshooter`, `production`, `maintenance`) remain readable in
conversation history and UI labels. Existing workflow agent-role tokens are also dispatched through
compatibility aliases so in-flight work keeps functioning. New assistant specialist IDs are domain
slugs; tools continue to be declared once and MCP-only workflow mutation tools remain unavailable to
assistant specialists.

The cross-cutting Data Scientist / Operations Research Analyst persona uses existing domain analysis
methods and plots, not an additional agent. Data Steward / Platform Administrator is outside the
operational roster and remains in platform administration, Sources/Settings/Jobs and governance
workflows. No platform permission is granted by persona coverage.

Only current data-backed methods are enabled: dated production and well-test trends, deferment and
constraint summaries, supported gas-lift/back-pressure evidence, incidents and event timelines,
maintenance/reliability/backlog/compliance/turnaround views, and plots backed by those records.
Every numeric claim still needs loaded evidence with units, time range and provenance. Synthetic
time-series data is labeled as synthetic; absence of OEM curves, validated choke characteristics,
vibration sources, authoritative environmental/regulatory records, or authoritative commercial/
accounting/cost sources remains a limitation. Agents can organize evidence and propose questions,
not authorize or execute field, control, maintenance, HSE, emergency, schedule, resource or
commercial decisions.

### Adding a persona, skill or capability

Add a persona to its owning `AgentDefinition` and assign an existing skill path in the capability
registry; update the prerequisite, output, constraint and human-decision fields at the same time.
Skills are `SKILL.md` directories and must be assigned explicitly to each domain that can use them.
Add tool ownership only to the MCP capability decorator for the domain that can act on its evidence.
Then run the capability and agent tests and regenerate `docs/capabilities.md` with
`uv run python -m fuzer.agents.capabilities > docs/capabilities.md`. Add new analysis only when
current domain data and validation support it; document missing prerequisites rather than
substituting synthetic or inferred facts.

## Views and agent tools

**Charts:**
- `plot_propagation(upset)`: an SRE upset's units on a timeline of when each was first detected, with
  the plant model's links between them and the likely first cause starred. Train C's trip starts at
  20-V-2001.
- `plot_operating_window(point)`: the value against each limit, and the minutes outside per day.
- `plot_loss_waterfall(period)`: oil potential, then planned losses, unplanned losses by reason, losses
  no downtime record explains, then actual, priced with the pack's oil price.
- `plot_score_breakdown(ref)`: the #100 components, urgency and effort for one item.
- `plot_flare(period)`: daily flare from DAY001 and its CO2 per barrel, estimated at 2 t CO2 per kSm3.
  Fuel gas and venting aren't in DAY001.

**Tools (`cases.py`):**
- `compare_periods(ref, before, after)`: each point's median in two windows and the change. Welch's t,
  with each window's sample cut to its effective size for autocorrelation, says whether it is
  significant. It closes the loop on "did the fix work".
- `similar_cases(ref)`: past incidents like this one (an incident, an upset's incident, or a unit's
  latest), with the work orders done for each and whether the fix held (no incident on the unit within
  90 days).

## Tools

Every tool and the agents that use it: [capabilities](capabilities.md).
