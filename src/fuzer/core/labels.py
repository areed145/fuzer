"""An entity's display label."""


def label(conn, fid: str) -> str:
    """The entity's label (a tag, well or record name), or its id when it has none."""
    r = conn.execute("select label from entity_label where fuzer_id = %s", (fid,)).fetchone()
    return r["label"] if r else fid
