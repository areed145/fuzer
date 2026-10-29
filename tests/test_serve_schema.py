"""The API brings an older database up to date when it starts."""

import os
import tempfile

from fastapi.testclient import TestClient

from fuzer import db


def test_api_start_creates_missing_tables():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        c.execute("drop table register_summary")
        c.commit()
    from fuzer import api

    with TestClient(api.app) as client:  # runs startup
        assert client.get("/ontology").status_code == 200
