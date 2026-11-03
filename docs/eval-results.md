# Eval results

Generated from the recorded runs in `evals/runs/`; don't edit by hand (`uv run python -m fuzer.reference`).
Record a run with `fuzer eval --agents --record` or `fuzer eval --ops --full --live --record` (both cost money), then regenerate. How the evals work: [evals](evals.md); notes on each assistant run: [assistant eval](assistant-eval.md).

## Operations assistant

Mechanical checks against `answer_keys/assistant.json`: the facts a good answer names, sources cited, no plant-operating instructions, the expected specialists asked.

| Run | Model | Questions | Facts named | Cited 3+ | Safe | Routed | Chart drawn | Cost / answer | Time / answer |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 | claude-opus-5-5 | 14 | 94% | 86% | 100% | 100% | 75% | $0.84 | 86 s |
| 2026-11-02 | claude-opus-5-5 | 14 | 98% | 86% | 100% | 100% | 75% | $0.67 | 102 s |
| 2026-10-29 | claude-opus-5-5 | 9 | 100% | 100% | 100% | 100% |  | $0.55 | 71 s |

### Latest run by question (2026-11-03)

| Question | Specialists | Facts | Missing | Citations | Charts | Tool calls | Time | Cost |
|---|---|---|---|---|---|---|---|---|
| curves: Is oil export pump 10-P-1001A performing to its curve, and what… | maintenance, troubleshooter | 100% |  | 9 | vibration, pump_curve | 30 | 85 s | $1.04 |
| curves: Why did OK-J12-02's choke stop controlling the rate in 2025? | maintenance, production | 100% |  | 9 | choke, custom | 43 | 106 s | $0.83 |
| charts: Show me the decline on OK-J07-03 and what it will make over the next… | production | 100% |  | 3 | decline, measurements | 9 | 68 s | $0.35 |
| charts: Which pipelines are losing wall thickness fastest, and why? | maintenance, production | 67% | OK-J03-02 | 1 | custom, custom | 86 | 142 s | $1.50 |
| troubleshooting: Why did 20-K-2200B keep tripping in March 2024, and what should we… | maintenance, troubleshooter | 100% |  | 4 | trend | 31 | 80 s | $0.87 |
| production efficiency: What was our production efficiency in 2025, and what were the… | production | 100% |  | 9 | bar | 11 | 56 s | $0.32 |
| production surveillance: How did oil production trend through 2025, and what explains the… | maintenance, production | 100% |  | 6 | custom, bar, bar | 26 | 72 s | $0.67 |
| constraint management: What has been limiting production over the last two years, and how… | maintenance, production | 100% |  | 10 | custom, bar | 69 | 168 s | $2.07 |
| production engineering: Which wells lost the most oil rate in 2025, and why? | production | 100% |  | 8 | bar, decline | 22 | 73 s | $0.44 |
| reliability: Who are our bad actors over the last two years, and what are their… | maintenance | 50% | MTBF | 3 | bar, bar | 27 | 75 s | $0.63 |
| work orders: What in the work order backlog should we tackle first? | maintenance | 100% |  | 13 | custom | 13 | 69 s | $0.65 |
| turnarounds: What is planned for the next turnaround, and what is missing from… | maintenance | 100% |  | 2 | wall_ranking | 7 | 55 s | $0.52 |
| gas lift: How is lift gas distributed across the headers, and where would… | production | 100% |  | 3 | gas_lift_allocation | 8 | 55 s | $0.39 |
| across domains: Why does lift gas keep running short, and what should go in the next… | maintenance, production, troubleshooter | 100% |  | 12 | bar, bar | 47 | 98 s | $1.51 |

## Operations loop

History replayed with the fake plant's injected upsets: SRE detection, grouping, the troubleshooter's diagnosis, daily reports and opportunities.

| Run | Window | Recall | Precision | Upsets (process / instrument / slow / standing) | False upsets / week | First cause right | Diagnosed (named first cause) | Daily reports OK | Gas lift gain error |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-03 | 2024-06-10 to 2024-06-17 | 100% | 79% | 18 / 1 / 1 / 2 | 0.0 | 100% | none new | 100% | 0.04% |
| 2026-11-02 | 2024-06-10 to 2024-06-17 | 100% | 78% | 20 / 182 / 1 / 0 | 1.0 | 100% | 2 (100%) | 100% | 0.04% |

### Gates

A run fails when a score is under its floor or over its ceiling (`src/fuzer/evals/ops_eval.py`), or when recall fell since the last run of the same window.

| Score | Limit |
|---|---|
| `sre.recall` | at least 0.9 |
| `grouping.first_cause_right` | at least 0.9 |
| `daily_report.all_checks_ok` | at least 1 |
| `daily_report.trip_days_led_by_the_trip` | at least 1 |
| `diagnosis.first_cause_named` | at least 0.5 |
| `grouping.false_upsets_per_week` | at most 5 |
| `daily_report.with_operating_instructions` | at most 0 |
| `opportunities.worst_gain_error` | at most 0.02 |
