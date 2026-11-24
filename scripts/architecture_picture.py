"""Draw the architecture picture for the docs: docs/assets/diagrams/fuzer-architecture-{light,dark}.svg.

    uv run python scripts/architecture_picture.py

One drawing, two themes. The docs show the light file in the light scheme and the dark file in the slate scheme
(`#only-light` / `#only-dark`), so each file carries its own colours and no web fonts. Sources and logic sit below the
register and the work model; the surfaces people and agents use sit above; approved updates go back down the right
edge to the systems of action. Colours follow ui/src/styles.css: the brand ink and hub, the kind colours for entities
and the Ops IQ teal-to-violet gradient, which marks AI and nothing else.
"""

from __future__ import annotations

import math
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "diagrams"
W, H = 1280, 930
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
SPARK = ("M10 3.5c.7 3.9 2.6 5.8 6.5 6.5-3.9.7-5.8 2.6-6.5 6.5-.7-3.9-2.6-5.8-6.5-6.5 3.9-.7 5.8-2.6 6.5-6.5z"
         "M18 13.5c.35 1.9 1.1 2.65 3 3-1.9.35-2.65 1.1-3 3-.35-1.9-1.1-2.65-3-3 1.9-.35 2.65-1.1 3-3z")

THEMES = {
    "light": {
        "bg": "#f4f7fa", "card": "#ffffff", "text": "#16222f", "muted": "#5b6b7d", "line": "#c3cfdb",
        "wire": "#9fb1c3", "ink": "#1d3b5c", "hub": "#0f8f9c", "plane": "#e6eff4", "hex": "#cddbe5",
        "planeEdge": "#9fbccc", "iqFrom": "#0f8f9c", "iqTo": "#5b4fd6", "onInk": "#ffffff",
        "data": "#e3edf8", "dataEdge": "#b9cfe8", "logic": "#dff1f2", "logicEdge": "#aedbdf",
        "action": "#f5ebdf", "actionEdge": "#e2c9a8", "tile": "#f3f7fa",
        "kinds": {"facility": "#5f6b7a", "area": "#1f9e9e", "tag": "#2f7bd8", "equipment": "#d9822b",
                  "well": "#8a5cd6", "pipeline": "#4f8a8b", "document": "#7b8794", "timeseries": "#2a9d5c"},
    },
    "dark": {
        "bg": "#111821", "card": "#18222e", "text": "#e6e9ee", "muted": "#97a6b7", "line": "#2c3b4c",
        "wire": "#4a6077", "ink": "#a9bdd4", "hub": "#3fc6d4", "plane": "#15212d", "hex": "#223243",
        "planeEdge": "#35506a", "iqFrom": "#1fa9b8", "iqTo": "#7b6cf6", "onInk": "#0b1118",
        "data": "#18283a", "dataEdge": "#2d4866", "logic": "#142b2f", "logicEdge": "#26545a",
        "action": "#2a2219", "actionEdge": "#5a4630", "tile": "#111821",
        "kinds": {"facility": "#9aa6b6", "area": "#4fc9c9", "tag": "#6aa8f0", "equipment": "#f0a35a",
                  "well": "#b391f0", "pipeline": "#7fbcbd", "document": "#a7b2c0", "timeseries": "#5fcf8f"},
    },
}


def text(x, y, s, size=13, weight=400, fill="text", anchor="start", spacing=0.0, t=None):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{t[fill]}" '
            f'text-anchor="{anchor}"{ls}>{escape(s)}</text>')


def label(x, y, s, t, anchor="start", fill="muted"):
    """A section label: small capitals, spaced."""
    return text(x, y, s.upper(), 11, 600, fill, anchor, 1.4, t)


def width(s, size=13):
    """Rough text width for the system font stack; enough to size chips."""
    narrow = sum(1 for c in s if c in "iljtfr .,·:'()|")
    return size * (0.56 * (len(s) - narrow) + 0.3 * narrow)


def spark(x, y, size, fill):
    k = size / 20
    return f'<path d="{SPARK}" transform="translate({x} {y}) scale({k})" fill="{fill}"/>'


