"""Charts for the operations assistant (#89): each builder on the Okan stories, the checks on what a
figure may contain, storage and the figure endpoints, the MCP tools (marker and summary out, never the
arrays), look_at_figure's image, and images attached to a question."""

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient

from fuzer import plots
from fuzer.agents import mcp_server
from fuzer.core.pack import load_pack
from fuzer.graph import queries
from fuzer.plots import general, maintenance, production, troubleshooting

PACK = load_pack("okan")


@pytest.fixture(scope="module")
def conn(okan_db):
    c = okan_db.conn
    yield c


@pytest.fixture
def okan_pack(monkeypatch):
    monkeypatch.setattr(mcp_server, "PACK", PACK)


def fid(conn, ref):
    return queries.resolve_ref(conn, PACK, ref)


def renders(fig: plots.Figure) -> None:
    plots.check(fig)
    assert plots.png(fig.spec)[:4] == b"\x89PNG"


# --- production ---------------------------------------------------------------------------------

def test_decline_fits_after_the_valve_repair(conn):
    fig = production.decline(conn, fid(conn, "OK-J07-03"))
    s = fig.summary
    steps = [x["at"] for x in s["step_changes"]]
    assert any(a.startswith("2024-09") for a in steps) and any(a.startswith("2024-12") for a in steps)
    assert s["fit_from"] >= "2024-11" and s["tests_in_fit"] >= 10
    assert 3 < s["effective_decline_pct_per_year"] < 20 and s["r2_log"] > 0.5
    assert s["forecast"]["oil_sm3"] > 0 and fig.layout["yaxis"]["type"] == "log"
    renders(fig)


def test_breakthrough_shows_in_decline_and_water_diagnostic(conn):
    well = fid(conn, "OK-J03-02")
    assert any(x["at"].startswith("2025-06") for x in production.decline(conn, well).summary["step_changes"])
    w = production.water_diagnostic(conn, well).summary
    assert w["largest_wor_step"]["at"].startswith("2025-06") and w["water_cut"]["latest"] > 70
    renders(production.water_diagnostic(conn, well))


def test_gas_lift_curve_leaves_out_the_failed_valve_tests(conn):
    fig = production.gas_lift_curve(conn, fid(conn, "OK-J07-03"))
    s = fig.summary
    assert s["tests_left_out"] and all("2024-09" <= t["at"] <= "2024-11-20" for t in s["tests_left_out"])
    assert s["now"]["marginal_oil_per_extra_gas"] > 0 and s["curve"]["max_gain_from_gas"] > 0
    renders(fig)
    with pytest.raises(plots.ChartError):  # a naturally flowing well has no curve
        nf = conn.execute("select l.label from measurement m join entity_label l using (fuzer_id) "
                          "where m.quantity = 'lift_gas_rate' group by l.label having max(m.value) = 0 limit 1").fetchone()
        production.gas_lift_curve(conn, fid(conn, nf["label"]))


def test_gas_lift_allocation(conn):
    fig = production.gas_lift_allocation(conn)
    s = fig.summary
    assert s["wells"] > 20 and len(s["headers"]) == 3 and s["suggested_moves"]
    assert all(m["oil_gain_sm3d"] >= 0 for m in s["suggested_moves"])
    renders(fig)


# --- maintenance --------------------------------------------------------------------------------

def test_wall_loss_ranking_and_one_pipeline(conn):
    rank = maintenance.wall_thickness(conn, kind="pipeline")
    top = rank.summary["items"][0]
    assert "production flowline" in top["description"] and top["fastest_loss_mm_per_year"] > 0.35
    assert top["fastest_loss_mm_per_year"] > 2 * rank.summary["median_of_shown_mm_per_year"]
    renders(rank)
    line = maintenance.wall_thickness(conn, fid(conn, top["label"]), min_required_mm=6.0)
    assert len(line.summary["cmls"]) == 4 and all("retirement_year" in c for c in line.summary["cmls"])
    renders(line)


