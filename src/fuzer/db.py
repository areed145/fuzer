"""Database connection.

Set FUZER_DATABASE_URL to use a real Postgres (e.g. Azure Database for
PostgreSQL). Without it, an embedded Postgres is started under .fuzer/pgdata.
"""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA = Path(__file__).with_name("schema.sql")
_server = None


def database_url() -> str:
    if url := os.environ.get("FUZER_DATABASE_URL"):
        return url
    global _server
    if _server is None:
        import pgserver

        data_dir = Path(os.environ.get("FUZER_PGDATA", ".fuzer/pgdata")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        _server = pgserver.get_server(data_dir)
    return _server.get_uri()


def connect(autocommit: bool = False, url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url(), row_factory=dict_row, autocommit=autocommit)


def init_schema(conn) -> None:
    conn.execute(SCHEMA.read_text())
    conn.commit()


def reset(conn) -> None:
    conn.execute("drop schema public cascade; create schema public;")
    init_schema(conn)


def copy_rows(conn, table: str, columns: list[str], rows) -> int:
    """Bulk insert with COPY; much faster than INSERT for tens of thousands of rows."""
    n = 0
    with conn.cursor() as cur, cur.copy(f"copy {table} ({', '.join(columns)}) from stdin") as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def sync_table(conn, table: str, key_cols: list[str], value_cols: list[str], rows,
               nullable_keys: tuple[str, ...] = ()) -> dict:
    """Make `table` hold exactly `rows`, touching only what changed.

    Rows are staged in a temporary table (no indexes, no WAL), then three set-based statements
    delete what's gone, update what changed and insert what's new. On a rebuild where little
    changed, index work and dead tuples stay small, and readers keep seeing the table throughout.
    """
    cols = key_cols + value_cols
    stage = f"{table}__stage"
    conn.execute(f"drop table if exists {stage}")
    conn.execute(f"create temp table {stage} on commit drop as select {', '.join(cols)} from {table} limit 0")
    n = copy_rows(conn, stage, cols, rows)
    conn.execute(f"analyze {stage}")

    def k(alias, col):
        return f"coalesce({alias}.{col}::text, '')" if col in nullable_keys else f"{alias}.{col}"

    match = " and ".join(f"{k('t', c)} = {k('s', c)}" for c in key_cols)
    # With enough work_mem, hashing both sides beats sorting them on text keys (measured ~8x on the
    # delete at 500k rows); switch merge joins off just for these statements.
    conn.execute("set local enable_mergejoin = off")
    deleted = conn.execute(f"delete from {table} t where not exists (select 1 from {stage} s where {match})").rowcount
    updated = 0
    if value_cols:
        sets = ", ".join(f"{c} = s.{c}" for c in value_cols)
        changed = " or ".join(f"t.{c} is distinct from s.{c}" for c in value_cols)
        updated = conn.execute(f"update {table} t set {sets} from {stage} s where {match} and ({changed})").rowcount
    inserted = conn.execute(
        f"insert into {table} ({', '.join(cols)}) select {', '.join('s.' + c for c in cols)} from {stage} s "
        f"where not exists (select 1 from {table} t where {match})"
    ).rowcount
    conn.execute("set local enable_mergejoin = on")
    return {"rows": n, "inserted": inserted, "updated": updated, "deleted": deleted}
