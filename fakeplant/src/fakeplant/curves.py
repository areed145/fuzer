"""OEM performance curves (#92): pump curves (head, efficiency, NPSHr and power against flow) and choke
characteristics (Cv against opening) per equipment model, the way vendors deliver them: a CSV export
for some models, a datasheet PDF with a data table and the curve drawn for others, and a scanned
datasheet (an image in a PDF, no text) for the rest. The same curves drive the process model, and are
written as answer keys so curves Claude reads off datasheets can be scored."""

import hashlib
import io
import random
from pathlib import Path

RHO, G = 850.0, 9.81  # crude oil


def _rng(*key) -> random.Random:
    return random.Random(int(hashlib.sha1("|".join(map(str, key)).encode()).hexdigest()[:8], 16))


def pump(maker: str, model: str, q_bep: float | None = None, h_bep: float | None = None) -> dict:
    """A centrifugal pump's curves at rated speed, from affinity-law consistent polynomials."""
    r = _rng(maker, model)
    q_bep = q_bep or round(r.uniform(140, 240), 0)
    h_bep = h_bep or round(r.uniform(380, 480), 0)
    h0 = round(h_bep * r.uniform(1.17, 1.24), 0)
    eff_bep = round(r.uniform(0.74, 0.82), 3)
    flows = [round(q_bep * f, 1) for f in (0, 0.2, 0.4, 0.55, 0.7, 0.85, 1.0, 1.1, 1.2, 1.3)]

    def head(q):
        return h0 - (h0 - h_bep) * (q / q_bep) ** 2

    def eff(q):
        return max(0.0, eff_bep * (1 - (q / q_bep - 1) ** 2))

    def npshr(q):
        return 3.5 + 4.0 * (q / q_bep) ** 2.2

    def power(q):  # kW; at zero flow the losses
        return RHO * G * (q / 3600) * head(q) / max(eff(q), 0.25) / 1000 if q else RHO * G * (q_bep / 3600) * h0 * 0.35 / 1000

    series = {"head": [(q, round(head(q), 1)) for q in flows],
              "efficiency": [(q, round(eff(q) * 100, 1)) for q in flows if q],
              "npshr": [(q, round(npshr(q), 2)) for q in flows if q],
              "power": [(q, round(power(q), 0)) for q in flows]}
    return {"kind": "pump", "manufacturer": maker, "model": model, "rated_speed_rpm": 2980,
            "impeller_mm": round(r.uniform(330, 410), 0), "q_bep": q_bep, "h_bep": h_bep, "h0": h0, "eff_bep": eff_bep,
            "mcsf": round(0.3 * q_bep, 0), "por": [round(0.7 * q_bep, 0), round(1.2 * q_bep, 0)],
            "aor": [round(0.5 * q_bep, 0), round(1.3 * q_bep, 0)], "series": series,
            "units": {"x": "m3/h", "head": "m", "efficiency": "%", "npshr": "m", "power": "kW"}}


def choke(maker: str, model: str, cv_max: float, trim: str = "equal_percentage", trim_in: float = 2.0) -> dict:
    """A production choke's flow characteristic: Cv against opening."""
    opens = list(range(0, 101, 10))

    def cv(o):
        return cv_max * o / 100 if trim == "linear" else cv_max * 30 ** (o / 100 - 1) if o else 0.0

    return {"kind": "choke", "manufacturer": maker, "model": model, "trim": trim, "trim_in": trim_in, "cv_max": cv_max,
            "xt": 0.72, "series": {"cv": [(o, round(cv(o), 2)) for o in opens]}, "units": {"x": "%", "cv": "Cv (US)"}}


def cv_at(c: dict, opening: float) -> float:
    """Cv at an opening, interpolated on the characteristic."""
    pts = c["series"]["cv"]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= opening <= x1:
            return y0 + (y1 - y0) * (opening - x0) / (x1 - x0)
    return pts[-1][1]


def csv_rows(c: dict) -> list[dict]:
    """A vendor curve export: one row per point, plus the rating figures."""
    rows = []
    for name, pts in c["series"].items():
        for x, y in pts:
            rows.append({"manufacturer": c["manufacturer"], "model": c["model"], "curve": c["kind"], "series": name,
                         "x": x, "y": y, "x_unit": c["units"]["x"], "y_unit": c["units"][name]})
    extra = ({"bep": (c["q_bep"], c["h_bep"]), "mcsf": (c["mcsf"], ""), "por": tuple(c["por"]), "aor": tuple(c["aor"]),
              "rated_speed": (c["rated_speed_rpm"], ""), "impeller": (c["impeller_mm"], "")}
             if c["kind"] == "pump" else {"cv_max": (c["cv_max"], ""), "trim_size": (c["trim_in"], ""), "xt": (c["xt"], "")})
    for name, (x, y) in extra.items():
        rows.append({"manufacturer": c["manufacturer"], "model": c["model"], "curve": c["kind"], "series": name,
                     "x": x, "y": y, "x_unit": "", "y_unit": ""})
    return rows