def chip(x, y, w, h, s, t, fill="card", edge="line", color="text", size=12.5, weight=500, ai=False):
    if ai:
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}" fill="url(#iq)"/>',
               spark(x + 9, y + h / 2 - 8, 16, "#ffffff"),
               f'<text x="{x + w / 2 + 9}" y="{y + h / 2 + 4.5}" font-size="{size}" font-weight="600" '
               f'fill="#ffffff" text-anchor="middle">{escape(s)}</text>']
        return "".join(out)
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(8, h / 2)}" fill="{t[fill]}" '
            f'stroke="{t[edge]}"/>' + text(x + w / 2, y + h / 2 + 4.5, s, size, weight, color, "middle", t=t))


def card(x, y, w, h, t, fill="card", edge="line"):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{t[fill]}" stroke="{t[edge]}"/>'


def hexagon(cx, cy, r):
    pts = []
    for i in range(6):

        a = math.radians(60 * i - 90)
        pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
    return " ".join(pts)


def arrow(d, t, color="wire", dash=False, width_=1.4, head=True, marker="a"):
    da = ' stroke-dasharray="5 4"' if dash else ""
    mk = f' marker-end="url(#{marker})"' if head else ""
    return f'<path d="{d}" fill="none" stroke="{t[color]}" stroke-width="{width_}"{da}{mk}/>'


# --- the bands -------------------------------------------------------------------------------------------------------

def people(t):
    out = [label(W / 2, 40, "Who works the cases", t, "middle")]
    roles = ["IOC operator", "Field operator", "Planner", "Data steward", "Engineer"]
    ws = [width(r, 13) + 30 for r in roles]
    iq = width("Ops IQ specialists", 13) + 50
    total = sum(ws) + iq + 12 * len(roles) + 26
    x = (W - total) / 2
    for r, w in zip(roles, ws, strict=True):
        out.append(f'<rect x="{x}" y="54" width="{w}" height="30" rx="15" fill="{t["card"]}" stroke="{t["ink"]}"/>')
        out.append(text(x + w / 2, 73.5, r, 13, 500, "text", "middle", t=t))
        x += w + 12
    out.append(text(x + 6, 74, "+", 15, 600, "muted", "middle", t=t))
    out.append(chip(x + 18, 54, iq, 30, "Ops IQ specialists", t, size=13, ai=True))
    return out


def screen_tile(x, y, w, h, name, glyph, t):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{t["tile"]}" stroke="{t["line"]}"/>']
    gx, gy = x + 12, y + 12
    ink, hub, wire = t["ink"], t["hub"], t["wire"]
    if glyph == "map":
        out.append(f'<path d="M{gx} {gy + 6} l12 -5 12 5 12 -5 v24 l-12 5 -12 -5 -12 5z" fill="none" stroke="{wire}"/>')
        out.append(f'<polygon points="{hexagon(gx + 18, gy + 14, 5)}" fill="{hub}"/>')
        out.append(f'<circle cx="{gx + 30}" cy="{gy + 21}" r="2.6" fill="{t["kinds"]["equipment"]}"/>')
        out.append(f'<circle cx="{gx + 8}" cy="{gy + 20}" r="2.6" fill="{t["kinds"]["well"]}"/>')
    elif glyph == "list":
        for i in range(3):
            yy = gy + 4 + i * 10
            out.append(f'<rect x="{gx}" y="{yy - 3}" width="6" height="6" rx="1.5" fill="none" stroke="{hub}"/>')
            out.append(f'<path d="M{gx + 11} {yy}h{26 - i * 5}" stroke="{wire}" stroke-width="2.2" '
                       'stroke-linecap="round"/>')
    elif glyph == "board":
        for i in range(3):
            out.append(f'<rect x="{gx + i * 13}" y="{gy}" width="10" height="28" rx="2" fill="none" stroke="{wire}"/>')
            for j in range(2 - i % 2):
                out.append(f'<rect x="{gx + i * 13 + 2}" y="{gy + 3 + j * 8}" width="6" height="5" rx="1" '
                           f'fill="{hub if i == 1 else ink}"/>')
    elif glyph == "plan":
        for i, (dx, ww) in enumerate([(0, 18), (8, 22), (16, 16), (4, 28)]):
            out.append(f'<rect x="{gx + dx}" y="{gy + i * 7}" width="{ww}" height="4.5" rx="2" '
                       f'fill="{hub if i == 1 else wire}"/>')
    elif glyph == "explore":
        out.append(f'<path d="M{gx + 8} {gy + 22} L{gx + 20} {gy + 10} L{gx + 34} {gy + 20}" stroke="{wire}" '
                   'fill="none"/>')
        out.append(f'<polygon points="{hexagon(gx + 20, gy + 10, 6)}" fill="{t["kinds"]["equipment"]}"/>')
        out.append(f'<polygon points="{hexagon(gx + 8, gy + 22, 5)}" fill="{t["kinds"]["area"]}"/>')
        out.append(f'<polygon points="{hexagon(gx + 34, gy + 20, 5)}" fill="{t["kinds"]["tag"]}"/>')
    elif glyph == "reports":
        for i, hh in enumerate([12, 20, 15, 26]):
            out.append(f'<rect x="{gx + i * 9}" y="{gy + 28 - hh}" width="6" height="{hh}" rx="1.5" '
                       f'fill="{hub if i == 3 else wire}"/>')
    out.append(text(x + 62, y + h / 2 + 5, name, 13, 600, t=t))
    return out


