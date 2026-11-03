"""Every `from ... import ...` in src/fuzer resolves, including the ones inside functions (#135c).

Ruff and a plain import of each module only see module-level imports; a relative import inside a function
that points at the wrong package fails only when that function runs. This resolves each one statically.

    uv run python scripts/check_imports.py
"""

import ast
import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def main() -> int:
    bad = []
    for p in sorted((SRC / "fuzer").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        pkg = ".".join(p.relative_to(SRC).parent.parts)
        for node in ast.walk(ast.parse(p.read_text())):
            if not isinstance(node, ast.ImportFrom) or not (node.level or (node.module or "").startswith("fuzer")):
                continue
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), pkg) if node.level else node.module
            if importlib.util.find_spec(base) is None:
                bad.append(f"{p.relative_to(SRC)}:{node.lineno}: no module {base}")
                continue
            for a in node.names:  # `from pkg import name`: a submodule, or an attribute the module defines
                if a.name == "*":
                    continue
                mod = importlib.import_module(base)
                if hasattr(mod, a.name):
                    continue
                if not (hasattr(mod, "__path__") and importlib.util.find_spec(f"{base}.{a.name}") is not None):
                    bad.append(f"{p.relative_to(SRC)}:{node.lineno}: {base} has no {a.name}")
    print("\n".join(bad) or "every import resolves")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
