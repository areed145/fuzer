"""The generated P&IDs (#475): laid out by process flow, with the symbols, loops, off-page connectors and
degradation a real sheet has, and an answer key complete enough to score extraction per element."""

import json

import pytest


@pytest.fixture(scope="module")
def truth(okan_db):
    yield json.loads((okan_db.data / "pid_truth.json").read_text())


def test_every_degradation_level_is_drawn(truth):
    assert {k["level"] for k in truth.values()} == {"clean", "scan", "old", "poor"}
    assert all(k["size"][0] >= 2200 and k["size"][1] >= 1500 for k in truth.values())


def test_off_page_connectors_pair_up_across_sheets(truth):
    ends = [(s, o) for s, k in truth.items() for o in k["offpage"]]
    assert len(ends) == 8
    for sheet, o in ends:
        other = truth[o["sheet"]]["offpage"]
        back = [x for x in other if x["line"] == o["line"] and x["sheet"] == sheet]
        assert back and back[0]["direction"] != o["direction"] and back[0]["tag"] == o["other"]
    # each end is a connection in the key, naming the sheet it continues on
    crossing = [c for k in truth.values() for c in k["connections"] if c.get("offpage")]
    assert {(c["from"], c["to"]) for c in crossing} >= {("20-V-2001", "20-V-2101A"), ("10-V-2004", "10-P-1001A")}


def test_loops_run_transmitter_controller_valve(truth):
    loops = [lp for k in truth.values() for lp in k["loops"]]
    assert loops and all(len(lp["members"]) in (2, 3) for lp in loops) and sum(len(lp["members"]) == 3 for lp in loops) > 10
    for sheet, k in truth.items():
        unregistered = {u["text"] for u in k["unregistered"]}
        valves = {v.get("tag") for v in k["valves"]}
        tags = {t["key"] for t in k["tags"]}
        for lp in k["loops"]:
            transmitter, controller, *valve = lp["members"]
            assert transmitter in tags and controller in unregistered and set(valve) <= valves, (sheet, lp)


def test_symbols_and_line_numbers(truth):
    types = {v["type"] for k in truth.values() for v in k["valves"]}
    assert {"gate", "globe", "ball", "check", "butterfly", "control", "esd"} <= types
    conns = [c for k in truth.values() for c in k["connections"]]
    assert all("via" in c for c in conns) and any(len(c["via"]) > 1 for c in conns)
    assert any(c["to"] is None and c.get("service") == "closed drain" for c in conns)
    lines = [c["line"] for c in conns]
    assert all(ln.count("-") >= 3 and '"' in ln for ln in lines)  # size"-service-number-spec[-insulation]
    assert any(u["kind"] == "relief_valve" for k in truth.values() for u in k["unregistered"])


def test_in_sheet_lines_stay_scorable(truth):
    """The `lines` the piping score reads are the in-sheet equipment-to-equipment connections."""
    for k in truth.values():
        pairs = {(c["from"], c["to"]) for c in k["connections"] if c["to"] and not c.get("offpage")}
        assert {(ln["from"], ln["to"]) for ln in k["lines"]} == pairs
