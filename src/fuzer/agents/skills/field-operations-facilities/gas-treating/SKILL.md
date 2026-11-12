---
name: gas-treating
description: Gas treating and dehydration - amine and glycol contactors foaming or fouling, treated gas (acid gas, water content) against its window, reboiler temperatures drifting toward a limit, and refrigeration chillers holding the dew point, for any treating unit (ISO 14224 classes VE, HE, HB).
---

# Gas treating and dehydration

This is the gas treating and dehydration specialist pack: the playbook below, and `pack.yaml` beside it naming the
tools, methods, detectors, charts and measurement roles it uses and the eval it is held to. The practice is the GPSA
Engineering Data Book's (and API 12GDU for glycol units); each check's response names the basis it used.

**Start here.** The treating train works as one: a contactor that foams or a reboiler that runs cold shows up as
off-spec gas downstream. For a unit named, run the check that fits it and then walk the train both ways. With
nothing named ("is our gas on spec?"), run `calc("treated_gas", ref)` on each contactor with an analyser and start
from the one off or approaching its window. Every limit comes from the site's own record: `limits(ref)` shows the
IOW, alarm and learned windows on a point, and each check says which one it judged against. Quote that basis, the
numbers and dates, and what could not be checked.

- **Contactor foaming** (`calc("contactor_foaming", ref)`): column differential pressure against its clean level
  before the last two days. A sudden rise (`foaming`) is foam: hydrocarbon carryover into the solvent, surfactants,
  solids, or a restart with a cold or contaminated solvent. The check reads the treated gas analyser over the same
  hours: gas going off as the pressure drop peaks confirms it. A slow climb over the week (`fouling`) is packing
  or tray fouling, or damaged internals, not foam. Recommend the field checks (lean solvent temperature above the
  feed gas, inlet separator and filter performance, a foam test on a solvent sample) before antifoam: antifoam
  treats the symptom and fouls the filters.
- **Treated gas** (`calc("treated_gas", ref)`): the analyser against its window, with its trend. Off spec
  (`off_spec`) is the finding to act on; on course for the limit (`approaching_spec`) gives the date. When a glycol
  unit's water content climbs, look at its reboiler next; when an amine unit's acid gas climbs, look at the
  contactor's differential pressure and its reboiler.
- **Regenerator** (`calc("regenerator", ref)`): the reboiler temperature against its window, both sides. A cold
  reboiler (`reboiler_low`, or `reboiler_falling` with the date it reaches the limit) leaves the solvent
  under-stripped, so lean glycol purity or lean amine loading falls and the treated gas follows days later. A hot one
  (`reboiler_high`, `reboiler_rising`) degrades the solvent. Chart it with `plot_operating_window(ref, since, until)`; put the
  reboiler and the treated gas on one `plot_trend` to show the lag.
- **Chiller** (`calc("chiller", ref)`): the low temperature separator against its dew point window. Warm or warming
  (`chiller_warm`, `chiller_warming`) means hydrocarbon dew point at risk: check the refrigeration compressor's
  suction pressure and the chiller's approach.

**Already in the queue.** A treating point outside its IOW is an `iow_breach` case, and a differential pressure or
reboiler outlet temperature that steps away from its normal is an `upset` case. Say when the unit you are asked
about already has one.

**Where no analyser is online**, the lab is the evidence: `measurements(ref)` gives the sample points on a unit
with their latest results and trend (water dew point on a dry gas sample, H2S and CO2 on a sweet gas one). Quote the
sample date, and say that a monthly sample cannot see a foaming event or a reboiler drifting over a week.

**Say what you cannot check.** Amine loading, glycol purity, solvent contamination and foam height are not among the
results Fuzer reads; recommend the sample rather than guessing it. A unit with no analyser or no differential
pressure point says which check it could not run.

**Escalate to the IOC** when treated gas is off spec or will be within days, when a contactor foams, or when a
reboiler is outside its window. A good answer names the unit, the status, the two or three findings that drive it
with their numbers, basis and dates, the link along the train, what is unknown, and the recommendation. Advises:
antifoam, a solvent change-out, a rate cut or a reboiler setpoint change is the operator's and engineer's call.
