"""Reference pages generated from the code (#134): settings from the registry, CLI commands from argparse,
the database schema from schema.sql, and the architecture (components, their imports, tables, settings and
tests; what feeds which entity kinds; the scheduled jobs and the scan's path). A test fails when a committed
page is out of date.

    uv run python -m fuzer.reference      # writes docs/settings.md, cli.md, schema.md, architecture.md
"""

import argparse
import ast
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


GITHUB = "https://github.com/areed145/fuzer/tree/main/"

# The components, in the order data moves through them. The rest of the page is read from the code.
COMPONENTS = {
    "sources": ("Sources and ingestion", "Connectors read each source table; mappings turn rows into records, "
                "entities and links; Claude reads logs, P&IDs and documents.", "sources.md"),
    "graph": ("Graph and golden records", "Resolves records into one entity per real thing, links them, and "
              "answers queries over the graph.", "identity.md"),
    "analysis": ("Analyses", "Signals, limits, calcs, hydraulics, studies and clustering over the graph and the "
                 "process values.", "analyses.md"),
    "ops": ("Operations", "The scheduled loop: SRE detection and grouping, hand-off, the board, daily reports, "
            "opportunities and studies.", "workflow.md"),
    "plots": ("Charts", "Interactive charts the agents and the app build from Fuzer data.", "agents.md"),
    "workflow": ("Work engine", "BPMN processes that move work between agents and people.", "workflow.md"),
    "agents": ("Agents and MCP", "The operations assistant, its specialists, and the MCP server with their tools.",
               "agents.md"),
    "evals": ("Evals", "Answer keys, the history replay and the ops eval that score it all.", "evals.md"),
    "api": ("API", "The HTTP API the app uses.", "deployment.md"),
    "cli": ("Command line", "`fuzer up` and the other commands.", "cli.md"),
    "core": ("Core", "Database, settings, projects and packs, Claude calls, tags and units: used by everything.",
             "deployment.md"),
}
PKG = Path(__file__).parent


def _imports(pkg: str) -> set[str]:
    """The fuzer packages `pkg` imports, inside functions too."""
    out = set()
    for f in (PKG / pkg).rglob("*.py"):
        here = ["fuzer", *f.relative_to(PKG).parent.parts]  # the file's package
        for n in ast.walk(ast.parse(f.read_text())):
            if not isinstance(n, ast.ImportFrom):
                continue
            base = here[:len(here) - n.level + 1] if n.level else []
            full = [*base, *(n.module or "").split(".")] if n.module else base
            names = [full] if n.module or len(full) > 1 else [[*full, a.name] for a in n.names]
            for x in names:
                if len(x) > 1 and x[0] == "fuzer" and x[1] in COMPONENTS and x[1] != pkg:
                    out.add(x[1])
    return out


def _source(pkg: str) -> str:
    return "\n".join(f.read_text() for f in sorted((PKG / pkg).rglob("*.py")))


def _tables() -> list[str]:
    return re.findall(r"create table if not exists (\w+)", (PKG / "schema.sql").read_text())


SCAN = [  # the scheduled scan, step by step: (function, what it does, the table it writes)
    ("ops.jobs:sre_scan", "every 6 hours", None),
    ("ops.sre:scan", "detectors read the process values since the last run", "detection"),
    ("ops.sre:regroup", "detections close in time and in the plant model become one upset", "upset"),
    ("ops.sre_handoff:handoff", "the troubleshooter diagnoses new upsets; each becomes a work item", "work_item"),
    ("ops.board:cards", "upsets, work, opportunities and reviews ranked by value", "board_card"),
    ("ops.daily_report:close_day", "the day's facts and a drafted report, checked against them", "daily_ops_report"),
]


def _mermaid_id(x: str) -> str:
    return re.sub(r"\W", "_", x)


