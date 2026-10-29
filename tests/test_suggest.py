"""Suggested mappings: rules name keys and references, a reviewer accepts or edits the draft, and the
next ingest loads the table; Claude names what rules can't."""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fuzer import connectors, db, evaluate, fakedata, ingest, pipeline, suggest
from fuzer.pack import load_pack

PACK = load_pack()


@pytest.fixture(scope="module")
def setup():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    fakedata.generate(data)
    with db.connect() as c:
        db.reset(c)
        ingest.ingest(c, data)
        pipeline.rebuild(c, PACK)
        yield c, data


class FakeClaude:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        sent = json.loads(kw["messages"][0]["content"])
        rules = {c["column"]: c["rules"] for c in sent["columns"]}
        text = json.dumps({"key": "", "columns": [{"column": c, "meaning": self.answer.get(c, rules[c])}
                                                  for c in sent["ask_about"]]})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=500, output_tokens=50, cache_read_input_tokens=0))


def test_rules_find_keys_and_references(setup):
    conn, data = setup
    cases = json.loads((data / "answer_keys" / "schema.json").read_text())
    built_in = evaluate.schema(conn, PACK, [c for c in cases if c["dir"] == "."], data)["rules"]
    assert built_in["key"] == 1.0 and built_in["references"] == 1.0
    assert built_in["columns"] >= 0.95
    incoming = evaluate.schema(conn, PACK, [c for c in cases if c["dir"] == "incoming"], data)["rules"]
    assert incoming["key"] == 1.0 and incoming["references"] == 1.0


def test_references_to_known_records(setup):
    conn, data = setup
    s = suggest.suggest_table(conn, PACK, "sap_work_orders", connectors.find(data, "sap_work_orders"),
                              exclude={("sap", "work_order")})
    cols = s["suggestion"]["columns"]
    assert cols["equnr"] == {"meaning": "ref", "by": "rules", "why": "values are keys of sap:equipment records",
                             "ref": "sap:equipment"}
    assert cols["qmnum"]["ref"] == "sap:notification"
    assert {"rel": "refers_to", "when": "equnr", "to": {"ref": "sap:equipment", "from": "equnr"}} in s["mapping"]["links"]


def test_incoming_folder_is_not_a_table(setup):
    _, data = setup
    assert connectors.find(data, "incoming") is None
    assert connectors.find(data / "incoming", "well_tests").kind == "parquet"
    assert "incoming" not in ingest.unmapped(data)
    assert set(ingest.unmapped(data / "incoming")) >= {"line_list", "location_hierarchy", "well_tests"}


def test_accept_edited_mapping_then_ingest(setup):
    conn, data = setup
    out = suggest.run(conn, PACK, data / "incoming")
    assert out["location_hierarchy"]["shape"] == "hierarchy" and out["line_list"]["shape"] == "network"
    item = conn.execute("select * from review_item where kind = 'mapping' and subject = 'location_hierarchy'").fetchone()
    client = TestClient(__import__("fuzer.api", fromlist=["app"]).app)

    bad = client.post(f"/review/{item['id']}", json={"decision": "choose", "value": "source: [", "by": "t"})
    assert bad.status_code == 400 and "not valid" in bad.json()["detail"]
    moved = item["payload"]["yaml"].replace("dataset: location_hierarchy", "dataset: elsewhere")
    assert client.post(f"/review/{item['id']}", json={"decision": "choose", "value": moved, "by": "t"}).status_code == 400

    edited = item["payload"]["yaml"].replace("source: location", "source: cmms")
    assert client.post(f"/review/{item['id']}", json={"decision": "choose", "value": edited, "by": "t"}).status_code == 200
    v = conn.execute("select * from mapping_version order by id desc limit 1").fetchone()
    assert (v["source"], v["record_type"], v["approved_by"]) == ("cmms", "hierarchy", "t")

    assert "location_hierarchy" not in ingest.unmapped(data / "incoming", conn)
    stats = ingest.ingest(conn, data / "incoming")
    assert stats["loaded"] == ["location_hierarchy"]
    pipeline.rebuild(conn, PACK)
    # The contractor's location codes are the same places SAP knows: merged into the same entities.
    row = conn.execute(
        """
        select count(*) as n from crosswalk c join crosswalk s on s.fuzer_id = c.fuzer_id and s.source = 'sap'
        where c.source = 'cmms'
        """).fetchone()
    total = conn.execute("select count(*) as n from crosswalk where source = 'cmms'").fetchone()["n"]
    assert total > 0 and row["n"] == total


def test_claude_names_what_rules_cannot(setup):
    conn, data = setup
    c = connectors.find(data / "incoming", "export_0425")
    rules_only = suggest.suggest_table(conn, PACK, "export_0425", c)
    assert rules_only["suggestion"]["columns"]["C3"]["meaning"] == "code"

    fake = FakeClaude({"C3": "person"})
    s = suggest.suggest_table(conn, PACK, "export_0425", c, claude=True, client=fake)
    assert s["suggestion"]["columns"]["C3"] == {"meaning": "person", "by": "claude", "why": "Claude, from header and samples"}
    assert s["suggestion"]["columns"]["C1"]["by"] == "rules"  # rules keep what they named
    suggest.suggest_table(conn, PACK, "export_0425", c, claude=True, client=fake)
    assert fake.calls == 1  # cached

    cases = [x for x in json.loads((data / "answer_keys" / "schema.json").read_text()) if x["dataset"] == "export_0425"]
    scores = evaluate.schema(conn, PACK, cases, data)
    assert scores["rules_and_claude"]["columns"] == 1.0 > scores["rules"]["columns"]
