"""The method registry (P1.3): every calculation an agent or a detector can call, declared once as data.

`standards/methods.yaml` names each method's standard and clause (or the engineering practice it follows where no
published standard applies), its inputs with units, where it is valid, the data it needs, the thresholds it flags at
and its test vectors. A result carries its method (`describe`), a finding names it by id, and a method that cannot
answer refuses by name (`refusal`), so nobody is handed a number the data can't support.

    methods.get("corrosion_circuit")        the method, or MethodError
    methods.label("corrosion_circuit")      "API 570 7.1, corrosion circuit: long and short term rate, ..."
    methods.describe("corrosion_circuit")   {"id", "title", "standard", "label"}: what a result carries
    methods.refusal(id, "fewer than ...")   {"refused": "too_little_data", "reason": ..., "method": {...}}
"""

import functools
from dataclasses import dataclass, field

import yaml

from .taxonomy import STANDARDS_DIR

# Why a method would not answer, by name. The reason in words comes from the calculation itself.
REFUSALS = {
    "missing_input": "A measurement, curve, rating or record the method needs is not on the asset.",
    "too_little_data": "The input is there, but too few readings, tests or running hours to support a result.",
    "not_applicable": "The method does not apply to this kind of asset.",
    "insufficient_inputs": "The inputs cannot support a result, for the reason given.",
}


class MethodError(ValueError):
    """A method nobody registered."""


@dataclass(frozen=True)
class Method:
    id: str
    title: str
    standard: str | None = None   # the published standard, as the calculation applies it
    clause: str | None = None
    practice: str | None = None   # the engineering practice followed where no published standard applies
    inputs: tuple = ()            # ({"name", "unit"}, ...)
    valid: str = ""               # where the method holds
    needs: str = ""               # the data it needs before it answers
    thresholds: dict = field(default_factory=dict)  # name -> {"value", "unit", "means", "code"?}
    vectors: tuple = ()           # ({"kernel", "args", "expect", "says"} | {"worked": "<test node>"}, ...)

    @property
    def cites(self) -> str:
        """The standard and clause, or that it is the practice named."""
        if self.standard:
            return self.standard + (f" {self.clause}" if self.clause else "")
        return "Engineering practice"

    @property
    def label(self) -> str:
        """How a result names its method to a person: "API 570 7.1, corrosion circuit: ..."."""
        title = self.title[:1].lower() + self.title[1:] if self.standard else self.title
        return f"{self.cites}, {title}" if self.standard else title


@functools.cache
def registry() -> dict[str, Method]:
    raw = yaml.safe_load((STANDARDS_DIR / "methods.yaml").read_text())
    return {k: Method(id=k, **{**v, "inputs": tuple(v.get("inputs", ())), "vectors": tuple(v.get("vectors", ()))})
            for k, v in raw.items()}


def get(method_id: str) -> Method:
    try:
        return registry()[method_id]
    except KeyError:
        raise MethodError(f"no method {method_id!r} in the registry (standards/methods.yaml)") from None


def known(method_id: str | None) -> bool:
    return bool(method_id) and method_id in registry()


def label(method_id: str) -> str:
    return get(method_id).label


def describe(method_id: str) -> dict:
    """What a result carries about its method."""
    m = get(method_id)
    return {"id": m.id, "title": m.title, "standard": m.cites if m.standard else None, "label": m.label}


def refusal_name(reason: str) -> str:
    """The name of a refusal, from the calculation's own reason."""
    r = reason.lower().lstrip()
    if r.startswith(("unknown", "not a ")) or " isn't " in r or "does not apply" in r:
        return "not_applicable"
    if any(w in r for w in ("fewer than", "too few", "hardly ran", "only ", "too little", "did not ", "not enough")):
        return "too_little_data"
    if r.startswith("no ") or " no " in r or "gives no" in r:
        return "missing_input"
    return "insufficient_inputs"


def refusal(method_id: str, reason: str, name: str | None = None) -> dict:
    """A named refusal: the method would not answer, and why."""
    return {"refused": name or refusal_name(reason), "reason": reason, "method": describe(method_id)}
