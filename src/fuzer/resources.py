"""Where Fuzer's data files live (#135c): they stay at the package root while modules move into layers."""

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
SCHEMA = PACKAGE / "schema.sql"
UNITS = PACKAGE / "units.yaml"
PACKS = PACKAGE / "packs"
