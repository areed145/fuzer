"""The pipelines and commercial specialist's tools (P4.7): a line's segments, stations and meters, the customer and
contract register, and the outages on the plan with what they cost the customers they hit. Declared with
`capability` (#133); mcp_server serves them."""

from typing import Any

from ...core import db
from ..mcp_server import _plain, capability

NO_CONTRACTS = {"unknown": True, "label": "Customer contracts", "note": "no customer contracts loaded"}


@capability("pipelines-commercial", kind="query")
def pipeline_segments(line: str | None = None) -> dict[str, Any]:
    """Every pipeline, or one by its name: service, diameter, length and MOP, the areas it starts and ends at with the
    flow meters there (in at the start, out at the end), and its route segments in station order, each with its
    length, wall thickness, grade, class location, the block valves or stations bounding it and how many in-line
    inspection features are found on it. No hydraulic model: pressures and flows come from `measurements` and
    `plot_trend` on the meters and transmitters."""
    from ...graph import positions
    with db.connect() as conn:
        return _plain(positions.lines(conn, line))


@capability("pipelines-commercial", kind="query")
def customer_contracts() -> dict[str, Any]:
    """The customer and contract register: each contract's customer (a shipper or a utility), what it buys, the
    delivery point it receives or delivers at (a meter or a tank), its daily volume (bbl/d), its rate ($/bbl) and the
    dates it runs. Contract volumes, not nominations: no nominations or shipper balances are loaded."""
    from ...ops import outages
    with db.connect() as conn:
        book = outages.contracts(conn)
    if not book:
        return dict(NO_CONTRACTS)
    return _plain({"contracts": book, "source": "the customer and contract register (contracts source)"})


@capability("pipelines-commercial", kind="calc")
def outage_impact(days: int = 90) -> dict[str, Any]:
    """The outages on the plan over the next `days`: each one's dates and hours, the plant it takes down (units and
    areas), the contracts it hits (delivery point down or left with no feed while the contract runs) with the volume
    (bbl) and dollars each loses, its crews and any over capacity; then each customer hit, with how many separate
    times and what it loses over the window (a contract's hours counted once where outages overlap)."""
    from ...ops import outages
    with db.connect() as conn:
        got = outages.outages(conn, days=max(1, min(days, 365)))
    keep = ("key", "title", "project", "start", "end", "hours", "units", "areas", "crews", "over_capacity", "hits",
            "volume_bbl", "usd")
    out = {"start": got["start"], "end": got["end"], "days": got["days"], "contracts": got["contracts"],
           "outages": [{k: o[k] for k in keep} for o in got["outages"]], "by_customer": got["by_customer"],
           "basis": got["basis"]}
    return _plain({**out, **NO_CONTRACTS} if not got["contracts"] else out)
