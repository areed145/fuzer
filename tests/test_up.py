"""`fuzer up` steps (without serving): fake plant when there's no data, load, quick second start."""

import os
import tempfile
from pathlib import Path

from fuzer import db, up
from fuzer.pack import load_pack

PACK = load_pack()


def test_up_makes_data_loads_and_skips_unchanged(monkeypatch, capsys):
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    folder = Path(tempfile.mkdtemp(prefix="fzup")) / "plant"
    monkeypatch.setenv("FUZER_DATA_DIR", str(folder))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert up.data_folder(fresh=False) == folder and (folder / "sap_floc.csv").exists()
    with db.connect() as conn:
        db.reset(conn)
        up.load(conn, PACK, folder, fresh=False)
        assert conn.execute("select count(*) as n from entity").fetchone()["n"] > 100
        up.read_drawings(conn, PACK)
        out = capsys.readouterr().out
        assert "Building the register" in out and "add ANTHROPIC_API_KEY" in out

        up.load(conn, PACK, folder, fresh=False)  # nothing changed: no rebuild
        out = capsys.readouterr().out
        assert "0 changed" in out and "Building the register" not in out


def test_up_without_data_or_fake_plant_says_what_to_do(monkeypatch):
    monkeypatch.setenv("FUZER_DATA_DIR", tempfile.mkdtemp(prefix="fzempty"))
    monkeypatch.setattr(up, "_fake_plant", lambda: None)
    try:
        up.data_folder(fresh=False)
    except SystemExit as e:
        assert "No source exports found" in str(e)
    else:
        raise AssertionError("expected SystemExit")


def test_demo_picks_pack_and_folder(monkeypatch):
    for k in ("FUZER_PACK", "FUZER_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    pack = up.use_demo("okan")
    assert pack.facility == "OKAN" and os.environ["FUZER_PACK"] == "okan"
    assert os.environ["FUZER_DATA_DIR"].endswith("fakeplant/data/okan")


def test_refuses_to_mix_assets():
    with db.connect() as conn:
        db.reset(conn)
        conn.execute("insert into source_record (source, record_type, source_key, payload) "
                     "values ('sap', 'floc', 'OSB', '{\"parent\": \"\"}')")
        conn.commit()
        assert up.other_asset(conn, load_pack("okan")) == "OSB"
        assert up.other_asset(conn, load_pack("offshore_demo")) is None
