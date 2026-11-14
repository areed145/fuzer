"""Draws fake P&IDs (PNG) for Claude to read: clean CAD exports and damaged scans, hard enough that reading them
well means something (#475).

A sheet is laid out by process flow, not in rows: equipment left to right in the order the process runs through it,
vessels high and pumps low, wrapping into a second band when a unit has many items. Process lines route
orthogonally from outlet nozzles (a vessel's liquid leaves at the bottom, its gas at the top) to inlets, with
bends, crossings (some with jumps, some plain: a reader must not join lines that only cross) and inline valves
of each type (gate, globe, ball, check, butterfly), reducers, spectacle blinds, Y-strainers and orifice flow
elements, with drains, vents and sample points branching off (not part of what a line runs through). Line numbers carry size,
service, number, spec and insulation (`6"-P-20012-A1A-IH`), written along the line, rotated on vertical runs.

Instruments are drawn the way real sheets draw them: field bubbles (plain circle), panel bubbles (circle with a
divider) and DCS shared-display bubbles (circle in a square), function letters over the loop number without the
area. Control loops run transmitter -> controller -> control valve with signal lines (electric dashed, software
links dash-circle), control valves with actuators and fail positions (FC / FO), ESD valves with interlock
diamonds. Controllers, control valves and relief valves are drawn even when the register doesn't hold them, as on
a real sheet; the answer key lists them as `unregistered`, so a reader that reports them is right to and a
reader that links them to the register is wrong. Loose meters and valves sit on a header along the foot of
the sheet. Off-page connectors (`TO OKAN-20-PID-004` with the tag at the other end) carry lines across sheets.
Notes, a hold cloud and a revision triangle fill the margins. Given `data`, the sheet's foot carries an
equipment data table (design pressure, temperature, capacity), an alarm and trip table (LL / L / H / HH per
instrument, with units) and numbered notes that name items on the sheet, and the title block a date.

Degradation levels: `clean` (CAD export), `scan` (slight rotation, blur, speckle), `old` (faded strokes, more
skew, stains, hand redlines) and `poor` (low resolution, heavy speckle and a torn corner; tags lost in the tear
are marked `hidden` in the key).

`draw` returns the answer key, so extraction is scored per element: every tag and where it sits (`tags`), each
in-sheet equipment-to-equipment line (`lines`, as before), every connection with the valves it runs through and
the sheet it continues on (`connections`), the valves (`valves`), the control loops (`loops`), the off-page
connectors (`offpage`), the unregistered items drawn (`unregistered`), how many line crossings there are (none of
them a connection), the degradation level, the data tables with each row's tag (`tables`) and the notes with the
tags they name (`notes`).
"""

import itertools
import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 2200, 1500  # the base sheet; sheets with many items grow wider and get a second band
BAND_H = 860
PER_BAND = 6
EQUIPMENT_FUNCS = ("P", "K", "V", "E", "GT")
# Gas plants also draw tanks, filters, fired heaters and generators as equipment (Kestrel's bundle).
PLANT_EQUIPMENT_FUNCS = EQUIPMENT_FUNCS + ("T", "F", "H", "G")
LEVELS = ("clean", "scan", "old", "poor")
HAND_VALVES = ("gate", "globe", "ball", "check", "butterfly")
BRANCHES = ("sample_point", "drain", "vent")  # drawn off a line, not on it: not part of what a line runs through
INSULATION = ("", "", "-IH", "-PP", "-IC", "-ET")  # none, heat conservation, personnel protection, cold, traced
TALL = ("V", "T", "F", "H", "FL")
ROW = 28  # data table row height
EQ_COLS = (("TAG", 170), ("SERVICE", 330), ("DES. P (barg)", 150), ("DES. T (DEG C)", 160), ("CAPACITY", 160))
SP_COLS = (("TAG", 160), ("LL", 110), ("L", 110), ("H", 110), ("HH", 110), ("UNITS", 120))
ABBREV = {"SEPARATOR": "SEP.", "COMPRESSOR": "COMPR.", "EXCHANGER": "EXCH.", "PRESSURE": "PRESS.", "TRAIN": "TRN",
          "DISCHARGE": "DISCH.", "SUCTION": "SUCT.", "COOLER": "CLR", "SCRUBBER": "SCRUB.", "PRODUCTION": "PROD."}


def _font(size: int):
    return ImageFont.load_default(size=size)


def _dashed(d: ImageDraw.ImageDraw, a, b, dash=10, width=2, circles=False):
    """A signal line: dashed (electric), or dashes with small circles (a software / data link)."""
    (x1, y1), (x2, y2) = a, b
    n = max(1, int(math.dist(a, b) // dash))
    for i in range(0, n, 2):
        t0, t1 = i / n, min(1, (i + 1) / n)
        d.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0), (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)],
               fill=0, width=width)
        if circles and i % 6 == 0:
            cx, cy = x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], outline=0, width=1)


