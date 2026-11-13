"""Vibration spectra (#367): what a condition-monitoring spectrum says about why a machine's vibration rose. Each
measurement point's spectra are read against the frequencies its faults would show at: running speed (1x) and its
second harmonic (2x), and the four rolling element bearing defect frequencies from the bearing's geometry. Fault
indications for the reliability engineer to confirm, never a diagnosis.

    spectra(conn, tag=None)   per point: its latest spectrum, the fault frequencies, the peaks that sit on them, and
                              the indications with the month each first showed

A bearing defect is indicated when its frequency shows at least BEARING_HARMONICS of its first three harmonics above
AMPLITUDE_MIN; a lone peak can be anything. 2x over MISALIGN_RATIO of 1x suggests misalignment, and 1x dominant over
UNBALANCE_RATIO of the overall level suggests unbalance. Peaks within TOLERANCE_PCT of a frequency sit on it; a
bearing harmonic that lands on a whole order of running speed can't be told from it and isn't counted.
"""

import math

TOLERANCE_PCT = 1.5  # a peak within this share of a fault frequency sits on it (speed and resolution spread)
AMPLITUDE_MIN = 0.1  # mm/s RMS: a peak under this is the spectrum's floor
BEARING_HARMONICS = 2  # of the first three harmonics, at least this many must show for a bearing indication
MISALIGN_RATIO = 0.5  # 2x over this share of 1x: a misalignment indication
UNBALANCE_RATIO = 0.9  # 1x over this share of the overall level: an unbalance indication
BEARING = {"bpfo": "ball pass frequency, outer race", "bpfi": "ball pass frequency, inner race",
           "bsf": "ball spin frequency", "ftf": "fundamental train (cage) frequency"}


def bearing_frequencies(rpm: float, elements: float, element_d: float, pitch_d: float, angle_deg: float = 0.0) -> dict:
    """The rolling element bearing defect frequencies (Hz) at a shaft speed, from the bearing's geometry."""
    fr = rpm / 60
    r = element_d / pitch_d * math.cos(math.radians(angle_deg))
    return {"bpfo": elements / 2 * fr * (1 - r), "bpfi": elements / 2 * fr * (1 + r),
            "bsf": pitch_d / (2 * element_d) * fr * (1 - r * r), "ftf": fr / 2 * (1 - r)}


def parse_peaks(text: str | None) -> list[tuple[float, float]]:
    """A peak table "frequency:amplitude;..." as (Hz, amplitude) pairs; malformed entries are left out."""
    out = []
    for part in (text or "").split(";"):
        try:
            f, a = part.split(":")
            out.append((float(f), float(a)))
        except ValueError:
            continue
    return sorted(out)


def _at(peaks: list[tuple[float, float]], f: float) -> float | None:
    near = [a for p, a in peaks if abs(p - f) <= f * TOLERANCE_PCT / 100]
    return max(near) if near else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read(s: dict) -> dict:
    """One spectrum's fault frequencies, the peaks on them, and its indications."""
    peaks = parse_peaks(s.get("peaks"))
    rpm = _num(s.get("rpm"))
    if not rpm or not peaks:
        return {"frequencies": {}, "indications": [], "why": "no running speed or no peaks on the spectrum"}
    fr = rpm / 60
    freqs = {"1x": fr, "2x": 2 * fr}
    geo = [_num(s.get(k)) for k in ("rolling_elements", "element_diameter_mm", "pitch_diameter_mm")]
    if all(geo):
        freqs |= bearing_frequencies(rpm, *geo, _num(s.get("contact_angle_deg")) or 0.0)
    ind = []
    for k, reads in BEARING.items():
        if k not in freqs:
            continue
        # a harmonic that lands on a running speed harmonic (a whole order) can't be told from it, so it isn't counted
        hits = [(h, _at(peaks, h * freqs[k])) for h in (1, 2, 3)
                if abs(h * freqs[k] - round(h * freqs[k] / fr) * fr) > h * freqs[k] * TOLERANCE_PCT / 100]
        shown = [(h, a) for h, a in hits if a is not None and a >= AMPLITUDE_MIN]
        if len(shown) >= BEARING_HARMONICS:
            ind.append({"fault": k, "reads": reads, "hz": round(freqs[k], 1), "order": round(freqs[k] / fr, 3),
                        "amplitude": round(max(a for _, a in shown), 3), "harmonics": [h for h, _ in shown]})
    one, two, overall = _at(peaks, fr), _at(peaks, 2 * fr), _num(s.get("overall"))
    if one and two and two > MISALIGN_RATIO * one and two >= AMPLITUDE_MIN:
        ind.append({"fault": "misalignment", "reads": "2x high against 1x", "hz": round(2 * fr, 1), "order": 2.0,
                    "amplitude": round(two, 3), "ratio_2x_1x": round(two / one, 2)})
    if one and overall and one > UNBALANCE_RATIO * overall:
        ind.append({"fault": "unbalance", "reads": "1x dominant", "hz": round(fr, 1), "order": 1.0,
                    "amplitude": round(one, 3), "share_of_overall": round(one / overall, 2)})
    return {"frequencies": {k: round(v, 2) for k, v in freqs.items()}, "indications": ind,
            "geometry": bool(all(geo)) or "no bearing geometry on the point, so no bearing frequencies"}


