"""Home is my shift (P5.1a, #649): what others did on a person's work since they last looked."""

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
        for name, roles in (("ines", ["ops_lead"]), ("fred", ["operator"]), ("pat", ["maintenance_planner"])):
            engine.set_person(c, name, roles)
        c.commit()
        yield c


def test_home_says_what_others_did_on_my_work_since_i_last_looked(conn):
    mine = engine.create_item(conn, "manual", "Pump P-101 noisy", by="pat", role="ops_lead")["id"]
    other = engine.create_item(conn, "manual", "Nothing to do with ines", by="pat", role="maintenance_planner")["id"]
    engine.comment(conn, mine, "ines", "my own note")  # her own doing isn't news to her
    conn.commit()
    first = engine.home(conn, "ines", mark=True)
    assert first["seen_at"] is None
    assert {(c["item_id"], c["kind"]) for c in first["changes"]} == {(mine, "created")}
    conn.commit()
    assert engine.home(conn, "ines")["changes"] == []  # nothing since she looked
    engine.comment(conn, mine, "fred", "gland weeping")
    engine.comment(conn, other, "fred", "not hers")
    conn.commit()
    later = engine.home(conn, "ines")
    assert later["seen_at"] and [(c["item_id"], c["kind"], c["data"]["text"]) for c in later["changes"]] == [
        (mine, "commented", "gland weeping")]
    conn.commit()


def test_a_watched_asset_and_case_show_under_watching_and_are_not_assigned(conn):
    engine.mark_seen(conn, "fred")
    conn.commit()
    engine.watch(conn, "fred", "asset:P-200")
    case = engine.create_item(conn, "manual", "Watched by number", by="pat", role="maintenance_planner")["id"]
    engine.watch(conn, "fred", f"case:{case}")
    about = engine.create_item(conn, "manual", "P-200 seal", by="pat", role="maintenance_planner",
                               subjects=["asset:P-200"])["id"]
    engine.create_item(conn, "manual", "Some other pump", by="pat", role="maintenance_planner", subjects=["asset:P-9"])
    engine.comment(conn, case, "pat", "parts ordered")
    conn.commit()
    out = engine.home(conn, "fred")
    assert out["watching"] == ["asset:P-200", f"case:{case}"]
    seen = {(c["item_id"], c["kind"]) for c in out["changes"] if c["watching"]}
    assert seen == {(about, "created"), (case, "created"), (case, "commented")}
    item = conn.execute("select assignee from work_item where id = %s", (about,)).fetchone()
    assert item["assignee"] is None  # watching is not holding
    engine.watch(conn, "fred", "asset:P-200", on=False)
    assert engine.watching(conn, "fred") == [f"case:{case}"]
    with pytest.raises(ValueError):
        engine.watch(conn, "fred", " ")
    conn.commit()
