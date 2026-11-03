# Agents and capabilities

The operations assistant, its specialists and their tools.

## Operations assistant (Claude agents)
A team of agents built with `create_harness_agent` from Microsoft Agent Framework (MAF), calling Claude
through MAF's Anthropic client with the same key as the Claude readers (`src/fuzer/agents/`):

- **Orchestrator.** Answers lookups itself and hands domain work to specialists as MAF
  *background agents*, which run at the same time. It writes the final answer using its `answering`
  skill: a source on every fact, units and dates, gaps stated, and next steps. It never gives
  plant-operating instructions.
- **Specialists**, split by the data they work from rather than one agent per topic:
  - `troubleshooter`: one unit or incident.
  - `production`: surveillance and optimisation, efficiency and deferment, constraints, wells.
  - `maintenance`: reliability and inspection, work orders, turnarounds.

  Each has its own tool subset and skills; it loads a skill's method only when it needs it.
- **Tools** are the MCP server's functions called in-process, so outside MCP clients see the same
  tool surface. They run in threads so concurrent specialists don't block each other. Asset-wide
  questions use `operations.py`: production summary and PE, deferment Pareto, constraints with
  trend and cascade causes, well-test performance, bad actors and MTBF, work order backlog, PM and
  inspection compliance, and turnaround scope. All are read-only, compact, and dated from the data's
  latest daily report.
- **Claude settings.** `claude-opus-5-5` by default (the settings `agents.model` for the orchestrator,
  `agents.specialist_model` for the specialists), effort `high` for the orchestrator and `medium`
  for the specialists. Each answer reports calls, tokens and cost per model, as the API reports the
  serving model; a model missing from `pricing.py` is flagged as unpriced. Automatic prompt caching
  is on. The server-side refusal fallback is on (the setting `agents.fallbacks` turns it off). No `thinking` parameter is sent: MAF's adapter only knows
  the older budget form, and Opus 5.5 thinks adaptively by default. Web search and file memory are
  off. The agent framework is a core dependency. `agent-framework-anthropic` still declares `anthropic<2`, so a uv override keeps Fuzer's 2.x
  SDK; the tests run the adapter against it.
- **Surfaces.** `fuzer ask` / `fuzer chat`, and the Ask page. The page uses `/assistant/ask`, which
  starts a background job, and polls `/assistant/jobs/{id}` to show which agent is calling which
  tool. Every answer is stored in `assistant_answer` with its tools, tokens and cost, and engineers
  rate it through `feedback` (`answer:<id>`).
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
