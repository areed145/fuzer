"""What every route module shares: the pack, the database connection, the rebuild queue and the UI build."""

from ..core import db
from ..core.pack import Pack, current_pack
from ..ops import pipeline
from ..resources import UI_DIST  # noqa: F401  (for the routes that serve the built UI)

# Resolved when each rebuild runs, from the database it rebuilds (#376): not fixed when the API is imported.
rebuilder = pipeline.BackgroundRebuild(current_pack)


def pack() -> Pack:
    """The asset pack of the project this server's database holds, read at runtime (#376)."""
    return current_pack()


def get_conn():
    with db.connect() as conn:
        conn.execute("set statement_timeout = '30s'")
        conn.commit()
        yield conn
