"""Across assets (P7.5b): the plant scorecard for every project a person may open, read in each project's own register;
the projects they aren't granted are named as left out, never read (D6)."""

import uuid

import pytest

from fuzer.core import access, db, projects
from fuzer.ops import fleet


@pytest.fixture
def ridge(okan_db):
    """A second field beside Okan, in its own schema, with nothing loaded yet."""
    with db.connect() as conn:
        db.create_project_schema(conn, "proj_ridge")
        projects.save(conn, "ridge", {"schema": "proj_ridge", "pack": "okan"}, "test")
        conn.commit()
    yield "ridge"
    with db.connect() as conn:
        conn.execute("delete from project where name = 'ridge'")
        conn.execute("drop schema if exists proj_ridge cascade")
        conn.commit()


def _person(conn, roles=("reader",)):
    who, _ = access.sign_in(conn, {"tid": str(uuid.UUID(int=1)), "oid": str(uuid.uuid4()), "name": "Asset manager"})
    conn.execute("update access_account set enabled = true where id = %s", (who.id,))
    for r in roles:
        conn.execute("insert into access_membership (account_id, role) values (%s, %s)", (who.id, r))
    conn.commit()
    return access.principal(conn, who.id)


def test_a_fleet_view_reads_only_granted_projects_and_names_the_rest(okan_db, ridge):
    with db.connect() as conn:
        access.seed(conn)
        conn.commit()
        me = _person(conn)
        first = projects.active(conn)
        allowed = access.projects_for(conn, me)
    assert allowed == [first]
    out = fleet.scorecards(allowed)
    assert [p["project"] for p in out["projects"]] == [first] and out["left_out"] == [ridge]  # named, never read
    okan = out["projects"][0]["scorecard"]
    assert okan["now"]["through"] == "2025-12-30" and len(okan["kpis"]) == len(out["kpis"]) == 7

    # once granted, the second field reads in its own register: nothing loaded there, so every KPI is unknown
    with db.connect() as conn:
        boss, _ = access.sign_in(conn, {"tid": str(uuid.UUID(int=1)), "oid": str(uuid.uuid4()), "name": "Owner"})
        conn.execute("update access_account set enabled = true, owner = true where id = %s", (boss.id,))
        conn.commit()
        access.set_project_roles(conn, access.principal(conn, boss.id), me.id, ridge, ["reader"])
        conn.commit()
        allowed = access.projects_for(conn, me)
    out = fleet.scorecards(allowed)
    assert out["left_out"] == [] and {p["project"] for p in out["projects"]} == {first, ridge}
    there = next(p for p in out["projects"] if p["project"] == ridge)["scorecard"]
    assert {k["verdict"] for k in there["kpis"]} == {"unknown"}  # an empty field is unknown, never zero
    assert all(k["now"]["value"] is None for k in there["kpis"])
    assert fleet.scorecards(None)["left_out"] == []  # an instance without sign-in reads every project