def datasheet(c: dict, path: Path, scanned: bool, doc_number: str, table: bool = True) -> None:
    """A one-page datasheet: title block, the curve and (with `table`) its data table. `scanned` makes it
    an image in a PDF with no text layer, slightly rotated, the way an old datasheet comes back from a
    scanner; without a table the values can only be read off the drawn curve."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.08, 0.95, f"{c['manufacturer']} {c['model']}", fontsize=16, weight="bold")
    fig.text(0.08, 0.925, ("Centrifugal pump performance curve" if c["kind"] == "pump" else "Production choke flow characteristic")
             + f"   Doc {doc_number}", fontsize=10)
    ax = fig.add_axes([0.12, 0.42, 0.78, 0.45])
    if c["kind"] == "pump":
        q, h = zip(*c["series"]["head"])
        ax.plot(q, h, "k-", lw=2, label="Head (m)")
        ax.set_xlabel("Flow (m3/h)")
        ax.set_ylabel("Head (m)")
        ax2 = ax.twinx()
        qe, e = zip(*c["series"]["efficiency"])
        ax2.plot(qe, e, "k--", lw=1.5, label="Efficiency (%)")
        ax2.set_ylabel("Efficiency (%)")
        ax2.set_ylim(0, 100)
        ax.axvline(c["q_bep"], color="grey", lw=0.8, ls=":")
        ax.text(c["q_bep"], c["h_bep"] * 1.02, " BEP", fontsize=9)
        ax.set_title(f"Rated speed {c['rated_speed_rpm']} rpm, impeller {c['impeller_mm']:.0f} mm, SG 0.85", fontsize=9)
        rows = [["Flow m3/h", "Head m", "Eff %", "NPSHr m", "Power kW"]]
        eff = dict(c["series"]["efficiency"])
        npsh = dict(c["series"]["npshr"])
        for (qq, hh), (_, p) in zip(c["series"]["head"], c["series"]["power"]):
            rows.append([f"{qq:.0f}", f"{hh:.0f}", f"{eff.get(qq, 0):.1f}", f"{npsh.get(qq, 0):.1f}", f"{p:.0f}"])
        notes = (f"BEP {c['q_bep']:.0f} m3/h at {c['h_bep']:.0f} m. Minimum continuous stable flow {c['mcsf']:.0f} m3/h. "
                 f"Preferred operating region {c['por'][0]:.0f}-{c['por'][1]:.0f} m3/h; allowable {c['aor'][0]:.0f}-{c['aor'][1]:.0f} m3/h.")
    else:
        o, cv = zip(*c["series"]["cv"])
        ax.plot(o, cv, "k-", lw=2)
        ax.set_xlabel("Opening (%)")
        ax.set_ylabel("Cv (US gpm/psi^0.5)")
        ax.set_title(f"{c['trim'].replace('_', ' ')} trim, {c['trim_in']} in, xT {c['xt']}", fontsize=9)
        rows = [["Opening %", "Cv"]] + [[f"{a}", f"{b:.2f}"] for a, b in c["series"]["cv"]]
        notes = f"Maximum Cv {c['cv_max']} at full opening. Pressure recovery factor xT {c['xt']}."
    ax.grid(True, lw=0.4)
    if table:
        tab = fig.add_axes([0.12, 0.1, 0.78, 0.25])
        tab.axis("off")
        tab.table(cellText=rows, loc="upper center", cellLoc="center").scale(1, 1.1)
    fig.text(0.08, 0.06, notes, fontsize=8, wrap=True)
    if not scanned:
        fig.savefig(path, format="pdf")
        plt.close(fig)
        return
    from PIL import Image, ImageFilter

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    img = Image.open(buf).convert("L").rotate(0.8, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    img.convert("RGB").save(path, "PDF", resolution=110)


def truth(c: dict) -> dict:
    keep = {k: v for k, v in c.items() if k != "series"}
    return {**keep, "series": {k: [list(p) for p in v] for k, v in c["series"].items()}}
