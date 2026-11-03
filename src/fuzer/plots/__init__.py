"""Interactive charts for the operations assistant (#89).

The agent picks a chart and its options; the server reads the data, fits what needs fitting and
builds a Plotly figure (plain JSON, rendered by plotly.js in the browser). The figure is stored in
assistant_figure, and the tool returns only its id, a `[[figure:<id>]]` marker for the answer and a
short summary of what it shows, so the agent reasons from exact numbers and never copies arrays.
`look_at_figure` renders a stored figure to PNG (matplotlib, no browser) for the rare times the agent
needs to see it.

Builders live in general.py, troubleshooting.py, production.py and maintenance.py; each returns a
Figure. Specs are built only by this code, from allow-listed trace types, and checked again before
they're stored (no HTML beyond Plotly's plain formatting, no images, no URLs).
"""

import io
import json
import re
import secrets
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb

from ..analysis.calcs import CalcError

TRACES = {"scatter", "scattergl", "bar", "histogram", "box", "heatmap", "contour"}
MAX_POINTS = 20000
SAFE_TAGS = re.compile(r"</?(b|i|br|sup|sub|em|extra)\s*/?>", re.I)


@dataclass
class Figure:
    kind: str
    title: str
    data: list[dict]
    layout: dict
    summary: dict
    sources: list[str] = field(default_factory=list)
    by: str = "fuzer"  # "assistant" when the agent supplied the numbers (plot_custom)

    @property
    def spec(self) -> dict:
        return {"data": self.data, "layout": self.layout}


# A chart fails for the same reasons its calc does (too few tests, no curve); one error for both.
ChartError = CalcError


# ---- house style ----------------------------------------------------------------------------------

def layout(title: str, x: str | None = None, y: str | None = None, **kw) -> dict:
    """Common layout: the page's theme (colours, fonts) is applied by the browser."""
    out = {"title": {"text": title}, "hovermode": "closest", "showlegend": True,
           "legend": {"orientation": "h", "y": -0.18}, "margin": {"l": 60, "r": 30, "t": 50, "b": 60}}
    if x:
        out["xaxis"] = {"title": {"text": x}}
    if y:
        out["yaxis"] = {"title": {"text": y}}
    return out | kw


def stacked(n: int, gap: float = 0.04) -> list[list[float]]:
    """y domains for n stacked panels sharing an x axis, top first."""
    h = (1 - gap * (n - 1)) / n
    return [[round(1 - (i + 1) * h - i * gap, 4), round(1 - i * h - i * gap, 4)] for i in range(n)]


def axis(i: int) -> tuple[str, str]:
    """Trace axis reference and layout key for panel i (0 -> y / yaxis)."""
    return ("y", "yaxis") if i == 0 else (f"y{i + 1}", f"yaxis{i + 1}")


def thin(xs: list, ys: list, n: int = 1500) -> tuple[list, list]:
    """At most about n points, keeping each bucket's min and max so spikes survive."""
    if len(xs) <= n:
        return xs, ys
    step = len(xs) / (n / 2)
    ox, oy = [], []
    i = 0.0
    while int(i) < len(xs):
        a, b = int(i), min(len(xs), int(i + step))
        idx = sorted({min(range(a, b), key=lambda k: ys[k]), max(range(a, b), key=lambda k: ys[k])})
        ox += [xs[k] for k in idx]
        oy += [ys[k] for k in idx]
        i += step
    return ox, oy


def rnd(v, n: int = 3):
    return None if v is None else round(float(v), n)


# ---- checks and storage ---------------------------------------------------------------------------

