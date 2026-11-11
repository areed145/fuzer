---
name: upset-correlation
description: Method for upsets that belong together - correlated events and cascades across units - so the IOC triages one event from where it started, merges duplicate cases and sends one operator; and for the cascades that keep recurring.
---

# Upsets that belong together

The SRE groups detections into one upset while each starts within a couple of hours of the last and sits within a
few links of the others in the plant model. A cascade slower than that, or carried by something the plant model
doesn't hold (power, instrument air, fuel gas, a shared flare header), arrives as several upsets, each its own case.
The IOC then triages one event several times and may send operators out more than once.

1. **The upset itself.** `upset_detail(upset_id)` lists its detections in time order; the first is the likely first
   cause. `plot_propagation(upset_id)` shows the units it reached, coloured by minutes after the first detection.
2. **What else was happening.** `related_upsets(upset_id, hours=12)` scores every other upset in the window by time,
   plant-model hops and history together, and gives each a role:
   - `likely_cause`: started earlier and connected. Triage from there: its first unit is where to send someone.
   - `likely_knock_on`: started later and connected. Usually the same event: recommend merging it into this case.
   - `same_time`: connected and simultaneous: one event, one first cause still to find.
   - `seen_together`: no plant-model link, but the two signatures came together before. Name the link to check
     (a shared utility, a common header, a control interaction) and say the model doesn't hold it.
   - `coincident`: neither. Leave it alone.
   Give the lag in minutes, the hops and how many times they were seen together, so a person can judge it.
3. **How it spread.** `plot_state_timeline(since, until, scope)` shows each unit's run state; the first to stop
   usually started it. `trace_flow(ref, "up")` confirms the path between two units, and `trip_analysis` or
   `alarm_analysis` over the window show whether an alarm flood hid the first alarm.
4. **Does it recur?** `cascade_patterns(since, until)` lists sequences that repeat ("a stop on 20-K-2001C, then an
   excursion on 20-PT-3003 about 40 minutes later, 7 of 9 times"). A recurring cascade with high confidence is a
   case for a barrier or a change in the first unit, not for faster triage of the knock-ons. Keep it with
   `start_study("cascades", ...)` to cite as study:<id>.

Report: the event as one story, first cause first (unit, time, finding), then each knock-on with its lag and the
link that carries it; which upsets to merge into which case and why; where to send one operator; and any cascade
that keeps recurring. Merging is the IOC's decision on the triage page: recommend it, never claim it was done.
A sequence in time and a plant-model link are leads, not proof of cause; say what would confirm it.
