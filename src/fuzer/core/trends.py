"""Trend templates (P5.12): the platform's, in `trend_templates.yaml`, and the ones a bundle adds or replaces in its
pack (`trend_templates: {classes: {CODE: template}}`). Checked when the pack is read, so a bundle with a template that
names an unknown role or relation is refused at init instead of drawing a broken trend. `analysis/trend_view.py`
reads them."""

from functools import cache
from pathlib import Path

import yaml

from . import roles as vocabulary

BUILTIN = Path(__file__).with_name("trend_templates.yaml")
RELATIONS = ("self", "driver", "driven", "upstream", "downstream")


def parse(entry: str) -> tuple[str, str]:
    """`driver.speed` -> ("driver", "speed"); `speed` -> ("self", "speed")."""
    relation, _, role = str(entry).rpartition(".")
    return relation or "self", role


def _check(code: str, t: dict) -> None:
    if not isinstance(t, dict) or not t.get("title") or not isinstance(t.get("groups"), list) or not t["groups"]:
        raise ValueError(f"trend template {code}: needs a title and a list of groups")
    for g in t["groups"]:
        if not g.get("key") or not g.get("label") or not g.get("traces"):
            raise ValueError(f"trend template {code}: every group needs a key, a label and traces")
        for entry in g["traces"]:
            relation, role = parse(entry)
            if relation not in RELATIONS or role not in vocabulary.ROLES:
                raise ValueError(f"trend template {code}.{g['key']}: {entry!r} isn't <relation>.<role>")
    for alternatives in t.get("expect", []):
        for entry in alternatives:
            relation, role = parse(entry)
            if relation not in RELATIONS or role not in vocabulary.ROLES:
                raise ValueError(f"trend template {code}: expects {entry!r}, which isn't <relation>.<role>")


def checked(raw: dict) -> dict:
    """A bundle's trend templates, checked: {"classes": {code: template}}."""
    if not raw:
        return {}
    if not isinstance(raw.get("classes") if isinstance(raw, dict) else None, dict):
        raise TypeError("trend_templates holds `classes`, a mapping of class code to template")
    for code, t in raw["classes"].items():
        _check(code, t)
    return raw


@cache
def builtin() -> dict:
    """The platform's templates: {"generic": ..., "classes": {code: template}}."""
    raw = yaml.safe_load(BUILTIN.read_text())
    for code, t in raw["classes"].items():
        _check(code, t)
    return raw


def for_pack(pack=None) -> dict:
    """The platform's templates with the pack's on top, each with where it came from (`source`)."""
    base = builtin()
    own = (getattr(pack, "trend_templates", None) or {}).get("classes", {})
    classes = {code: {**t, "source": "platform"} for code, t in base["classes"].items()}
    classes |= {code: {**t, "source": "bundle"} for code, t in own.items()}
    return {"generic": base["generic"], "classes": classes}