def check(fig: Figure) -> None:
    """Refuse anything outside what the builders make: unknown trace types, images, scripts, links,
    HTML beyond plain formatting, or too many points."""
    points = 0
    for t in fig.data:
        if t.get("type", "scatter") not in TRACES:
            raise ChartError(f"trace type {t.get('type')!r} not allowed")
        if "transforms" in t:
            raise ChartError("transforms not allowed")
        for k in ("x", "y", "z"):
            v = t.get(k)
            if isinstance(v, list):
                points += sum(len(r) if isinstance(r, list) else 1 for r in v)
    if points > MAX_POINTS:
        raise ChartError(f"{points} points; at most {MAX_POINTS}")
    if "images" in fig.layout:
        raise ChartError("layout images not allowed")

    def walk(v):
        if isinstance(v, str):
            low = v.lower()
            if "javascript:" in low or "<script" in low or "http://" in low or "https://" in low:
                raise ChartError("links and scripts not allowed in chart text")
            if "<" in SAFE_TAGS.sub("", v).replace("<=", "").replace("< ", ""):
                raise ChartError("HTML not allowed in chart text")
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v[:50] if v and not isinstance(v[0], (dict, list)) else v:
                walk(x)

    walk(fig.data)
    walk(fig.layout)


def store(conn, fig: Figure, params: dict) -> dict:
    """Check and keep a figure; return what the agent sees."""
    check(fig)
    fid = "f_" + secrets.token_hex(5)
    conn.execute(
        "insert into assistant_figure (id, kind, title, params, spec, summary, sources, by_source) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (fid, fig.kind, fig.title, Jsonb(params), Jsonb(fig.spec), Jsonb(fig.summary), fig.sources, fig.by),
    )
    conn.commit()
    return {"figure": fid, "marker": f"[[figure:{fid}]]", "title": fig.title, "summary": fig.summary,
            "link": f"/figures/{fid}.html",
            "note": "Put the marker on its own line in your answer where the chart belongs and say what it shows."}


def get(conn, fid: str) -> dict | None:
    return conn.execute("select * from assistant_figure where id = %s", (fid,)).fetchone()


# ---- PNG for look_at_figure ------------------------------------------------------------------------