def test_reliability_and_bad_actors(conn):
    k = fid(conn, "20-K-2200B")
    ca = maintenance.reliability(conn, k, "crow_amsaa")
    assert ca.summary["failures"] >= 4 and ca.summary["beta"] > 0
    renders(ca)
    cls = conn.execute("select dst from edge where rel = 'classified_as' and src = %s", (k,)).fetchone()["dst"]
    wb = maintenance.reliability(conn, cls, "weibull")
    assert wb.summary["times_between_failures"] >= 3 and wb.summary["beta"] > 0 and wb.summary["eta_days"] > 0
    renders(wb)
    ba = maintenance.bad_actors(conn)
    assert "20-K-2200B" in [t["tag"] for t in ba.summary["top"]]
    renders(ba)


# --- troubleshooting ----------------------------------------------------------------------------

def test_backlog_and_compliance(conn):
    fig = maintenance.backlog(conn)
    s = fig.summary
    assert s["open_orders"] == sum(s["by_age"].values()) == sum(s["by_priority"].values()) > 0
    assert sum(sum(t["y"]) for t in fig.data) == s["open_orders"]  # every order is plotted, in one bar
    assert fig.layout["barmode"] == "stack" and "count" in fig.layout["yaxis"]["title"]["text"]
    assert s["as_of"] in fig.title and "SAP" in s["source"]

    fig = maintenance.compliance(conn)
    s = fig.summary
    assert sum(sum(t["y"]) for t in fig.data) == sum(k["plans"] for k in s["summary"].values())
    assert next(t["name"] for t in fig.data) == "overdue" and "condition" in s["note"]  # a date, not a verdict
    assert s["as_of"] in fig.title


def test_every_stored_chart_says_what_it_covers(conn):
    fig = maintenance.backlog(conn)
    out = plots.store(conn, fig, {})
    assert out["summary"]["coverage"]["provenance"] == "fuzer" and out["summary"]["coverage"]["points"] > 0
    gap = plots.Figure("t", "t", [{"type": "scatter", "x": ["2025-01-01", "2025-01-03"], "y": [1.0, None]}],
                       plots.layout("t", "day", "Sm3/d"), {}, ["fz-1"])
    assert plots.coverage(gap) == {"from": "2025-01-01", "to": "2025-01-03", "points": 2, "missing_points": 1,
                                   "source_records": 1, "provenance": "fuzer"}


def test_incident_correlation_and_vibration(conn):
    k = fid(conn, "20-K-2200B")
    inc = troubleshooting.incident(conn, k)
    assert inc.summary["signals"] and inc.summary["moved_most"]
    assert any("trip" in (s["detail"] or "") + (s["text"] or "") for s in inc.summary["signals"])
    renders(inc)
    vt = next(r["dst"] for r in conn.execute(
        "select h.dst from edge p join edge h on h.src = p.src and h.rel = 'has_timeseries' "
        "join entity_label l on l.fuzer_id = h.dst where p.rel = 'part_of' and p.dst = %s and l.label ilike '%%VT%%'", (k,)))
    cor = troubleshooting.correlation(conn, k, target=vt)
    n = len(cor.summary["points"])
    assert n >= 3 and len(cor.data[0]["z"]) == n and cor.summary["against_target"]["best_lag"]
    renders(cor)
    vib = troubleshooting.vibration(conn, k)
    assert vib.summary["zone_limits_mm_s"] == [3.5, 7.1, 11.0] and vib.summary["peak_zone"] in "ABCD"
    renders(vib)


# --- general ------------------------------------------------------------------------------------

