"""Smart P&IDs as DEXPI XML (#858): Kestrel's sheets written the way a P&ID tool exports them read back to the same
tags and piping, and the bundle's flare and sales metering sheet, kept only as DEXPI, loads like any smart sheet."""

import json
from pathlib import Path

import pytest

from fakeplant import dexpi as writer
from fuzer.core import bundles
from fuzer.sources import dexpi, ingest

ROOT = Path(__file__).resolve().parents[2]
KESTREL = ROOT / "fakeplant" / "bundles" / "kestrel"


@pytest.fixture(scope="module")
def sheets():
    return {s["sheet"]: s for _, _, _, s in ingest._pid_sheets(KESTREL / "exports" / "pid_sheets.json")}


@pytest.mark.unit
def test_every_kestrel_sheet_reads_back_from_dexpi(sheets, tmp_path):
    pack = bundles.read(KESTREL)["pack"]
    for name, s in sheets.items():
        area = name.split("-")[1]
        f = tmp_path / f"{name}.xml"
        f.write_text(writer.sheet_xml(s))
        got = dexpi.read(f, area)
        assert sorted(pack.tags.parse(t, area) for t in got["tags"]) == sorted(s["tags"]), name
        assert sorted((c["from"], c["to"], c["line"]) for c in got["connections"]) == sorted(
            (c["from"], c["to"], c["line"]) for c in s["connections"]), name
        assert got["unresolved"] == 0 and got["title"] == s["title"]


@pytest.mark.unit
def test_the_compression_sheet_ships_as_dexpi_and_loads_as_a_smart_sheet(sheets):
    entry = next(s for s in json.loads((KESTREL / "exports" / "pid_sheets.json").read_text())
                 if s["sheet"] == "KRGP-H1-PID-001")
    assert entry["dexpi"].endswith(".xml") and "tags" not in entry and "connections" not in entry
    s = sheets["KRGP-H1-PID-001"]
    assert s["format"] == "smart" and s["dexpi_version"] == "4.1.1" and s["dexpi_sha256"]
    assert s["tags"] == ["H1-V-8001", "H1-FT-8003", "H1-FT-8010"]  # the sales meter sits on no line drawn here
    assert {"from": "H1-V-8001", "to": "H1-FT-8003", "line": '10"-FL-8001-A1'} in s["connections"]
    assert {"from": "B1-PSV-2004", "to": "H1-V-8001", "line": '4"-FL-8002-A1'} in s["connections"]  # off the sheet
    assert s["positions"]["H1-V-8001"] == [1080, 734] and '6"-FL-8003-A1' in s["lines"]


@pytest.mark.unit
def test_a_dexpi_13_file_with_tag_names_nozzles_and_chained_segments(tmp_path):
    f = tmp_path / "p.xml"
    f.write_text("""<?xml version="1.0"?>
<PlantModel xmlns="http://www.example.com/proteus">
  <PlantInformation SchemaVersion="3.6.0"/>
  <Equipment ID="E1" TagName="10-V-2001"><Nozzle ID="E1N1"/><Nozzle ID="E1N2"/></Equipment>
  <Equipment ID="E2"><GenericAttributes><GenericAttribute Name="TagNamePrefixAssignmentClass" Value="10-P-"/>
    <GenericAttribute Name="TagNameSequenceNumberAssignmentClass" Value="1001"/>
    <GenericAttribute Name="TagNameSuffixAssignmentClass" Value="A"/></GenericAttributes><Nozzle ID="E2N1"/></Equipment>
  <PipingNetworkSystem ID="S1"><GenericAttributes><GenericAttribute Name="LineNumberAssignmentClass" Value="4-P-1"/>
    </GenericAttributes>
    <PipingNetworkSegment ID="G1"><PipingComponent ID="V1" ComponentClass="GateValve"/>
      <Connection FromID="E1N2" ToID="G2"/></PipingNetworkSegment>
    <PipingNetworkSegment ID="G2"><PipingComponent ID="V2" TagName="10-XV-1002"/>
      <Connection FromID="G1" ToID="E2N1"/></PipingNetworkSegment>
  </PipingNetworkSystem>
</PlantModel>""")
    got = dexpi.read(f)
    assert got["tags"] == ["10-V-2001", "10-P-1001A", "10-XV-1002"] and got["dexpi_version"] == "3.6.0"
    # an untagged gate valve is not a hop; the end on the next segment is followed to its first tagged item
    assert got["connections"] == [{"from": "10-V-2001", "to": "10-XV-1002", "line": "4-P-1"},
                                  {"from": "10-XV-1002", "to": "10-P-1001A", "line": "4-P-1"}]


@pytest.mark.unit
def test_dexpi_2_reads_tags_and_anything_else_is_refused(tmp_path):
    f = tmp_path / "v2.xml"
    f.write_text('<Model><Object type="Equipment/Tank"><Data property="TagName"><String>T-101</String></Data></Object></Model>')
    got = dexpi.read(f)
    assert got["tags"] == ["T-101"] and got["dexpi_version"] == "2.x" and "tags only" in got["note"]
    (tmp_path / "x.xml").write_text("<Drawing/>")
    with pytest.raises(ValueError, match="not a DEXPI file"):
        dexpi.read(tmp_path / "x.xml")