def architecture_md() -> str:
    import importlib

    from .agents import capabilities
    from .sources import mappings

    tables = _tables()
    deps = {p: _imports(p) for p in COMPONENTS}
    out = ["# Architecture", "", HEADER.format(what="the code: package imports, mappings, the capability and settings "
                                                    "registries, the schedules and `schema.sql`"),
           "Each box is a package under `src/fuzer`; click one to open it. An arrow points from a package to the ones "
           "that use it, so data flows along the arrows. Everything uses core, so its arrows are left out.", "",
           "## Components", "", "```mermaid", "flowchart LR",
           '  systems[("Source systems<br>SAP, PI, EC, P&IDs, logs")] --> c_sources',
           '  app(["The app (ui/)"])', "  c_api --> app"]
    for p, (title, _, _) in COMPONENTS.items():
        n = sum(1 for f in (PKG / p).rglob("*.py") if f.name != "__init__.py")
        out.append(f'  c_{p}["{title}<br><small>fuzer.{p}, {n} module{"s" if n != 1 else ""}</small>"]')
    for p in COMPONENTS:
        out += [f"  c_{d} --> c_{p}" for d in sorted(deps[p]) if d != "core"]
    out += [f'  click c_{p} "{GITHUB}src/fuzer/{p}"' for p in COMPONENTS]
    out += ["```", ""]

    # what feeds which entity kinds
    feeds: dict[str, set[str]] = {}
    for m in mappings._load("").values():
        e = m.raw.get("entity") or {}
        if kind := e.get("kind") or ("plant hierarchy: facility, area, tag, well" if "node" in e else None):
            feeds.setdefault(m.source, set()).add(kind)
    out += ["## What feeds which entities", "", "From the mapping files in `src/fuzer/mappings` (source → entity kind). "
            "Claude's reads of logs, drawings and documents add events, links and mentions on top.", "",
            "```mermaid", "flowchart LR"]
    kinds = sorted({k for ks in feeds.values() for k in ks})
    out += [f'  src_{_mermaid_id(s)}[/"{s}"/]' for s in sorted(feeds)]
    out += [f'  kind_{_mermaid_id(k)}(["{k}"])' for k in kinds]
    out += [f"  src_{_mermaid_id(s)} --> kind_{_mermaid_id(k)}" for s in sorted(feeds) for k in sorted(feeds[s])]
    out += ["```", ""]

    # the scheduled scan
    out += ["## A scheduled scan, start to finish", "", "```mermaid", "flowchart TD"]
    prev = None
    for i, (ref, what, table) in enumerate(SCAN):
        mod, fn = ref.split(":")
        assert callable(getattr(importlib.import_module(f"fuzer.{mod}"), fn)), ref  # the step exists
        assert table is None or table in tables, table
        out.append(f'  s{i}["<b>{fn}</b><br>{what}"]' + (f" -.-> t{i}[(\"{table}\")]" if table else ""))
        out.append(f'  click s{i} "{GITHUB}src/fuzer/{mod.replace(".", "/")}.py"')
        if prev is not None:
            out.append(f"  s{prev} --> s{i}")
        prev = i
    out += ["```", ""]

    # the schedules
    from .ops import jobs

    out += ["## Scheduled jobs", "", "| Job | When (cron) | What it does |", "|---|---|---|"]
    for sch in jobs.schedules:
        out.append(f"| `{sch.job.name}` | `{sch.cron_schedule}` | {_cell(sch.job.description or '')} |")
    out.append("")

    # each component
    reads = {p: sorted(set(re.findall(r'settings\.get\([^,()]+,\s*"([\w.]+)"', _source(p)))) for p in COMPONENTS}
    writes = {p: sorted({t for t in tables if re.search(rf"(insert into|update|delete from) {t}\b", _source(p))})
              for p in COMPONENTS}
    by_agent: dict[str, int] = {}
    for c in capabilities.REGISTRY.values():
        for a in c.agents:
            by_agent[a] = by_agent.get(a, 0) + 1
    out += ["## Each component", ""]
    for p, (title, purpose, page) in COMPONENTS.items():
        mods = sorted(str(f.relative_to(PKG / p))[:-3].replace("/", ".") for f in (PKG / p).rglob("*.py")
                      if f.name != "__init__.py")
        tests = sum(len(re.findall(r"^def test_", f.read_text(), re.M)) for f in (PKG.parent.parent / "tests" / p).rglob("test_*.py")) \
            if (PKG.parent.parent / "tests" / p).exists() else 0
        out += [f"### {title}", "", f"{purpose} More in [{page[:-3].replace('-', ' ')}]({page}).", "",
                f"- **Code:** [`src/fuzer/{p}`]({GITHUB}src/fuzer/{p}): {', '.join(f'`{m}`' for m in mods)}",
                f"- **Uses:** {', '.join(sorted(deps[p])) or 'nothing else in fuzer'}"]
        if writes[p]:
            out.append(f"- **Writes:** {', '.join(f'`{t}`' for t in writes[p])} ([schema](schema.md))")
        if reads[p]:
            out.append(f"- **Settings:** {', '.join(f'`{k}`' for k in reads[p])} ([settings](settings.md))")
        if p == "agents":
            out.append("- **Tools:** " + ", ".join(f"{a} {n}" for a, n in sorted(by_agent.items()))
                       + " ([capabilities](capabilities.md))")
        if tests:
            out.append(f"- **Tests:** {tests} in [`tests/{p}`]({GITHUB}tests/{p})")
        out.append("")
    return "\n".join(out)


PAGES = {"settings.md": settings_md, "cli.md": cli_md, "schema.md": schema_md, "architecture.md": architecture_md}


def write(docs: Path = ROOT / "docs") -> list[str]:
    for name, fn in PAGES.items():
        (docs / name).write_text(fn())
    return sorted(PAGES)


if __name__ == "__main__":
    print("wrote", ", ".join(write()))