def surfaces(t):
    out = []
    # IOC and field surfaces
    x, y, w, h = 40, 128, 470, 196
    out += [label(x, y - 10, "IOC and field surfaces", t), card(x, y, w, h, t)]
    tiles = [("Tower map", "map"), ("My work", "list"), ("All work", "board"),
             ("Plan", "plan"), ("Explore entity", "explore"), ("Reports", "reports")]
    tw, th = 140, 52
    for i, (name, g) in enumerate(tiles):
        out += screen_tile(x + 16 + (i % 3) * (tw + 13), y + 18 + (i // 3) * (th + 12), tw, th, name, g, t)
    out.append(text(x + 16, y + h - 26, "Triage, dispatch, hand over, close out", 12.5, 500, "muted", t=t))
    out.append(text(x + 16, y + h - 10, "Phone, iPad and desktop; installable for the field", 12.5, 400, "muted",
                    t=t))

    # Ops IQ
    x, w = 530, 250
    out += [label(x, y - 10, "Ops IQ", t),
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{t["card"]}" stroke="url(#iq)" '
            'stroke-width="2"/>',
            f'<rect x="{x + 1}" y="{y + 1}" width="{w - 2}" height="40" rx="11" fill="url(#iq)"/>',
            f'<rect x="{x + 1}" y="{y + 30}" width="{w - 2}" height="11" fill="url(#iq)"/>',
            spark(x + 14, y + 11, 20, "#ffffff"),
            f'<text x="{x + 42}" y="{y + 26}" font-size="14" font-weight="700" fill="#ffffff">'
            'Operations agent</text>']
    for i, s in enumerate(["Ask about any entity", "Watch for what changed", "Brief a case", "Draft the next step"]):
        yy = y + 56 + i * 30
        out.append(f'<circle cx="{x + 22}" cy="{yy + 9}" r="3.5" fill="url(#iq)"/>')
        out.append(text(x + 34, yy + 13.5, s, 13, 500, t=t))

    # APIs
    x, w = 800, 210
    out += [label(x, y - 10, "APIs", t), card(x, y, w, h, t)]
    for i, (a, b) in enumerate([("HTTP API", "apps and dashboards"), ("MCP server", "AI agents"),
                                ("Command line", "fuzer …"), ("Events", "outbox, webhooks")]):
        yy = y + 16 + i * 44
        out.append(f'<rect x="{x + 14}" y="{yy}" width="{w - 28}" height="36" rx="8" fill="{t["tile"]}" '
                   f'stroke="{t["line"]}"/>')
        out.append(text(x + 26, yy + 16, a, 13, 600, t=t))
        out.append(text(x + 26, yy + 30, b, 11.5, 400, "muted", t=t))

    # Write-back
    x, w = 1030, 210
    out += [label(x, y - 10, "Write-back", t), card(x, y, w, h, t)]
    steps = [("Draft", "update for the source"), ("Approve", "a named person"), ("Send", "SAP PM, or an export")]
    for i, (a, b) in enumerate(steps):
        yy = y + 18 + i * 56
        out.append(f'<circle cx="{x + 28}" cy="{yy + 16}" r="12" fill="{t["hub"] if i == 1 else t["tile"]}" '
                   f'stroke="{t["hub"]}"/>')
        out.append(text(x + 28, yy + 20.5, str(i + 1), 12, 700, "onInk" if i == 1 else "hub", "middle", t=t))
        out.append(text(x + 50, yy + 13, a, 13, 600, t=t))
        out.append(text(x + 50, yy + 29, b, 11.5, 400, "muted", t=t))
        if i < 2:
            out.append(f'<path d="M{x + 28} {yy + 30} v28" stroke="{t["line"]}" stroke-width="1.5"/>')
    return out


def bundle(x0, x1, y0, y1, n, t, color="wire", up=False, marker="a"):
    """`n` curves fanning from a span on one edge to a span on another: data moving between bands."""
    out = []
    for i in range(n):
        f = i / (n - 1) if n > 1 else 0.5
        a = x0[0] + (x0[1] - x0[0]) * f
        b = x1[0] + (x1[1] - x1[0]) * f
        m = (y0 + y1) / 2
        d = f"M{a:.1f} {y0} C{a:.1f} {m} {b:.1f} {m} {b:.1f} {y1}"
        stroke = "url(#iqv)" if color == "iq" else t[color]
        mk = f' marker-end="url(#{marker})"' if up or marker == "a" else ""
        out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="1.3"{mk}/>')
    return out


def register(t):
    x, y, w, h = 40, 386, 1200, 252
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{t["plane"]}" stroke="{t["planeEdge"]}" '
           'stroke-width="1.5"/>',
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="url(#hexes)"/>',
           label(x + 24, y + 28, "Register and ontology", t, fill="ink"),
           label(668, y + 28, "Common work model", t, fill="ink"),
           f'<path d="M644 {y + 18} V{y + h - 18}" stroke="{t["planeEdge"]}" stroke-dasharray="3 5"/>']

    # Entity callout
    cx, cy, cw, ch = 64, 430, 196, 150
    k = t["kinds"]
    out += [card(cx, cy, cw, ch, t),
            f'<polygon points="{hexagon(cx + 22, cy + 22, 9)}" fill="{k["equipment"]}"/>',
            text(cx + 40, cy + 21, "K-101", 14, 700, t=t),
            text(cx + 40, cy + 36, "Compressor · equipment", 11.5, 400, "muted", t=t),
            f'<path d="M{cx + 12} {cy + 48} h{cw - 24}" stroke="{t["line"]}"/>']
    rows = [("Sources", "SAP · JDE · P&ID"), ("Golden values", "14"), ("Trends", "PI · ADX"), ("Open cases", "2")]
    for i, (a, b) in enumerate(rows):
        yy = cy + 68 + i * 21
        out.append(text(cx + 14, yy, a, 12, 400, "muted", t=t))
        out.append(text(cx + cw - 14, yy, b, 12, 600, "text", "end", t=t))

    # Entities and links
    nodes = {
        "eq": (340, 492, "equipment", "K-101"), "area": (440, 438, "area", "Train 2"),
        "fac": (566, 450, "facility", "Okan field"), "tag": (452, 546, "tag", "PT-1101"),
        "doc": (360, 592, "document", "P&ID 102"), "well": (596, 536, "well", "OK-14"),
        "pipe": (548, 594, "pipeline", "Flowline 7"),
    }
    links = [("fac", "area"), ("fac", "well"), ("area", "eq"), ("eq", "tag"), ("eq", "doc"), ("well", "pipe"),
             ("pipe", "tag")]
    for a, b in links:
        (x1, y1, *_), (x2, y2, *_) = nodes[a], nodes[b]
        out.append(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{t["ink"]}" stroke-opacity=".45" stroke-width="1.6"/>')
    out.append(f'<path d="M{cx + cw} {nodes["eq"][1]} H{nodes["eq"][0] - 20}" '
               f'stroke="{k["equipment"]}" stroke-width="1.4" stroke-dasharray="3 3" fill="none"/>')
    for key, (nx, ny, kind, name) in nodes.items():
        r = 15 if key == "eq" else 12
        out.append(f'<polygon points="{hexagon(nx, ny, r + 4)}" fill="{t["plane"]}"/>')
        out.append(f'<polygon points="{hexagon(nx, ny, r)}" fill="{k[kind]}"/>')
        out.append(f'<circle cx="{nx}" cy="{ny}" r="{r * 0.32:.1f}" fill="{t["plane"]}"/>')
        below = ny + r + 15
        if key in ("pipe", "doc", "well", "tag"):
            out.append(text(nx + r + 8, ny + 4.5, name, 12, 600, t=t))
        elif key == "fac":
            out.append(text(nx, ny - r - 8, name, 12, 600, "text", "middle", t=t))
        else:
            out.append(text(nx, below, name, 12, 600, "text", "middle", t=t))
    out.append(text(x + 24, y + h - 14, "Matched across sources: crosswalk, golden values, dated links, review queue",
                    11.5, 400, "muted", t=t))

    # Work model
    def obj(cx_, cy_, name, strong=False):
        w_ = 96
        fill = t["ink"] if strong else t["card"]
        col = "onInk" if strong else "text"
        return (f'<rect x="{cx_ - w_ / 2}" y="{cy_ - 15}" width="{w_}" height="30" rx="8" fill="{fill}" '
                f'stroke="{t["ink"]}"/>' + text(cx_, cy_ + 4.5, name, 13, 600, col, "middle", t=t))

    c1, c2, c3, c4 = 716, 870, 1024, 1172
    r1, r2, r3 = 454, 528, 602
    out += [
        arrow(f"M{c1} {r1 + 15} V{r2 - 18}", t, "ink"),
        text(c1 + 8, (r1 + r2) / 2 + 4, "raises", 11, 400, "muted", t=t),
        arrow(f"M{c2} {r1 + 15} V{r2 - 18}", t, "ink"),
        text(c2 + 8, (r1 + r2) / 2 + 4, "groups", 11, 400, "muted", t=t),
        arrow(f"M{c1 + 48} {r2} H{c2 - 51}", t, "ink"),
        text((c1 + c2) / 2, r2 - 7, "opens", 11, 400, "muted", "middle", t=t),
        arrow(f"M{c2 + 48} {r2} H{c3 - 51}", t, "ink"),
        text((c2 + c3) / 2, r2 - 7, "has jobs", 11, 400, "muted", "middle", t=t),
        arrow(f"M{c3} {r2 - 15} V{r1 + 18}", t, "ink"),
        text(c3 + 8, (r1 + r2) / 2 + 4, "booked", 11, 400, "muted", t=t),
        arrow(f"M{c3 + 48} {r1} H{c4 - 51}", t, "ink"),
        text((c3 + c4) / 2, r1 - 7, "uses", 11, 400, "muted", "middle", t=t),
        arrow(f"M{c3} {r2 + 15} V{r3 - 18}", t, "ink"),
        text(c3 + 8, (r2 + r3) / 2 + 4, "produces", 11, 400, "muted", t=t),
        arrow(f"M{c3 - 48} {r3} H{c1 + 10} Q{c1} {r3} {c1} {r3 - 10} V{r2 + 18}", t, "hub", dash=True,
              marker="ah"),
        text((c1 + c3) / 2 - 20, r3 - 7, "raises new needs", 11, 500, "hub", "middle", t=t),
        obj(c1, r1, "Routine"), obj(c2, r1, "Container"), obj(c3, r1, "Booking"), obj(c4, r1, "Resource"),
        obj(c1, r2, "Need"), obj(c2, r2, "Case", strong=True), obj(c3, r2, "Job"), obj(c3, r3, "Result"),
    ]
    return out


def foundations(t):
    out = []
    y, h = 712, 186
    groups = [
        (40, "Data sources", "data", "dataEdge", "Connectors · mappings · held loads",
         ["SCADA · DCS", "Historian · PI, ADX", "PI AF", "P&IDs", "Documents · EDMS", "Operator logs",
          "GIS · pipelines", "Production · EC"], set()),
        (450, "Logic", "logic", "logicEdge", "Runs on the register, opens cases",
         ["Routing table", "Workflows · BPMN", "Detectors", "Analyses", "Metrics", "Matching · review",
          "AI specialists", "Document readers"], {"AI specialists", "Document readers"}),
        (860, "Systems of action", "action", "actionEdge", "Status mapping · source links",
         ["SAP S/4 PM", "JDE", "Maximo", "Primavera P6", "Permits", "Inspection", "MOC", "Rounds"], set()),
    ]
    for x, name, fill, edge, lid, items, ai in groups:
        w = 380
        out += [card(x, y, w, h, t, fill, edge),
                f'<rect x="{x}" y="{y}" width="{w}" height="30" rx="12" fill="{t[edge]}" fill-opacity=".55"/>',
                f'<rect x="{x}" y="{y + 18}" width="{w}" height="12" fill="{t[edge]}" fill-opacity=".55"/>',
                text(x + w / 2, y + 20, lid, 12, 600, "text", "middle", t=t),
                label(x + w / 2, y + h + 22, name, t, "middle", "ink")]
        cw, chh = (w - 16 * 2 - 12) / 2, 30
        for i, s in enumerate(items):
            cx = x + 16 + (i % 2) * (cw + 12)
            cy = y + 44 + (i // 2) * (chh + 8)
            out.append(chip(cx, cy, cw, chh, s, t, ai=s in ai))
    return out


def svg(theme: str) -> str:
    t = THEMES[theme]
    defs = f"""<defs>
<linearGradient id="iq" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{t['iqFrom']}"/>\
<stop offset="1" stop-color="{t['iqTo']}"/></linearGradient>
<linearGradient id="iqv" x1="0" y1="1" x2="0" y2="0" gradientUnits="objectBoundingBox">\
<stop offset="0" stop-color="{t['iqFrom']}"/><stop offset="1" stop-color="{t['iqTo']}"/></linearGradient>
<pattern id="hexes" width="24" height="41.57" patternUnits="userSpaceOnUse">\
<path d="M12 0 L24 6.93 V20.78 L12 27.71 L0 20.78 V6.93 Z M12 27.71 V41.57" fill="none" stroke="{t['hex']}" \
stroke-width="1"/></pattern>
<marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">\
<path d="M0 0 L10 5 L0 10 z" fill="{t['wire']}"/></marker>
<marker id="ai" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">\
<path d="M0 0 L10 5 L0 10 z" fill="{t['iqTo']}"/></marker>
<marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">\
<path d="M0 0 L10 5 L0 10 z" fill="{t['hub']}"/></marker>
</defs>"""
    body: list[str] = [f'<rect width="{W}" height="{H}" rx="18" fill="{t["bg"]}"/>']
    body += people(t)
    body += surfaces(t)
    # Register up to the surfaces: the IOC page, Ops IQ (AI, so the gradient), the APIs.
    body += bundle((90, 460), (220, 600), 384, 330, 9, t)
    body += bundle((560, 750), (622, 742), 384, 330, 5, t, color="iq", marker="ai")
    body += bundle((830, 980), (780, 900), 384, 330, 5, t)
    # Cases ready to go back to their source, into the write-back queue.
    body += bundle((1060, 1210), (1000, 1200), 384, 330, 5, t)
    body += register(t)
    body += foundations(t)
    # Sources and logic up into the register.
    body += bundle((80, 380), (100, 400), 710, 642, 9, t)
    body += bundle((510, 590), (520, 600), 710, 642, 4, t)
    body += bundle((690, 770), (680, 760), 642, 710, 4, t)
    body += bundle((900, 1200), (880, 1180), 710, 642, 9, t)
    # Approved updates back down to the systems of action.
    body.append(arrow("M1240 226 H1258 Q1268 226 1268 236 V794 Q1268 804 1258 804 H1244", t, "hub", width_=2,
                      dash=True, marker="ah"))
    body.append(f'<text x="1254" y="515" font-size="11" font-weight="600" fill="{t["hub"]}" letter-spacing="1.4" '
                f'text-anchor="middle" transform="rotate(90 1254 515)">APPROVED UPDATES</text>')
    title = "Fuzer architecture"
    desc = ("Sources and logic feed the register of asset entities and the common work model; IOC and field "
            "surfaces, Ops IQ and the APIs sit on top, and approved updates go back to the systems of action.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-labelledby="t d" font-family="{FONT}">\n<title id="t">{title}</title><desc id="d">{desc}</desc>\n'
            + defs + "\n" + "\n".join(body) + "\n</svg>\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (OUT / f"fuzer-architecture-{theme}.svg").write_text(svg(theme))


if __name__ == "__main__":
    main()
