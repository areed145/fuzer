"""Fuzer's components as data (#134): each package with its purpose, its modules, the fuzer packages it
imports and the tables it writes, read from the source. docs/architecture.md
both show it. Imports nothing from fuzer, so any layer can use it."""

import ast
import re
from pathlib import Path

GITHUB = "https://github.com/areed145/fuzer/tree/main/"

# The components, in the order data moves through them. The rest of the page is read from the code.
COMPONENTS = {
    "sources": ("Sources and ingestion", "The source catalog (systems, connections, datasets, mapping versions, "
                "runs) drives loading; connectors read each table; mappings turn rows into records, entities and links; "
                "Claude reads logs, P&IDs and documents.", "sources.md"),
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
    "cli": ("Command line", "The fuzer command: up, eval, settings and the rest.", "cli.md"),
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


def owned_tables(pkg: str) -> list[str]:
    """The tables `pkg` owns: the ones its own schema.sql creates."""
    f = PKG / pkg / "schema.sql"
    return re.findall(r"create table if not exists (?:\w+\.)?(\w+)", f.read_text()) if f.exists() else []


def _tables() -> list[str]:
    return [t for p in COMPONENTS for t in owned_tables(p)]


