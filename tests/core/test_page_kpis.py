"""P7.1b: a page shows a KPI the server computed (the metric registry or the module that owns it); it doesn't compute
one from server counts, where it could drift from the number the daily report and an agent quote.

The check is a lint over the page sources for the shapes a client-side KPI takes: a sum divided by something, or a ratio
scaled to a percentage. Layout arithmetic (a position along an axis) doesn't take these shapes. A page that does compute
one is listed below with why, until the number moves to the server.
"""

import re
from pathlib import Path

PAGES = Path(__file__).resolve().parents[2] / "ui" / "src" / "pages"
KPI = re.compile(r"reduce\(.*\)\s*/|/\s*[A-Za-z_.()]+\)?\s*\*\s*100|100\s*\*\s*[A-Za-z_.]+\s*/")

# path relative to ui/src/pages: the reason it is allowed for now
KNOWN = {
    "Performance.tsx": "the four-week arrivals and closed averages beside this week's counts (P7.2b); the backlog "
                       "average already comes from the server as backlog_avg4, and these should follow it",
}


def test_no_page_computes_a_kpi():
    found: dict[str, list[str]] = {}
    for path in sorted(PAGES.rglob("*.tsx")):
        rel = path.relative_to(PAGES).as_posix()
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if KPI.search(line):
                found.setdefault(rel, []).append(f"{rel}:{n}: {line.strip()}")
    new = [hit for rel, hits in found.items() if rel not in KNOWN for hit in hits]
    assert not new, "a page computes a KPI; read it from GET /metrics/{key} or the API that owns it:\n" + "\n".join(new)
    assert set(KNOWN) <= set(found), f"no longer computed on the page, take it off KNOWN: {set(KNOWN) - set(found)}"


def test_the_lint_knows_a_kpi_from_layout():
    for kpi in ("const rate = rows.reduce((s, r) => s + r.n, 0) / rows.length;", "`${Math.round(done / total * 100)}%`",
                "const pct = 100 * late / due;"):
        assert KPI.search(kpi), kpi
    for layout in ("Math.min(100, ((t - t0) / (t1 - t0)) * 100)", "style={{ width: `${pct}%` }}",
                   "Math.round(c.filled * 100)"):
        assert not KPI.search(layout), layout
