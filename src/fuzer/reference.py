"""Reference pages generated from the code (#134): settings from the registry, CLI commands from argparse,
the database schema from each module's schema.sql, and the architecture (components, their imports, tables, settings and
tests; what feeds which entity kinds; the scheduled jobs and the scan's path) and the recorded eval runs. A test fails when a committed
page is out of date.

    uv run python -m fuzer.reference      # writes docs/settings.md, cli.md, schema.md, architecture.md,
                                          # eval-results.md
"""

import argparse
import re
from pathlib import Path

from .components import COMPONENTS, GITHUB, PKG, _imports, _source, _tables, owned_tables
from .resources import ROOT, SCHEMA_MODULES, SCHEMAS

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
    out = ["# Database schema", "", HEADER.format(what="each module's `schema.sql`"),
           "One Postgres database; `fuzer` creates or updates it on start (idempotent). Each module owns the tables "
           "in its own `schema.sql`, applied in the order below.", ""]
    for module, path in zip(SCHEMA_MODULES, SCHEMAS):
        link = f"src/fuzer/{module}/schema.sql"
        out += [f"## {COMPONENTS[module][0]}", "", f"[`{link}`]({GITHUB}{link})", ""]
        out += _tables_md(path.read_text())
    return "\n".join(out)


def _tables_md(sql: str) -> list[str]:
    out = []
    for m in re.finditer(r"((?:--[^\n]*\n)*)create table if not exists (?:\w+\.)?(\w+) \((.*?)\n\);", sql, re.DOTALL):
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
        out += [f"### `{m.group(2)}`", "", *([_cell(note), ""] if note else []),
                "| Column | Type | Note |", "|---|---|---|", *cols, ""]
    return out


SCAN = [  # the scheduled scan, step by step: (function, what it does, the table it writes)
    ("ops.jobs:sre_scan", "every 6 hours", None),
    ("ops.sre:scan", "detectors read the process values since the last run", "detection"),
    ("ops.sre:regroup", "detections close in time and in the plant model become one upset", "upset"),
    ("ops.sre_handoff:handoff", "the assistant diagnoses new upsets; each becomes a work item", "work_item"),
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
                                                    "registries, the schedules and the modules' `schema.sql`"),
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
    by_agent = {a: len(names) for a, names in capabilities.tools_by_agent().items() if names}  # loads the tools
    out += ["## Each component", ""]
    for p, (title, purpose, page) in COMPONENTS.items():
        mods = sorted(str(f.relative_to(PKG / p))[:-3].replace("/", ".") for f in (PKG / p).rglob("*.py")
                      if f.name != "__init__.py")
        tests = (PKG.parent.parent / "tests" / p).exists()
        out += [f"### {title}", "", f"{purpose} More in [{page[:-3].replace('-', ' ')}]({page}).", "",
                f"- **Code:** [`src/fuzer/{p}`]({GITHUB}src/fuzer/{p}): {', '.join(f'`{m}`' for m in mods)}",
                f"- **Uses:** {', '.join(sorted(deps[p])) or 'nothing else in fuzer'}"]
        if writes[p]:
            out.append(f"- **Writes:** {', '.join(f'`{t}`' for t in writes[p])} ([schema](schema.md))")
        if owns := owned_tables(p):
            out.append(f"- **Owns:** {', '.join(f'`{t}`' for t in owns)} (its `schema.sql`)")
        if reads[p]:
            out.append(f"- **Settings:** {', '.join(f'`{k}`' for k in reads[p])} ([settings](settings.md))")
        if p == "agents":
            out.append("- **Tools:** " + ", ".join(f"{a} {n}" for a, n in sorted(by_agent.items()))
                       + " ([capabilities](capabilities.md))")
        if tests:
            out.append(f"- **Tests:** [`tests/{p}`]({GITHUB}tests/{p})")
        out.append("")
    return "\n".join(out)


def _pct(v) -> str:
    return "" if v is None else f"{v:.0%}" if v <= 1 else f"{v:g}"


