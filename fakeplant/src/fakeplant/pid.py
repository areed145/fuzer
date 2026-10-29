"""Draws fake scanned P&IDs (PNG) for testing tag reading from images.

Each drawing has equipment with tag labels, instrument bubbles (function letters over the loop
number, as on real P&IDs), process lines with line numbers that are not tags, a title block, and
scan damage: a slight rotation, blur and speckle.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 2200, 1500
EQUIPMENT_FUNCS = ("P", "K", "V", "E")


def _font(size: int):
    return ImageFont.load_default(size=size)


def _dashed(d: ImageDraw.ImageDraw, a, b, dash=10):
    (x1, y1), (x2, y2) = a, b
    n = max(1, int(math.dist(a, b) // dash))
    for i in range(0, n, 2):
        t0, t1 = i / n, min(1, (i + 1) / n)
        d.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0), (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)],
               fill=0, width=2)


def _equipment(d: ImageDraw.ImageDraw, func: str, cx: float, cy: float) -> tuple[float, float, float, float]:
    """Draw the symbol; return its bounding box."""
    if func == "V":
        box = (cx - 50, cy - 120, cx + 50, cy + 120)
        d.rounded_rectangle(box, radius=45, outline=0, width=3)
    elif func == "K":
        box = (cx - 60, cy - 50, cx + 60, cy + 50)
        d.polygon([(cx - 60, cy - 30), (cx + 60, cy - 50), (cx + 60, cy + 50), (cx - 60, cy + 30)], outline=0, width=3)
    elif func == "E":
        box = (cx - 50, cy - 50, cx + 50, cy + 50)
        d.ellipse(box, outline=0, width=3)
        d.line([(cx - 35, cy + 20), (cx - 10, cy - 20), (cx + 10, cy + 20), (cx + 35, cy - 20)], fill=0, width=3)
    else:  # pump
        box = (cx - 45, cy - 45, cx + 45, cy + 45)
        d.ellipse(box, outline=0, width=3)
        d.polygon([(cx - 20, cy + 45), (cx + 20, cy + 45), (cx + 35, cy + 70), (cx - 35, cy + 70)], outline=0, width=3)
        box = (box[0], box[1], box[2], cy + 70)
    return box


def draw(path: Path, sheet: dict, tags: list, titles: dict[str, str], rng: random.Random) -> list[str]:
    """Render one drawing; returns the canonical tags drawn on it (the answer key)."""
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    f_tag, f_small, f_bubble = _font(26), _font(18), _font(20)

    d.rectangle([20, 20, W - 20, H - 20], outline=0, width=4)
    tb = (W - 640, H - 210, W - 20, H - 20)
    d.rectangle(tb, outline=0, width=3)
    d.text((tb[0] + 20, tb[1] + 20), sheet["title"], font=f_tag, fill=0)
    d.text((tb[0] + 20, tb[1] + 75), f"DWG No. {sheet['sheet']}", font=f_tag, fill=0)
    d.text((tb[0] + 20, tb[1] + 130), f"REV {sheet['revision']}    SCALE NTS", font=f_tag, fill=0)

    equipment = [t for t in tags if t.func in EQUIPMENT_FUNCS]
    groups = [(eq, [t for t in tags if t.func not in EQUIPMENT_FUNCS and t.loop == eq.loop and t.suffix == eq.suffix])
              for eq in equipment]
    loose = [t for t in tags if t.func not in EQUIPMENT_FUNCS and not any(t in g for _, g in groups)]
    if loose:
        groups.append((None, loose))

    cols = min(len(groups), 4) or 1
    rows = math.ceil(len(groups) / cols) or 1
    cell_w, cell_h = (W - 120) / cols, (H - 320) / rows
    drawn, boxes = [], []
    for i, (eq, insts) in enumerate(groups):
        cx = 60 + cell_w * (i % cols) + cell_w / 2
        cy = 60 + cell_h * (i // cols) + cell_h * 0.62
        if eq:
            box = _equipment(d, eq.func, cx, cy)
            boxes.append(box)
            d.text((cx, box[3] + 30), eq.canonical, font=f_tag, fill=0, anchor="mm")
            d.text((cx, box[3] + 60), titles.get(eq.canonical, "").upper(), font=f_small, fill=0, anchor="mm")
            drawn.append(eq.canonical)
            top = box[1]
        else:
            top = cy
        by = 60 + cell_h * (i // cols) + cell_h * 0.2
        for j, inst in enumerate(insts):
            bx = cx - (len(insts) - 1) * 55 + j * 110
            d.ellipse([bx - 44, by - 44, bx + 44, by + 44], outline=0, width=2)
            d.line([(bx - 44, by), (bx + 44, by)], fill=0, width=2)  # DCS bubble divider
            d.text((bx, by - 16), inst.func, font=f_bubble, fill=0, anchor="mm")
            d.text((bx, by + 17), f"{inst.loop}{inst.suffix}", font=f_bubble, fill=0, anchor="mm")
            if eq:
                _dashed(d, (bx, by + 44), (cx, top))
            drawn.append(inst.canonical)

    # Process lines between equipment, labelled with line numbers (not tags).
    area = tags[0].area if tags else "00"
    for a, b in zip(boxes, boxes[1:]):
        y = (a[1] + a[3]) / 2 if abs(((a[1] + a[3]) / 2) - ((b[1] + b[3]) / 2)) < 5 else a[3] - 20
        x1, x2 = a[2], b[0]
        if x2 <= x1:
            continue
        d.line([(x1, y), (x2, y)], fill=0, width=3)
        d.polygon([(x2, y), (x2 - 16, y - 8), (x2 - 16, y + 8)], fill=0)
        label = f'{rng.choice([4, 6, 8, 10])}"-P-{area}{rng.randint(100, 999)}-A1'
        d.text(((x1 + x2) / 2, y - 18), label, font=f_small, fill=0, anchor="mm")

    # Scan damage.
    img = img.rotate(rng.uniform(-0.8, 0.8), fillcolor=255, resample=Image.Resampling.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(img)
    for _ in range(2500):
        x, y = rng.randrange(W), rng.randrange(H)
        d.point((x, y), fill=rng.randrange(0, 160))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return drawn
