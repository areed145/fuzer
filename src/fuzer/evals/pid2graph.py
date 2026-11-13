"""Score a reading of a P&ID drawing against PID2Graph's ground truth (#858).

`evals/pid2graph/` keeps a slice of PID2Graph (CC BY-SA 4.0, see its README): the 12 real OPEN100
drawings and 20 synthetic ones, each an image with a `.graphml` of what is on it. Symbols are nodes
with a class and a bounding box in pixels, and lines are edges. Lines meet symbols at `connector` nodes,
cross each other at `crossing` nodes, and carry flow `arrow`s. None of those three is a symbol. The
graphs carry no tag text, so the score covers what was found and what connects to what, not the names.

A reading has the same shape as `truth()`: `symbols` as {id, label, box: (xmin, ymin, xmax, ymax)},
and `connections` as pairs of symbol ids joined by a line. `connections()` collapses a graph's line
network into those pairs. It walks through connectors, arrows and line nodes. In PID2Graph a
`crossing` is any point where lines meet: mostly bends (two lines) and tees (three), which join
everything. Where four lines meet, it goes straight on: each line pairs with the one leaving most
nearly opposite, so two lines that only cross don't join.

`score(reading, truth)` matches symbols one to one, most overlap first: same class, and intersection
over union at least `iou` (default 0.5). It returns precision and recall for symbols overall and per
class, then for connections between matched symbols (a connection to an unmatched symbol counts as
missed or wrong). Standard library only.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "evals" / "pid2graph"
NS = "{http://graphml.graphdrawing.org/xmlns}"
LINE_NODES = {"connector", "crossing", "arrow"}


def sheets(root=ROOT):
    """Every drawing in the slice: [{set, id, image, graph}], OPEN100 first, in number order."""
    out = []
    for kind in ("open100", "synthetic"):
        for g in sorted((Path(root) / kind).glob("*.graphml"), key=lambda p: int(p.stem)):
            image = next(p for p in g.parent.glob(f"{g.stem}.*") if p.suffix != ".graphml")
            out.append({"set": kind, "id": f"{kind}/{g.stem}", "image": image, "graph": g})
    return out


def load(path):
    """A graphml file as {nodes: {id: {label, box}}, edges: [(a, b, label)]}."""
    root = ET.parse(path).getroot()
    keys = {k.get("id"): k.get("attr.name") for k in root.iter(NS + "key")}
    nodes = {}
    for n in root.iter(NS + "node"):
        d = {keys[x.get("key")]: x.text for x in n.iter(NS + "data")}
        box = tuple(float(d[k]) for k in ("xmin", "ymin", "xmax", "ymax"))
        nodes[n.get("id")] = {"label": d.get("label"), "box": box}
    edges = []
    for e in root.iter(NS + "edge"):
        d = {keys[x.get("key")]: x.text for x in e.iter(NS + "data")}
        edges.append((e.get("source"), e.get("target"), d.get("edge_label")))
    return {"nodes": nodes, "edges": edges}


def _centre(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def connections(graph):
    """Pairs of symbols joined by a line, as a set of sorted (a, b) tuples."""
    nodes, adj = graph["nodes"], {}
    for a, b, _ in graph["edges"]:
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    def straight_on(cross, came):
        # The neighbour leaving the crossing most nearly opposite the one we came from.
        c, p = _centre(nodes[cross]["box"]), _centre(nodes[came]["box"])
        back = math.atan2(p[1] - c[1], p[0] - c[0])
        best, gap = None, None
        for n in adj[cross] - {came}:
            q = _centre(nodes[n]["box"])
            turn = abs((math.atan2(q[1] - c[1], q[0] - c[0]) - back + math.pi) % (2 * math.pi) - math.pi)
            if gap is None or abs(turn - math.pi) < gap:
                best, gap = n, abs(turn - math.pi)
        return [best] if best else []

    pairs = set()
    for s, node in nodes.items():
        if node["label"] in LINE_NODES:
            continue
        seen, stack = {s}, [(n, s) for n in adj.get(s, ())]
        while stack:
            here, came = stack.pop()
            if (here, came) in seen:
                continue
            seen.add((here, came))
            label = nodes[here]["label"]
            if label not in LINE_NODES:
                if here != s:
                    pairs.add(tuple(sorted((s, here))))
                continue
            onward = straight_on(here, came) if label == "crossing" and len(adj[here]) == 4 else adj[here] - {came}
            stack.extend((n, here) for n in onward)
    return pairs


def truth(path):
    """A drawing's ground truth in the shape a reading takes: {symbols, connections}."""
    g = load(path)
    symbols = [{"id": i, "label": n["label"], "box": n["box"]} for i, n in g["nodes"].items()
               if n["label"] not in LINE_NODES]
    return {"symbols": symbols, "connections": connections(g)}


def _iou(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def _pr(found, said, there):
    return {"found": found, "said": said, "there": there,
            "precision": round(found / said, 3) if said else None,
            "recall": round(found / there, 3) if there else None}


def score(reading, truth, iou=0.5):
    """Precision and recall of a reading's symbols (overall and by class) and connections."""
    candidates = sorted(
        ((_iou(r["box"], t["box"]), r["id"], t["id"]) for r in reading["symbols"] for t in truth["symbols"]
         if r["label"] == t["label"]),
        reverse=True)
    match, used = {}, set()
    for overlap, r, t in candidates:
        if overlap < iou:
            break
        if r not in match and t not in used:
            match[r] = t
            used.add(t)
    labels = {t["id"]: t["label"] for t in truth["symbols"]}
    by_class = {}
    for label in sorted({s["label"] for s in truth["symbols"]} | {s["label"] for s in reading["symbols"]}):
        said = sum(s["label"] == label for s in reading["symbols"])
        there = sum(s["label"] == label for s in truth["symbols"])
        found = sum(labels[t] == label for t in match.values())
        by_class[label] = _pr(found, said, there)
    mapped = {tuple(sorted((match[a], match[b]))) for a, b in reading["connections"] if a in match and b in match}
    return {
        "symbols": _pr(len(match), len(reading["symbols"]), len(truth["symbols"])),
        "by_class": by_class,
        "connections": _pr(len(mapped & truth["connections"]), len(reading["connections"]),
                           len(truth["connections"])),
    }
