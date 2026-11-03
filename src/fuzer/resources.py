"""Where Fuzer's data files live (#135c): they stay at the package root while modules move into layers."""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]  # the repository (src/fuzer -> .)
SCHEMA = PACKAGE / "schema.sql"
UNITS = PACKAGE / "units.yaml"
PACKS = PACKAGE / "packs"
MAPPINGS = PACKAGE / "mappings"
STANDARDS = PACKAGE / "standards"
UI_DIST = ROOT / "ui" / "dist"  # the built web UI (cd ui && npm run build)
