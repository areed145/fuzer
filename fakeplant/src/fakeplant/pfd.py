"""Draws fake process flow diagrams (PFDs, PNG) for Claude to read (#475).

A PFD shows a unit's main equipment and the process streams between them, not instruments or valves. Each
stream carries a number in a diamond, and a heat and material balance table at the foot gives each stream's
conditions in the design case. The table runs the way real ones do: one column per stream, one row per property.
Trains built alike are drawn once, "typical of" all of them: the tag reads `20-K-2100A/B/C`, and a reader has to
expand it to three items. Streams entering or leaving the unit start or end at a battery-limit arrow with a label
(`FROM JACKETS`, `TO EXPORT`).

`draw` returns the answer key: every item with each tag it stands for and where its label sits (`tags`), each
stream between items expanded per train (`connections`, with the stream number), each stream with its ends as
drawn and its design case (`streams`), the stream table as drawn (`tables`), the notes and the degradation level, in the same shape as a P&ID's key so the same
scoring reads it.
"""

import random
from pathlib import Path

from . import pid

W, H = 2600, 1050
ROWS = ("DESCRIPTION", "PHASE", "PRESSURE (barg)", "TEMPERATURE (DEG C)", "FLOW")


def _diamond(d, x, y, n: int, font):
    d.polygon([(x, y - 22), (x + 26, y), (x, y + 22), (x - 26, y)], outline=0, fill=255, width=2)
    d.text((x, y), str(n), font=font, fill=0, anchor="mm")


def _expand(text: str) -> list[str]:
    """20-K-2100A/B/C -> the three tags it stands for."""
    if "/" not in text:
        return [text]
    head, *more = text.split("/")
    return [head] + [head[:-1] + s for s in more]


