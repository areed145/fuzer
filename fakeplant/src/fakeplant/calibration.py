"""Check a fake plant's design point against an equation of state (#858, step 6).

A fake plant's process model sets its pressures, temperatures and rates by hand. This checks that they hang
together as a real plant's would, using the Peng-Robinson equation of state from `thermo` (MIT; a dev
dependency, so the tests run it and the deployed image doesn't carry it). Each check says what the model has,
what the physics says, and whether they agree:

- **Inlet gas.** The gas leaving the inlet separator is at its dew point there. `saturated_gas` fits a
  composition (a lean associated gas with a C5 to C7 tail) whose dew point is the separator's temperature at its
  pressure, and the gas analysis's relative density should match it.
- **Compressor stages.** The model raises each stage's temperature as Ts x ratio^0.27. On the fitted gas, the
  polytropic efficiency that gives the same discharge temperature should be what a centrifugal stage achieves
  (0.70 to 0.85). Fuzer's own real-gas calcs (`analysis.compression`: Papay Z from Sutton pseudo-criticals, the
  Schultz efficiency on an ideal k) are checked against the equation of state at the same points.
- **Gas wells.** A gas producer's wellhead pressure must be above the header it flows into, and its flowing
  wellhead temperature above the hydrate temperature at that pressure (Motiee's correlation for a gas of the
  analysis's relative density; thermo has no hydrate model).

The design point is the model's nominal state: its design pressures, the climate's mean ambient and sea
temperatures, coolers at their approach. `report(spec, relative_density)` runs every check on a process model.
"""

import math
from functools import cache

COMPONENTS = ["methane", "ethane", "propane", "isobutane", "butane", "isopentane", "pentane", "hexane", "heptane",
              "nitrogen", "carbon dioxide"]
LEAN = [0.840, 0.070, 0.035, 0.007, 0.010, 0.003, 0.003, 0.002, 0.0005, 0.015, 0.015]
TAIL = {"isopentane": 0.075, "pentane": 0.075, "hexane": 0.5, "heptane": 0.35}  # how a heavier gas adds its C5+
AIR = 28.9647
EXPONENT = 0.27  # the process model's temperature exponent for a compressor stage
ETA = (0.70, 0.85)  # what a centrifugal stage's polytropic efficiency should be
HYDRATE_MARGIN = 5.0  # degC between a flowing well and its hydrate temperature
BAR = 1.01325


@cache
def _flasher():
    from thermo import PRMIX, CEOSGas, CEOSLiquid, ChemicalConstantsPackage, FlashVL
    from thermo.interaction_parameters import IPDB

    consts, props = ChemicalConstantsPackage.from_IDs(COMPONENTS)
    kw = {"Tcs": consts.Tcs, "Pcs": consts.Pcs, "omegas": consts.omegas,
          "kijs": IPDB.get_ip_asymmetric_matrix("ChemSep PR", consts.CASs, "kij")}
    gas = CEOSGas(PRMIX, kw, HeatCapacityGases=props.HeatCapacityGases)
    liquid = CEOSLiquid(PRMIX, kw, HeatCapacityGases=props.HeatCapacityGases)
    return FlashVL(consts, props, liquid=liquid, gas=gas), consts.MWs


def composition(tail: float) -> list[float]:
    """The lean gas with `tail` mole fraction more C5+ (normalised)."""
    z = list(LEAN)
    for name, share in TAIL.items():
        z[COMPONENTS.index(name)] += tail * share
    s = sum(z)
    return [x / s for x in z]


def molar_mass(zs: list[float]) -> float:
    return sum(z * m for z, m in zip(zs, _flasher()[1], strict=True))


def dew_point(zs: list[float], p_barg: float) -> float:
    """The gas's hydrocarbon dew point (degC) at a gauge pressure."""
    return _flasher()[0].flash(P=(p_barg + BAR) * 1e5, VF=1, zs=zs).T - 273.15


def saturated_gas(p_barg: float, t_c: float) -> list[float]:
    """A composition whose dew point at p_barg is t_c: the gas leaving a separator at those conditions."""
    lo, hi = 0.0, 0.2
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if dew_point(composition(mid), p_barg) < t_c else (lo, mid)
    return composition((lo + hi) / 2)


def polytropic_outlet(zs, p1_barg: float, t1_c: float, p2_barg: float, eta: float, steps: int = 16) -> float:
    """The discharge temperature (degC) of a polytropic compression at efficiency eta: dh = v dP / eta in steps."""
    flash = _flasher()[0]
    p1, p2 = (p1_barg + BAR) * 1e5, (p2_barg + BAR) * 1e5
    s = flash.flash(P=p1, T=t1_c + 273.15, zs=zs)
    for i in range(steps):
        a, b = p1 * (p2 / p1) ** (i / steps), p1 * (p2 / p1) ** ((i + 1) / steps)
        s = flash.flash(P=b, H=s.H() + s.V() * (b - a) / eta, zs=zs)
    return s.T - 273.15


