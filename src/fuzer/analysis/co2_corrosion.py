"""CO2 corrosion screening (#368): what a CO2 corrosion model predicts for each production line from the water it
carries and the gas over it, set against the wall loss its CMLs actually measure. A screening model with its method
stated, not an integrity verdict.

    screen(conn, until=None, days=365)   per line with produced water chemistry: the uninhibited predicted rate, the
                                         measured CML rate, their ratio, and lines that corrode out of step with peers

The model is de Waard and Milliams (1991, with the 1993 fugacity and scale corrections): log10(V mm/y) = 5.8 - 1710/T
+ 0.67 log10(fCO2), T the water's temperature (K) at the line's sample point and fCO2 the CO2 partial pressure at the
line's MOP (an upper bound on its operating pressure) times the fugacity coefficient. Above the scaling temperature
a protective iron carbonate film cuts the rate. It predicts the uninhibited, water-wet rate: inhibition, oil wetting
and pH buffering all lower the real one, so a measured rate far under it is expected. What it screens is a line whose
measured rate sits far above its peers' share of their prediction (OUTLIER_RATIO times the median): something on that
line the model doesn't capture (more water, solids, a failed inhibitor, another mechanism). Where H2S is a large
share of the acid gas the CO2 model doesn't hold and the line gets no prediction.

Gas CO2 and H2S are the line's own gas samples where it has them, else the field's median gas sample, and the result
names which. pH, chloride, bicarbonate and dissolved iron are reported beside the rate; the model doesn't adjust for
them.
"""

import math
from datetime import date, timedelta
from statistics import median

from ..graph import operations as ops
from . import integrity

OUTLIER_RATIO = 2.0  # measured/predicted above this many times the peers' median: corroding out of step
SOUR_RATIO = 500.0  # pCO2/pH2S under this: H2S takes part, and a CO2-only model doesn't hold (sweet above)
WATER = ("ph", "sample_temperature", "chloride", "bicarbonate", "iron")
ATM_BAR = 1.01325


def predicted_rate(temp_c: float, pressure_bara: float, co2_mol_pct: float) -> dict:
    """De Waard-Milliams CO2 corrosion rate (mm/y), uninhibited, with the fugacity and scale factors."""
    t = temp_c + 273.15
    pco2 = pressure_bara * co2_mol_pct / 100
    fug = 10 ** (pressure_bara * (0.0031 - 1.4 / t))
    fco2 = pco2 * fug
    base = 10 ** (5.8 - 1710 / t + 0.67 * math.log10(fco2))
    scale = min(1.0, 10 ** (2400 / t - 0.6 * math.log10(fco2) - 6.7))
    return {"rate_mm_per_year": base * scale, "base_mm_per_year": base, "scale_factor": scale, "pco2_bar": pco2,
            "fco2_bar": fco2, "fugacity_coefficient": fug}


def _gas(conn, fid: str | None, since: str, until: str) -> dict:
    out = {}
    for q in ("co2", "h2s"):
        own = [float(r["value"]) for r in conn.execute(
            "select value from measurement where quantity = %s and fuzer_id = %s and at >= %s and at < %s",
            (q, fid, since, until))] if fid else []
        field = own or [float(r["value"]) for r in conn.execute(
            "select value from measurement where quantity = %s and at >= %s and at < %s", (q, since, until))]
        out[q] = (median(field), "the line's own gas samples" if own else f"the field's gas samples ({len(field)})"
                  ) if field else (None, None)
    return out