def _equipment(d: ImageDraw.ImageDraw, func: str, cx: float, cy: float) -> tuple[float, float, float, float]:
    """Draw the symbol; return its bounding box."""
    if func == "V":  # vessel with a demister pad and a weir
        box = (cx - 55, cy - 130, cx + 55, cy + 130)
        d.rounded_rectangle(box, radius=48, outline=0, width=3)
        d.rectangle([cx - 40, cy - 95, cx + 40, cy - 80], outline=0, width=1)
        for x in range(int(cx - 36), int(cx + 40), 8):
            d.line([(x, cy - 95), (x + 4, cy - 80)], fill=0, width=1)
        d.line([(cx + 20, cy + 128), (cx + 20, cy + 60)], fill=0, width=2)
    elif func == "K":
        box = (cx - 60, cy - 50, cx + 60, cy + 50)
        d.polygon([(cx - 60, cy - 30), (cx + 60, cy - 50), (cx + 60, cy + 50), (cx - 60, cy + 30)], outline=0, width=3)
    elif func == "GT":  # gas turbine: tapered casing with a combustor can
        box = (cx - 70, cy - 45, cx + 70, cy + 45)
        d.polygon([(cx - 70, cy - 25), (cx + 70, cy - 45), (cx + 70, cy + 45), (cx - 70, cy + 25)], outline=0, width=3)
        d.rectangle([cx - 20, cy - 70, cx + 20, cy - 35], outline=0, width=3)
        box = (box[0], cy - 70, box[2], box[3])
    elif func == "T":  # storage tank: flat bottom, domed roof
        box = (cx - 70, cy - 90, cx + 70, cy + 90)
        d.rectangle([cx - 70, cy - 70, cx + 70, cy + 90], outline=0, width=3)
        d.arc([cx - 70, cy - 90, cx + 70, cy - 50], 180, 360, fill=0, width=3)
    elif func == "F":  # filter: housing with a diagonal element
        box = (cx - 40, cy - 80, cx + 40, cy + 80)
        d.rounded_rectangle(box, radius=20, outline=0, width=3)
        d.line([(cx - 40, cy + 60), (cx + 40, cy - 60)], fill=0, width=2)
    elif func == "H":  # fired heater: box with a burner zigzag
        box = (cx - 60, cy - 80, cx + 60, cy + 80)
        d.rectangle(box, outline=0, width=3)
        d.line([(cx - 40, cy + 50), (cx - 20, cy + 20), (cx, cy + 50), (cx + 20, cy + 20), (cx + 40, cy + 50)],
               fill=0, width=3)
    elif func == "G":  # generator: circle marked G
        box = (cx - 50, cy - 50, cx + 50, cy + 50)
        d.ellipse(box, outline=0, width=3)
        d.text((cx, cy), "G", font=_font(40), fill=0, anchor="mm")
    elif func == "FL":  # flare: a stack with a tip, a pilot and a flame above it
        box = (cx - 40, cy - 150, cx + 40, cy + 130)
        d.rectangle([cx - 14, cy - 90, cx + 14, cy + 130], outline=0, width=3)
        d.rectangle([cx - 22, cy - 112, cx + 22, cy - 90], outline=0, width=3)
        d.line([(cx + 22, cy - 100), (cx + 34, cy - 100), (cx + 34, cy - 60)], fill=0, width=2)  # pilot gas line
        d.line([(cx - 10, cy - 114), (cx - 20, cy - 132), (cx - 4, cy - 124), (cx, cy - 150), (cx + 6, cy - 124),
                (cx + 20, cy - 134), (cx + 10, cy - 114)], fill=0, width=2)
    elif func == "E":  # shell and tube exchanger
        box = (cx - 50, cy - 50, cx + 50, cy + 50)
        d.ellipse(box, outline=0, width=3)
        d.line([(cx - 35, cy + 20), (cx - 10, cy - 20), (cx + 10, cy + 20), (cx + 35, cy - 20)], fill=0, width=3)
    else:  # pump
        box = (cx - 45, cy - 45, cx + 45, cy + 45)
        d.ellipse(box, outline=0, width=3)
        d.polygon([(cx - 20, cy + 45), (cx + 20, cy + 45), (cx + 35, cy + 70), (cx - 35, cy + 70)], outline=0, width=3)
        box = (box[0], box[1], box[2], cy + 70)
    return box


def _bowtie(d, x, y, fill_left=False, fill_right=False):
    d.polygon([(x - 15, y - 10), (x - 15, y + 10), (x, y)], outline=0, fill=0 if fill_left else 255, width=2)
    d.polygon([(x + 15, y - 10), (x + 15, y + 10), (x, y)], outline=0, fill=0 if fill_right else 255, width=2)


def _hand_valve(d, kind: str, x: float, y: float):
    """A valve on a horizontal line, by type."""
    d.rectangle([x - 16, y - 3, x + 16, y + 3], fill=255)
    if kind == "check":
        _bowtie(d, x, y, fill_right=True)
        d.line([(x + 15, y - 14), (x + 15, y + 14)], fill=0, width=2)
    elif kind == "butterfly":
        d.line([(x - 12, y - 12), (x - 12, y + 12)], fill=0, width=2)
        d.line([(x + 12, y - 12), (x + 12, y + 12)], fill=0, width=2)
        d.line([(x - 8, y + 8), (x + 8, y - 8)], fill=0, width=2)
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=0)
    else:
        _bowtie(d, x, y)
        if kind == "globe":
            d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=0)
        elif kind == "ball":
            d.ellipse([x - 7, y - 7, x + 7, y + 7], outline=0, fill=255, width=2)


def _text(img, d, xy, text, font, rotate=False):
    """Text centred on xy; rotated 90 degrees to run along a vertical line."""
    if not rotate:
        d.text(xy, text, font=font, fill=0, anchor="mm")
        return
    w = int(d.textlength(text, font=font)) + 8
    t = Image.new("L", (w, 30), 255)
    ImageDraw.Draw(t).text((w / 2, 15), text, font=font, fill=0, anchor="mm")
    t = t.rotate(90, expand=True, fillcolor=255)
    img.paste(t, (int(xy[0] - t.size[0] / 2), int(xy[1] - t.size[1] / 2)))


def _route(p0, out_dir, p1, corridor):
    """An orthogonal route from an outlet (leaving in out_dir) to an inlet entered from the left."""
    step = {"right": (40, 0), "down": (0, 85), "up": (0, -40)}[out_dir]  # down: below the tag and its title
    a = (p0[0] + step[0], p0[1] + step[1])
    b = (p1[0] - 40, p1[1])
    if a[0] < b[0]:
        xm = (a[0] + b[0]) / 2
        pts = [p0, a, (xm, a[1]), (xm, b[1]), b, p1]
    elif out_dir == "right":  # the inlet is behind the outlet (the next band): run along the corridor between bands
        pts = [p0, a, (a[0], corridor), (b[0], corridor), b, p1]
    else:  # back over (or under) the equipment between
        pts = [p0, a, (b[0], a[1]), b, p1]
    out = [pts[0]]
    for p in pts[1:]:
        if p != out[-1]:
            out.append(p)
    i = 1
    while i < len(out) - 1:  # drop collinear points
        (x0, y0), (x1, y1), (x2, y2) = out[i - 1], out[i], out[i + 1]
        if (x0 == x1 == x2) or (y0 == y1 == y2):
            out.pop(i)
        else:
            i += 1
    return out


