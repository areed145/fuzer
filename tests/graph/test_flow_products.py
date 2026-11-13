"""What each flow edge carries (P2.7, #205): taken from the source's service where it says, else worked out from the
nodes' process types, and left unsaid where nothing settles it. Kestrel's EC network states no service at all, so
every product there is worked out; Okan's lift gas network says its own."""

import pytest
from test_calibration import kestrel  # noqa: F401  (the Kestrel bundle, built fresh)

from fuzer.graph import products

pytestmark = pytest.mark.sequence


def _edges(conn) -> dict[tuple[str, str], dict]:
    rows = conn.execute(
        """
        select coalesce(s.value #>> '{}', e.src) as a, coalesce(d.value #>> '{}', e.dst) as b, e.rel, e.props
        from edge e
        left join golden s on s.fuzer_id = e.src and s.field = 'ec_code'
        left join golden d on d.fuzer_id = e.dst and d.field = 'ec_code'
        where e.rel = any(%s)
        """,
        (list(products.LINKS),),
    ).fetchall()
    return {(r["a"], r["b"]): r for r in rows}


def test_rules_settle_what_they_can_and_leave_the_rest():
    nodes = {
        "w": {"ec_type": "well", "well_type": "oil"},
        "g": {"ec_type": "well", "well_type": "gas"},
        "m": {"ec_type": "manifold"},
        "s": {"ec_type": "separator"},
        "k": {"ec_type": "compressor"},
        "t": {"ec_type": "tank"},
        "x": {"ec_type": "separator"},
        "q": {"ec_type": "sales_point"},
    }
    edges = [
        {"id": 1, "src": "w", "rel": "flows_to", "dst": "m"},
        {"id": 2, "src": "g", "rel": "flows_to", "dst": "s"},
        {"id": 3, "src": "m", "rel": "flows_to", "dst": "s"},
        {"id": 4, "src": "s", "rel": "flows_to", "dst": "k"},
        {"id": 5, "src": "s", "rel": "flows_to", "dst": "x"},
        {"id": 6, "src": "x", "rel": "flows_to", "dst": "t"},
        {"id": 7, "src": "k", "rel": "lift_gas_to", "dst": "w"},
        {"id": 8, "src": "s", "rel": "flows_to", "dst": "q", "service": "Condensate"},
    ]
    got = {i: p for i, (p, _) in products.infer(edges, nodes).items()}
    assert got == {
        1: "multiphase",
        2: "gas",
        3: "multiphase",
        4: "gas",
        5: "liquid",
        6: "liquid",
        7: "lift_gas",
        8: "liquid",
    }
    # a separator outlet nothing downstream explains, fed more than gas, is left unsaid rather than guessed
    assert 9 not in products.infer(edges + [{"id": 9, "src": "s", "rel": "flows_to", "dst": "zz"}], nodes)


def test_kestrel_gas_plant_says_what_every_edge_carries(kestrel):  # noqa: F811
    e = _edges(kestrel)
    product = {k: (v["props"] or {}).get("product") for k, v in e.items()}
    assert product[("KR_12_03H", "PAD12-MAN")] == "gas"  # two gas wells
    assert product[("KRGP-INLET", "KRGP-COMP")] == "gas" and product[("KRGP-INLET", "KRGP-FLARE")] == "gas"
    assert product[("KRGP-INLET", "KRGP-STAB")] == "liquid" and product[("KRGP-STAB", "KRGP-CONDTK")] == "liquid"
    assert product[("KRGP-HCDP", "KRGP-SALESMTR")] == "gas" and product[("KRGP-SALESMTR", "SALES-TIE")] == "gas"
    assert None not in product.values()
    assert all(v["props"]["product_basis"] != "source" for v in e.values())  # Kestrel's export says no service


def test_okan_lift_gas_says_its_own_and_wells_carry_what_they_make(okan_db):
    e = _edges(okan_db.conn)
    props = {k: v["props"] or {} for k, v in e.items()}
    lift = [p for k, p in props.items() if e[k]["rel"] == "lift_gas_to"]
    assert lift and all((p["product"], p["product_basis"]) == ("lift_gas", "source") for p in lift)
    from_wells = [p for p in props.values() if p.get("product_basis") == "well type"]
    assert {p["product"] for p in from_wells} == {"gas", "multiphase"} and len(from_wells) > 150
    assert props[("PP-HPSEP", "PP-LPSEP")].get("product") is None  # a separator fed multiphase: not guessed
    unsaid = {k for k, p in props.items() if not p.get("product")}
    # only what leaves a separator fed multiphase fluid, and the oil meter it passes through, is left unsaid
    assert unsaid == {
        ("ST1-PRODSEP", "PP-HPSEP"),
        ("ST2-PRODSEP", "PP-HPSEP"),
        ("NWP-PRODSEP", "PP-HPSEP"),
        ("PP-HPSEP", "PP-LPSEP"),
        ("PP-HPSEP", "GGCP-INLET"),
        ("PP-LPSEP", "PP-OILMTR"),
        ("PP-OILMTR", "ESCRAVOS-TERM"),
    }, unsaid
    stats = products.build(okan_db.conn)["flow_products"]  # the same again: re-running changes nothing
    assert stats["unknown"] == len(unsaid) and sum(stats.values()) == len(e)