def test_general_charts(conn):
    renders(general.trend(conn, [fid(conn, "20-K-2200B")]))
    m = general.measurements(conn, fid(conn, "10-V-2001"))
    assert {"oil_in_water", "wall_thickness"} <= {s["quantity"] for s in m.summary["series"]}
    renders(m)
    b = general.bar(conn, "deferment_by_reason")
    assert b.summary["items"] and b.data[1]["yaxis"] == "y2"
    renders(b)
    pts = [r["dst"] for r in conn.execute(
        "select h.dst from edge p join edge h on h.src = p.src and h.rel = 'has_timeseries' "
        "where p.rel = 'part_of' and p.dst = %s limit 2", (fid(conn, "20-K-2200B"),))]
    xy = general.xy(conn, pts[0], pts[1])
    assert xy.summary["n"] > 10 and "r" in xy.summary["fit"]
    renders(xy)
    split = xy.summary["x"]
    d = general.distribution(conn, pts[0], "2024-03-20T00:00")
    assert d.summary["before"]["n"] > 3 and d.summary["after"]["n"] > 3 and split
    renders(d)
    c = general.custom("Gain per move", [{"name": "gain", "x": ["a", "b"], "y": [1.5, 2.0], "kind": "bar"}])
    assert c.by == "assistant" and "assistant-entered" in c.title
    renders(c)
    with pytest.raises(plots.ChartError):
        general.custom("too big", [{"y": list(range(600))}])


def test_checks_refuse_what_builders_never_make():
    ok = {"type": "scatter", "x": [1], "y": [1]}
    bad = [
        plots.Figure("x", "t", [{"type": "scattergeo"}], {}, {}),
        plots.Figure("x", "t", [ok | {"name": "<a href='javascript:alert(1)'>x</a>"}], {}, {}),
        plots.Figure("x", "t", [ok | {"name": "see https://evil.example"}], {}, {}),
        plots.Figure("x", "t", [ok | {"name": "<img src=x onerror=alert(1)>"}], {}, {}),
        plots.Figure("x", "t", [ok], {"images": [{"source": "x"}]}, {}),
        plots.Figure("x", "t", [{"type": "scatter", "x": list(range(30000)), "y": list(range(30000))}], {}, {}),
    ]
    for fig in bad:
        with pytest.raises(plots.ChartError):
            plots.check(fig)
    plots.check(plots.Figure("x", "t", [ok | {"hovertemplate": "%{y}<br>%{x}<extra></extra>"}], {}, {}))


# --- storage, API, MCP, agents ------------------------------------------------------------------

def test_tools_return_a_marker_and_summary_not_arrays(conn, okan_pack):
    out = mcp_server.plot_decline("OK-J07-03")
    assert out["marker"] == f"[[figure:{out['figure']}]]" and "effective_decline_pct_per_year" in out["summary"]
    assert len(json.dumps(out)) < 3000  # what the agent reads stays small
    assert "error" in mcp_server.plot_decline("no-such-well") or "candidates" in mcp_server.plot_decline("no-such-well")

    from fuzer import api

    client = TestClient(api.app)
    got = client.get(f"/figures/{out['figure']}").json()
    assert got["kind"] == "decline" and got["spec"]["data"] and got["by_source"] == "fuzer"
    page = client.get(f"/figures/{out['figure']}.html")
    assert page.status_code == 200 and "Plotly.newPlot" in page.text and "plotly.js-cartesian-dist-min@" in page.text
    png = client.get(f"/figures/{out['figure']}.png")
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert client.get("/figures/f_nope").status_code == 404

    seen = mcp_server.look_at_figure(out["marker"])
    assert json.loads(seen[0])["figure"] == out["figure"] and seen[1].data[:4] == b"\x89PNG"
    from fuzer import agents

    content = agents._content(seen)
    assert content[1].type == "data" and content[1].media_type == "image/png"
    assert agents._content({"a": 1}) == {"a": 1}


def test_every_role_has_its_charts():
    from fuzer import agents

    for role, extra in {"field-operations-facilities": "plot_incident",
                        "production-surveillance": "plot_decline",
                        "maintenance-integrity": "plot_wall_thickness", "orchestrator": "look_at_figure"}.items():
        assert extra in agents.TOOLS[role]
        assert all(hasattr(mcp_server, t) for t in agents.TOOLS[role])