def _segments(pts):
    return list(itertools.pairwise(pts))


def _abbrev(title: str, rng: random.Random) -> str:
    return " ".join(ABBREV.get(w, w) if rng.random() < 0.5 else w for w in title.upper().split())


class _Sheet:
    def __init__(self, w, h, rng):
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)
        self.rng, self.w, self.h = rng, w, h
        self.drawn_lines = []  # (orientation, fixed, lo, hi) of every process segment drawn
        self.crossings = 0
        self.valves, self.unregistered = [], []
        self.f_tag, self.f_small, self.f_bubble = _font(26), _font(18), _font(20)

    def line(self, pts, label=None, arrow=True):
        """A process line along pts, with jumps where it crosses earlier lines; returns the label position."""
        d = self.d
        for (x1, y1), (x2, y2) in _segments(pts):
            d.line([(x1, y1), (x2, y2)], fill=0, width=3)
            if y1 == y2:  # a horizontal run crosses earlier vertical runs
                lo, hi = sorted((x1, x2))
                for o, fx, a, b in self.drawn_lines:
                    if o == "v" and lo + 20 < fx < hi - 20 and a + 20 < y1 < b - 20:
                        self.crossings += 1
                        if self.rng.random() < 0.5:  # a jump; otherwise a plain crossing
                            d.rectangle([fx - 10, y1 - 2, fx + 10, y1 + 2], fill=255)
                            d.arc([fx - 10, y1 - 10, fx + 10, y1 + 10], 180, 360, fill=0, width=3)
            elif x1 == x2:
                lo, hi = sorted((y1, y2))
                for o, fy, a, b in self.drawn_lines:
                    if o == "h" and lo + 20 < fy < hi - 20 and a + 20 < x1 < b - 20:
                        self.crossings += 1
                        if self.rng.random() < 0.5:
                            d.rectangle([x1 - 2, fy - 10, x1 + 2, fy + 10], fill=255)
                            d.arc([x1 - 10, fy - 10, x1 + 10, fy + 10], 270, 90, fill=0, width=3)
        for (x1, y1), (x2, y2) in _segments(pts):
            if y1 == y2:
                self.drawn_lines.append(("h", y1, *sorted((x1, x2))))
            else:
                self.drawn_lines.append(("v", x1, *sorted((y1, y2))))
        if arrow:
            (xa, ya), (xb, yb) = pts[-2], pts[-1]
            if ya == yb:
                s = 1 if xb > xa else -1
                d.polygon([(xb, yb), (xb - 16 * s, yb - 8), (xb - 16 * s, yb + 8)], fill=0)
        if not label:
            return None
        (x1, y1), (x2, y2) = max(_segments(pts), key=lambda s: math.dist(*s))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if y1 == y2:
            # labels usually sit above the line; on some sheets they overlap it
            pos = (mx, my - 18 if self.rng.random() < 0.8 else my - 4)
            _text(self.img, d, pos, label, self.f_small)
        else:
            pos = (mx + 16, my)
            _text(self.img, d, pos, label, self.f_small, rotate=True)
        return pos

    def inline(self, pts, n_hand: int, extra: list | None = None) -> list[dict]:
        """Valves, reducers and blinds along the longest horizontal run of a route."""
        runs = [s for s in _segments(pts) if s[0][1] == s[1][1] and abs(s[1][0] - s[0][0]) > 160] or [
            s for s in _segments(pts) if s[0][1] == s[1][1] and abs(s[1][0] - s[0][0]) > 50]
        if not runs:
            return []
        (x1, y), (x2, _) = max(runs, key=lambda s: abs(s[1][0] - s[0][0]))
        lo, hi = sorted((x1, x2))
        items = [("hand", self.rng.choice(HAND_VALVES)) for _ in range(n_hand)] + (extra or [])
        if self.rng.random() < 0.25:
            items.append(("reducer", None))
        if self.rng.random() < 0.15:
            items.append(("blind", None))
        for what, p in (("strainer", 0.2), ("orifice", 0.15), ("sample_point", 0.15), ("drain", 0.25), ("vent", 0.2)):
            if self.rng.random() < p:
                items.append((what, None))
        out = []
        for k, (what, kind) in enumerate(items):
            x = lo + (hi - lo) * (k + 1) / (len(items) + 1)
            if what == "hand":
                _hand_valve(self.d, kind, x, y)
                out.append({"type": kind, "x": round(x), "y": round(y)})
            elif what == "reducer":
                self.d.polygon([(x - 12, y - 12), (x + 12, y - 7), (x + 12, y + 7), (x - 12, y + 12)], outline=0,
                               fill=255, width=2)
                out.append({"type": "reducer", "x": round(x), "y": round(y)})
            elif what == "blind":
                closed = self.rng.random() < 0.5
                self.d.ellipse([x - 7, y - 22, x + 7, y - 8], outline=0, fill=0 if closed else 255, width=2)
                self.d.ellipse([x - 7, y + 8, x + 7, y + 22], outline=0, fill=255 if closed else 0, width=2)
                self.d.line([(x, y - 8), (x, y + 8)], fill=0, width=3)
                out.append({"type": "spectacle_blind", "closed": closed, "x": round(x), "y": round(y)})
            elif what == "strainer":  # Y-strainer: a slanted leg with its screen
                self.d.rectangle([x - 14, y - 3, x + 14, y + 3], fill=255)
                self.d.line([(x - 14, y), (x + 14, y)], fill=0, width=3)
                self.d.line([(x - 6, y), (x + 10, y + 22)], fill=0, width=3)
                self.d.line([(x + 2, y), (x + 18, y + 22)], fill=0, width=3)
                self.d.line([(x + 10, y + 22), (x + 18, y + 22)], fill=0, width=2)
                out.append({"type": "strainer", "x": round(x), "y": round(y)})
            elif what == "orifice":  # a flow element: two plates across the line
                self.d.line([(x - 5, y - 14), (x - 5, y + 14)], fill=0, width=3)
                self.d.line([(x + 5, y - 14), (x + 5, y + 14)], fill=0, width=3)
                self.d.text((x, y + 26), "FE", font=self.f_small, fill=0, anchor="mm")
                out.append({"type": "orifice", "x": round(x), "y": round(y)})
            elif what in ("sample_point", "drain", "vent"):  # a branch off the line with its own small valve
                up = what == "vent"
                end = y - 46 if up else y + 46
                self.d.line([(x, y), (x, end)], fill=0, width=2)
                vy = (y + end) / 2
                self.d.polygon([(x - 7, vy - 9), (x + 7, vy - 9), (x, vy)], outline=0, fill=255, width=2)
                self.d.polygon([(x - 7, vy + 9), (x + 7, vy + 9), (x, vy)], outline=0, fill=255, width=2)
                label = {"sample_point": "SP", "drain": "D", "vent": "V"}[what]
                self.d.text((x + 16, end), label, font=self.f_small, fill=0, anchor="lm")
                out.append({"type": what, "x": round(x), "y": round(y)})
            else:  # ("control", {...}): a control or ESD valve with its actuator
                out.append(self.control_valve(x, y, **kind))
        return out

    def control_valve(self, x, y, tag: str, fail: str, esd: bool = False, registered: bool = False) -> dict:
        d = self.d
        d.rectangle([x - 16, y - 3, x + 16, y + 3], fill=255)
        _bowtie(d, x, y)
        d.line([(x, y), (x, y - 30)], fill=0, width=2)
        if esd:  # ESD valve: square actuator and an interlock diamond
            d.rectangle([x - 12, y - 52, x + 12, y - 30], outline=0, width=2)
            dx, dy = x + 50, y - 40
            d.polygon([(dx, dy - 16), (dx + 16, dy), (dx, dy + 16), (dx - 16, dy)], outline=0, width=2)
            d.text((dx, dy), "I", font=self.f_small, fill=0, anchor="mm")
            _dashed(d, (x + 12, y - 40), (dx - 16, dy))
        else:
            d.chord([x - 16, y - 46, x + 16, y - 14], 180, 360, outline=0, width=2)
        d.text((x, y + 24), fail, font=self.f_small, fill=0, anchor="mm")
        if not registered:
            self.unregistered.append({"text": tag, "kind": "valve", "x": round(x), "y": round(y)})
        return {"type": "esd" if esd else "control", "tag": tag, "fail": fail, "x": round(x), "y": round(y)}

    def bubble(self, x, y, func: str, number: str, style: str):
        d = self.d
        if style == "dcs":
            d.rectangle([x - 46, y - 46, x + 46, y + 46], outline=0, width=2)
        d.ellipse([x - 44, y - 44, x + 44, y + 44], outline=0, fill=255, width=2)
        if style in ("dcs", "panel"):
            d.line([(x - 44, y), (x + 44, y)], fill=0, width=2)
        d.text((x, y - 16), func, font=self.f_bubble, fill=0, anchor="mm")
        d.text((x, y + 17), number, font=self.f_bubble, fill=0, anchor="mm")


