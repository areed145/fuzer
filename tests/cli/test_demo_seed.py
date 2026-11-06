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
        return {"records": 1, "inserted": 0, "updated": 0, "removed": 0, "held": [], "paused": [], "missing": []}

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
        assert calls == ["ingest", "ingest", "rebuild", "ingest"]  # later starts look, and unchanged data doesn't rebuild
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


def _built(conn):
    conn.execute("create table if not exists fuzer_demo_bootstrap (id integer primary key, complete boolean not null)")
    conn.execute("insert into fuzer_demo_bootstrap values (1, true) on conflict do nothing")
    conn.commit()


def _exports(folder):
    (folder / "process_model.json").write_text("{}")
    (folder / "sap_floc.csv").write_text("")


def _ingested(**changes):
    return {"records": 1, "inserted": 0, "updated": 0, "removed": 0, "held": [], "paused": [], "missing": [], **changes}


@pytest.mark.parametrize("seeded, stats, rebuilds", [
    ({}, _ingested(), 0),
    ({}, _ingested(updated=2), 1),
    ({"datasets": ["capacity_statement"]}, _ingested(), 1),
])
def test_a_built_register_takes_new_mappings_and_changed_exports(tmp_path, monkeypatch, capsys, seeded, stats, rebuilds):
    calls = []
    monkeypatch.setattr(demo_seed.catalog, "seed", lambda conn: seeded)
    monkeypatch.setattr(demo_seed.ingest, "ingest", lambda conn, folder: stats)
    monkeypatch.setattr(demo_seed.pipeline, "rebuild", lambda conn, pack: calls.append(pack) or {"entities": 5})
    _exports(tmp_path)
    with db.connect() as conn:
        db.reset(conn)
        db.init_schema(conn)
        _built(conn)
        demo_seed.seed(conn, tmp_path)
    assert len(calls) == rebuilds
    assert ("Data refreshed" if rebuilds else "Data unchanged") in capsys.readouterr().out


def test_a_held_load_is_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo_seed.catalog, "seed", lambda conn: {})
    monkeypatch.setattr(demo_seed.ingest, "ingest", lambda conn, folder: _ingested(held=["ec_day001"]))
    _exports(tmp_path)
    with db.connect() as conn:
        db.reset(conn)
        db.init_schema(conn)
        _built(conn)
        demo_seed.seed(conn, tmp_path)
    assert "Data refresh held: ec_day001" in capsys.readouterr().out


def test_a_failed_refresh_reports_once_and_startup_carries_on(tmp_path, monkeypatch, capsys):
    attempts = []

    def fail(conn, folder):
        attempts.append(folder)
        raise RuntimeError("bad export")

    monkeypatch.setattr(demo_seed.catalog, "seed", lambda conn: {})
    monkeypatch.setattr(demo_seed.ingest, "ingest", fail)
    _exports(tmp_path)
    with db.connect() as conn:
        db.reset(conn)
        db.init_schema(conn)
        _built(conn)
        demo_seed.seed(conn, tmp_path)  # does not raise
    assert len(attempts) == 1
    assert "Data refresh failed" in capsys.readouterr().out
