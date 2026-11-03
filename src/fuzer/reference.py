"""Reference pages generated from the code (#134): settings from the registry, CLI commands from argparse,
and the database schema from schema.sql. A test fails when a committed page is out of date.

    uv run python -m fuzer.reference      # writes docs/settings.md, docs/cli.md, docs/schema.md
"""

import argparse
import re
from pathlib import Path

from .resources import ROOT

HEADER = "Generated from {what}; don't edit by hand (`uv run python -m fuzer.reference`)."


def _cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def settings_md() -> str:
    from .core import settings

    out = ["# Settings", "", HEADER.format(what="the settings registry (`src/fuzer/core/settings.py`, #131)"),
           "Change them on the Settings page or with `fuzer settings set <key> <value>`; a project's value beats the "
           "global one, which beats the default. Secrets hold a reference (`env:NAME`), never the value.", ""]
    groups: dict[str, list] = {}
    for s in settings.REGISTRY.values():
        groups.setdefault(s.group, []).append(s)
    for g, items in groups.items():
        out += [f"## {g}", "", "| Setting | Type | Default | Range | What it is |", "|---|---|---|---|---|"]
        for s in items:
            rng = " / ".join(s.choices) if s.choices else " to ".join(
                f"{x:g}" for x in (s.min, s.max) if x is not None) if s.min is not None and s.max is not None else \
                (f"≥ {s.min:g}" if s.min is not None else f"≤ {s.max:g}" if s.max is not None else "")
            note = (" Takes effect on restart." if s.restart else "") + (f" Replaces `{s.env}`." if s.env else "")
            out.append(f"| `{s.key}` | {s.type} | `{_cell(s.default)}` | {rng} | {_cell(s.description)}.{note} |"
                       .replace("..", "."))
        out.append("")
    return "\n".join(out)


def cli_md() -> str:
    from .cli import parser

    p = parser()
    out = ["# Command line", "", HEADER.format(what="the argument parser (`src/fuzer/cli/__init__.py`)"),
           "Every command takes `--project` (the project to use) and `--pack` before the command name.", ""]
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    helps = {a.dest: a.help for a in sub._choices_actions}
    for name, sp in sub.choices.items():
        out += [f"## `fuzer {name}`", "", _cell(helps.get(name) or ""), ""]
        rows = []
        for a in sp._actions:
            if isinstance(a, argparse._HelpAction):
                continue
            flag = ", ".join(a.option_strings) if a.option_strings else a.dest
            choices = f" ({' / '.join(map(str, a.choices))})" if a.choices else ""
            rows.append(f"| `{flag}`{choices} | {_cell(a.help or '')} |")
        if rows:
            out += ["| Argument | What it does |", "|---|---|", *rows, ""]
    return "\n".join(out)


def schema_md() -> str:
    sql = (Path(__file__).parent / "schema.sql").read_text()
    out = ["# Database schema", "", HEADER.format(what="`src/fuzer/schema.sql`"),
           "One Postgres database; `fuzer` creates or updates it on start (idempotent).", ""]
    for m in re.finditer(r"((?:--[^\n]*\n)*)create table if not exists (\w+) \((.*?)\n\);", sql, re.DOTALL):
        note = " ".join(x.lstrip("- ").strip() for x in m.group(1).splitlines())
        cols = []
        for line in m.group(3).splitlines():
            line = line.strip().rstrip(",")
            if not line or line.startswith("--") or re.match(r"(primary key|unique|foreign key|check|constraint)\b",
                                                             line, re.IGNORECASE):
                continue
            name, _, rest = line.partition(" ")
            typ = re.split(r"\s+(not null|default|references|primary key|unique|generated|check)\b|--", rest.strip(),
                           maxsplit=1, flags=re.IGNORECASE)[0].strip()
            comment = line.split("--", 1)[1].strip() if "--" in line else ""
            cols.append(f"| `{name}` | {_cell(typ)} | {_cell(comment)} |")
        out += [f"## `{m.group(2)}`", "", *([_cell(note), ""] if note else []),
                "| Column | Type | Note |", "|---|---|---|", *cols, ""]
    return "\n".join(out)


PAGES = {"settings.md": settings_md, "cli.md": cli_md, "schema.md": schema_md}


def write(docs: Path = ROOT / "docs") -> list[str]:
    for name, fn in PAGES.items():
        (docs / name).write_text(fn())
    return sorted(PAGES)


if __name__ == "__main__":
    print("wrote", ", ".join(write()))
