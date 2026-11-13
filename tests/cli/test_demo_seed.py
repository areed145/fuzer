import pytest

from fuzer.cli import demo_seed
from fuzer.core import db


def _bundle(exports, name="okan"):
    """A seed bundle for the shipped Okan pack whose exports are the test's folder."""
    folder = exports / "bundle"
    folder.mkdir(exist_ok=True)
    (folder / "bundle.yaml").write_text(f"bundle: 1\nname: {name}\npack: {demo_seed.BUNDLES / 'okan' / 'pack.yaml'}\n"
                                        f"project:\n  data: {exports}\n")
    return folder


def test_seed_refuses_other_asset(tmp_path):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    with db.connect() as conn:
        db.reset(conn)
        conn.execute("insert into source_record (source,record_type,source_key,payload) "
                     "values ('sap','floc','OTHER','{\"parent\":\"\"}')")
        conn.commit()
        with pytest.raises(RuntimeError, match="another asset"):
            demo_seed.seed(conn, _bundle(tmp_path))
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
            demo_seed.seed(conn, _bundle(tmp_path))
        demo_seed.seed(conn, _bundle(tmp_path))
        demo_seed.seed(conn, _bundle(tmp_path))
        assert calls == ["ingest", "ingest", "rebuild", "ingest"]  # later starts look, and unchanged data doesn't rebuild
        assert conn.execute("select complete from fuzer_demo_bootstrap").fetchone()["complete"]


def test_seed_loads_okan(tmp_path, monkeypatch):
    from fakeplant import okan

    okan.generate(tmp_path)
    monkeypatch.setenv("FUZER_DATA_DIR", str(tmp_path))
    with db.connect() as conn:
        conn.execute("drop schema public cascade; create schema public")
        conn.commit()
        demo_seed.seed(conn, _bundle(tmp_path))
        assert conn.execute("select count(*) as n from source_record").fetchone()["n"] > 0
        assert conn.execute("select count(*) as n from entity").fetchone()["n"] > 0
        assert demo_seed.projects.active(conn) == "okan"  # the bundle is the database's first project
        assert conn.execute("select complete from fuzer_demo_bootstrap").fetchone()["complete"]
        counts = conn.execute("select count(*) as n from source_record").fetchone()
        demo_seed.seed(conn, _bundle(tmp_path))
        assert conn.execute("select count(*) as n from source_record").fetchone() == counts
        conn.execute("drop table fuzer_demo_bootstrap")
        conn.commit()
        demo_seed.seed(conn, _bundle(tmp_path))
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
            demo_seed.seed(conn, _bundle(tmp_path))
        assert conn.execute("select count(*) as n from source_record").fetchone()["n"] == 1


def test_seed_rejects_empty_ingest(tmp_path, monkeypatch):
    (tmp_path / "process_model.json").touch()
    (tmp_path / "sap_floc.csv").touch()
    monkeypatch.setattr(demo_seed.ingest, "ingest", lambda *args: {"records": 0})
    with db.connect() as conn:
        db.reset(conn)
        with pytest.raises(RuntimeError, match="zero records"):
            demo_seed.seed(conn, _bundle(tmp_path))
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
        demo_seed.seed(conn, _bundle(tmp_path))
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
        demo_seed.seed(conn, _bundle(tmp_path))
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
        demo_seed.seed(conn, _bundle(tmp_path))  # does not raise
    assert len(attempts) == 1
    assert "Data refresh failed" in capsys.readouterr().out


def test_deploy_registers_okan_first_and_adds_each_other_bundle(tmp_path, capsys):
    """Multi-project phase 4: on deploy, Okan becomes the database's first project and every other seed bundle
    (Kestrel) is added beside it in its own schema, with its exports loaded and rebuilt there."""
    import shutil

    from fuzer.core import projects

    bundles = tmp_path / "bundles"
    shutil.copytree(demo_seed.BUNDLES / "kestrel", bundles / "kestrel")
    (bundles / "okan").mkdir()
    (bundles / "okan" / "bundle.yaml").write_text(
        f"bundle: 1\nname: okan\npack: {demo_seed.BUNDLES / 'okan' / 'pack.yaml'}\nproject:\n  data: {tmp_path / 'okan'}\n")
    (bundles / "broken").mkdir()
    (bundles / "broken" / "bundle.yaml").write_text("bundle: 1\nname: broken\n")  # no pack: reported, skipped
    with db.connect() as conn:
        db.reset(conn)
        demo_seed.bundles.init(conn, bundles / "okan", by="deploy")  # what seed() does first
        assert demo_seed.register_projects(conn, "okan", bundles) == ["kestrel"]
        assert projects.active(conn) == "okan" and projects.stored(conn) == ["kestrel", "okan"]
        assert conn.execute("select count(*) as n from proj_kestrel.entity").fetchone()["n"] >= 34
        assert conn.execute("select count(*) as n from entity").fetchone()["n"] == 0  # nothing in Okan's
        # Kestrel's open notifications reach its IOC queue at deploy, not at the next scheduled register run.
        assert conn.execute("select count(*) as n from proj_kestrel.exception_record "
                            "where type = 'sap_notification' and state = 'open'").fetchone()["n"] == 4
        assert demo_seed.register_projects(conn, "okan", bundles) == ["kestrel"]  # a redeploy changes nothing
    out = capsys.readouterr().out
    assert "Project broken isn't registered" in out and "Project kestrel: unchanged" in out
    with db.connect() as conn:
        db.reset(conn)
        assert demo_seed.register_projects(conn, "okan", tmp_path / "none") == []  # no first project: none added


def test_deploy_registers_every_shipped_bundle(tmp_path, capsys):
    """Gate C follow-up (#962): the bundles the image ships, as they are, each register beside Okan at deploy with
    their exports loaded, so a bundle that would fail on the next deploy fails here first."""
    from fuzer.core import projects

    shipped = sorted(p.name for p in demo_seed.BUNDLES.iterdir() if (p / "bundle.yaml").is_file() and p.name != "okan")
    assert {"kestrel", "harrier"} <= set(shipped), shipped
    okan = tmp_path / "okan"  # Okan's own exports are generated at build time; its seed is not what this checks
    okan.mkdir()
    (okan / "bundle.yaml").write_text(
        f"bundle: 1\nname: okan\npack: {demo_seed.BUNDLES / 'okan' / 'pack.yaml'}\nproject:\n  data: {tmp_path / 'data'}\n")
    with db.connect() as conn:
        db.reset(conn)
        demo_seed.bundles.init(conn, okan, by="deploy")
        assert demo_seed.register_projects(conn, "okan") == shipped, capsys.readouterr().out
        assert projects.stored(conn) == sorted([*shipped, "okan"])
        for name in shipped:
            n = conn.execute(f"select count(*) as n from proj_{name}.entity").fetchone()["n"]
            assert n > 0, f"{name} registered with an empty register"
    assert "isn't registered" not in capsys.readouterr().out
