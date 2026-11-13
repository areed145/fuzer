"""LAS depth logs (#901): a LAS 2.0 file's curves read by depth, in metres or feet, with null values left out."""

import pytest

from fuzer.sources import las

pytestmark = pytest.mark.unit

LOG = """~VERSION INFORMATION
 VERS.                  2.0 :   CWLS LOG ASCII STANDARD -VERSION 2.0
 WRAP.                  NO  :   ONE LINE PER DEPTH STEP
~WELL INFORMATION
 STRT.FT             1000.0 :
 NULL.              -999.25 :
 WELL.      X-1 : WELL
 DATE.      2023-06-20 12:30 : LOG DATE, with a colon in the data
# a comment line
~CURVE INFORMATION
 DEPT.{unit}                    :   DEPTH
 GR  .GAPI                  :   GAMMA RAY
 RT  .OHMM                  :   DEEP RESISTIVITY
~A  DEPTH     GR      RT
1000.0   45.2   12.1
1000.5  -999.25 13.0
1001.0   80.0
  2.5
"""


def write(tmp_path, text, name="x.las"):
    (tmp_path / name).write_text(text)
    return tmp_path / name


def test_curves_read_by_depth_in_metres_with_nulls_left_out(tmp_path):
    log = las.read(write(tmp_path, LOG.format(unit="FT")))
    assert log["version"] == "2.0" and log["well"]["WELL"] == "X-1" and log["well"]["DATE"] == "2023-06-20 12:30"
    assert [c["mnemonic"] for c in log["curves"]] == ["DEPT", "GR", "RT"] and log["curves"][1]["unit"] == "GAPI"
    assert log["rows"] == [{"md_m": 304.8, "GR": 45.2, "RT": 12.1}, {"md_m": 304.952, "GR": None, "RT": 13.0},
                           {"md_m": 305.105, "GR": 80.0, "RT": 2.5}]  # a row may run over lines when unwrapped
    assert las.read(write(tmp_path, LOG.format(unit="M")))["rows"][0]["md_m"] == 1000.0


def test_an_unknown_depth_unit_is_named(tmp_path):
    with pytest.raises(las.LasError, match="depth in 'FURLONG'"):
        las.read(write(tmp_path, LOG.format(unit="FURLONG")))


def test_wrapped_and_other_versions_are_refused_not_guessed(tmp_path):
    with pytest.raises(las.LasError, match="WRAP YES"):
        las.read(write(tmp_path, LOG.format(unit="FT").replace("NO  :", "YES :")))
    with pytest.raises(las.LasError, match="LAS version 3.0"):
        las.read(write(tmp_path, LOG.format(unit="FT").replace("2.0 :", "3.0 :")))
    with pytest.raises(las.LasError, match="don't divide into rows of 3"):
        las.read(write(tmp_path, LOG.format(unit="FT") + "1001.5 1.0\n"))
    with pytest.raises(las.LasError, match="not a depth"):
        las.read(write(tmp_path, LOG.format(unit="FT").replace(" DEPT.", " TIME.")))


def test_kestrels_lwd_log_reads():
    from fuzer.resources import BUNDLES

    log = las.read(BUNDLES / "kestrel" / "exports" / "well_logs" / "KR12-03H_lwd.las")
    assert log["well"]["WELL"] == "KR 12-03H" and len(log["rows"]) == 2001
    assert log["rows"][0]["RT"] is None and log["rows"][-1]["md_m"] == 4600.0  # the resistivity tool started late
