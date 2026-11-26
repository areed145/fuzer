---
name: pipeline-integrity
description: Pipeline integrity - in-line inspection anomalies screened against MOP (modified ASME B31G) with their growth between pig runs, the derate or dig an anomaly over the screen asks for, and a leak alarm read against the balance between the meters at each end of the line, for any pipeline with route segments.
---

# Pipeline integrity

This is the pipeline integrity specialist pack: the playbook below, and `pack.yaml` beside it naming the tools,
methods, detectors, charts and measurement roles it uses and the eval it is held to. The practice is modified ASME
B31G for remaining strength, the ASME B31.4 / B31.8 design factor of each segment's class location and API 1160 for
the integrity programme, by name; each response names the basis it used.

**Start here.** For "is this anomaly a problem" or a pig run anomaly case, run `ili_anomalies(line)` and
`pipeline_segments(line)`. Quote the anomaly, its segment and station, its depth (% of wall) and length, its ERF now
and at its growth, the two runs it was matched across, and the screen it crosses.

- **Anomalies and growth** (`ili_anomalies(line)`, charted with `plot_ili_anomalies(line)`): each metal loss anomaly
  of the latest run matched to the run before by type and station, its growth over the years between the runs,
  projected ahead; failure pressure by modified B31G at SMYS + 10 ksi, the safe pressure at the class location's
  design factor, ERF = MOP over the safe pressure. `exceeds_now` (ERF 1 or more, or 80 % of wall) asks for a pressure
  restriction until it is assessed; `exceeds_within_5_years` asks for a dig or repair planned before it gets there,
  with the date it would cross. Growth within the tool's sizing tolerance is said so: a dig or the next run confirms
  it. A segment with no recorded grade gets no strength: say the grade is missing, never assume one.
- **Where it is** (`pipeline_segments(line)`): the segment's wall, grade and class location and the valves either side
  of it, which isolate it. A heavy wall or higher class stretch (a crossing, a populated area) has its own design
  factor; quote it.
- **The pressure it sees**: a derate is against MOP; the operating pressure is context. `plot_trend([ref])` on the
  station discharge pressure upstream shows what the anomaly actually sees, and `measurements(ref)` any dig or wall
  reading recorded on the line.
- **A leak alarm**: no leak detection (CPM) alarms are loaded. For "is the line leaking", read the flow at the meters
  at each end that `pipeline_segments` names with `plot_trend([in, out])` over the same hours: a sustained gap beyond
  the meters' usual agreement, with the pressure falling, is a leak to confirm now; a step on one meter alone is a
  metering question for Production & Surveillance. Say the window, the gap in bbl/h and the pressure.

**Already in the queue.** The `ili_anomalies` detector raises each anomaly over the screen as work for the integrity
engineer, keyed by line and feature, and clears it when a later run no longer flags it. Say when the anomaly you are
asked about already has one.

**Say what you cannot check.** Fuzer holds no dig reports or field-measured depths unless loaded, no crack, dent or
weld assessment (API 579, ASME B31.8 dents), no cathodic protection survey and no pressure cycle fatigue: name them
as the integrity engineer's next step.

**Escalate to the integrity engineer and the controller** when an anomaly exceeds the screen now or a balance gap
holds with falling pressure. Advises: fitness for service, a MOP derate, a dig and confirming or clearing a leak are
their accountable decisions.