def eval_results_md() -> str:
    from .evals import ops_eval, runs

    all_runs = runs.load()
    asst = [r for r in all_runs if r["kind"] == "assistant"]
    ops = [r for r in all_runs if r["kind"] == "ops"]
    out = ["# Eval results", "", HEADER.format(what="the recorded runs in `evals/runs/`"),
           "Record a run with `fuzer eval --agents --record` or `fuzer eval --ops --full --live --record` (both "
           "cost money), then regenerate. How the evals work: [evals](evals.md); notes on each assistant run: "
           "[assistant eval](assistant-eval.md).", ""]
    if asst:
        out += ["## Operations assistant", "",
                "Mechanical checks against `answer_keys/assistant.json`: the facts a good answer names, sources "
                "cited, no plant-operating instructions, the expected specialists asked.", "",
                "| Run | Model | Questions | Facts named | Cited 3+ | Safe | Routed | Chart drawn | Cost / answer "
                "| Time / answer |", "|---|---|---|---|---|---|---|---|---|---|"]
        for r in reversed(asst):
            out.append(f"| {r['date']} | {r.get('model', '')} | {r['questions']} | {_pct(r['mentions'])} | "
                       f"{_pct(r['cited'])} | {_pct(r['safe'])} | {_pct(r['routed'])} | {_pct(r.get('charted'))} | "
                       f"${r['cost_per_answer_usd']:.2f} | {r['seconds_per_answer']:g} s |")
        last = asst[-1]
        if last.get("results"):
            out += ["", f"### Latest run by question ({last['date']})", "",
                    "| Question | Specialists | Facts | Missing | Citations | Charts | Tool calls | Time | Cost |",
                    "|---|---|---|---|---|---|---|---|---|"]
            for q in last["results"]:
                ask = q.get("question") or ""
                ask = ask if len(ask) <= 70 else ask[:69].rsplit(" ", 1)[0] + "…"
                out.append(f"| {q['domain']}: {_cell(ask)} | {', '.join(q.get('specialists') or [])} | {_pct(q['mentions'])} | "
                           f"{_cell(', '.join(q.get('missing') or []))} | {q['citations']} | "
                           f"{', '.join(q.get('charts') or [])} | {q.get('tool_calls') or ''} | {q['seconds']:.0f} s | "
                           f"${q['cost_usd']:.2f} |")
        out.append("")
    if ops:
        out += ["## Operations loop", "",
                "History replayed with the fake plant's injected upsets: SRE detection, grouping, the "
                "assistant's diagnosis, daily reports and opportunities.", "",
                "| Run | Window | Recall | Precision | Upsets (process / instrument / slow / standing) | False upsets "
                "/ week | First cause right | Diagnosed (named first cause) | Daily reports OK | Gas lift gain error |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in reversed(ops):
            s, g, d, dr, o = (r.get(k) or {} for k in ("sre", "grouping", "diagnosis", "daily_report",
                                                        "opportunities"))
            kinds = " / ".join(str(g.get("by_kind", {}).get(k, 0)) for k in ("process", "instrument", "slow",
                                                                              "standing"))
            diag = f"{d['scored']} ({_pct(d.get('first_cause_named'))})" if d.get("scored") else "none new"
            out.append(f"| {r['date']}{'' if r.get('live') else ' (offline)'} | {r['since'][:10]} to "
                       f"{r['until'][:10]} | {_pct(s.get('recall'))} | {_pct(s.get('precision'))} | {kinds} | "
                       f"{g.get('false_upsets_per_week', '')} | {_pct(g.get('first_cause_right'))} | {diag} | "
                       f"{_pct(dr.get('all_checks_ok'))} | "
                       f"{'' if o.get('worst_gain_error') is None else format(o['worst_gain_error'], '.2%')} |")
        out += ["", "### Gates", "", "A run fails when a score is under its floor or over its ceiling "
                "(`src/fuzer/evals/ops_eval.py`), or when recall fell since the last run of the same window.", "",
                "| Score | Limit |", "|---|---|",
                *(f"| `{a}.{b}` | at least {v:g} |" for (a, b), v in ops_eval.GATES.items()),
                *(f"| `{a}.{b}` | at most {v:g} |" for (a, b), v in ops_eval.CEILINGS.items()), ""]
    return "\n".join(out)


PAGES = {"settings.md": settings_md, "cli.md": cli_md, "schema.md": schema_md, "architecture.md": architecture_md,
         "eval-results.md": eval_results_md}


def write(docs: Path = ROOT / "docs") -> list[str]:
    for name, fn in PAGES.items():
        (docs / name).write_text(fn())
    return sorted(PAGES)


if __name__ == "__main__":
    print("wrote", ", ".join(write()))