def screen(conn, until: str | None = None, days: int = 365) -> dict:
    """Each line with produced water chemistry in the window: predicted against measured corrosion rate."""
    until = until or ops.as_of(conn)
    since = (date.fromisoformat(until[:10]) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "select m.fuzer_id, m.quantity, m.value, m.unit, m.record from measurement m join entity e using (fuzer_id) "
        "where e.kind = 'pipeline' and m.quantity = any(%s) and m.at >= %s and m.at < %s order by m.at",
        (list(WATER), since, until)).fetchall()
    base = {"since": since, "until": until[:10],
            "thresholds": {"outlier_ratio": OUTLIER_RATIO, "sour_ratio": SOUR_RATIO},
            "method": "de Waard-Milliams (1991) CO2 corrosion, with the 1993 fugacity and protective scale factors; "
                      "uninhibited and water-wet",
            "source": "produced water chemistry (LIMS), gas CO2 and H2S (LIMS), GIS pipelines (MOP), CML wall "
                      "thickness readings"}
    by: dict[str, dict[str, list]] = {}
    for r in rows:
        by.setdefault(r["fuzer_id"], {}).setdefault(r["quantity"], []).append(r)
    if not by:
        return base | {"lines": [], "verdict": "no_water_chemistry", "summary": {"lines": 0},
                       "note": "No produced water chemistry recorded on a line in the window: no prediction is made."}
    at = date.fromisoformat(until[:10])
    lines = []
    for fid, qs in by.items():
        f = integrity._fields(conn, fid)
        label = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
        water = {q: round(median(float(r["value"]) for r in qs[q]), 2) for q in WATER if q in qs}
        gas = _gas(conn, fid, since, until)
        mop = integrity._num(f.get("mop_barg"))
        measured = integrity._line(conn, fid, until, None, at)
        row = {"line": label["label"] if label else fid, "id": fid, "service": f.get("service"), "mop_barg": mop,
               "water": water, "samples": max(len(v) for v in qs.values()),
               "co2_mol_pct": gas["co2"][0], "h2s_ppmv": gas["h2s"][0], "gas_from": gas["co2"][1],
               "measured_mm_per_year": measured["rate_mm_per_year"] if measured else None,
               "measured_cmls": measured["cmls"] if measured else 0,
               "records": [qs[q][-1]["record"] for q in WATER if q in qs][:2] + (measured["records"] if measured else [])}
        missing = [n for n, v in (("water temperature", water.get("sample_temperature")), ("MOP", mop),
                                  ("gas CO2", gas["co2"][0])) if v is None]
        if missing:
            row |= {"predicted_mm_per_year": None, "why": f"no {' or '.join(missing)} recorded, so no prediction"}
        else:
            p = mop + ATM_BAR
            ratio = (p * gas["co2"][0] / 100) / (p * gas["h2s"][0] / 1e6) if gas["h2s"][0] else None
            row["pco2_ph2s"] = round(ratio) if ratio else None
            if ratio is not None and ratio < SOUR_RATIO:
                row |= {"predicted_mm_per_year": None,
                        "why": f"pCO2/pH2S {ratio:.0f} is under {SOUR_RATIO:g}: H2S takes part, and a CO2 model "
                               "doesn't hold"}
            else:
                m = predicted_rate(water["sample_temperature"], p, gas["co2"][0])
                row |= {"predicted_mm_per_year": round(m["rate_mm_per_year"], 2), "pco2_bar": round(m["pco2_bar"], 3),
                        "fco2_bar": round(m["fco2_bar"], 3), "scale_factor": round(m["scale_factor"], 3)}
        if row["predicted_mm_per_year"] and row["measured_mm_per_year"] is not None:
            row["measured_over_predicted"] = round(row["measured_mm_per_year"] / row["predicted_mm_per_year"], 4)
        lines.append(row)
    ratios = [x["measured_over_predicted"] for x in lines if x.get("measured_over_predicted") is not None]
    typical = median(ratios) if len(ratios) >= 3 else None
    for x in lines:
        r = x.get("measured_over_predicted")
        x["flag"] = ("above_prediction" if r is not None and r > 1 else
                     "out_of_step" if r is not None and typical and r > OUTLIER_RATIO * typical else
                     "no_prediction" if x["predicted_mm_per_year"] is None else
                     "no_cml_rate" if r is None else None)
    rank = {"above_prediction": 0, "out_of_step": 1, None: 3, "no_cml_rate": 2, "no_prediction": 2}
    lines.sort(key=lambda x: (rank[x["flag"]], -(x.get("measured_over_predicted") or 0), x["line"]))
    flagged = [x for x in lines if x["flag"] in ("above_prediction", "out_of_step")]
    return base | {
        "lines": lines, "typical_ratio": round(typical, 4) if typical else None,
        "verdict": "flagged" if flagged else "in_step" if typical else "too_few_lines",
        "summary": {"lines": len(lines), "predicted": sum(x["predicted_mm_per_year"] is not None for x in lines),
                    "compared": len(ratios), "flagged": [x["line"] for x in flagged]},
        "note": ("The prediction is the uninhibited, water-wet CO2 rate at the line's MOP: a measured rate well under it "
                 "is expected. A line is out of step when its measured share of the prediction is over "
                 f"{OUTLIER_RATIO:g} times its peers' median, and above prediction when it beats the model outright. "
                 "A screening model, not an integrity verdict.")}
