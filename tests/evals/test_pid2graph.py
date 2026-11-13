"""The PID2Graph slice loads, and a reading is scored against its ground truth (#858)."""

from fuzer.evals import pid2graph


def test_the_slice_is_the_12_open100_drawings_and_20_synthetic_ones_with_their_licence():
    sheets = pid2graph.sheets()
    assert [s["set"] for s in sheets].count("open100") == 12
    assert [s["set"] for s in sheets].count("synthetic") == 20
    assert all(s["image"].exists() for s in sheets)
    assert (pid2graph.ROOT / "LICENSE").read_text().startswith("Creative Commons Attribution-ShareAlike 4.0")


def test_every_drawing_has_symbols_joined_by_lines_and_no_line_nodes_as_symbols():
    for s in pid2graph.sheets():
        t = pid2graph.truth(s["graph"])
        ids = {x["id"] for x in t["symbols"]}
        assert t["symbols"] and t["connections"], s["id"]
        assert not {x["label"] for x in t["symbols"]} & pid2graph.LINE_NODES
        assert all(a in ids and b in ids and a < b for a, b in t["connections"])


def test_lines_that_cross_do_not_join_and_a_tee_joins_all_three():
    def box(x, y):
        return {"label": None, "box": (x - 1, y - 1, x + 1, y + 1)}

    nodes = {"W": box(0, 50), "E": box(100, 50), "N": box(50, 0), "S": box(50, 100), "X": box(50, 50),
             "T": box(150, 50), "U": box(150, 0), "V": box(200, 50)}
    for k in "WENSUV":
        nodes[k]["label"] = "valve"
    nodes["X"]["label"] = nodes["T"]["label"] = "crossing"
    graph = {"nodes": nodes, "edges": [("W", "X", "solid"), ("X", "E", "solid"), ("N", "X", "solid"),
                                       ("X", "S", "solid"), ("U", "T", "solid"), ("T", "V", "solid"),
                                       ("E", "T", "solid")]}
    pairs = pid2graph.connections(graph)
    assert ("E", "W") in pairs and ("N", "S") in pairs
    assert ("N", "W") not in pairs and ("E", "N") not in pairs
    assert {("E", "U"), ("E", "V"), ("U", "V")} <= pairs


def test_the_truth_scores_perfect_and_a_missed_symbol_costs_its_connections():
    t = pid2graph.truth(pid2graph.sheets()[0]["graph"])
    perfect = pid2graph.score(t, t)
    assert perfect["symbols"]["recall"] == perfect["connections"]["precision"] == 1.0

    gone = t["symbols"][0]["id"]
    reading = {"symbols": t["symbols"][1:], "connections": {c for c in t["connections"] if gone not in c}}
    lost = sum(gone in c for c in t["connections"])
    s = pid2graph.score(reading, t)
    assert s["symbols"]["found"] == len(t["symbols"]) - 1 and s["symbols"]["precision"] == 1.0
    assert s["connections"]["found"] == len(t["connections"]) - lost


def test_a_box_that_barely_overlaps_or_has_the_wrong_class_is_not_a_match():
    t = {"symbols": [{"id": "a", "label": "valve", "box": (0, 0, 10, 10)},
                     {"id": "b", "label": "pump", "box": (20, 0, 30, 10)}],
         "connections": {("a", "b")}}
    reading = {"symbols": [{"id": "1", "label": "valve", "box": (6, 0, 16, 10)},
                           {"id": "2", "label": "tank", "box": (20, 0, 30, 10)}],
               "connections": {("1", "2")}}
    s = pid2graph.score(reading, t)
    assert s["symbols"]["found"] == 0 and s["connections"]["found"] == 0
    assert s["by_class"]["tank"] == {"found": 0, "said": 1, "there": 0, "precision": 0.0, "recall": None}
