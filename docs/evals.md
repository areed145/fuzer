# Evals and quality

How the detectors, the ops loop and the assistant are scored.

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

## The assistant

Questions with answer keys, and the latest results: [assistant eval](assistant-eval.md).
