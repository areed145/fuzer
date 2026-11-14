"""A demonstration facility's first studies (#1078): an empty Studies page on a project with a sample crew runs the
studies that need no point chosen, once; any other project, or one that has studies, is left alone."""

from psycopg.types.json import Jsonb

from fuzer.core.pack import load_pack
from fuzer.ops import sample_crew, study

PACK = load_pack("okan")


def test_sample_studies_run_once_on_a_demo_project_only(okan_db, monkeypatch):
    conn = okan_db.conn
    kept = conn.execute("select * from study").fetchall()
    conn.execute("delete from study")
    conn.commit()
    try:
        assert study.sample(conn, PACK) == 0  # no sample crew, no studies made up
        monkeypatch.setattr(sample_crew, "enabled", lambda conn: True)
        assert study.sample(conn, PACK) == len(study.SAMPLES)
        made = study.listing(conn)
        assert sorted(s["kind"] for s in made) == sorted(k for k, _ in study.SAMPLES)
        # run there and then (the test database may hold too few upsets for some to find anything), by the crew
        assert {(s["status"] in ("done", "failed"), s["created_by"]) for s in made} == {(True, "sample crew")}
        assert study.sample(conn, PACK) == 0  # once: the page has studies now
    finally:
        conn.rollback()
        conn.execute("delete from study")
        for r in kept:  # other suites' studies, as they were
            conn.execute("insert into study (id, kind, params, status, result, summary, figure_id, error, created_by, "
                         "created_at, finished_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (r["id"], r["kind"], Jsonb(r["params"]), r["status"],
                          None if r["result"] is None else Jsonb(r["result"]), r["summary"], r["figure_id"], r["error"],
                          r["created_by"], r["created_at"], r["finished_at"]))
        conn.commit()
