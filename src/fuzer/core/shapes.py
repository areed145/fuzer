"""Schematic shapes (P5.16): the platform's, in `scene_shapes.yaml`, and the entries a bundle adds or replaces in its
pack (`scene_shapes: {shapes, classes, types, kinds, frames}`). Checked when the pack is read, so a bundle naming a
solid the viewer can't draw, or a shape that isn't defined, is refused at init. `graph/scene.py` reads them."""

from functools import cache
from pathlib import Path

import yaml

BUILTIN = Path(__file__).with_name("scene_shapes.yaml")
SOLIDS = ("capsule_v", "capsule_h", "cylinder_v", "cylinder_h", "box", "sphere", "well", "boom")
FRAMES = ("slab", "lattice")
LOOKUPS = ("classes", "types", "kinds")
FALLBACK = "item"


def _check_shape(name: str, s: dict) -> None:
    size = s.get("size") if isinstance(s, dict) else None
    if not isinstance(s, dict) or s.get("solid") not in SOLIDS:
        raise ValueError(f"scene shape {name}: its solid must be one of {', '.join(SOLIDS)}")
    if not (isinstance(size, list) and len(size) == 3 and all(isinstance(v, int | float) and v > 0 for v in size)):
        raise ValueError(f"scene shape {name}: its size is [along the flow, height, across], each above zero")


def _check(raw: dict, known: set[str]) -> None:
    for name, s in (raw.get("shapes") or {}).items():
        _check_shape(name, s)
    for table in LOOKUPS:
        for key, shape in (raw.get(table) or {}).items():
            if shape not in known:
                raise ValueError(f"scene shapes: {table}.{key} names {shape!r}, which is not a shape")
    for word, frame in (raw.get("frames") or {}).items():
        if frame not in FRAMES:
            raise ValueError(f"scene shapes: frames.{word} must be one of {', '.join(FRAMES)}")


@cache
def builtin() -> dict:
    raw = yaml.safe_load(BUILTIN.read_text())
    _check(raw, set(raw["shapes"]))
    return raw


def checked(raw: dict) -> dict:
    """A bundle's shapes, checked against the platform's and its own."""
    if not raw:
        return {}
    if not isinstance(raw, dict) or set(raw) - {"shapes", *LOOKUPS, "frames"}:
        raise TypeError("scene_shapes holds shapes, classes, types, kinds and frames")
    _check(raw, set(builtin()["shapes"]) | set(raw.get("shapes") or {}))
    return raw


def for_pack(pack=None) -> dict:
    """The platform's shapes with the pack's on top, table by table."""
    base, own = builtin(), getattr(pack, "scene_shapes", None) or {}
    return {table: {**base.get(table, {}), **(own.get(table) or {})} for table in ("shapes", *LOOKUPS, "frames")}


def pick(table: dict, classes: list[str], kind_type: str | None, kind: str) -> str:
    """The shape for an item: by its ISO 14224 class (the full code before its parent's), its process type, its kind."""
    for code in sorted(classes, key=lambda c: (-c.count("."), c)):
        while code:
            if code in table["classes"]:
                return table["classes"][code]
            code = code.rpartition(".")[0]
    return table["types"].get((kind_type or "").lower()) or table["kinds"].get(kind) or FALLBACK
