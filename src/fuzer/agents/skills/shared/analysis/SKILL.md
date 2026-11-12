---
name: analysis
description: Reusable analysis methods - comparing periods, detecting changes, correlation, clustering and kept studies - with the assumptions, data sufficiency and uncertainty to state with each result.
---

# Analysis methods

Use the method that answers the question, from the tools in your own list. If you don't have a method here, say so
and name the specialist who does. Don't work around it.

**State with every result:** the window, how many points it rests on, the time-series provider, what you assumed and
what would change the conclusion.

- **Did something change?** `detect_changes(ref)` finds steps, drifts and jitter changes with their start times.
- **Did the fix work?** `compare_periods(ref, before_since, before_until, after_since, after_until)` gives medians and whether the change is significant,
  allowing for autocorrelation. A significant change after a fix doesn't prove the fix caused it. Name the points
  under `not_compared` and why, and say when a row is `thin`.
- **What moves with this?** `correlate(target)` ranks points and says what moved first, and `correlation_matrix(scope)`
  shows groups; `plot_correlation(ref)` draws them. Correlation is a lead, never a cause. Check the plant model
  with `trace_flow(ref)`: a strong pair with no physical link is a question, not a finding.
- **What groups exist?** `cluster_entities` for peer groups, `cluster_modes` for a unit's operating modes,
  `cluster_events` for recurring upset types. Name the features used; cluster labels are descriptions, not diagnoses.
- **Too big to run now?** `start_study` keeps one and `get_study` reads it back.

**Forecasts.** A forecast is an estimate, never a plan or a commitment. Quote it with its `range`, `window` and
`points`; a date to a limit without its range is a guess. Every forecast carries `sufficiency`, the one shared check:
`too_little_data` means say that and give no number, `thin` means quote it with the reasons it gives.

**Sufficiency.** Say when too few points, a stopped unit or missing data limits a result. Report that rather than a
number with false precision. On the synthetic historian, process values move as the generator shapes them, so results
show the demo's upsets and not plant physics. Say that whenever a result rests on them.
