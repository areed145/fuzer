---
name: alarms-and-instruments
description: Alarm performance, instrument evidence and control loops - alarm rates, floods, chattering and standing alarms, whether readings can be trusted, whether a loop is cycling, signal changes, and how close a unit runs to its limits.
---

# Alarms, instruments and control loops

These methods need process values. Check the provider first: on the synthetic historian say that findings show how
the generator behaves, not the plant.

1. **Can the reading be trusted?** Start with `instrument_health(ref)`: frozen, stuck, noisy, out of range, and
   redundant transmitters drifting apart. A suspect instrument can explain an alarm or a trend, so rule that out
   before reading the process into it. `plot_instrument_health(ref)` shows it.
2. **Alarm performance.** `alarm_analysis(since, until)` gives alarms per hour against the ISA-18.2 / EEMUA 191
   target of 6, floods, chattering and standing alarms, and the top points. `plot_alarms(since, until)` charts it.
3. **Is a loop cycling?** `calc("loop_oscillation", ref)` says whether a measurement on the unit cycles regularly,
   as a loop hunting or limit-cycling on a sticking valve does, with its period and amplitude. Noise is not a cycle.
   A cycle that ramps and snaps back points at the valve (stiction); a smooth one at tuning or an interacting loop.
   Name it as a check for the instrument technician. Where the loop is on the DCS loop list, read its output and
   mode too (step 4).
4. **Which loops need attention?** `loop_health(days, until)` ranks every loop on the loop list, worst first: time
   in manual, output saturated (out of valve), setpoint error and a regular oscillation, each flagged at its
   threshold. `control_loop(ref)` reads one loop's PV, SP, OP and mode together; `plot_loop_health(days, until)`
   charts the ranking. A loop in manual or out of valve isn't a tuning problem: say what the record shows and who
   owns it.
5. **When did it change?** `detect_changes(ref)` dates steps, drifts and jitter changes. `plot_changes(ref)` marks them.
6. **Limits and headroom.** `limits(ref)` gives alarm limits, IOWs and learned ranges with excursions while the unit
   runs. `capacity_headroom(ref)` shows the nearest limit, and `plot_operating_window(ref, since, until)` the minutes
   outside per day. Alarm limits, IOWs and learned ranges are different things: name which one you mean.

**Boundaries.** A learned range is what the plant has done, not what is safe. Nothing here validates an operating
envelope. Recommend checks to instrument technicians and control-system owners; never propose a setpoint, tuning,
alarm limit or bypass change as something to do. A cycling loop or a ranked worklist is evidence for them, not a tuning or change request.
