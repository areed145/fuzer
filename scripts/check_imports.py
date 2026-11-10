"""Every `from ... import ...` of fuzer in src/ and tests/ resolves, including the ones inside functions, and
every `module.name` on an imported fuzer module exists (#135c).

Ruff and a plain import of each module only see module-level imports. A relative import inside a function
that points at the wrong package, or a call like `timeseries.around(...)` after `around` moved, fails only
when that code runs. This checks both statically.

    uv run python scripts/check_imports.py
"""

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _module(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def check(p: Path, pkg: str | None) -> list[str]:
    bad, where = [], p.relative_to(ROOT)
    tree = ast.parse(p.read_text())
    modules: dict[str, object] = {}  # local name -> the fuzer module it is bound to
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("fuzer") and a.asname and (m := _module(a.name)):
                    modules[a.asname] = m
            continue
        if not isinstance(node, ast.ImportFrom) or not (node.level or (node.module or "").startswith("fuzer")):
            continue
        if node.level and pkg is None:
            continue
        base = importlib.util.resolve_name("." * node.level + (node.module or ""), pkg) if node.level else node.module
        if importlib.util.find_spec(base) is None:
            bad.append(f"{where}:{node.lineno}: no module {base}")
            continue
        mod = importlib.import_module(base)
        for a in node.names:  # `from pkg import name`: a submodule, or an attribute the module defines
            if a.name == "*":
                continue
            local = a.asname or a.name
            if hasattr(mod, a.name):
                value = getattr(mod, a.name)
                if type(value).__name__ == "module":
                    modules[local] = value
                continue
            if hasattr(mod, "__path__") and importlib.util.find_spec(f"{base}.{a.name}") is not None:
                modules[local] = importlib.import_module(f"{base}.{a.name}")
                continue
            bad.append(f"{where}:{node.lineno}: {base} has no {a.name}")
    # names rebound to something else in this file (a parameter, an assignment) aren't checked
    rebound = {t.id for n in ast.walk(tree)
               if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.With, ast.comprehension, ast.NamedExpr))
               for t in ast.walk(n) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store)}
    rebound |= {a.arg for n in ast.walk(tree) if isinstance(n, ast.arguments) for a in [*n.args, *n.kwonlyargs]}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in modules
                and node.value.id not in rebound and isinstance(node.ctx, ast.Load)):
            m = modules[node.value.id]
            if not _has(m, node.attr) and not (hasattr(m, "__path__")
                                                  and importlib.util.find_spec(f"{m.__name__}.{node.attr}")):
                bad.append(f"{where}:{node.lineno}: {m.__name__} has no {node.attr}")
    return bad


def _has(module, name: str) -> bool:
    """hasattr, except that a module __getattr__ failing for another reason (mcp_server.PACK needs a project in
    the database) still means the name exists."""
    try:
        return hasattr(module, name)
    except Exception:
        return True


def main() -> int:
    bad = []
    for p in sorted([*(SRC / "fuzer").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]):
        if "__pycache__" in p.parts:
            continue
        pkg = ".".join(p.relative_to(SRC).parent.parts) if p.is_relative_to(SRC) else None
        bad += check(p, pkg)
    print("\n".join(sorted(set(bad))) or "every import resolves")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
