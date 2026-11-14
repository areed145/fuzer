"""The historian connectors on responses Fuzer did not write (R.2): each fixture under fixtures/historian names where it
came from, when, and how much of it is verbatim. The connectors are run against them with no network; the fake
historian is checked to serve the same shapes, so a wrong reading of the vendor's API fails here and not on a pilot's
first day."""

import json
from pathlib import Path

import httpx
import pytest

from fuzer.sources import adx, connections

FIXTURES = Path(__file__).parent / "fixtures" / "historian"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def respond(fx: dict) -> httpx.Response:
    body = fx["body"]
    headers = fx.get("headers") or {}
    if isinstance(body, (dict, list)):
        return httpx.Response(fx["status"], headers=headers, content=json.dumps(body).encode())
    return httpx.Response(fx["status"], headers=headers, content=(body or "").encode())


# ---- ADX (R.2b) -------------------------------------------------------------------------------------------------

def adx_replay(*names: str, answers: dict[str, str] | None = None) -> adx.Client:
    """An ADX client whose cluster answers each query with the fixture recorded for it (by its exact text), or with the
    fixture `answers` names for that text."""
    by_csl = {(fx := fixture(f"adx/{n}"))["request"]["csl"]: fx for n in names}
    by_csl |= {csl: fixture(f"adx/{n}") for csl, n in (answers or {}).items()}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rest/query", "only the query endpoint, never management"
        assert request.headers.get("accept") == "application/json"
        fx = by_csl.get(json.loads(request.content)["csl"])
        assert fx is not None, f"no recorded response for {request.content!r}"
        return respond(fx)

    return adx.Client("https://help.kusto.windows.net", "Samples", http=httpx.Client(transport=httpx.MockTransport(handle)))


def test_every_fixture_says_where_it_came_from():
    for f in sorted(FIXTURES.rglob("*.json")):
        fx = json.loads(f.read_text())
        assert fx["source"].startswith("https://") and fx["retrieved"] and fx["how"], f.name
        assert "status" in fx and "body" in fx, f.name


def test_adx_reads_the_reference_example_and_a_response_of_four_tables():
    c = adx_replay("hello_world.json", "catalog.json")
    assert c.query('print Test="Hello, World!"') == [{"Text": "Hello, World!"}]  # the reference's one-table example
    cat = list(adx.SeriesCatalog(c).rows())
    assert [r["pi_point"] for r in cat] == ["SITE:FI-101.PV", "SITE:PI-102.PV", "SITE:XS-103.PV"]
    assert cat[2]["units"] == "" and cat[2]["description"] == ""  # null units read as empty
    assert all(r["values_table"] == "PiValues" and r["cluster"] == "https://help.kusto.windows.net" for r in cat)


def test_adx_values_skip_empty_bins_and_non_finite_reals():
    from datetime import datetime

    p = adx.Provider(adx_replay("values.json"))
    got = p.series({"adx_series_id": "5b0e6f8c-1d2a-4e3b-9c4d-5e6f7a8b9c0d", "adx_table": "PiValues"},
                   datetime(2026, 11, 1), datetime(2026, 11, 1, 2), 30)
    assert got == [("2026-11-01T00:00", 1520.25), ("2026-11-01T00:30", 1519.5), ("2026-11-01T02:00", 1522.0)]


def test_adx_an_empty_window_is_no_values_not_an_error():
    from datetime import datetime

    p = adx.Provider(adx_replay("empty.json"))
    assert p.series({"adx_series_id": "5b0e6f8c-1d2a-4e3b-9c4d-5e6f7a8b9c0d", "adx_table": "PiValues"},
                    datetime(2020, 1, 1), datetime(2020, 1, 1, 2), 30) == []


def test_adx_a_query_that_fails_after_the_200_raises_rather_than_return_part_of_the_result():
    fx = fixture("adx/partial_failure.json")
    with pytest.raises(adx.AdxError, match="failed partway: .*E_QUERY_RESULT_SET_TOO_LARGE") as e:
        adx_replay("partial_failure.json").query(fx["request"]["csl"])
    assert e.value.status == 0


def test_adx_a_kql_error_names_what_the_cluster_said():
    with pytest.raises(adx.AdxError, match=r"\(400\): Request is invalid and cannot be processed: Semantic error: "
                                           r"SEM0100: 'table' operator: Failed to resolve table expression named 'aaa'"):
        adx_replay("error_semantic.json").query("aaa")


@pytest.mark.parametrize("name, status, words", [
    ("throttled.json", 429, "the server is throttling requests (429): wait and test again; Fuzer does not retry"),
    ("unauthorized.json", 401, "the server refused the credential (401)"),
])
def test_adx_throttled_and_refused_requests_fail_in_plain_words(name, status, words):
    c = adx_replay(name)
    fx = fixture(f"adx/{name}")
    with pytest.raises(adx.AdxError) as e:
        c.query(fx["request"]["csl"])
    assert e.value.status == status
    assert connections.plain(e.value).startswith(words)


@pytest.mark.parametrize("answers, step", [
    ({"print ok = 1": "unauthorized.json"}, "authenticate"),
    ({"print ok = 1": "hello_world.json", "SeriesCatalog | take 5": "throttled.json"}, "read"),
])
def test_adx_connection_test_names_the_failing_step(monkeypatch, answers, step):
    """`fuzer sources test`: a refused token fails at authenticate, a throttled catalog read at read, in plain words."""
    monkeypatch.setattr(connections.Adx, "client", lambda self: adx_replay(answers=answers))
    steps = connections.Adx({"url": "https://help.kusto.windows.net", "database": "Samples"}, {}).test(Path("."))
    assert [s["step"] for s in steps if not s["ok"]] == [step], steps
    assert steps[-1]["detail"].startswith("the server ") and all(s["ok"] for s in steps[:-1])


def test_the_fake_cluster_answers_in_the_same_shape(okan_data):
    """The fake historian sends the four v1 tables with the column types ADX names, so the connector reads it and the
    recordings the same way (R.2b found it sent one table, with ColumnType 'double' and 'int64')."""
    from fastapi.testclient import TestClient

    from fakeplant import historian

    server = TestClient(historian.app(str(okan_data)))
    body = server.post("/v1/rest/query", json={"db": "OKAN", "csl": "SeriesCatalog | take 1"}).json()
    real = fixture("adx/catalog.json")["body"]
    assert [t["TableName"] for t in body["Tables"]] == [t["TableName"] for t in real["Tables"]]
    assert [r[1] for r in body["Tables"][3]["Rows"]] == [r[1] for r in real["Tables"][3]["Rows"]]
    assert [c["ColumnName"] for c in body["Tables"][2]["Columns"]] == [c["ColumnName"] for c in real["Tables"][2]["Columns"]]
    vals = server.post("/v1/rest/query", json={"db": "OKAN", "csl": (
        "PiValues | where SeriesId == 'x' and Timestamp between (datetime(2024-01-01T00:00) .. datetime(2024-01-01T01:00)) "
        "| summarize Value = avg(Value), n = count() by bin(Timestamp, 30m)")}).json()["Tables"][0]["Columns"]
    assert [(c["DataType"], c["ColumnType"]) for c in vals] == [("DateTime", "datetime"), ("Double", "real"), ("Int64", "long")]
    assert server.post("/v1/rest/query", json={"db": "OKAN", "csl": ".show tables"}).status_code == 400
    assert server.post("/v1/rest/query", json={"db": "OKAN", "csl": "print ok = 1"}).json()["Tables"][0]["Rows"] == [[1]]
