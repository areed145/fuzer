import pytest

from fuzer.cli import demo_seed
from fuzer.core import db


def test_seed_refuses_other_asset(tmp_path):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    with db.connect() as conn:
        db.reset(conn)
        conn.execute("insert into source_record (source,record_type,source_key,payload) "
                     "values ('sap','floc','OTHER','{\"parent\":\"\"}')")
        conn.commit()
        with pytest.raises(RuntimeError, match="another asset"):
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
        return {"records": 1}

    monkeypatch.setattr(demo_seed.catalog, "seed", lambda conn: None)
    monkeypatch.setattr(demo_seed.ingest, "ingest", ingest)
    def rebuild(conn, pack):
        calls.append("rebuild")
        return {"entities": 1}

    monkeypatch.setattr(demo_seed.pipeline, "rebuild", rebuild)
    with db.connect() as conn:
        db.reset(conn)  # the existing Render service has already initialized the schema
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
        counts = conn.execute("select count(*) as n from source_record").fetchone()
        demo_seed.seed(conn, tmp_path)
        assert conn.execute("select count(*) as n from source_record").fetchone() == counts
        conn.execute("drop table fuzer_demo_bootstrap")
        conn.commit()
        monkeypatch.setattr(demo_seed.ingest, "ingest", lambda *args: pytest.fail("existing Okan must not be reloaded"))
        demo_seed.seed(conn, tmp_path)
        assert conn.execute("select count(*) as n from source_record").fetchone() == counts


def test_seed_refuses_unidentified_existing_data(tmp_path):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    with db.connect() as conn:
        db.reset(conn)
        conn.execute("insert into source_record (source,record_type,source_key,payload) "
                     "values ('sap','equipment','CUSTOM','{}')")
        conn.commit()
        with pytest.raises(RuntimeError, match="existing data was left unchanged"):
            demo_seed.seed(conn, tmp_path)
        assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == 1


def test_seed_rejects_empty_ingest(tmp_path, monkeypatch):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    monkeypatch.setattr(demo_seed.ingest, "ingest", lambda *args: {"records": 0})
    with db.connect() as conn:
        db.reset(conn)
        with pytest.raises(RuntimeError, match="zero records"):
            demo_seed.seed(conn, tmp_path)
        assert not conn.execute("select complete from fuzer_demo_bootstrap").fetchone()["complete"]
