"""What keeps pages quick under a shift's worth of users (#1069): shipped YAML parsed once per version, hashed assets
cached for good, and the 3D status read once a minute for everyone."""

import os

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from fuzer.api import graph
from fuzer.api.security import RequestBounds
from fuzer.core import yamlfiles


def test_shipped_yaml_is_parsed_once_per_version_and_each_caller_gets_its_own_copy(tmp_path):
    f = tmp_path / "pack.yaml"
    f.write_text("briefs: [machine_health]\n")
    first = yamlfiles.load(f)
    first["briefs"].append("changed by a caller")
    assert yamlfiles.load(f) == {"briefs": ["machine_health"]}  # a caller's change never reaches the next
    f.write_text("briefs: [machine_health, upset]\n")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # an edit in the same clock tick still counts
    assert yamlfiles.load(f) == {"briefs": ["machine_health", "upset"]}


def test_hashed_assets_are_cached_for_good_and_everything_else_is_not():
    app = FastAPI()
    for path in ("/assets/index-D3Rgv03k.js", "/assets/apple-touch-icon.png", "/stats"):
        app.add_api_route(path, lambda: PlainTextResponse("x"))
    app.add_middleware(RequestBounds)
    client = TestClient(app)
    assert client.get("/assets/index-D3Rgv03k.js").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "cache-control" not in client.get("/assets/apple-touch-icon.png").headers  # same name in every build
    assert client.get("/stats").headers["cache-control"] == "no-store"


def test_one_read_of_a_scenes_status_serves_the_next_minute(monkeypatch):
    from fuzer.graph import scene as scenes
    from fuzer.ops import status_now

    reads = []
    monkeypatch.setattr(scenes, "scene", lambda conn, at, rels, pack, *a: {"items": [{"id": "fz-1"}]})
    monkeypatch.setattr(status_now, "status", lambda conn, ids: reads.append(ids) or {"status": {}, "totals": {}})
    monkeypatch.setattr(graph.deps, "pack", lambda: None)
    monkeypatch.setattr(graph.db, "database_url", lambda: "postgresql:///test")
    clock = [1000.0]
    monkeypatch.setattr(graph.time, "monotonic", lambda: clock[0])
    graph._scene_status.clear()
    first = graph.scene_status(at="fz-area", conn=None)
    assert graph.scene_status(at="fz-area", conn=None) == first and len(reads) == 1
    graph.scene_status(at="fz-other", conn=None)  # another scene is its own read
    assert len(reads) == 2
    clock[0] += graph.SCENE_STATUS_SECONDS
    graph.scene_status(at="fz-area", conn=None)  # a minute on, it is read again
    assert len(reads) == 3 and "as_of" in first
