# Evals and quality

How the detectors, the ops loop and the assistant are scored.

## The loop, end to end

`fuzer eval --loop` (`src/fuzer/evals/loop.py`) drives every type of work in the routing table through the loop
with the same operations the API calls: it **arrives** about a tagged asset, is **matched** to it, **routed** where
the routing table says, seen in someone's Inbox or on the board (**triage**), **dispatched** to a field operator
with what to check, read on their job list with no raw ids and reported on site then done (**field**), every
remaining decision taken until the case and the work **close**, and closed with a time and outcome a measure can
**count**. A type the field never sees is worked at the desk instead. The report names the first step that failed
for each type and why, and the command exits 1 when any type fails. It writes cases, so run it on a scratch copy;
`tests/evals/test_loop.py` runs it on Okan and Kestrel.

The Okan run keeps the job each field type handed the operator in `ui/tests/fixtures/loop-field-jobs.json`
(rewrite it with `FUZER_WRITE_FIXTURES=1`; the test fails when the API's job shape drifts from it), and the browser
tests render each one at 390 px: title, what the IOC asked and the asset visible, no raw ids, no sideways scroll,
and 44 px tap targets for On site and Done.

Decisions come from the eval's playbook, one entry per process step, and what each type's own source says when it
raises one from `ARRIVE`; a new process or work type adds its entries there. Agent steps get a scripted answer and
nothing calls a model. Work arrives through the front door every source uses; later epics make each type arrive from
its own source or detector.

## Measurement roles against the process model

Every historian point gets a role from the controlled list in `core/roles.py` with each rebuild (`graph/roles.py`,
the `measurement_role` table): what it means for the equipment or well it is on, proposed from its description, its
instrument letters, its units and that equipment's class. `fuzer eval --roles` (`src/fuzer/evals/roles.py`) scores
the proposals against the fake plant's `process_model.json`, which says which unit variable each point is; a unit
that holds one value names its role there (`"role": "differential_pressure"`). It fails under 90% right, or when a
wrong role was confident enough not to go to a steward. `tests/evals/test_measurement_roles.py` runs it on Okan and Kestrel.

A proposal below the confidence threshold is steward work: it is a question on Master data › Questions › Measurement roles and a case in the data steward's queue,
with the point's evidence and the proposed role. Confirming it or choosing another role pins it in
`measurement_role_pin`, live at once and kept by every rebuild.

Compressor monitoring reads roles (P1.4c): a trip's cause family comes from the role of the point that moved first
and the ISO 14224 class of what it is on, the train's run signal is its driver's speed or power role, vibration zones
read the casing vibration role and the heat rate reads the ambient temperature role. `tests/analysis/test_trips.py`
blanks every point's description and renames the tags, and the trips, vibration and ambient come out the same.

## Replaying history to score the detectors

The SRE runs unattended, so nobody checks each thing it raises. Before trusting it, and after
every detector change, it is replayed over a fake plant's past, where the process model's events are
the ground truth (`replay.py`, `fuzer eval --ops`).

**Replay.** The period is walked in 6-hour windows, as the schedule runs. Each window's detector call
gets a time-series source that refuses values after the window's end (`Peek`), so a detector can't
use what happened later. Normal ranges are learned from the 90 days before the period and again every
30 days, then put back. A detection still open at a window's end and picked up in the next is one
detection, first seen at the end of the window that raised it.

**Ground truth.** Upsets (trips, shutdowns, shut-ins, bearing wear, fouling, high discharge
temperature, high level, a bad instrument, a gas lift valve failing open, hydrate restriction, pump
head loss, choke wear) are what a detector should find. Routine events (choke moves, pump changeovers,
planned downtime, curtailment, bypass opening) don't need finding but explain what they cause. A ramp
runs from where it leaves zero to where it returns.

**Matching.** An upset is found by a detection on its own unit (a train: any of its machines; a field
shutdown: anything) starting from an hour before it to 6 hours after it ends. A detection that finds
none is explained if it is on something an event affects at the time, downstream in the model: a trip
reaches the lift gas headers, their child headers, the wells and jackets on them, the export meters
and their pumps; a slow ramp only its own unit, else months of fouling would excuse every alarm. The
rest are false alarms.

**Scores.** Recall and median hours to detect per kind of upset (with the missed ones), precision,
false alarms per week, seconds per window. Each `fuzer eval --ops` run is kept in `ops_eval`; it exits
1 if overall or any kind's recall fell since the last run of the same detector and period.
`tests/evals/test_replay.py` is the CI check: it replays 3–13 November 2024 on Okan, with a scripted
detector to test the scoring and the `limits` detector (the #101 excursions, standing in until #96).
`fuzer eval --ops --full` (`ops_eval.py`) scores everything that runs on a schedule:
- **SRE:** the replay of the period, with grouping.
- **Diagnosis:** the period's upsets handed off. The likeliest cause must name a unit of the matching
  injected event, and cite its records.
- **Daily reports:** each day closed. Every line cited, a trip day led by the trip, no operating
  instructions.
- **Opportunities:** gas lift gains against a calculation by hand.

It uses a scripted Claude by default (no network) and the real one with `--live`. Each run is kept, and
it fails when a score is under its floor (`GATES`, `CEILINGS`) or recall fell since the last run. The
GitHub workflow (`.github/workflows/ci.yml`) runs the tests and this eval on the fake Okan plant for
4–10 November 2024.

## Reading drawings

`fuzer pid-score` scores what Claude read off the fake plant's P&IDs and PFDs against their answer key
(`pid_truth.json`): precision and recall for tags, piping, off-page lines, loops, line numbers, alarm and
trip settings, design ratings, notes and PFD streams, overall and per degradation level. `--gate` fails a
run under an element's floor (`llm_pids.FLOORS`) or more than 0.05 below the last recorded run of the same
model; `--record` keeps the run in `evals/runs/`. Model calls are cached per image, so a prompt change
re-reads once and a pipeline change re-scores for free. `tests/sources/test_llm_pids.py` gates the
pipeline behind a scripted reader in CI.

## The assistant

Questions with answer keys, and the latest results: [assistant eval](assistant-eval.md).