def _degrade(img: Image.Image, level: str, rng: random.Random, hide: list) -> Image.Image:
    """Damage a sheet to its level. `hide` collects the box torn off a poor scan (sheet pixels)."""
    w, h = img.size
    if level == "clean":
        return img
    if level == "old":
        img = img.point(lambda p: 95 + p * 0.63)  # faded strokes on a yellowed sheet
        stains = Image.new("L", (w, h), 255)
        for _ in range(rng.randint(2, 4)):  # coffee and water stains darken what is under them
            x, y, r = rng.randrange(w), rng.randrange(h), rng.randint(60, 180)
            ImageDraw.Draw(stains).ellipse([x - r, y - r, x + r, y + r], fill=rng.randint(170, 210))
        img = ImageChops.multiply(img, stains.filter(ImageFilter.GaussianBlur(20)))
        d = ImageDraw.Draw(img)
        for _ in range(rng.randint(2, 3)):  # hand redlines: a cloud and a note
            x, y = rng.randrange(200, w - 400), rng.randrange(200, h - 400)
            pts = [(x + 90 * math.cos(t / 6 * math.pi) + rng.uniform(-8, 8),
                    y + 50 * math.sin(t / 6 * math.pi) + rng.uniform(-8, 8)) for t in range(13)]
            d.line(pts, fill=40, width=3)
            d.text((x + 100, y - 60), rng.choice(["FIELD CHANGE", "AS BUILT", "CHECK", "RELOCATED", "SEE MOC"]),
                   font=_font(24), fill=40)
        img = img.rotate(rng.uniform(-2.0, 2.0), fillcolor=235, resample=Image.Resampling.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.9))
        speckle = 4000
    elif level == "poor":
        img = img.resize((int(w * 0.55), int(h * 0.55)), Image.Resampling.BILINEAR).resize((w, h), Image.Resampling.BILINEAR)
        img = img.rotate(rng.uniform(-1.2, 1.2), fillcolor=255, resample=Image.Resampling.BICUBIC)
        tear = (0, int(h * 0.62), int(w * 0.16), h)  # a torn bottom-left corner
        ImageDraw.Draw(img).polygon([(0, tear[1]), (tear[2], h), (0, h)], fill=255)
        hide.append(tear)
        speckle = 6000
    else:
        img = img.rotate(rng.uniform(-0.8, 0.8), fillcolor=255, resample=Image.Resampling.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        speckle = 2500
    d = ImageDraw.Draw(img)
    for _ in range(speckle):
        d.point((rng.randrange(w), rng.randrange(h)), fill=rng.randrange(0, 160))
    return img


def _num(v) -> str:
    if v is None:
        return "-"
    return f"{v:g}" if abs(v) < 1000 else f"{v:.0f}"


def _table(s: "_Sheet", x: float, y: float, title: str, cols, rows: list[list[str]]) -> dict:
    """A data table in the sheet's foot: a title, a header row and one row per item, ruled. Returns it with the
    centre of each row's first cell (where a reader would point to it)."""
    d, w = s.d, sum(c[1] for c in cols)
    d.text((x + 4, y + ROW / 2), title, font=s.f_small, fill=0, anchor="lm")
    y0 = y + ROW
    d.rectangle([x, y0, x + w, y0 + ROW * (len(rows) + 1)], outline=0, width=2)
    d.line([(x, y0 + ROW), (x + w, y0 + ROW)], fill=0, width=2)
    cx = x
    for k, (name, cw) in enumerate(cols):
        if k:
            d.line([(cx, y0), (cx, y0 + ROW * (len(rows) + 1))], fill=0, width=1)
        d.text((cx + cw / 2, y0 + ROW / 2), name, font=s.f_small, fill=0, anchor="mm")
        cx += cw
    out = []
    for i, row in enumerate(rows):
        ry = y0 + ROW * (i + 1.5)
        if i:
            d.line([(x, ry - ROW / 2), (x + w, ry - ROW / 2)], fill=0, width=1)
        cx = x
        for (_, cw), cell in zip(cols, row, strict=True):
            d.text((cx + cw / 2, ry), cell, font=s.f_small, fill=0, anchor="mm")
            cx += cw
        out.append({"cells": row, "x": round(x + cols[0][1] / 2), "y": round(ry)})
    return {"title": title, "columns": [c[0] for c in cols], "rows": out}


def _table_h(n: int) -> int:
    return ROW * (n + 2) if n else 0


def _in_tear(x, y, tear) -> bool:
    x0, y0, x1, y1 = tear
    return x <= x0 + (x1 - x0) * (y - y0) / (y1 - y0) and y >= y0


def draw(path: Path, sheet: dict, tags: list, titles: dict[str, str], rng: random.Random,
         connections: list[dict] = (), scan: bool = True, equipment_funcs: tuple[str, ...] = EQUIPMENT_FUNCS,
         level: str | None = None, offpage: list[dict] = (), data: dict | None = None) -> dict:
    """Render one drawing and return its answer key (see the module docstring). `connections`: the sheet's
    process lines {from, to, line} between its equipment; `offpage`: lines leaving or entering the sheet
    {tag, direction: to|from, sheet, other, line}. `level` defaults to `scan` when scan is set, else `clean`.
    `data` fills the sheet's foot: `equipment` {tag: {service, design_pressure, design_temperature, capacity}}
    for an equipment data table, `setpoints` {instrument tag: {lolo, lo, hi, hihi, unit}} for an alarm and trip
    table, `notes` [{text, tags}] numbered after the standard ones, and `date` for the title block."""
    data = data or {}
    level = level or ("scan" if scan else "clean")
    equipment = [t for t in tags if t.func in equipment_funcs]
    by_key = {t.canonical: t for t in equipment}
    # process order: a topological sort of the sheet's lines (sources first), stable in the order they're listed
    seen_keys = list(dict.fromkeys(k for c in connections for k in (c["from"], c["to"]) if k in by_key))
    edges = [(c["from"], c["to"]) for c in connections if c["from"] in by_key and c["to"] in by_key]
    order, left = [], list(seen_keys)
    while left:
        k = next((k for k in left if not any(b == k and a in left for a, b in edges)), left[0])
        order.append(by_key[k])
        left.remove(k)
    order += [t for t in equipment if t not in order]
    per_band = min(PER_BAND, max(1, len(order)))
    bands = max(1, math.ceil(len(order) / per_band))
    moved = data.get("moved", {})

    def owner(t):
        """The equipment an instrument belongs to: same loop and suffix, or a pump's suction transmitter (loop + 10);
        or where the sheet shows it moved to (`data["moved"]`, a field change the register may not have)."""
        if t.canonical in moved and moved[t.canonical] in by_key:
            return by_key[moved[t.canonical]]
        return next((e for e in equipment if t.loop == e.loop and t.suffix == e.suffix), None) or next(
            (e for e in equipment if e.func == "P" and t.loop == e.loop + 10 and t.suffix == e.suffix), None)

    loose = [t for t in tags if t.func not in equipment_funcs and owner(t) is None]
    width = W + max(0, per_band - 4) * 330
    body_h = H + (bands - 1) * BAND_H + (140 if loose else 0)
    # the foot: data tables at the left, notes in the middle, the title block at the right
    on_sheet = {t.canonical: t for t in tags}
    eq_rows = [[k, _abbrev(v.get("service") or titles.get(k, ""), rng), _num(v.get("design_pressure")),
                _num(v.get("design_temperature")), v.get("capacity") or "-"]
               for k, v in data.get("equipment", {}).items() if k in by_key]
    sp_keys = [k for k in data.get("setpoints", {}) if k in on_sheet]
    sp_rows = [[on_sheet[k].fmt("{f}-{l}{s}"), *(_num(data["setpoints"][k].get(t)) for t in ("lolo", "lo", "hi", "hihi")),
                data["setpoints"][k].get("unit") or ""] for k in sp_keys]
    room = width - 1300 - 40  # left of the notes
    eq_w, sp_w = sum(c[1] for c in EQ_COLS), sum(c[1] for c in SP_COLS)
    side = room >= eq_w + sp_w + 40
    tables_h = max(_table_h(len(eq_rows)), _table_h(len(sp_rows))) if side else (
        _table_h(len(eq_rows)) + _table_h(len(sp_rows)) + (20 if eq_rows and sp_rows else 0))
    extra_notes = data.get("notes", [])
    foot_h = max(210, tables_h + 50, 34 * (5 + len(extra_notes)) + 30)
    height = body_h + foot_h - 210
    s = _Sheet(width, height, rng)
    d, img = s.d, s.img

    # frame, title block, notes
    d.rectangle([20, 20, width - 20, height - 20], outline=0, width=4)
    tb = (width - 640, height - 210, width - 20, height - 20)
    d.rectangle(tb, outline=0, width=3)
    d.text((tb[0] + 20, tb[1] + 20), sheet["title"], font=s.f_tag if d.textlength(sheet["title"], font=s.f_tag) < 580
           else s.f_small, fill=0)
    d.text((tb[0] + 20, tb[1] + 75), f"DWG No. {sheet['sheet']}", font=s.f_tag, fill=0)
    rev = f"REV {sheet['revision']}  {data['date']}  SCALE NTS" if data.get("date") else f"REV {sheet['revision']}    SCALE NTS"
    d.text((tb[0] + 20, tb[1] + 130), rev, font=s.f_tag if not data.get("date") else s.f_small, fill=0)
    area = equipment[0].area if equipment else (tags[0].area if tags else "")
    notes = ["NOTES:", f"1. INSTRUMENT TAGS ARE PREFIXED {area}- UNLESS SHOWN.",
             "2. LINE No.: SIZE-SERVICE-NUMBER-SPEC-INSULATION.", "3. ALL VALVES LINE SIZE UNLESS NOTED.",
             "4. HOLD 1: TIE-IN LOCATION TO BE CONFIRMED."]
    notes_key = [{"n": k, "text": t.split(". ", 1)[1], "tags": [], "x": round(tb[0] - 620), "y": round(height - foot_h + 10 + 34 * k)}
                 for k, t in enumerate(notes) if k]
    for j, n in enumerate(extra_notes):
        k = len(notes)
        notes.append(f"{k}. {n['text']}")
        notes_key.append({"n": k, "text": n["text"], "tags": list(n.get("tags", [])), "x": round(tb[0] - 620),
                          "y": round(height - foot_h + 10 + 34 * k)})
    for k, n in enumerate(notes):
        d.text((tb[0] - 620, height - foot_h + 10 + 34 * k), n, font=s.f_small, fill=0)
    tables = []
    tx, ty = 40, height - foot_h + 10
    if eq_rows:
        tables.append({"kind": "equipment", **_table(s, tx, ty, "EQUIPMENT DATA", EQ_COLS, eq_rows)})
        for r in tables[-1]["rows"]:
            r["key"] = r["cells"][0]
        if side:
            tx += eq_w + 40
        else:
            ty += _table_h(len(eq_rows)) + 20
    if sp_rows:
        tables.append({"kind": "setpoints", **_table(s, tx, ty, "ALARM AND TRIP SETTINGS", SP_COLS, sp_rows)})
        for r, k in zip(tables[-1]["rows"], sp_keys, strict=True):
            r["key"] = k

    # equipment, laid out by process flow
    spacing = (width - 480) / per_band
    boxes, centres, drawn = {}, {}, []
    for i, eq in enumerate(order):
        band, col = divmod(i, per_band)
        cx = 220 + spacing * (col + 0.5)
        base = 300 + band * BAND_H
        cy = base + (260 if eq.func in TALL else 470 if eq.func == "P" else 360) + rng.uniform(-20, 20)
        box = _equipment(d, eq.func, cx, cy)
        boxes[eq.canonical], centres[eq.canonical] = box, (cx, cy)
        d.text((cx, box[3] + 30), eq.canonical, font=s.f_tag, fill=0, anchor="mm")
        d.text((cx, box[3] + 58), _abbrev(titles.get(eq.canonical, ""), rng), font=s.f_small, fill=0, anchor="mm")
        drawn.append({"key": eq.canonical, "kind": "equipment", "x": round(cx), "y": round(box[3] + 30)})

    def outlet(k, gas=False):
        (_, y0, x1, y1), (cx, cy) = boxes[k], centres[k]
        f = by_key[k].func
        if f in TALL:
            return ((cx, y0), "up") if gas else ((cx - 25, y1), "down")
        if f == "P":
            return (cx, y0), "up"
        return (x1, cy - (25 if gas else 0)), "right"

    def inlet(k):
        x0, cy = boxes[k][0], centres[k][1]
        return (x0, cy - 60 if by_key[k].func in TALL else cy)

    def corridor(k):  # between a band's labels and the next band's instrument bubbles
        return 300 + (order.index(by_key[k]) // per_band) * BAND_H + 640

    # in-sheet process lines
    lines, conns, routes = [], [], {}
    for c in connections:
        if c["from"] not in boxes or c["to"] not in boxes:
            continue
        gas = by_key[c["to"]].func in ("K", "E", "GT")  # a scrubber feeds its compressor from the top
        p0, od = outlet(c["from"], gas=gas)
        n_in = sum(1 for x in conns if x["to"] == c["to"])  # a second line into the same item gets its own nozzle
        x_in, y_in = inlet(c["to"])
        pts = _route(p0, od, (x_in, y_in + 34 * n_in), corridor(c["from"]))
        pos = s.line(pts, c["line"])
        if not gas or by_key[c["from"]].func not in TALL:
            routes[c["from"]] = pts  # the line a level or flow loop's control valve can sit on
        lines.append({"text": c["line"], "from": c["from"], "to": c["to"], "x": round(pos[0]), "y": round(pos[1])})
        conns.append({"from": c["from"], "to": c["to"], "line": c["line"], "pts": pts})

    # off-page connectors at the sheet edges
    off = []
    n_to = n_from = 0
    for o in offpage:
        if o["tag"] not in boxes:
            continue
        if o["direction"] == "to" and o.get("low"):  # leaves under the sheet's items, clear of a tall one beside it
            (_, _, _, y1), (cx, _) = boxes[o["tag"]], centres[o["tag"]]
            p0, y = (cx + 25, y1), y1 + 170  # below the corridor its sheet-mates run in
            edge = (width - 200, y)
            pts = [p0, (p0[0], y), edge]
            pos = s.line(pts, o["line"], arrow=False)
            x, yy = edge
            d.polygon([(x, yy - 20), (x + 110, yy - 20), (x + 140, yy), (x + 110, yy + 20), (x, yy + 20)], outline=0, width=2)
            d.text((x + 60, yy - 32), f"TO {o['sheet']}", font=s.f_small, fill=0, anchor="mm")
            d.text((x + 62, yy), o["other"], font=s.f_small, fill=0, anchor="mm")
        elif o["direction"] == "to":
            p0, od = outlet(o["tag"], gas=True)
            y = p0[1] - 40 - 60 * n_to if od == "up" else p0[1]  # leaves over the top, below the bubbles
            n_to += 1
            edge = (width - 200, y)
            pts = [p0, (p0[0], y), edge] if od == "up" else [p0, edge]
            pos = s.line(pts, o["line"], arrow=False)
            x, yy = edge
            d.polygon([(x, yy - 20), (x + 110, yy - 20), (x + 140, yy), (x + 110, yy + 20), (x, yy + 20)], outline=0, width=2)
            d.text((x + 60, yy - 32), f"TO {o['sheet']}", font=s.f_small, fill=0, anchor="mm")
            d.text((x + 62, yy), o["other"], font=s.f_small, fill=0, anchor="mm")
        else:
            y = inlet(o["tag"])[1] + (40 if any(c["to"] == o["tag"] and "offpage" not in c for c in conns) else 0)
            # more lines into the same item come in one under another (a relief header's many tie-ins)
            k = sum(1 for x in off if x["direction"] == "from" and x["tag"] == o["tag"])
            y += 64 * ((k + 1) // 2) * (1 if k % 2 else -1)
            n_from += 1
            edge = (180, y)
            target = (boxes[o["tag"]][0], y)
            pts = [edge, (target[0] - 40, y), target] if target[0] - 40 > edge[0] else [edge, target]
            pos = s.line(pts, o["line"])
            x, yy = 40, y
            d.polygon([(x, yy - 20), (x + 110, yy - 20), (x + 140, yy), (x + 110, yy + 20), (x, yy + 20)], outline=0, width=2)
            d.text((x + 70, yy - 32), f"FROM {o['sheet']}", font=s.f_small, fill=0, anchor="mm")
            d.text((x + 62, yy), o["other"], font=s.f_small, fill=0, anchor="mm")
        off.append({"tag": o["tag"], "direction": o["direction"], "sheet": o["sheet"], "other": o["other"],
                    "line": o["line"], "x": round(edge[0]), "y": round(edge[1])})
        a, b = (o["tag"], o["other"]) if o["direction"] == "to" else (o["other"], o["tag"])
        conns.append({"from": a, "to": b, "line": o["line"], "pts": pts, "offpage": o["sheet"]})

    # instruments by their equipment, with control loops where a transmitter has something to control
    loops, pending = [], {}  # pending: control valves to place on an equipment's outlet line, with their controller
    styles = ("dcs", "dcs", "panel", "field")
    for eq in order:
        insts = [t for t in tags if t.func not in equipment_funcs and owner(t) == eq]
        cx, cy = centres[eq.canonical]
        band = order.index(eq) // per_band
        row_y = 150 + band * BAND_H
        if eq.func == "V" and eq.canonical not in routes and any(t.func == "LT" for t in insts):
            # liquid leaves a scrubber by a drain line to the closed drain, where its level valve sits
            x0, y1 = cx - 25, boxes[eq.canonical][3]
            pts = [(x0, y1), (x0, y1 + 120), (x0 + 260, y1 + 120)]
            drain = f'2"-D-{eq.area}{eq.loop}{eq.suffix}-A1A'
            pos = s.line(pts, drain)
            d.text((x0 + 330, y1 + 120), "TO CL. DRAIN", font=s.f_small, fill=0, anchor="mm")
            routes[eq.canonical] = pts
            conns.append({"from": eq.canonical, "to": None, "line": drain, "pts": pts, "service": "closed drain"})
        controlled = [t for t in insts if t.func in ("LT", "FT")] if eq.canonical in routes else []
        bubbles = []
        for t in insts:  # a loop's controller sits next to its transmitter
            bubbles += [t, ("C", t)] if controlled and t == controlled[0] else [t]
        per_row = max(2, int(spacing // 110))
        for j, inst in enumerate(bubbles):
            r, k = divmod(j, per_row)
            in_row = min(per_row, len(bubbles) - r * per_row)
            bx, by = cx - (in_row - 1) * 55 + k * 110, row_y + r * 105
            if isinstance(inst, tuple):  # the controller of a loop: a DCS function, not in the register
                t = inst[1]
                func = t.func[0] + "IC"
                s.bubble(bx, by, func, f"{t.loop}{t.suffix}", "dcs")
                ctl = f"{t.area}-{func}-{t.loop}{t.suffix}"
                s.unregistered.append({"text": ctl, "kind": "controller", "x": round(bx), "y": round(by)})
                prev = next(x for x in drawn if x["key"] == t.canonical)
                _dashed(d, (prev["x"] + 44, prev["y"]), (bx - 44, by), circles=True)  # software link
                valve_tag = f"{t.area}-{t.func[0]}V-{t.loop}{t.suffix}"
                pending.setdefault(eq.canonical, []).append((valve_tag, rng.choice(["FC", "FO"]), bx, by))
                loops.append({"loop": t.canonical, "members": [t.canonical, ctl, valve_tag]})
                continue
            s.bubble(bx, by, inst.func, f"{inst.loop}{inst.suffix}", rng.choice(styles))
            _dashed(d, (bx, by + 44), (bx if abs(bx - cx) < 40 else cx + (bx - cx) * 0.3, boxes[eq.canonical][1]))
            drawn.append({"key": inst.canonical, "kind": "instrument", "x": round(bx), "y": round(by),
                          "on": eq.canonical})
            if inst.canonical in moved:  # the field change, clouded in red on the sheet
                pts = [(bx + 70 * math.cos(a / 8 * math.pi), by + 60 * math.sin(a / 8 * math.pi)) for a in range(17)]
                d.line(pts, fill=40, width=3)
                d.text((bx + 80, by - 70), "RELOCATED (FIELD CHANGE)", font=s.f_small, fill=40)
        if eq.func == "V":  # a relief valve on top of every vessel, tagged on the sheet, rarely in the register
            x0, y0 = cx + 30, boxes[eq.canonical][1]
            d.line([(x0, y0), (x0, y0 - 40)], fill=0, width=2)
            d.polygon([(x0 - 10, y0 - 40), (x0 + 10, y0 - 40), (x0, y0 - 56)], outline=0, width=2)
            d.line([(x0, y0 - 56), (x0 + 30, y0 - 56)], fill=0, width=2)
            psv = f"PSV-{eq.loop}{eq.suffix}"
            d.text((x0 + 70, y0 - 56), psv, font=s.f_small, fill=0, anchor="mm")
            s.unregistered.append({"text": f"{eq.area}-{psv}", "kind": "relief_valve", "x": round(x0 + 70),
                                   "y": round(y0 - 56)})

    # hand valves on every in-sheet and off-page line
    for c in conns:
        ctl = pending.pop(c["from"], []) if c["pts"] is routes.get(c["from"]) else []
        vs = s.inline(c["pts"], rng.choice([1, 1, 2]), [("control", {"tag": t, "fail": f}) for t, f, _, _ in ctl])
        for t, _, bx, by in ctl:  # the controller's output to its valve
            v = next((x for x in vs if x.get("tag") == t), None)
            if v:
                _dashed(d, (bx, by + 44), (v["x"], v["y"] - 46))
        placed = {v.get("tag") for v in vs}
        for lp in loops:  # a valve with nowhere to sit leaves its loop at transmitter and controller
            if lp["members"][-1] in {t for t, _, _, _ in ctl} - placed:
                lp["members"].pop()
        s.valves += vs
        c["via"] = [v.get("tag") or v["type"] for v in sorted(vs, key=lambda v: v["x"]) if v["type"] not in BRANCHES]

    for rest in pending.values():  # loops whose line wasn't drawn
        for t, _, _, _ in rest:
            for lp in loops:
                if lp["members"][-1] == t:
                    lp["members"].pop()

    # loose meters and valves on a header along the foot of the sheet
    if loose:
        hy = body_h - 330
        hx0, hx1 = 200, width - 720
        header = f'{rng.choice([8, 10, 12])}"-P-{area}{900 + len(loose)}-B1A{rng.choice(INSULATION)}'
        s.line([(hx0, hy), (hx1, hy)], header)
        lines_header = {"text": header, "from": None, "to": None}
        for j, t in enumerate(loose):
            x = hx0 + (hx1 - hx0) * (j + 1) / (len(loose) + 1)
            if t.func.endswith("V"):  # an ESD / bypass valve on the header, in the register
                v = s.control_valve(x, hy, t.canonical, rng.choice(["FC", "FO"]), esd=t.func == "XV", registered=True)
                s.valves.append(v)
                s.bubble(x, hy - 150, t.func, f"{t.loop}{t.suffix}", "dcs")
                _dashed(d, (x, hy - 106), (x, hy - 52))
                drawn.append({"key": t.canonical, "kind": "instrument", "x": round(x), "y": round(hy - 150)})
                continue
            if t.func.startswith("F"):  # orifice flow element under the meter
                d.line([(x - 6, hy - 14), (x - 6, hy + 14)], fill=0, width=3)
                d.line([(x + 6, hy - 14), (x + 6, hy + 14)], fill=0, width=3)
            s.bubble(x, hy - 120, t.func, f"{t.loop}{t.suffix}", rng.choice(styles))
            _dashed(d, (x, hy - 76), (x, hy - 14))
            drawn.append({"key": t.canonical, "kind": "instrument", "x": round(x), "y": round(hy - 120)})
    else:
        lines_header = None

    # a hold cloud and a revision triangle
    if drawn:
        x, y = rng.choice(drawn)["x"], rng.choice(drawn)["y"]
        pts = [(x + 120 + 70 * math.cos(t / 8 * math.pi), y + 40 * math.sin(t / 8 * math.pi)) for t in range(17)]
        for a, b in itertools.pairwise(pts):
            d.arc([min(a[0], b[0]) - 6, min(a[1], b[1]) - 6, max(a[0], b[0]) + 6, max(a[1], b[1]) + 6], 0, 360, fill=0)
        d.text((x + 120, y), "HOLD 1", font=s.f_small, fill=0, anchor="mm")
        r = rng.choice(drawn)
        tx, ty = r["x"] + 70, r["y"] - 30
        d.polygon([(tx, ty - 16), (tx + 16, ty + 12), (tx - 16, ty + 12)], outline=0, width=2)
        d.text((tx, ty + 3), str(sheet["revision"]), font=_font(14), fill=0, anchor="mm")

    tear: list = []
    img = _degrade(s.img, level, rng, tear)
    if tear:
        for x in drawn + notes_key + [r for t in tables for r in t["rows"]]:
            if _in_tear(x["x"], x["y"], tear[0]):
                x["hidden"] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return {"size": [width, height], "level": level, "tags": drawn, "lines": lines,
            "connections": [{k: v for k, v in c.items() if k != "pts"} for c in conns],
            "header": lines_header, "valves": s.valves, "loops": loops, "offpage": off,
            "unregistered": s.unregistered, "crossings": s.crossings, "tables": tables, "notes": notes_key,
            **({"date": data["date"]} if data.get("date") else {})}
