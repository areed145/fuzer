---
name: alarms-and-instruments
description: Alarm performance and instrument evidence - alarm rates, floods, chattering and standing alarms, whether readings can be trusted, signal changes, and how close a unit runs to its limits.
---

# Alarms and instruments

These methods need process values. Check the provider first: on the synthetic historian say that findings show how
the generator behaves, not the plant.

1. **Can the reading be trusted?** Start with `instrument_health(ref)`: frozen, stuck, noisy, out of range, and
   redundant transmitters drifting apart. A suspect instrument can explain an alarm or a trend, so rule that out
   before reading the process into it. `plot_instrument_health(ref)` shows it.
2. **Alarm performance.** `alarm_analysis(since, until)` gives alarms per hour against the ISA-18.2 / EEMUA 191
   target of 6, floods, chattering and standing alarms, and the top points. `plot_alarms(since, until)` charts it.
3. **When did it change?** `detect_changes(ref)` dates steps, drifts and jitter changes. `plot_changes(ref)` marks them.
4. **Limits and headroom.** `limits(ref)` gives alarm limits, IOWs and learned ranges with excursions while the unit
   runs. `capacity_headroom(ref)` shows the nearest limit, and `plot_operating_window(ref, since, until)` the minutes
   outside per day. Alarm limits, IOWs and learned ranges are different things: name which one you mean.

**Boundaries.** A learned range is what the plant has done, not what is safe. Nothing here validates an operating
envelope. Recommend checks to instrument technicians and control-system owners; never propose a setpoint, tuning,
alarm limit or bypass change as something to do.
