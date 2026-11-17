"""A page opened before a deploy can still load the built files it names (P5.18a, #1171)."""

import os
import time

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from fuzer.core import kept_assets


def _build(path, *names):
    path.mkdir(parents=True, exist_ok=True)
    for n in names:
        (path / n).write_text(f"// {n}")
    return path


def test_a_file_from_the_last_deploy_is_still_served_after_the_next(tmp_path):
    kept = tmp_path / "kept"
    old = _build(tmp_path / "old", "index-AAAAAAAA.js", "ganttDiagram-R7GPNTJY-CF2q9BH-.js")
    assert kept_assets.keep(old, kept)["copied"] == 2
    new = _build(tmp_path / "new", "index-BBBBBBBB.js", "ganttDiagram-R7GPNTJY-BsA0yh8I.js")
    kept_assets.keep(new, kept)

    app = FastAPI()
    assets = StaticFiles(directory=new)
    assets.all_directories.append(kept)
    app.mount("/assets", assets)
    with TestClient(app) as client:
        assert client.get("/assets/ganttDiagram-R7GPNTJY-BsA0yh8I.js").text.endswith("BsA0yh8I.js")  # current build
        assert client.get("/assets/ganttDiagram-R7GPNTJY-CF2q9BH-.js").status_code == 200  # the page opened before
        assert client.get("/assets/ganttDiagram-R7GPNTJY-ZZZZZZZZ.js").status_code == 404


def test_files_no_build_has_named_for_two_weeks_are_dropped(tmp_path):
    kept = tmp_path / "kept"
    kept_assets.keep(_build(tmp_path / "a", "old-AAAAAAAA.js", "shared-CCCCCCCC.css"), kept)
    month_ago = time.time() - 30 * 86400
    for f in kept.iterdir():
        os.utime(f, (month_ago, month_ago))
    (kept / ".half-written.js.part").write_text("")
    out = kept_assets.keep(_build(tmp_path / "b", "new-BBBBBBBB.js", "shared-CCCCCCCC.css"), kept)
    assert sorted(f.name for f in kept.iterdir()) == ["new-BBBBBBBB.js", "shared-CCCCCCCC.css"]
    assert out["dropped"] == 2
    assert time.time() - (kept / "shared-CCCCCCCC.css").stat().st_mtime < 60, "a file the build still names is current"


def test_no_kept_folder_unless_it_is_configured_and_present(tmp_path, monkeypatch):
    monkeypatch.delenv(kept_assets.ENV, raising=False)
    assert kept_assets.folder() is None
    monkeypatch.setenv(kept_assets.ENV, str(tmp_path / "missing"))
    assert kept_assets.folder() is None
    monkeypatch.setenv(kept_assets.ENV, str(tmp_path))
    assert kept_assets.folder() == tmp_path
