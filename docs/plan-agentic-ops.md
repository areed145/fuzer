# Implementation plan: Agentic operations milestone (#96–#103)

Build the shared foundations first (#97 workflow engine, #100 value score). Then build the SRE in three parts, putting the eval harness in early so detection is measured from the first version. The things people see (board, daily report, opportunity scan) come last, because they read from everything else.

Each chunk is one PR and should fit one session. Start each chunk in a fresh session (or run `/compact` first) and paste the starter prompt. Each prompt names its issue, so Claude can read the full scope with `gh issue view`.

## Phase 1: Foundations (no Claude spend)

| # | Chunk | Starter prompt |
|---|---|---|
| 1 | **#100 value score**: pure scoring functions, per-asset config, score breakdown, unit tests. Smallest chunk with no dependencies, and the SRE, board and opportunity scan all rank with it. | "Implement #100 value/risk score. Pure functions plus config, no UI. Branch `value-score`." |
| 2 | **#97a workflow engine**: our own engine for the supported BPMN subset (`work_process`, `work_token`, `work_item` and `work_event` tables; `SKIP LOCKED` worker; timers; messages matched to the right process; `requiresHuman` guard); `.bpmn` import and publish checks; `upset` and `daily_report` definitions; the API. Its behaviour is tested against SpiffWorkflow on shared test files. | "Implement the #97 engine: schema, interpreter for the BPMN subset, worker, timers, message matching, publish checks and API. No UI yet. Test its behaviour against SpiffWorkflow." |
| 3 | **#97b action inbox and MCP**: my work and my roles' queue, claim, form-js task forms with their evidence, approve or reject, bulk close, an item page with its timeline and the bpmn-js viewer, MCP tools (`my_work`, `claim_work`, `complete_work`, `hand_off`, `send_evidence`). | "Implement the #97 action inbox, item page and MCP tools." |
| 3b | **#97c modeler**: the bpmn-js modeler with the palette cut down to the subset, the Fuzer properties panel, the form-js form editor, draft → checked → published with version comparison, and the process monitor overlay. It can wait until after Phase 2 if time is tight, because the starting processes are imported from `.bpmn` files. | "Implement the #97 modeler page: restricted palette, Fuzer properties panel, form-js editor, publish flow, monitor overlay." |

## Phase 2: Agentic SRE

| # | Chunk | Starter prompt |
|---|---|---|
| 4 | **#101 limits and IOWs**: per-PI-point operating limits table, loaded from source files, with synthetic limits for the fake plant taken from `process_model.py`. | "Implement #101. Generate synthetic limits for the fake plant from the process model." |
| 5 | **#103a replay harness (skeleton)**: replays history in 6-hour windows and scores detections against the process model's injected events (precision/recall). Detection only for now. | "Implement the replay harness part of #103, scoring detection only. The SRE detectors come next." |
| 5b | **#105 A + C (part)**: shared calc layer, instrument health, `detect_changes`, `what_changed`. The detectors in chunk 6 are built on these. | "Implement #105 sections A, instrument health and detect_changes from C, and what_changed from F." |
| 6 | **#96a detectors**: the 6-hour Dagster job, starting where the last run stopped, plus the numeric checks (limit breaches, trips, level shifts, drift, bad instruments). Measure them with chunk 5. | "Implement #96 detection only, with no Claude. Use the replay harness to tune it." |
| 7 | **#96b grouping**: groups detections close in time and connected in the plant model (`part_of`, `flows_to`, `lift_gas_to`, piping), finds the likely first cause, and merges groups into incidents in `incidents.py`. | "Implement #96 grouping by time and plant topology, and merging into incidents. Extend the replay score to grouping." |
| 7b | **#106a correlation studies**: aligned matrix loader with a Parquet cache, cleaning, `correlate` (lag ranking) and `correlation_matrix`, with `plot_correlation` rebuilt on top. The diagnosis step in chunk 8 uses the lag ranking to find which points moved first. | "Implement #106: data loader, cache and correlation studies (no clustering yet). Meet the performance target." |
| 8 | **#96c diagnosis**: troubleshooter agent call per group, Claude spend cap, proposed fix, #100 priority, and the work item created in #97. | "Finish #96: diagnosis, proposed fix, priority and handoff to workflow, within the ClaudeSettings cap." |

## Phase 3: What people see

| # | Chunk | Starter prompt |
|---|---|---|
| 9 | **#99a board backend**: one ranked view across SAP backlog, PMs, turnaround scope, upsets and reviews; duplicate cards merged; pinned priorities kept across rebuilds; `next_work` MCP tool. | "Implement the #99 board backend and the next_work tool." |
| 10 | **#99b board UI** | "Implement the #99 board UI." |
| 11 | **#98 daily report**: day-close job, fixed outline, every claim cited, ops lead review step in #97. | "Implement #98 (shift handover is out of scope for now)." |
| 11b | **#107a back-pressure model and calcs**: couple well rate to back-pressure in the process model, with the new injected events; add `pressure_profile`, `well_constraint`, `backpressure_sensitivity`, pressure-drop drift detection, and capacity headroom. | "Implement #107 sections 1 and 2: process model coupling first, keeping the current test-rate calibration, then the calcs." |
| 11c | **#107b debottlenecking**: `debottleneck_options`, the #107 charts, and the `production/back-pressure` skill. Must come before #102, which uses these options as one of its sources. | "Implement #107 sections 3–5." |
| 12 | **#102 opportunity scan**: gas lift reallocation, wells producing below expected decline, constraints that keep coming back, all feeding the board. | "Implement #102." |
| 12b | **#105 B, D, E and the rest of F**: equipment performance, alarms and trips, agentic operations views, `compare_periods` and `similar_cases`. Split it across sessions by section. Control loop performance waits until the process model is extended. | "Implement #105 section <B/D/E>." |
| 12c | **#106b clustering and studies**: operating mode, point, well and event clustering, a PCA/SPE anomaly score as a new #96 detector, `study` records, the background job, and the study page. Takes two sessions: calcs first, then the job and UI. | "Implement #106 clustering (modes, entities, events, PCA/SPE)." then "Implement #106 study records, background job and UI." |
| 13 | **#103b full eval in CI**: score diagnosis, reports and the scan against the replay; add a CI gate. | "Finish #103: score diagnosis, reports and the scan; add the CI gate." |

## Keeping each session within usage

- **One chunk per session, one PR per chunk.** Merge before starting the next, so later sessions read the merged code rather than needing the earlier session's context.
- **Ask Claude not to use subagents or broad repo exploration.** Each issue already lists the files it builds on, which is cheaper than sweeping the repo.
- **Claude-heavy chunks (8, 11, 12, 13):** run tests against recorded or mocked Claude responses, and do one live run at the end.
- **If time is short:** chunks 1–8 are the core value (a working SRE that is measured and hands off work). Chunks 9–13 can wait.
- **Natural stopping points:** after chunks 3, 8 and 10. Each leaves a usable feature behind.
