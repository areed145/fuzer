"""Saved views per role on All work (P5.1c, #651)."""

import os
import tempfile

import pytest

from fuzer.core import db
from fuzer.workflow import engine


@pytest.fixture(scope="module")
def conn():
    os.environ.pop("FUZER_DATABASE_URL", None)
    os.environ.setdefault("FUZER_PGDATA", tempfile.mkdtemp(prefix="fzpg"))
    with db.connect() as c:
        db.reset(c)
        engine._parsed.clear()
        engine.load_defaults(c)
        for name, roles in (("ines", ["ops_lead"]), ("olu", ["ops_lead"]), ("fred", ["operator"])):
            engine.set_person(c, name, roles)
        c.commit()
        yield c


def test_a_view_saved_for_a_role_is_seen_by_everyone_in_it_and_only_them(conn):
    v = engine.save_view(conn, "ines", "ops_lead", "Upsets back from the field", "back",
                         {"type": "upset", "who": "", "junk": "x"})
    assert v["filters"] == {"type": "upset"}  # only All work's own filters, empty ones dropped
    conn.commit()
    assert [x["name"] for x in engine.views(conn, "olu")] == ["Upsets back from the field"]
    assert engine.views(conn, "fred") == []
    again = engine.save_view(conn, "olu", "ops_lead", "Upsets back from the field", "open", {"type": "upset"})
    assert again["id"] == v["id"] and again["list"] == "open"  # the same name in the role updates the view
    with pytest.raises(PermissionError):
        engine.save_view(conn, "fred", "ops_lead", "Mine", "open")
    with pytest.raises(ValueError):
        engine.save_view(conn, "ines", "ops_lead", "Bad", "nowhere")
    with pytest.raises(PermissionError):
        engine.remove_view(conn, "fred", v["id"])
    engine.remove_view(conn, "olu", v["id"])
    assert engine.views(conn, "ines") == []
    conn.commit()
