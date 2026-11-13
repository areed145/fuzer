"""The generated P&IDs (#475): laid out by process flow, with the symbols, loops, off-page connectors and
degradation a real sheet has, and an answer key complete enough to score extraction per element."""

import json

import pytest


@pytest.fixture(scope="module")
def truth(okan_db):
    """The P&IDs' answer keys (the PFDs' are in `pfds`)."""
    yield {s: k for s, k in json.loads((okan_db.data / "pid_truth.json").read_text()).items() if k.get("kind") != "pfd"}


@pytest.fixture(scope="module")
def pfds(okan_db):
    yield {s: k for s, k in json.loads((okan_db.data / "pid_truth.json").read_text()).items() if k.get("kind") == "pfd"}


def test_pfds_draw_trains_once_with_a_stream_table(pfds):
    """The gas plant's PFD draws its three trains once (20-K-2100A/B/C): the key expands each to its tags, a stream
    between trains to one connection per train, and the header into the trains to one per train (#475)."""
    gas = pfds["OKAN-20-PFD-002"]
    assert set(pfds) == {"OKAN-10-PFD-001", "OKAN-20-PFD-002"} and gas["level"] == "old"
    k = {t["key"]: t["as_drawn"] for t in gas["tags"]}
    assert k["20-K-2100B"] == "20-K-2100A/B/C" and k["20-V-2001"] == "20-V-2001"
    pairs = {(c["from"], c["to"], c["stream"]) for c in gas["connections"]}
    assert {("20-V-2001", "20-V-2101A", 2), ("20-V-2001", "20-V-2101C", 2), ("20-K-2100B", "20-E-2150B", 4)} <= pairs
    assert not any(c["from"][-1] != c["to"][-1] and c["from"] != "20-V-2001" for c in gas["connections"])
    table = gas["tables"][0]
    assert table["kind"] == "streams" and table["columns"][0] == "STREAM No." and len(table["columns"]) == 12
    assert [r["cells"][0] for r in table["rows"]] == ["DESCRIPTION", "PHASE", "PRESSURE (barg)", "TEMPERATURE (DEG C)",
                                                     "FLOW"]


def test_every_degradation_level_is_drawn(truth):
    assert {k["level"] for k in truth.values()} == {"clean", "scan", "old", "poor"}
    assert all(k["size"][0] >= 2200 and k["size"][1] >= 1500 for k in truth.values())


def test_off_page_connectors_pair_up_across_sheets(truth):
    ends = [(s, o) for s, k in truth.items() for o in k["offpage"]]
    assert len(ends) == 12  # the surge drum to each export pump, and the inlet separator to each train
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
    assert {"gate", "globe", "ball", "check", "butterfly", "control", "esd", "strainer", "orifice", "sample_point",
            "drain", "vent"} <= types
    conns = [c for k in truth.values() for c in k["connections"]]
    assert all("via" in c for c in conns) and any(len(c["via"]) > 1 for c in conns)
    assert not any(set(c["via"]) & {"sample_point", "drain", "vent"} for c in conns)  # branches, not in line
    assert any(c["to"] is None and c.get("service") == "closed drain" for c in conns)
    lines = [c["line"] for c in conns]
    assert all(ln.count("-") >= 3 and '"' in ln for ln in lines)  # size"-service-number-spec[-insulation]
    assert any(u["kind"] == "relief_valve" for k in truth.values() for u in k["unregistered"])


def test_in_sheet_lines_stay_scorable(truth):
    """The `lines` the piping score reads are the in-sheet equipment-to-equipment connections."""
    for k in truth.values():
        pairs = {(c["from"], c["to"]) for c in k["connections"] if c["to"] and not c.get("offpage")}
        assert {(ln["from"], ln["to"]) for ln in k["lines"]} == pairs


def test_tables_and_notes_in_the_answer_key(tmp_path):
    """The foot carries an equipment data table, an alarm and trip table and numbered notes (#475), each in the key
    with where it sits; a poor scan's torn corner hides the table rows under it."""
    import random

    from fakeplant.common import Tag

    from fakeplant import pid

    tags = [Tag("20", "V", 2001, ""), Tag("20", "PT", 2001, ""), Tag("20", "LT", 2001, ""), Tag("20", "P", 2001, "A")]
    data = {"equipment": {"20-V-2001": {"service": "Inlet separator", "design_pressure": 110, "design_temperature": 120,
                                        "capacity": "170 MMscf/d"}},
            "setpoints": {"20-PT-2001": {"lolo": 40, "lo": 45, "hi": 90, "hihi": 95, "unit": "barg"},
                          "20-LT-2001": {"lo": 10, "hi": 62, "unit": "%"}},
            "notes": [{"text": "PSV-2001 ON 20-V-2001 SET AT 110 barg.", "tags": ["20-V-2001"]}], "date": "2019-05-14"}
    for level in ("clean", "poor"):
        key = pid.draw(tmp_path / f"{level}.png", {"sheet": "X-20-PID-001", "title": "AREA 20 - TEST", "revision": "B"},
                       tags, {}, random.Random(1), [{"from": "20-V-2001", "to": "20-P-2001A", "line": '6"-P-20001-A1'}],
                       level=level, data=data)
        kinds = {t["kind"]: t for t in key["tables"]}
        assert set(kinds) == {"equipment", "setpoints"} and key["date"] == "2019-05-14"
        eq = kinds["equipment"]["rows"][0]
        assert eq["key"] == "20-V-2001" and eq["cells"][2:] == ["110", "120", "170 MMscf/d"]
        sp = {r["key"]: r["cells"] for r in kinds["setpoints"]["rows"]}
        assert sp["20-PT-2001"] == ["PT-2001", "40", "45", "90", "95", "barg"] and sp["20-LT-2001"][1] == "-"
        assert key["notes"][-1] == {**key["notes"][-1], "n": 5, "tags": ["20-V-2001"]}
        if level == "poor":
            assert any(r.get("hidden") for t in key["tables"] for r in t["rows"])
