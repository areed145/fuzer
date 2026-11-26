---
name: hydraulics-and-linepack
description: Read a pipeline's pressures and flows against their band, the pump station trips behind a change, and the line balance between the meters at each end; say plainly that linepack and capacity with a segment down need a hydraulic model Fuzer does not hold.
---

# Hydraulics and linepack

**Start here.** Place the line first: `pipeline_segments(line)` gives its MOP, its segments and stations, and the
meters at each end. For "why did the pressure drop" or "is the line running in its band", read the station suction
and discharge pressures and the end meters over the window with `plot_trend([refs])`, and `limits(ref)` for the band
each is judged against (alarm limits, integrity operating windows or the range learned from history).

- **Out of band**: `detect_changes(ref)` says when a pressure or flow stepped or drifted and by how much. A step down
  in discharge pressure with flow falling at both ends is a pump stopping; a step on one transmitter alone is an
  instrument for the controls specialist.
- **Pump station trips**: `trip_analysis(since, until, scope)` with the station as scope gives each stop, start and
  failed start with the time running. Quote the pump, when it stopped and how long the station ran on fewer pumps,
  and hand the pump's own condition (vibration, seals) to Maintenance & Integrity.
- **Line balance**: the flow in at the first meter against the flow out at the last over the same hours. Quote both
  in bbl/h, the gap and its share, and the window. A gap that follows a pump stop and recovers is linepack moving,
  not a loss; a gap that holds with pressure falling goes to the pipeline integrity skill as a possible leak.
- **Readings off the historian**: `measurements(ref)` for daily meter tickets or lab results where they are loaded.

**Say what you cannot check.** Fuzer holds no hydraulic model of the line: linepack in barrels, the capacity left with
a segment or pump station down, surge and the restart time are unknown, not estimated. Say so, and that the
controller's hydraulic model or the line's design capacity settles them.

**Advises.** A rate change, a pump start or stop and a restart after a shutdown are the controller's calls under the
line's operating procedures; recommend checks, never operating instructions.
