---
name: charts
description: When and how to put interactive charts in an answer - which plot tool fits which question, how to read and cite a chart's summary, and when (rarely) to look at the image.
---

# Charts

Only call the plot tools in your own list. If another specialist has the chart you need, say so.

A chart helps when the answer is a shape: a decline, a trend into a trip, a ranking, a curve and
where the well sits on it, wall loss across CMLs. A table or a sentence is better for a few numbers.
One to three charts per answer, each with a sentence on what it shows.

**How they work.** A plot tool reads the data itself and returns a figure id, a marker
`[[figure:<id>]]` and a `summary` with the exact numbers (fits, latest values, rates, step changes).
Reason and cite from the summary. Put the marker on its own line where the chart belongs; the
engineer sees an interactive chart there (zoom, hover, click a point to open its record).

**Which tool.**
- Process values around an event: `plot_trend` (any tags / points), `plot_incident` (an incident on
  one page), `plot_correlation` (what moves together; `target` = the point that tripped, to see what
  moved first), `plot_vibration` (against ISO 20816 zones), `plot_xy` (one point against another),
  `plot_distribution` (before / after a change), `plot_changes` (steps, drifts and jitter changes
  marked, with when they started), `plot_instrument_health` (readings that can't be trusted shaded).
- Wells: `plot_decline` (Arps fit after the last step change, forecast), `plot_water_diagnostic`
  (WOR and derivative: channelling, coning, breakthrough), `plot_gas_lift_curve` (where the well sits
  on its curve, marginal response, optimum), `plot_gas_lift_allocation` (moving gas between wells).
- Equipment against its OEM curve: `plot_pump_curve` (operating points on the head curve; head below
  the curve at the same flow = wear rings / impeller; time away from the BEP), `plot_choke` (effective Cv
  against the choke's characteristic; above = eroded trim, below = restriction). When the model has no
  curve yet, the error names the datasheet waiting to be read or reviewed: say so.
- Equipment and inspection: `plot_wall_thickness` (one item's CMLs, or without a ref the fastest wall
  loss across the asset), `plot_reliability` (Weibull of times between failures, or Crow-AMSAA),
  `plot_bad_actors`, `plot_bar` (deferment Pareto, failures, oil change by well).
- Work and compliance: `plot_backlog` (open orders by priority and age), `plot_compliance` (PM and
  inspection plans overdue, due soon and later).
- Landed readings of any kind: `plot_measurements`.
- Numbers you derived that no tool reads: `plot_custom` (labelled as assistant-entered; last resort).

**Reading the summary.** Say what the chart shows in words with the numbers: "OK-J07-03 declines
11 %/yr (exponential, r² 0.96) after the November 2024 valve repair; at this rate it makes 38 Sm³/d in
a year." Name step changes and left-out points; they are often the story. If a summary has a `note`
(synthetic values, assistant-entered data, no minimum thickness), carry it into the answer.

**Looking at a chart.** `look_at_figure` returns the chart as an image. Use it only when the summary
leaves you unsure how the chart reads: a fit that may miss the late points, an outlier that may set
the scale, few points, or the user asking how something looks. Not for every chart. If it looks wrong,
change the options (another model, a different window) or say what's wrong instead of showing it.

**Errors.** A plot tool that returns `error` made no chart (no data, too few tests): say so in a
sentence, don't retry with invented data.