def implied_efficiency(zs, p1_barg: float, t1_c: float, p2_barg: float, t2_c: float) -> float:
    """The polytropic efficiency that takes the gas from (p1, t1) to p2 at t2."""
    lo, hi = 0.3, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if polytropic_outlet(zs, p1_barg, t1_c, p2_barg, mid) > t2_c else (lo, mid)
    return (lo + hi) / 2


def hydrate_temperature(p_barg: float, relative_density: float) -> float:
    """Hydrate formation temperature (degC) of a natural gas by Motiee (1991), for 0.55 to 1.0 relative density."""
    lp = math.log10((p_barg + BAR) * 14.5038)
    g = relative_density
    tf = (-238.24469 + 78.99667 * lp - 5.352544 * lp * lp + 349.473877 * g - 150.854675 * g * g
          - 27.604065 * g * lp)
    return (tf - 32) / 1.8


def _climate(spec: dict) -> dict:
    return {"mean": 27.5, "coolant": "sea", "sea_mean": 26.5, **spec.get("climate", {})}


def _check(name: str, unit: str, model, physics, ok: bool, why: str) -> dict:
    return {"check": name, "unit": unit, "model": model, "physics": physics, "ok": bool(ok), "why": why}


def inlet_gas(spec: dict, relative_density: float, separator: str) -> tuple[list[float], dict]:
    """The gas saturated at the inlet separator, and whether the analysis's relative density matches it."""
    c = _climate(spec)
    p = spec["units"][separator]["pressure"]
    t = c["mean"] + 8  # the model's 1st-stage suction: ambient + 8
    zs = saturated_gas(p, t)
    rd = molar_mass(zs) / AIR
    return zs, _check("inlet_gas_relative_density", separator, round(relative_density, 3), round(rd, 3),
                      abs(relative_density - rd) <= 0.02,
                      f"gas at its dew point at {p} barg and {t:.1f} degC has relative density {rd:.3f}")


def stages(spec: dict, zs: list[float], heads: dict[int, float] | None = None) -> list[dict]:
    """Each distinct centrifugal stage design point: the efficiency the model's discharge temperature implies,
    Fuzer's real-gas calcs against the equation of state there, and the compressor map's design head (`heads`,
    kJ/kg by stage) against the head the stage does at its design point. Then each train's power: the gas power its
    stages absorb at their design flow against its gas turbine's output."""
    from fuzer.analysis import compression

    c = _climate(spec)
    flash = _flasher()[0]
    out, seen, power = [], {}, {}
    comp = sorted(((u.get("train", ""), u.get("stage", 1), n, u) for n, u in spec["units"].items()
                   if u["type"] == "compressor"), key=lambda x: x[:3])
    feed, train = zs, None
    for tr, _, name, u in comp:
        if tr != train:  # each train starts from the inlet gas
            feed, train = zs, tr
        cooler = spec["units"].get(u.get("cooler_before") or "", {})
        medium = c["mean"] if cooler.get("coolant", c["coolant"]) == "air" else c["sea_mean"]
        ts = medium + cooler.get("approach", 9.0) if u.get("cooler_before") else c["mean"] + 8
        ps, pd = u["p_suction"], u["p_discharge"]
        # the suction scrubber drops what condensed in the cooler before it: the stage takes the vapour
        at = flash.flash(P=(ps + BAR) * 1e5, T=ts + 273.15, zs=feed)
        stage_gas = list(at.gas.zs) if at.gas is not None and at.VF < 1 else list(feed)
        feed = stage_gas
        key = (ps, pd, round(ts, 2))
        if key in seen:
            power[tr] = power.get(tr, 0.0) + u["q_design"] / 3600 * seen[key]
            continue
        mw = molar_mass(stage_gas)
        sg = mw / AIR
        g = {"relative_density": sg, "molar_mass": mw, "tpc_k": (169.2 + 349.5 * sg - 74.0 * sg * sg) / 1.8,
             "ppc_bara": (756.8 - 131.0 * sg - 3.6 * sg * sg) * 0.0689476}
        td = (ts + 273.15) * ((pd + 1) / (ps + 1)) ** u.get("t_exponent", EXPONENT) - 273.15
        eta = implied_efficiency(stage_gas, ps, ts, pd, td)
        out.append(_check("stage_efficiency", name, round(td, 1), round(eta, 3), ETA[0] <= eta <= ETA[1],
                          f"{ps} to {pd} barg from {ts:.1f} degC (gas of molar mass {mw:.1f}): the model's {td:.1f} "
                          f"degC discharge is a polytropic efficiency of {eta:.3f} on the equation of state"))
        f = compression.stage_state(g, ps, pd, ts, td)
        out.append(_check("fuzer_efficiency", name, round(f["eta"], 3), round(eta, 3), abs(f["eta"] - eta) <= 0.03,
                          "Fuzer's Schultz efficiency on an ideal k against the equation of state's"))
        for end, p, t, z in (("suction", ps, ts, f["z1"]), ("discharge", pd, td, f["z2"])):
            zp = flash.flash(P=(p + BAR) * 1e5, T=t + 273.15, zs=stage_gas).Z()
            out.append(_check(f"fuzer_z_{end}", name, round(z, 4), round(zp, 4), abs(z - zp) / zp <= 0.02,
                              f"Papay Z from Sutton pseudo-criticals at {p} barg and {t:.1f} degC against Peng-Robinson"))
        s1 = flash.flash(P=(ps + BAR) * 1e5, T=ts + 273.15, zs=stage_gas)
        s2 = flash.flash(P=(pd + BAR) * 1e5, T=td + 273.15, zs=stage_gas)
        dh = (s2.H() - s1.H()) / mw  # kJ/kg the gas takes up
        head = eta * dh  # polytropic head
        rho = mw / 1000 / s1.V()  # kg/m3 at suction
        seen[key] = rho * dh  # kW per actual m3/s of suction flow
        power[tr] = power.get(tr, 0.0) + u["q_design"] / 3600 * seen[key]
        if heads and u.get("stage") in heads:
            h = heads[u["stage"]]
            out.append(_check("stage_head", name, h, round(head, 1), abs(h - head) / head <= 0.15,
                              f"the map's design head against the {head:.1f} kJ/kg polytropic head from {ps} to {pd} barg"))
    for tr, kw in sorted(power.items()):
        gt = next((u for u in spec["units"].values() if u["type"] == "gas_turbine" and u.get("train") == tr), None)
        if gt:
            out_kw = gt["iso_kw"] * gt.get("duty", 1.0)
            out.append(_check("train_power_closure", tr, round(out_kw), round(kw), 0.85 <= kw / out_kw <= 1.0,
                              f"its stages absorb {kw:.0f} kW at design flow against {out_kw:.0f} kW from its gas turbine "
                              f"(a closure of {kw / out_kw:.2f}; mechanical and gearbox losses make it 0.85 to 1.0)"))
    return out


