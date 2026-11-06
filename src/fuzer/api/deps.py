"""What every route module shares: the pack, the database connection, the rebuild queue and the UI build."""

from ..core import db
from ..core.pack import load_pack
from ..ops import pipeline
from ..resources import UI_DIST  # noqa: F401  (for the routes that serve the built UI)

PACK = load_pack()
rebuilder = pipeline.BackgroundRebuild(PACK)


def get_conn():
    with db.connect() as conn:
        conn.execute("set statement_timeout = '30s'")
        conn.commit()
        yield conn
