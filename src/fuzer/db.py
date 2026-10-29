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


def connect(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row, autocommit=autocommit)


def init_schema(conn) -> None:
    conn.execute(SCHEMA.read_text())
    conn.commit()


def reset(conn) -> None:
    conn.execute("drop schema public cascade; create schema public;")
    init_schema(conn)
