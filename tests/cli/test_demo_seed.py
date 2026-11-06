import pytest

from fuzer.cli import demo_seed
from fuzer.core import db


def test_seed_requires_an_empty_database(tmp_path):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    with db.connect() as conn:
        db.reset(conn)
        with pytest.raises(RuntimeError, match="dedicated empty database"):
            demo_seed.seed(conn, tmp_path)
        assert conn.execute("select to_regclass('public.source_record') as t").fetchone()["t"]


def test_seed_retries_interrupted_load_and_skips_after_success(tmp_path, monkeypatch):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    calls = []

    def ingest(conn, folder):
        calls.append("ingest")
        if len(calls) == 1:
            raise RuntimeError("interrupted")

    monkeypatch.setattr(demo_seed.catalog, "seed", lambda conn: None)
    monkeypatch.setattr(demo_seed.ingest, "ingest", ingest)
    monkeypatch.setattr(demo_seed.pipeline, "rebuild", lambda conn, pack: calls.append("rebuild"))
    with db.connect() as conn:
        conn.execute("drop schema public cascade; create schema public")
        conn.commit()
        with pytest.raises(RuntimeError, match="interrupted"):
            demo_seed.seed(conn, tmp_path)
        demo_seed.seed(conn, tmp_path)
        demo_seed.seed(conn, tmp_path)
        assert calls == ["ingest", "ingest", "rebuild"]
        assert conn.execute("select complete from fuzer_demo_bootstrap").fetchone()["complete"]


def test_seed_loads_okan(tmp_path, monkeypatch):
    from fakeplant import okan

    okan.generate(tmp_path)
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    with db.connect() as conn:
        conn.execute("drop schema public cascade; create schema public")
        conn.commit()
        demo_seed.seed(conn, tmp_path)
        assert conn.execute("select count(*) as n from source_record").fetchone()["n"] > 0
        assert conn.execute("select count(*) as n from entity").fetchone()["n"] > 0
        assert demo_seed.load_pack("okan").facility == "OKAN"
        assert conn.execute("select complete from fuzer_demo_bootstrap").fetchone()["complete"]
