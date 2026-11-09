"""Where Fuzer's data files live (#135c): they stay at the package root while modules move into layers."""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]  # the repository (src/fuzer -> .)
# Each module keeps the tables it owns in its own schema.sql, applied in this order (the layers from the bottom,
# so a module's migrations find the tables below it). A new module with tables adds its file here.
SCHEMA_MODULES = ("core", "workflow", "sources", "graph", "analysis", "ops", "plots", "agents", "evals")
SCHEMAS = tuple(PACKAGE / m / "schema.sql" for m in SCHEMA_MODULES)
UNITS = PACKAGE / "units.yaml"
PACKS = PACKAGE / "packs"
MAPPINGS = PACKAGE / "mappings"
STANDARDS = PACKAGE / "standards"
UI_DIST = ROOT / "ui" / "dist"  # the built web UI (cd ui && npm run build)


def schema_sql() -> str:
    """Every module's schema.sql, in order: the whole database schema as one script."""
    return "\n".join(f.read_text() for f in SCHEMAS)