def png(spec: dict, width: int = 1000, height: int = 640) -> bytes:
    """A plain rendering of a figure's traces (matplotlib): enough to judge its shape, not a copy of
    the browser's chart."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.dates import AutoDateLocator, ConciseDateFormatter

    lay = spec.get("layout", {})
    data = spec.get("data", [])
    keys = sorted({t.get("yaxis", "y") for t in data}, key=lambda k: int(k[1:] or 1))
    panels, twins = [], {}
    for k in keys:
        ax_lay = lay.get("yaxis" + k[1:], {})
        (twins if ax_lay.get("overlaying") else {})[k] = ax_lay.get("overlaying")
        if not ax_lay.get("overlaying"):
            panels.append((k, (ax_lay.get("domain") or [0, 1])[1]))
    panels.sort(key=lambda p: -p[1])
    fig, axes = plt.subplots(len(panels) or 1, 1, figsize=(width / 100, height / 100), sharex=True, squeeze=False)
    ax_of = {k: axes[i][0] for i, (k, _) in enumerate(panels)}
    for k, base in twins.items():
        ax_of[k] = ax_of.get(base, axes[0][0]).twinx()
    datey = False
    for t in data:
        ax = ax_of.get(t.get("yaxis", "y"), axes[0][0])
        kind, name = t.get("type", "scatter"), t.get("name")
        x, y = t.get("x"), t.get("y")
        if x and isinstance(x[0], str) and re.match(r"\d{4}-\d\d-\d\d", x[0]):
            from datetime import datetime
            x = [datetime.fromisoformat(v[:19]) for v in x]
            datey = True
        if kind == "heatmap":
            im = ax.imshow(t["z"], cmap="RdBu_r", vmin=t.get("zmin"), vmax=t.get("zmax"), aspect="auto")
            if t.get("x"):
                ax.set_xticks(range(len(t["x"])), t["x"], rotation=60, fontsize=7)
            if t.get("y"):
                ax.set_yticks(range(len(t["y"])), t["y"], fontsize=7)
            fig.colorbar(im, ax=ax)
        elif kind == "bar":  # side by side when there are several, as barmode "group" draws them
            group = [u for u in data if u.get("type") == "bar" and u.get("yaxis", "y") == t.get("yaxis", "y")]
            k, w = group.index(t), 0.8 / len(group)
            cats = y if t.get("orientation") == "h" else x
            pos = [i - 0.4 + w * (k + 0.5) for i in range(len(cats))]
            if t.get("orientation") == "h":
                ax.barh(pos, x, height=w, label=name)
                ax.set_yticks(range(len(cats)), cats, fontsize=7)
            else:
                ax.bar(pos, y, width=w, label=name)
                ax.set_xticks(range(len(cats)), cats, rotation=60, fontsize=7)
        elif kind == "histogram":
            ax.hist(x or y, bins=30, alpha=0.6, label=name)
        elif kind == "box":
            ax.boxplot([y], positions=[len(ax.get_lines())], tick_labels=[name or ""])
        else:
            mode = t.get("mode", "lines")
            dash = (t.get("line") or {}).get("dash")
            style = ("--" if dash else "-") if "lines" in mode else ""
            ax.plot(x, y, style + ("o" if "markers" in mode else ""), markersize=3, label=name)
    for i, (k, _) in enumerate(panels):
        ax_lay = lay.get("yaxis" + k[1:], {})
        ax = ax_of[k]
        ax.set_ylabel((ax_lay.get("title") or {}).get("text", ""), fontsize=8)
        if ax_lay.get("type") == "log":
            ax.set_yscale("log")
        if ax_lay.get("autorange") == "reversed":
            ax.invert_yaxis()
        if any(t.get("name") and t.get("type", "scatter") not in ("heatmap", "box") for t in data
               if t.get("yaxis", "y") == k):
            ax.legend(fontsize=7, loc="best")
    xl = lay.get("xaxis", {})
    if xl.get("type") == "log":
        for ax in ax_of.values():
            ax.set_xscale("log")
    bottom = axes[-1][0]
    bottom.set_xlabel((xl.get("title") or {}).get("text", ""), fontsize=8)
    if datey:
        loc = AutoDateLocator()
        bottom.xaxis.set_major_locator(loc)
        bottom.xaxis.set_major_formatter(ConciseDateFormatter(loc))
    for s in lay.get("shapes", []):  # shaded windows and bands
        if s.get("type") == "rect" and s.get("yref") == "paper" and datey:
            from datetime import datetime
            for ax in [a[0] for a in axes]:
                ax.axvspan(datetime.fromisoformat(s["x0"][:19]), datetime.fromisoformat(s["x1"][:19]), alpha=0.12, color="orange")
        elif s.get("type") == "rect" and s.get("xref") == "paper":
            ax_of.get(s.get("yref", "y"), axes[0][0]).axhspan(s["y0"], s["y1"], alpha=0.08, color="grey")
        elif s.get("type") == "line" and s.get("yref") == "paper" and datey:
            from datetime import datetime
            for ax in [a[0] for a in axes]:
                ax.axvline(datetime.fromisoformat(s["x0"][:19]), color="grey", lw=0.8, ls=":")
    fig.suptitle((lay.get("title") or {}).get("text", ""), fontsize=10)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def html(fid: str, title: str, spec: dict) -> str:
    """A standalone page for one figure (for MCP clients and sharing)."""
    safe = json.dumps(spec).replace("</", "<\\/")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{re.sub(r'[<>&]', '', title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-cartesian-dist-min@{PLOTLY_VERSION}/plotly-cartesian.min.js"></script>
<style>body{{margin:0;font-family:system-ui,sans-serif;background:#fff}}#c{{width:100vw;height:100vh}}</style></head>
<body><div id="c"></div><script>const s={safe};Plotly.newPlot("c",s.data,s.layout,{{responsive:true,displaylogo:false}});</script>
</body></html>"""


PLOTLY_VERSION = "4.1.2"  # same as the web UI (ui/package.json)

from . import general, maintenance, production, troubleshooting  # noqa: E402,F401  (builders)
