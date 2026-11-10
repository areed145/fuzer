"""A fresh `fuzer up` is ready for the IOC (#453): process work is waiting without running scans by hand, and a
command in another shell serves the project `fuzer up` serves without FUZER_PROJECT."""

import json
import os

from fuzer import cli
from fuzer.cli import up
from fuzer.core import db, projects
from fuzer.core.pack import load_pack
from fuzer.ops import pipeline

PACK = load_pack("okan")


def test_first_scan_runs_once_and_hands_upsets_to_the_ioc(okan_db):
    from fuzer.workflow import engine

    conn = okan_db.conn
    engine.load_defaults(conn)
    assert conn.execute("select count(*) as n from sre_run").fetchone()["n"] == 0
    out = pipeline.first_scan(conn, PACK, days=2)  # `fuzer up` scans 7 days in the background; 2 keeps this quick
    assert out is not None
    assert out["learned"]["ranges"] > 0  # normal ranges learned, as `limits learn` does
    assert out["sre"]["windows"] == 8 and out["sre"]["upsets"] > 0
    assert out["handoff"]["diagnosed"] == 0  # no model call on start
    handed = conn.execute("select count(*) as n from exception_record where type = 'upset'").fetchone()["n"]
    assert handed == out["handoff"]["handed_off"]
    assert pipeline.first_scan(conn, PACK) is None  # a second start leaves it to the scheduled job


def test_up_runs_the_first_scan_in_the_background_and_says_what_it_found(okan_db, monkeypatch, capsys):
    calls = []

    def fake(conn, pack, days):
        calls.append(days)
        return {"limits": {"excursions": 3, "iow_breaches": 1}, "sre": {"upsets": 2}, "handoff": {"handed_off": 2}}

    monkeypatch.setattr(pipeline, "first_scan", fake)
    monkeypatch.setattr("fuzer.ops.sre.watermark", lambda conn: None)
    thread = up.start_first_scan(PACK)
    thread.join(timeout=30)
    out = capsys.readouterr().out
    assert calls == [7] and "in the background" in out
    assert "2 upset(s) found in the last 7 days, 2 handed to the IOC; 1 IOW breach(es)" in out

    def broken(conn, pack, days):
        raise RuntimeError("historian unreachable")

    monkeypatch.setattr(pipeline, "first_scan", broken)
    up.first_scan(PACK)  # the UI is already up
    assert "First process scan stopped (historian unreachable)" in capsys.readouterr().out

    monkeypatch.setattr("fuzer.ops.sre.watermark", lambda conn: "2025-12-30")
    assert up.start_first_scan(PACK) is None  # scanned before: nothing to start


def test_up_stores_the_demo_project_so_other_commands_serve_it(okan_db, monkeypatch, capsys):
    conn = okan_db.conn
    conn.execute("delete from project")  # a database `fuzer up` loaded before it stored its project
    conn.commit()
    assert projects.active(conn) is None
    monkeypatch.setenv("FUZER_PROJECT", "okan")
    up.remember_project(conn)
    assert projects.active(conn) == "okan" and projects.config(conn, "okan")["source"] == "database"

    monkeypatch.delenv("FUZER_PROJECT")  # another shell, with nothing set
    # the command makes okan this process's project (its scope and variables): put them back after, or later tests
    # in this worker serve okan whatever database they open
    monkeypatch.setattr(db, "_default", db._default)
    for var in projects.FIELDS.values():
        if var in os.environ:
            monkeypatch.setenv(var, os.environ[var])
        else:
            monkeypatch.setenv(var, "")
            monkeypatch.delenv(var)
    cli.main(["projects", "show"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["name"] == "okan" and shown["pack"] == "okan"


def test_a_stored_project_is_never_replaced(okan_db, monkeypatch):
    conn = okan_db.conn
    projects.save(conn, "okan", {"pack": "okan", "note": "from a bundle"}, "tests")
    monkeypatch.setenv("FUZER_PROJECT", "okan")
    up.remember_project(conn)
    assert projects.config(conn, "okan").get("note") == "from a bundle"
