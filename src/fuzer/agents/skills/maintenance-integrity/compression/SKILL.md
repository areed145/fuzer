---
name: compression
description: Compressor health - centrifugal surge margin and surges, reciprocating rod load and valves, API 670 protection channels, dry gas seals, lube oil, and real-gas and fixed-k performance, for any compressor (ISO 14224 class CO).
---

# Compression

This is the compression specialist pack: the playbook below, and `pack.yaml` beside it naming the tools, methods,
detectors, charts and measurement roles it uses and the eval it is held to.

**Start here.** With no machine named ("anything wrong with the compressors?"), run `rotating_screen(at)` and take
the compressors it puts first. For one machine, `calc("rotating_health", ref)` runs every check that applies; read
its findings with the reliability skill's order (vibration first), then go deeper with the checks below. Quote each
check's basis, the numbers and dates, and what could not be checked.

**Performance.** `calc("compressor_performance", stage)` gives a stage's polytropic efficiency against its first
week (`efficiency_drop`: fouling, wear, recycle through a hot cooler); chart it with
`plot_compressor_performance`. `calc("cooler_fouling", cooler)` checks the stage's cooler when the discharge runs
hot; chart it with `plot_cooler_fouling`. Casing vibration against its zones: `plot_vibration`.

- **Surge** (centrifugal compressor stages, `calc("surge", ref)`): surge margin from the suction flow and
  the driver's speed against the machine's own OEM map, and how often the anti-surge valve is open. A 5th
  percentile margin under 5 % (`near_surge`) is a surge risk: surge reverses flow through the wheels and can
  wreck the thrust bearing and seals in seconds, so act on it. A valve open much of the time (`recycling`)
  is safe but burns fuel compressing gas twice. Compare `siblings` (the same stage on the other trains): one
  train starved while the others run full is a load-sharing problem the IOC can fix by moving gas between
  trains, not a machine fault. Maps are issued per machine; a stage without one says so, and then only the
  anti-surge valve's position tells you anything.
- **Reciprocating compressors** (ISO 14224 CO.RE, API 618): `calc("rod_load", ref)` gives each throw's gas rod
  load from its stage pressures and its bore and rod (the maker's cylinder data) against the frame rating.
  Past the rating (`rod_load_high`) breaks crossheads, pins and rods: act on it. A load that hardly reverses
  (`no_reversal`) starves the crosshead pin of oil. It is gas load only; inertia needs crank-angle data.
  `calc("valve_health", ref)`: a cylinder hotter than the others on its stage is a leaking valve (`valve_leak`,
  `hottest_cylinder`); the stage pressure ratios shifting (`ratio_shift`) shows which stage lost capacity
  (`weak_stage`). A leak on the 2nd stage pushes the interstage pressure up and loads the 1st stage throws, so
  a valve leak and a rod load problem are often the same story: say so. A recip's vibration is judged against
  its maker's frame alarm and trip, not the centrifugal zones.
- **Protection channels** (API 670, `calc("protection", ref)`): shaft vibration at the proximity probes, the
  rotor's axial (thrust) position and the journal and thrust bearing temperatures, each against its alarm and
  trip, with its trend and the date it reaches the trip (a 90 % range). Axial position or thrust pad temperature
  past its alarm (`thrust_wear`) means the thrust bearing is going, and a thrust failure wrecks the rotor: act.
  Read them with the casing vibration: a bearing problem shows on several channels at once.
- **Surges** (`calc("surge_events", ref)`): outright surges from their signature (the flow collapsing as the
  anti-surge valve snaps open), each with the thrust kick. Every surge loads the thrust bearing and the dry gas
  seals, so after one look at `protection` and `seal_gas` too.
- **Dry gas seals and lube oil**: `calc("seal_gas", ref)` (API 692) compares the primary vent flow with its own
  clean level and its alarm; `seal_leak` means the secondary seal is all that's left, so plan the cartridge change.
  `calc("lube_oil", ref)` (API 614) trends the header pressure to its low alarm and trip (a plugging filter shows
  as a steady fall: change over before it trips) and the supply temperature.
- **Real-gas performance**: `calc("gas_performance", ref)` gives polytropic efficiency, head and gas power with k
  and Z from the latest gas analysis (ASME PTC 10, Schultz), beside the fixed-k `compressor_performance`; quote
  the real-gas one when the two differ. `calc("power_balance", ref)` on a gas turbine sets its stages' gas power
  against the driver's output: a closure that shifts means a meter, the power estimate or a stage moved.

**Already in the queue.** A compressor the daily screen flags on its condition is a `machine_health` case for the
engineer; a trip is a `machine_trip` case, read with the troubleshooting skill (`compressor_trip`). Say when the
machine you are asked about already has one.

**Escalate to the IOC** when a compressor runs near surge or has surged, when rod load is past the frame rating, or
when a thrust bearing, dry gas seal or lube oil system is past its alarm. A good answer names the machine, the
status, the two or three findings that drive it with their numbers and dates, what is unknown, and the
recommendation.
