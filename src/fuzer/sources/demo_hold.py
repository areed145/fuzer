"""A held load for the demo (#1145): the data steward's "fix a held load" job needs one to fix, and a healthy sample
has none. On a deployment with the demo sign-in, each deploy that finds no held load sets one dataset up as if its
export had renamed a column since the last load (its expected columns get the old spelling), so the load that
follows holds it for review exactly as a real renamed column would. The steward accepts the new columns on Master
data's Questions, and the dataset loads again at once (api.graph.review_decide). Nothing in the export changes.
"""

from psycopg.types.json import Jsonb


def _old_name(col: str) -> str:
    """How the export spelled the column before: in capitals (or, for one already in capitals, in lower case)."""
    return col.lower() if col == col.upper() else col.upper()


def seed(conn) -> str | None:
    """Set up the held load when none is open. Returns the dataset's name, or None when one is already held or no
    dataset fits (a small active table with a few columns that holds on a renamed column)."""
    if conn.execute("select 1 from review_item where kind = 'schema' and status = 'open' limit 1").fetchone():
        return None
    d = conn.execute(
        """
        select name, schema from source_dataset
        where status = 'active' and record_type is not null and drift_policy <> 'ignore'
          and jsonb_typeof(schema) = 'array' and jsonb_array_length(schema) >= 3
        order by rows nulls last, name limit 1
        """).fetchone()
    if d is None:
        return None
    schema = [list(c) for c in d["schema"]]
    col = schema[-1][0]
    old = [*schema[:-1], [_old_name(col), *schema[-1][1:]]]
    conn.execute("update source_dataset set schema = %s, fingerprint = null where name = %s", (Jsonb(old), d["name"]))
    return d["name"]