def spectra(conn, tag: str | None = None) -> dict:
    """Each measurement point's latest spectrum read against its fault frequencies, with when each indication began."""
    rows = conn.execute(
        """
        select s.fuzer_id, jsonb_object_agg(g.field, g.value #>> '{}') as f, c.dst as tag, l.label as tag_label
        from entity s join golden g on g.fuzer_id = s.fuzer_id
        join edge c on c.src = s.fuzer_id and c.rel = 'checks' and c.valid_to is null
        left join entity_label l on l.fuzer_id = c.dst
        where s.kind = 'vibration_spectrum' and s.retired_at is null and s.merged_into is null
        group by s.fuzer_id, c.dst, l.label
        """).fetchall()
    if tag:
        rows = [r for r in rows if r["tag"] == tag or (r["tag_label"] or "").lower() == tag.lower()]
    base = {"thresholds": {"tolerance_pct": TOLERANCE_PCT, "amplitude_min": AMPLITUDE_MIN,
                           "bearing_harmonics": BEARING_HARMONICS, "misalign_ratio": MISALIGN_RATIO,
                           "unbalance_ratio": UNBALANCE_RATIO},
            "source": "condition-monitoring spectra (peak tables) with each point's bearing setup"}
    if not rows:
        return base | {"points": [], "verdict": "no_spectra", "summary": {"points": 0},
                       "note": "No condition-monitoring spectrum recorded" + (" on that machine." if tag else ".")
                               + " Overall vibration says it rose, not why."}
    by: dict[tuple, list] = {}
    for r in rows:
        by.setdefault((r["tag_label"] or r["tag"], r["f"].get("point") or ""), []).append(r)
    points = []
    for (name, point), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["f"].get("measured_at") or "")
        history = [(r["f"].get("measured_at", "")[:10], read(r["f"])) for r in rs]
        last, latest = rs[-1]["f"], history[-1][1]
        first = {}
        for when, h in history:
            now = {i["fault"] for i in h["indications"]}
            for k in list(first):
                if k not in now:
                    del first[k]  # it went away: a later return counts from then
            for k in now:
                first.setdefault(k, when)
        for i in latest["indications"]:
            i["since"] = first.get(i["fault"])
            i["trend"] = [{"date": w, "amplitude": next((x["amplitude"] for x in h["indications"] if x["fault"] == i["fault"]),
                                                        None)} for w, h in history if w >= (i["since"] or "")]
        points.append({"tag": name, "id": rs[-1]["tag"], "point": point, "spectra": len(rs),
                       "latest": last.get("measured_at", "")[:10], "rpm": _num(last.get("rpm")),
                       "overall": _num(last.get("overall")), "unit": last.get("unit"), "bearing": last.get("bearing"),
                       "overall_first": _num(rs[0]["f"].get("overall")), **latest,
                       "spectrum": rs[-1]["fuzer_id"]})
    flagged = [p for p in points if p["indications"]]
    flagged.sort(key=lambda p: -max(i["amplitude"] for i in p["indications"]))
    return base | {
        "points": flagged + [p for p in points if not p["indications"]],
        "verdict": "indications" if flagged else "no_indications",
        "summary": {"points": len(points), "machines": len({p["id"] for p in points}),
                    "with_indications": [f"{p['tag']} {p['point']}" for p in flagged]},
        "note": (f"A bearing defect shows when {BEARING_HARMONICS} of its first three harmonics stand above "
                 f"{AMPLITUDE_MIN:g} mm/s; peaks within {TOLERANCE_PCT:g}% of a frequency sit on it. Fault indications "
                 "for the reliability engineer to confirm, not a diagnosis.")}
