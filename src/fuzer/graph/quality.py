"""Reusable register quality checks and their thresholds."""


LIVE = "merged_into is null and retired_at is null"
LIVE_E = "e.merged_into is null and e.retired_at is null"


def _scalar(conn, sql: str) -> float:
    return conn.execute(sql).fetchone()["n"] or 0


def review_backlog(conn) -> dict:
    open_items = _scalar(conn, "select count(*) as n from review_item where status = 'open'")
    reviewable = _scalar(conn, f"select count(*) as n from entity where {LIVE} and kind in ('equipment', 'tag')")
    share = open_items / reviewable if reviewable else 0
    return {"passed": share <= 0.05, "open_reviews": int(open_items),
            "share_of_tags_and_equipment": round(share, 4)}


def orphan_timeseries(conn) -> dict:
    total = _scalar(conn, f"select count(*) as n from entity where kind = 'timeseries' and {LIVE}")
    orphans = conn.execute(
        f"""
        select l.label from entity e left join entity_label l using (fuzer_id)
        where e.kind = 'timeseries' and {LIVE_E}
          and not exists (select 1 from edge x where x.dst = e.fuzer_id and x.rel = 'has_timeseries')
        order by l.label
        """
    ).fetchall()
    share = len(orphans) / total if total else 0
    return {"passed": share <= 0.02, "orphans": len(orphans), "share": round(share, 4),
            "examples": [r["label"] for r in orphans[:20]]}


def tags_missing_from_sap(conn) -> dict:
    rows = conn.execute(
        f"""
        select l.label, array_agg(distinct c.source) as seen_in
        from entity e
        join crosswalk c using (fuzer_id)
        left join entity_label l using (fuzer_id)
        where e.kind = 'tag' and {LIVE_E}
        group by e.fuzer_id, l.label
        having not ('sap' = any(array_agg(c.source)))
        order by l.label
        """
    ).fetchall()
    return {"passed": not rows, "count": len(rows), "examples": [dict(r) for r in rows[:20]]}


def downtime_reason_codes(conn) -> dict:
    from . import queries

    rows = queries.downtime_reason_checks(conn, 20)
    total = _scalar(conn, "select count(*) as n from golden where field = 'reason_check'")
    return {"passed": total == 0, "mismatches": int(total), "examples": [dict(r) for r in rows]}


def document_revisions(conn) -> dict:
    from . import queries

    rows = queries.documents_with_several_current_revisions(conn)
    return {"passed": not rows, "count": len(rows), "examples": [dict(r) for r in rows]}


def flow_vs_piping(conn) -> dict:
    from . import queries

    diff = queries.compare_networks(conn, limit=20)
    gaps = len(diff["in_ec_not_on_pids"])
    return {"passed": gaps == 0, "in_ec_not_on_pids": gaps,
            "on_pids_not_in_ec": len(diff["on_pids_not_in_ec"]),
            "examples": [dict(r) for r in diff["in_ec_not_on_pids"]]}