def draw(path: Path, sheet: dict, items: list[dict], streams: list[dict], rng: random.Random, level: str = "clean",
         notes: list[str] = ()) -> dict:
    """Render one PFD. `items`: [{tag (as drawn, `/` for typicals), func, title}] in process order; `streams`:
    [{n, from: item index or a battery-limit label, to: item index or label, description, phase, pressure,
    temperature, flow}] (conditions as text, "-" when not given)."""
    per_row = len(items)
    width = max(W, 300 + per_row * 250)
    table_h = pid.ROW * (len(ROWS) + 2)
    height = H + table_h + 30
    s = pid._Sheet(width, height, rng)
    d = s.d
    d.rectangle([20, 20, width - 20, height - 20], outline=0, width=4)
    tb = (width - 640, height - 210, width - 20, height - 20)
    d.rectangle(tb, outline=0, width=3)
    d.text((tb[0] + 20, tb[1] + 20), sheet["title"], font=s.f_tag if d.textlength(sheet["title"], font=s.f_tag) < 580
           else s.f_small, fill=0)
    d.text((tb[0] + 20, tb[1] + 75), f"DWG No. {sheet['sheet']}", font=s.f_tag, fill=0)
    d.text((tb[0] + 20, tb[1] + 130), f"REV {sheet['revision']}  {sheet.get('date', '')}  PROCESS FLOW DIAGRAM",
           font=s.f_small, fill=0)

    # equipment along one row, tall vessels up, machines down, tags and titles under each
    spacing = (width - 400) / per_row
    boxes, drawn = [], []
    for i, it in enumerate(items):
        cx = 220 + spacing * (i + 0.5)
        cy = 520 + (-60 if it["func"] in pid.TALL else 80 if it["func"] == "P" else 0) + rng.uniform(-15, 15)
        box = pid._equipment(d, it["func"], cx, cy)
        boxes.append(box)
        d.text((cx, box[3] + 30), it["tag"], font=s.f_tag, fill=0, anchor="mm")
        d.text((cx, box[3] + 58), pid._abbrev(it.get("title", ""), rng), font=s.f_small, fill=0, anchor="mm")
        drawn += [{"key": k, "kind": "equipment", "x": round(cx), "y": round(box[3] + 30), "as_drawn": it["tag"]}
                  for k in _expand(it["tag"])]

    def side(i, out):
        x0, y0, x1, y1 = boxes[i]
        return (x1, (y0 + y1) / 2) if out else (x0, (y0 + y1) / 2)

    conns = []
    for st in streams:
        a, b = st["from"], st["to"]
        if isinstance(a, int) and isinstance(b, int):
            p0, p1 = side(a, True), side(b, False)
            xm = (p0[0] + p1[0]) / 2
            pts = [p0, (xm, p0[1]), (xm, p1[1]), p1]
        elif isinstance(b, int):  # from a battery limit at the left
            p1 = side(b, False)
            pts = [(70, p1[1] - 120), (p1[0] - 40, p1[1] - 120), (p1[0] - 40, p1[1]), p1]
            d.text((70, p1[1] - 96), str(a), font=s.f_small, fill=0, anchor="lm")
        else:  # to a battery limit at the right
            p0 = side(a, True)
            pts = [p0, (p0[0] + 40, p0[1]), (p0[0] + 40, p0[1] + 160), (width - 80, p0[1] + 160)]
            d.text((width - 80, p0[1] + 190), str(b), font=s.f_small, fill=0, anchor="rm")
        pts = [(round(x), round(y)) for x, y in pts]
        pts = [p for k, p in enumerate(pts) if k == 0 or p != pts[k - 1]]
        s.line(pts, None)
        (x1, y1), (x2, y2) = max(pid._segments(pts), key=lambda sg: abs(sg[1][0] - sg[0][0]) + abs(sg[1][1] - sg[0][1]))
        _diamond(d, (x1 + x2) / 2, (y1 + y2) / 2 - 34, st["n"], s.f_small)
        froms = _expand(items[a]["tag"]) if isinstance(a, int) else [None]
        tos = _expand(items[b]["tag"]) if isinstance(b, int) else [None]
        if len(froms) == len(tos):  # train to train, one connection each
            pairs = list(zip(froms, tos, strict=True))
        else:  # a header into (or out of) every train
            pairs = [(f, t) for f in froms for t in tos]
        for f, t in pairs:
            if f and t:
                conns.append({"from": f, "to": t, "line": None, "stream": st["n"], "via": []})

    # the heat and material balance, one column per stream
    cols = [("STREAM No.", 260)] + [(str(st["n"]), max(150, min(260, int((width - 1400) / max(1, len(streams))))))
                                    for st in streams]
    rows = [[name] + [str(st.get(k) or "-") for st in streams]
            for name, k in zip(ROWS, ("description", "phase", "pressure", "temperature", "flow"), strict=True)]
    while sum(c[1] for c in cols) > width - 760 and cols[1][1] > 110:  # narrower columns on crowded sheets
        cols = [cols[0]] + [(n, w - 10) for n, w in cols[1:]]
    rows = [[r[0]] + [c if d.textlength(c, font=s.f_small) < w - 8 else pid._abbrev(c, rng)[: max(4, w // 11)]
                      for c, (_, w) in zip(r[1:], cols[1:], strict=True)] for r in rows]
    table = pid._table(s, 40, height - 40 - table_h, "HEAT AND MATERIAL BALANCE: DESIGN CASE", cols, rows)
    nk = []
    for k, n in enumerate(["NOTES:", *[f"{j + 1}. {t}" for j, t in enumerate(notes)]] if notes else []):
        y = 140 + 34 * k
        d.text((width - 900, y), n, font=s.f_small, fill=0)
        if k:
            nk.append({"n": k, "text": n.split(". ", 1)[1], "tags": [], "x": width - 900, "y": y})

    def label(end):  # a stream's end as drawn: an item's tag (typicals with their /) or a battery-limit label
        return items[end]["tag"] if isinstance(end, int) else str(end)

    tear: list = []
    img = pid._degrade(s.img, level, rng, tear)
    if tear:
        for x in drawn + table["rows"]:
            if pid._in_tear(x["x"], x["y"], tear[0]):
                x["hidden"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return {"kind": "pfd", "size": [width, height], "level": level, "tags": drawn, "lines": [], "connections": conns,
            "valves": [], "loops": [], "offpage": [], "unregistered": [], "crossings": s.crossings,
            "tables": [{"kind": "streams", **table}], "notes": nk,
            "streams": [{"n": st["n"], "from": label(st["from"]), "to": label(st["to"]),
                         **{k: st.get(k) for k in ("description", "phase", "pressure", "temperature", "flow")}}
                        for st in streams],
            **({"date": sheet["date"]} if sheet.get("date") else {})}
