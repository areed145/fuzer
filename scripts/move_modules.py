"""Move fuzer modules into a subpackage and rewrite every import that names them (#135c).

    uv run python scripts/move_modules.py core db pack units pricing model tags

For each module: `git mv src/fuzer/<m>.py src/fuzer/<pkg>/<m>.py`, then every `from ... import ...` in
src/, tests/ and fakeplant/src/ is resolved to its absolute module, the moves applied, and written back:
relative inside the fuzer package (the fewest dots), absolute elsewhere. The old path becomes an alias
of the new module (the same module object, so state and monkeypatching are shared) for one release.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SHIM = '''"""Moved to fuzer.{pkg}.{mod} (#135c); this alias goes in the next release."""

import importlib
import sys
import warnings

warnings.warn("fuzer.{mod} moved to fuzer.{pkg}.{mod}", DeprecationWarning, stacklevel=2)
sys.modules[__name__] = importlib.import_module("fuzer.{pkg}.{mod}")
'''
IMPORT = re.compile(r"^(?P<indent>[ \t]*)from[ \t]+(?P<base>\.+[\w.]*|fuzer(?:\.[\w.]+)?)[ \t]+import[ \t]+"
                    r"(?P<names>\([^)]*\)|[^\n#]+?)(?P<tail>[ \t]*#[^\n]*)?$", re.MULTILINE)


def package_of(path: Path) -> str | None:
    """The dotted package a file sits in, if it's inside src/fuzer."""
    try:
        rel = path.relative_to(SRC)
    except ValueError:
        return None
    return ".".join(rel.parent.parts)


def absolute(base: str, pkg: str | None) -> str | None:
    if not base.startswith("."):
        return base
    if pkg is None:
        return None
    dots = len(base) - len(base.lstrip("."))
    parts = pkg.split(".")
    up = parts[: len(parts) - dots + 1]
    rest = base[dots:]
    return ".".join(up + ([rest] if rest else []))


def relative(target: str, pkg: str | None) -> str:
    if pkg is None or not pkg.startswith("fuzer"):
        return target
    p, t = pkg.split("."), target.split(".")
    common = 0
    while common < min(len(p), len(t)) and p[common] == t[common]:
        common += 1
    return "." * (len(p) - common + 1) + ".".join(t[common:])


def moved(module: str, moves: dict[str, str]) -> str:
    """fuzer.db -> fuzer.core.db; fuzer.db.x stays fuzer.core.db.x."""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "fuzer" and parts[1] in moves:
        return ".".join(["fuzer", moves[parts[1]], *parts[1:]])
    return module


def split_names(names: str) -> list[str]:
    inner = names.strip()
    if inner.startswith("("):
        inner = inner[1:-1]
    return [n.strip() for n in inner.replace("\n", " ").split(",") if n.strip()]


def render(indent: str, base: str, names: list[str], tail: str) -> str:
    line = f"{indent}from {base} import {', '.join(names)}"
    if len(line) <= 120:
        return line + tail
    return f"{indent}from {base} import (\n" + "".join(f"{indent}    {n},\n" for n in names) + f"{indent}){tail}"


def rewrite(text: str, old_pkg: str | None, new_pkg: str | None, moves: dict[str, str]) -> str:
    def one(m: re.Match) -> str:
        indent, base, tail = m["indent"], m["base"], m["tail"] or ""
        target = absolute(base, old_pkg)
        if target is None or not target.startswith("fuzer"):
            return m.group(0)
        names = split_names(m["names"])
        if target == "fuzer":  # from fuzer import db, ingest: moved modules go to their new package
            groups: dict[str, list[str]] = {}
            for n in names:
                mod = n.split(" as ")[0].strip()
                groups.setdefault(f"fuzer.{moves[mod]}" if mod in moves else "fuzer", []).append(n)
            out = [render(indent, relative(t, new_pkg), ns, tail if i == 0 else "")
                   for i, (t, ns) in enumerate(groups.items())]
            new = "\n".join(out)
        else:
            new = render(indent, relative(moved(target, moves), new_pkg), names, tail)
        if new.strip() == m.group(0).strip():
            return m.group(0)
        return new

    return IMPORT.sub(one, text)


def main(pkg: str, mods: list[str]) -> None:
    moves = {m: pkg for m in mods}
    dest = SRC / "fuzer" / pkg
    dest.mkdir(exist_ok=True)
    init = dest / "__init__.py"
    if not init.exists():
        init.write_text(f'"""fuzer.{pkg} (#135c)."""\n')
    files = [p for d in ("src", "tests", "fakeplant/src") for p in (ROOT / d).rglob("*.py") if "__pycache__" not in p.parts]
    new_home = {SRC / "fuzer" / f"{m}.py": dest / f"{m}.py" for m in mods}
    for p in files:
        text = p.read_text()
        out = rewrite(text, package_of(p), package_of(new_home.get(p, p)), moves)
        if out != text:
            p.write_text(out)
    for old, new in new_home.items():
        subprocess.run(["git", "mv", str(old), str(new)], check=True, cwd=ROOT)
        old.write_text(SHIM.format(pkg=pkg, mod=old.stem))
    print(f"moved {', '.join(mods)} to fuzer.{pkg}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
