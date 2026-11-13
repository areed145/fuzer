"""What each flow edge carries (P2.7, #205): `props.product` on flows_to and lift_gas_to, with `props.product_basis`
saying where it came from.

A source that says what an edge carries (its `service`, kept by the mapping) is taken as it is. Otherwise it is
worked out from the nodes' process types (`ec_type`) and a well's `well_type`, and an edge nothing settles has no
product rather than a guess:

- an edge on the lift gas network carries lift gas;
- what leaves a well carries what the well makes: gas from a gas well, an injector's water, else multiphase fluid;
- what goes into a compressor or a flare is gas, and into a tank or a pump is liquid;
- a node that only passes fluid on (a manifold, header, meter, compressor, tank, pipeline or sales point) has the
  same product in as out, so a product spreads along it both ways when every edge it knows of agrees;
- a separator splits fluid: an outlet keeps the product the rules above give it; a separator whose every known
  outlet is liquid takes liquid in, and one fed only gas puts out gas on any outlet still unsettled.

The link types stay as they are (`lift_gas_to` apart from `flows_to`, so a production trace never wanders into the lift
gas loop); this only says what each edge carries. Re-run after every edge build."""

from __future__ import annotations

from psycopg.types.json import Jsonb

LINKS = ("flows_to", "lift_gas_to")
GAS, LIQUID, MULTIPHASE, LIFT_GAS, WATER = "gas", "liquid", "multiphase", "lift_gas", "water"
PASS_THROUGH = {
    "manifold",
    "header",
    "meter",
    "compressor",
    "tank",
    "pipeline",
    "sales_point",
    "pump",
    "pig_trap",
    "valve",
    "station",
}
TAKES = {"compressor": GAS, "flare": GAS, "tank": LIQUID, "pump": LIQUID}  # what a node of this type takes in
SPLITS = {"separator"}
# what a source's `service` says, in this module's words; anything else is kept as the source wrote it
SERVICE = {
    "lift_gas": LIFT_GAS,
    "lift gas": LIFT_GAS,
    "gas": GAS,
    "oil": LIQUID,
    "condensate": LIQUID,
    "water": WATER,
    "liquid": LIQUID,
    "multiphase": MULTIPHASE,
}


def _well_fluid(well_type: str | None) -> str:
    t = (well_type or "").lower()
    return GAS if t == "gas" else WATER if "inject" in t and "water" in t else MULTIPHASE


def infer(edges: list[dict], nodes: dict[str, dict]) -> dict[int, tuple[str, str]]:
    """{edge id: (product, basis)} for the edges something settles. `edges` hold id, src, rel, dst and the source's
    `service`; `nodes` hold each endpoint's `ec_type` and `well_type`."""
    out: dict[int, tuple[str, str]] = {}
    kind = lambda n: (nodes.get(n) or {}).get("ec_type")
    for e in edges:
        said = (e.get("service") or "").strip()
        if said:
            out[e["id"]] = (SERVICE.get(said.lower(), said), "source")
        elif e["rel"] == "lift_gas_to":
            out[e["id"]] = (LIFT_GAS, "link type")
        elif kind(e["src"]) == "well":
            out[e["id"]] = (_well_fluid((nodes.get(e["src"]) or {}).get("well_type")), "well type")
        elif kind(e["dst"]) in TAKES:
            out[e["id"]] = (TAKES[kind(e["dst"])], "what it feeds")
    flow = [e for e in edges if e["rel"] == "flows_to"]
    ins: dict[str, list[dict]] = {}
    outs: dict[str, list[dict]] = {}
    for e in flow:
        ins.setdefault(e["dst"], []).append(e)
        outs.setdefault(e["src"], []).append(e)

    def settle(e: dict, p: str, basis: str) -> bool:
        if e["id"] in out:
            return False
        out[e["id"]] = (p, basis)
        return True

    def settle_all(es, p: str, basis: str) -> bool:  # every one, not just up to the first that changes
        return sum(settle(e, p, basis) for e in es) > 0

    def spread() -> None:  # pass-through nodes and liquid-only separators, to a fixed point
        changed = True
        while changed:
            changed = False
            for n in sorted(set(ins) | set(outs)):
                i, o = ins.get(n, []), outs.get(n, [])
                if kind(n) in PASS_THROUGH:
                    known = {out[e["id"]][0] for e in i + o if e["id"] in out}
                    if len(known) == 1:
                        p = known.pop()
                        changed |= settle_all(i + o, p, "passed on")
                elif kind(n) in SPLITS and o and all(e["id"] in out and out[e["id"]][0] == LIQUID for e in o):
                    changed |= settle_all(i, LIQUID, "separator outlets")

    # a separator fed only gas puts out gas, but only once nothing else can settle its outlets, so the order the
    # nodes are visited in never decides a product
    spread()
    while True:
        gas_fed = [
            n
            for n in sorted(outs)
            if kind(n) in SPLITS and ins.get(n) and all(e["id"] in out and out[e["id"]][0] == GAS for e in ins[n])
        ]
        if not settle_all([e for n in gas_fed for e in outs[n]], GAS, "separator inlet"):
            break
        spread()
    return out


def build(conn) -> dict:
    """Write each flow edge's product; returns how many were settled by each basis, and how many nothing settled."""
    rows = conn.execute("select id, src, rel, dst, props from edge where rel = any(%s)", (list(LINKS),)).fetchall()
    if not rows:
        return {"flow_products": {}}
    ids = list({r["src"] for r in rows} | {r["dst"] for r in rows})
    nodes: dict[str, dict] = {}
    for r in conn.execute(
        "select fuzer_id, field, value #>> '{}' as v from golden where fuzer_id = any(%s)"
        " and field in ('ec_type', 'well_type')",
        (ids,),
    ):
        nodes.setdefault(r["fuzer_id"], {})[r["field"]] = r["v"]
    edges = [
        {"id": r["id"], "src": r["src"], "rel": r["rel"], "dst": r["dst"], "service": (r["props"] or {}).get("service")}
        for r in rows
    ]
    found = infer(edges, nodes)
    counts: dict[str, int] = {}
    for r in rows:
        props = {k: v for k, v in (r["props"] or {}).items() if k not in ("product", "product_basis")}
        if props.get("service") is None:
            props.pop("service", None)
        if r["id"] in found:
            props["product"], props["product_basis"] = found[r["id"]]
        basis = found[r["id"]][1] if r["id"] in found else "unknown"
        counts[basis] = counts.get(basis, 0) + 1
        if props != (r["props"] or {}):
            conn.execute("update edge set props = %s where id = %s", (Jsonb(props) if props else None, r["id"]))
    return {"flow_products": counts}
