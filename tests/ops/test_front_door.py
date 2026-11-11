"""One front door for work (#509): outside the work engine, only the routing module opens a case."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "fuzer"
ALLOWED = {"workflow", "core/work.py", "ops/routing.py"}  # the engine, its port, and the front door


def test_no_module_outside_the_engine_opens_a_case():
    opens = re.compile(r"\b(open_case|create_item)\s*\(")
    found = []
    for f in sorted(SRC.rglob("*.py")):
        rel = f.relative_to(SRC).as_posix()
        if rel.split("/")[0] in ALLOWED or rel in ALLOWED:
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if opens.search(line) and not line.lstrip().startswith(("def ", "#")):
                found.append(f"{rel}:{n}: {line.strip()}")
    assert not found, "open cases through fuzer.ops.routing (route or open_task):\n" + "\n".join(found)
