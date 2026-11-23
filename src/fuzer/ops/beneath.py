"""An entity's place in the register, with the work rolled up through it: the chain above it, and the first levels
beneath it, each node carrying the open work about it or anything in it and how many things in it are down now.

    view(conn, fid, levels)   {up, children, more, total, kinds, open_work, down}

Open work is the List's open rows (`worklist.rows`, filtered `within` the entity), so a node's count is the number
All work shows when filtered to it. Down now is `status_now.down`: a newest downtime with no end.
"""

from ..graph import hierarchy


def view(conn, fid: str, levels: int = 2) -> dict:
    from . import status_now, worklist
    from .attention import RANK, band

    tree = hierarchy.subtree(conn, fid)
    out = hierarchy.beneath(conn, fid, levels, tree)
    work = worklist.rows(conn, within=fid, limit=100_000)["rows"]
    down = status_now.down(conn, list(tree))

    def chain(nid: str) -> list[str]:
        """The node and every ancestor up to (not including) fid."""
        seen: list[str] = []
        while nid in tree and nid not in seen:
            seen.append(nid)
            nid = tree[nid]["parent"]
        return seen

    counts: dict[str, int] = {}
    worst: dict[str, str] = {}
    for r in work:
        hit: set[str] = set()
        for s in r.get("subjects") or []:
            hit.update(chain(s))
        b = band(r.get("priority"))
        for nid in hit:
            counts[nid] = counts.get(nid, 0) + 1
            if nid not in worst or RANK[b] < RANK[worst[nid]]:
                worst[nid] = b
    downs: dict[str, int] = {}
    for nid in down:
        for a in chain(nid):
            downs[a] = downs.get(a, 0) + 1

    def dress(node: dict) -> dict:
        nid = node["id"]
        node.update(open_work=counts.get(nid, 0), work_band=worst.get(nid), down=downs.get(nid, 0))
        for c in node["children"]:
            dress(c)
        return node

    for c in out["children"]:
        dress(c)
    return {"up": hierarchy.up(conn, fid), **out, "open_work": len(work), "down": len(down)}
