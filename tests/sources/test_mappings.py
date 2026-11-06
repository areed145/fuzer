"""Mapping files: a new tabular source needs only a YAML file, and bad mappings fail clearly."""

import csv
import os
import tempfile
from pathlib import Path

import pytest

from fakeplant import okan
from fuzer.core import db
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline
from fuzer.sources import ingest, mappings

PACK = load_pack()

INSTRUMENT_INDEX = """\
# An instrument index from the engineering contractor: service and range per instrument tag.
source: einx
record_type: instrument
shape: records
dataset: instrument_index
key: tag_no
entity:
  kind: tag
  key: {from: tag_no, as: tag}
  fields:
    - service
    - [range_high, {from: range_high, as: number}]
    - [range_unit, unit]
    - [vendor, {from: vendor, as: upper}]
links:
  - rel: instrument_of
    when: equipment
    to: {kind: tag, from: equipment, as: tag}
"""


@pytest.fixture
def extra_mapping(monkeypatch):
    folder = Path(tempfile.mkdtemp(prefix="fzmap"))
    (folder / "einx_instrument.yaml").write_text(INSTRUMENT_INDEX)
    monkeypatch.setenv("FUZER_MAPPINGS", str(folder))
    return folder


INDEX = [["tag_no", "service", "range_high", "unit", "vendor", "equipment"],
         ["10-PT-1001B", "Pump discharge pressure", "25", "barg", "Instrumentco", "10P1001B"],
         ["20-PT-9999Z", "Spare", "", "", "", ""]]


def write_index(data: Path, fmt: str) -> None:
    """The contractor's instrument index as a CSV, an Excel sheet under a title row, or JSON Lines (#132)."""
    if fmt == "csv":
        with (data / "instrument_index.csv").open("w", newline="") as f:
            csv.writer(f).writerows(INDEX)
    elif fmt == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Instrument index, rev C"])  # a title row and a blank row above the table
        ws.append([])
        ws.append(INDEX[0])
        ws.append([*INDEX[1][:2], 25, *INDEX[1][3:]])
        ws.append([*INDEX[2][:2], None, None, None, None])
        wb.save(data / "instrument_index.xlsx")
    else:
        import json

        (data / "instrument_index.jsonl").write_text("\n".join(json.dumps(dict(zip(INDEX[0], r))) for r in INDEX[1:]))


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "jsonl"])
def test_new_source_from_a_mapping_file_only(extra_mapping, fmt):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    data = Path(tempfile.mkdtemp(prefix="fzdata"))
    okan.generate(data)
    write_index(data, fmt)

    assert "instrument_index" in ingest.tables()
    with db.connect() as conn:
        db.reset(conn)
        stats = ingest.ingest(conn, data)
        assert "instrument_index" in stats["loaded"]
        pipeline.rebuild(conn, PACK)
        row = conn.execute(
            """
            select c.fuzer_id, e.kind from crosswalk c join entity e on e.fuzer_id = c.fuzer_id
            where c.source = 'einx' and c.source_key = '10-PT-1001B'
            """).fetchone()
        assert row["kind"] == "tag"
        # Same tag as the one SAP and PI already know: merged, not a new entity.
        others = {r["source"] for r in conn.execute("select source from crosswalk where fuzer_id = %s",
                                                    (row["fuzer_id"],))}
        assert {"einx", "sap"} <= others
        golden = {r["field"]: r["value"] for r in conn.execute(
            "select field, value from golden where fuzer_id = %s", (row["fuzer_id"],))}
        assert golden["service"] == "Pump discharge pressure" and golden["range_high"] == 25.0
        assert golden["vendor"] == "INSTRUMENTCO" and "range_unit" in golden
        pump = conn.execute("select dst from edge where src = %s and rel = 'instrument_of'",
                            (row["fuzer_id"],)).fetchone()["dst"]
        assert conn.execute("select label from entity_label where fuzer_id = %s", (pump,)).fetchone()["label"] == "10-P-1001B"


def test_every_built_in_table_has_a_mapping():
    assert {"sap_equipment", "pi_points", "oplogs", "sap_bom"} <= set(ingest.tables())
    assert mappings.tables()["oplogs"].watermark == "timestamp"
    assert mappings.tables()["sap_bom"].key == ("equnr", "matnr")


@pytest.mark.parametrize("raw, message", [
    ({"source": "x", "record_type": "y"}, "missing 'shape'"),
    ({"source": "x", "record_type": "y", "shape": "blob"}, "unknown shape"),
    ({"source": "x", "record_type": "y", "shape": "records", "dataset": "t"}, "needs a key"),
    ({"source": "x", "record_type": "y", "shape": "records", "entity": {"kind": "tag", "key": {"from": "a", "as": "nope"}}},
     "unknown transform"),
    ({"source": "x", "record_type": "y", "shape": "records", "hooks": ["nope"]}, "unknown hook"),
    ({"source": "x", "record_type": "y", "shape": "records", "links": [{"to": {"kind": "tag", "from": "a"}}]},
     "link without rel"),
])
def test_bad_mappings_fail_clearly(raw, message):
    with pytest.raises(mappings.MappingError, match=message):
        mappings.Mapping(raw)


def test_when_and_unless_test_values():
    from fuzer.core.pack import load_pack

    pack = load_pack("okan")
    m = mappings.Mapping({"source": "ec", "record_type": "edge", "shape": "network", "links": [
        {"rel": "flows_to", "unless": {"service": "lift_gas"}, "from": {"kind": "well", "from": "a"},
         "to": {"kind": "well", "from": "b"}},
        {"rel": "lift_gas_to", "when": {"service": "lift_gas"}, "from": {"kind": "well", "from": "a"},
         "to": {"kind": "well", "from": "b"}}]})
    rels = lambda row: [e.rel for e in m.extract("k", row, pack)[1]]  # noqa: E731
    assert rels({"a": "x", "b": "y"}) == ["flows_to"]
    assert rels({"a": "x", "b": "y", "service": "lift_gas"}) == ["lift_gas_to"]
    with pytest.raises(mappings.MappingError, match="unless"):
        mappings.Mapping({"source": "s", "record_type": "r", "shape": "network",
                          "links": [{"rel": "x", "unless": "service", "to": {"kind": "well", "from": "b"}}]})
