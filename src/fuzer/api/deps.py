"""What every route module shares: the pack, the database connection, the rebuild queue and the UI build."""

from ..core import db
from ..core.pack import load_pack
from ..graph import pipeline
from ..resources import UI_DIST  # noqa: F401  (for the routes that serve the built UI)

PACK = load_pack()
rebuilder = pipeline.BackgroundRebuild(PACK)


def get_conn():
    with db.connect() as conn:
        yield conn
