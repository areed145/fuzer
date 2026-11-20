"""A page opened before a deploy can still load the built files it names (P5.18a, #1171). The files are kept in
Postgres, which a new instance shares with the old one on a zero-downtime deploy."""

import gzip

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fuzer.core import db, kept_assets


def _build(path, *names):
    path.mkdir(parents=True, exist_ok=True)
    for n in names:
        (path / n).write_text(f"// {n}")
    return path


@pytest.fixture
def conn():
    with db.connect() as c:
        c.execute(f"drop table if exists {kept_assets.TABLE}")
        c.commit()
        yield c
        c.rollback()
        c.execute(f"drop table if exists {kept_assets.TABLE}")
        c.commit()


def test_a_file_from_the_last_deploy_is_still_served_after_the_next(tmp_path, conn, monkeypatch):
    old = _build(tmp_path / "old", "index-AAAAAAAA.js", "ganttDiagram-R7GPNTJY-CF2q9BH-.js")
    assert kept_assets.keep(conn, old)["copied"] == 2
    new = _build(tmp_path / "new", "index-BBBBBBBB.js", "ganttDiagram-R7GPNTJY-BsA0yh8I.js")
    assert kept_assets.keep(conn, new) == {"copied": 2, "kept": 4, "dropped": 0}
    assert gzip.decompress(kept_assets.fetch(conn, "index-AAAAAAAA.js")) == b"// index-AAAAAAAA.js"

    app = FastAPI()
    app.mount("/assets", kept_assets.Kept(directory=new))
    monkeypatch.setenv(kept_assets.ENV, "1")
    with TestClient(app) as client:
        assert client.get("/assets/ganttDiagram-R7GPNTJY-BsA0yh8I.js").text.endswith("BsA0yh8I.js")  # current build
        before = client.get("/assets/ganttDiagram-R7GPNTJY-CF2q9BH-.js")  # the page opened before
        assert before.status_code == 200 and before.text == "// ganttDiagram-R7GPNTJY-CF2q9BH-.js"
        assert before.headers["content-type"].startswith("text/javascript")
        assert "immutable" in before.headers["cache-control"]
        plain = client.get("/assets/index-AAAAAAAA.js", headers={"accept-encoding": "identity"})
        assert plain.text == "// index-AAAAAAAA.js" and "content-encoding" not in plain.headers
        assert client.get("/assets/ganttDiagram-R7GPNTJY-ZZZZZZZZ.js").status_code == 404
        monkeypatch.delenv(kept_assets.ENV)
        assert client.get("/assets/index-AAAAAAAA.js").status_code == 404  # a deployment that keeps nothing


def test_files_no_build_has_named_for_a_week_are_dropped(tmp_path, conn):
    kept_assets.keep(conn, _build(tmp_path / "a", "old-AAAAAAAA.js", "shared-CCCCCCCC.css"))
    conn.execute(f"update {kept_assets.TABLE} set last_named = now() - interval '30 days'")
    conn.commit()
    out = kept_assets.keep(conn, _build(tmp_path / "b", "new-BBBBBBBB.js", "shared-CCCCCCCC.css"))
    names = [r["name"] for r in conn.execute(f"select name from {kept_assets.TABLE} order by name").fetchall()]
    assert names == ["new-BBBBBBBB.js", "shared-CCCCCCCC.css"]
    assert out == {"copied": 1, "kept": 2, "dropped": 1}
    fresh = conn.execute(f"select now() - last_named < interval '1 minute' as fresh from {kept_assets.TABLE} "
                         "where name = 'shared-CCCCCCCC.css'").fetchone()["fresh"]
    assert fresh, "a file the build still names is current"