def test_images_attached_to_a_question_reach_claude(monkeypatch):
    from fuzer import agents, api
    from fuzer.agents import service

    started = {}
    monkeypatch.setattr(service, "start", lambda q, c, by, images: started.update(q=q, images=images) or {"job_id": "j"})
    client = TestClient(api.app)
    png = plots.png({"data": [{"type": "scatter", "x": [1, 2], "y": [1, 2]}], "layout": {}})
    r = client.post("/assistant/ask", json={"question": "What does this spectrum show?",
                                            "images": [{"media_type": "image/png", "data": base64.b64encode(png).decode()}]})
    assert r.status_code == 200 and started["images"][0] == ("image/png", png)
    assert client.post("/assistant/ask", json={"question": "x", "images": [
        {"media_type": "image/png", "data": "not base64!"}]}).status_code == 400

    class Agent:  # what ask() hands the team
        context_providers = []

        async def run(self, message, session=None):
            self.message = message
            return type("R", (), {"messages": [], "text": "ok"})()

    a = Agent()
    asyncio.run(agents.ask("Read this", a, object(), [], [("image/png", png)]))
    kinds = [c.type for c in a.message.contents]
    assert kinds == ["text", "data"] and a.message.contents[1].media_type == "image/png"


def test_eval_scores_charts_in_answers(conn, okan_pack):
    from fuzer.evals import evaluate

    out = mcp_server.plot_decline("OK-J07-03")
    answer = {"answer": f"OK-J07-03 declines after the valve repair.\n\n{out['marker']}\n",
              "specialists": ["production-surveillance"],
              "tools": [{"agent": "production-surveillance", "tool": "look_at_figure", "args": {}}], "seconds": 1,
              "usage": {"cost_usd": 0.1}}
    case = {"question": "q", "must_mention": ["OK-J07-03"],
            "specialists": ["production-surveillance"], "charts": ["decline"]}
    r = evaluate.score_answer(case, answer)
    assert r["charts"] == ["decline"] and r["charted"] is True and r["looked_at_charts"] == 1
    assert evaluate.score_answer(case | {"charts": ["wall_ranking"]}, answer)["charted"] is False
    s = evaluate.summarise([r])
    assert s["charted"] == 1.0 and s["charts_per_answer"] == 1 and s["looked_at_charts"] == 1


def test_agent_makes_a_chart_looks_at_it_and_answers_with_the_marker(conn, okan_pack, monkeypatch):
    """The real MAF loop with a scripted Claude: plot, look (the PNG arrives as an image block in the
    tool result), answer with the marker."""
    from test_agents import _message

    from fuzer import agents

    monkeypatch.delenv("FUZER_AGENT_FALLBACKS", raising=False)

    class Claude:
        base_url = "https://fake.invalid"

        def __init__(self):
            self.requests = []
            self.beta = type("B", (), {})()
            self.beta.messages = type("M", (), {})()
            self.beta.messages.create = self.create

        async def create(self, **kw):
            self.requests.append(kw)
            results = [b for m in kw["messages"] if m["role"] == "user" and isinstance(m["content"], list)
                       for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
            step = len(results)
            if step == 0:
                return _message([{"type": "tool_use", "id": "t0", "name": "plot_bar",
                                  "input": {"dataset": "deferment_by_reason"}}], "tool_use")
            text = lambda b: "".join(x.get("text", "") for x in b["content"] if x.get("type") == "text")
            marker = json.loads(text(results[0]))["marker"]
            if step == 1:
                return _message([{"type": "tool_use", "id": "t1", "name": "look_at_figure",
                                  "input": {"figure": marker}}], "tool_use")
            return _message([{"type": "text", "text": f"Planned maintenance leads.\n\n{marker}\n"}], "end_turn")

    fake = Claude()
    team = agents.team(agents.client(anthropic_client=fake))
    out = asyncio.run(agents.ask("What are our biggest losses? Show me.", team))
    looked = next(b for b in fake.requests[2]["messages"][-1]["content"] if b.get("type") == "tool_result")
    assert [x["type"] for x in looked["content"]] == ["text", "image"]
    assert looked["content"][1]["source"]["media_type"] == "image/png"
    assert "[[figure:f_" in out["answer"] and [t["tool"] for t in out["tools"]] == ["plot_bar", "look_at_figure"]