def gas_wells(spec: dict, relative_density: float) -> list[dict]:
    """Each gas producer at its design rate: wellhead pressure against its header, and its flowing temperature
    against the hydrate temperature."""
    c = _climate(spec)
    out = []
    for name, u in sorted(spec["units"].items()):
        if u["type"] != "well" or u.get("kind") != "gas":
            continue
        whp = u["p_res"] - u["c_gas"] * u["qgas0"]
        header = spec["units"].get(u.get("header") or "", {}).get("p0")
        if header is not None:
            out.append(_check("gas_well_flows", name, round(whp, 1), header, whp > header + 2,
                              f"its wellhead at {whp:.1f} barg must be above its header's {header} barg to flow into it"))
        wht = c["mean"] + 30  # the model's flowing wellhead temperature: ambient + 30
        th = hydrate_temperature(whp, relative_density)
        out.append(_check("gas_well_hydrate_margin", name, round(wht, 1), round(th, 1), wht >= th + HYDRATE_MARGIN,
                          f"flowing at {wht:.1f} degC against a hydrate temperature of {th:.1f} degC at {whp:.1f} barg"))
    return out


def report(spec: dict, relative_density: float, separator: str, heads: dict[int, float] | None = None) -> dict:
    """Every check on a process model (process_model.json), with the gas analysis's relative density, the inlet
    separator the analysis is taken at, and the compressor maps' design heads by stage."""
    zs, gas = inlet_gas(spec, relative_density, separator)
    checks = [gas, *stages(spec, zs, heads), *gas_wells(spec, relative_density)]
    return {"checks": checks, "failed": [f"{c['check']} {c['unit']}" for c in checks if not c["ok"]],
            "gas": {"composition": {n: round(z, 5) for n, z in zip(COMPONENTS, zs, strict=True)},
                    "molar_mass": round(molar_mass(zs), 2), "relative_density": round(molar_mass(zs) / AIR, 3)},
            "basis": "Peng-Robinson (thermo, ChemSep interaction parameters) on a lean associated gas fitted to be at its "
                     "dew point in the inlet separator; polytropic compression in 16 steps of dh = v dP / eta; hydrate "
                     "temperature by Motiee (1991); the model's nominal design point (mean ambient and sea temperatures, "
                     "coolers at their approach, design pressures and rates)"}
