"""Teams troubleshooting threads (P5.8, #22) on a mock of Microsoft Graph, never a real tenant (#743): a thread is
kept as a summary and a link, with people's names replaced by their job titles; it lands on the assets it names; and a
thread still open about an asset raises one routed case that clears when the thread says it was fixed."""

import json
from pathlib import Path

import httpx
import pytest
from psycopg.conninfo import make_conninfo

from fuzer.core import bundles, db, settings
from fuzer.core import pack as packs
from fuzer.sources import teams

KESTREL = Path(__file__).resolve().parents[2] / "fakeplant" / "bundles" / "kestrel"
EXPORT = json.loads((KESTREL / "exports" / "teams_threads.json").read_text())
NAMES = ("Marta", "Quinn", "Owen", "Achebe", "Priya", "Lindqvist", "Tomas", "Herrera", "Ken", "Mbeki")
NAME = "kestrel_teams"


def test_a_thread_keeps_its_summary_not_the_chat_and_no_ones_name():
    got = {t["thread_id"]: t for t in teams.threads(EXPORT)}
    vib = got["1731000000001"]
    assert vib["state"] == "resolved" and vib["fix"].startswith("Found a loose mounting bolt")
    assert vib["tried"][1].startswith("Alignment within spec")  # "not fixed" there is not the fix
    assert vib["participants"] == ["Field Operator", "Rotating Equipment Engineer"]
    assert vib["tried"][0].startswith("Field Operator, can you check")  # the @mention, as a job title
    assert "[phone]" in vib["problem"] and "[email]" in vib["tried"][0]
    assert vib["web_url"].startswith("https://teams.microsoft.com/") and vib["channel"] == "KRGP Operations"
    assert got["1731000000201"]["state"] == "open"  # "Still not fixed"
    kept = json.dumps(list(got.values()))
    assert not any(n in kept for n in NAMES) and "body" not in kept and "@kestrel.example" not in kept


def test_replies_read_alone_deleted_and_system_messages_are_left_out():
    root = {**EXPORT["value"][1], "replies": [*EXPORT["value"][1]["replies"],
                                              {"id": "x", "messageType": "systemEventMessage", "body": {"content": "fixed"}},
                                              {"id": "y", "deletedDateTime": "2026-11-01", "body": {"content": "resolved"}}]}
    out = list(teams.threads({"value": [root, {**root, "id": "r", "replyToId": "1"}], "users": EXPORT["users"]}))
    assert len(out) == 1 and out[0]["replies"] == 2


def test_the_live_client_reads_only_the_listed_channels_in_the_same_shape():
    """Graph mocked: paging by @odata.nextLink, the token as a bearer header, job titles from /users."""
    asked = []
    root = EXPORT["value"][0]

    def graph(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        assert req.headers["Authorization"] == "Bearer t0ken"
        path = req.url.path
        if path.endswith("/channels/ops"):
            return httpx.Response(200, json={"displayName": "KRGP Operations"})
        if path.endswith("/channels/ops/messages") and "skip" not in str(req.url):
            return httpx.Response(200, json={"value": [root], "@odata.nextLink": f"{teams.GRAPH}/teams/kr/channels/ops/messages?skip=1"})
        if path.endswith("/channels/ops/messages"):
            return httpx.Response(200, json={"value": [EXPORT["value"][1]]})
        uid = path.rsplit("/", 1)[-1]
        title = {u["id"]: u["jobTitle"] for u in EXPORT["users"]}.get(uid)
        return httpx.Response(200 if title else 404, json={"id": uid, "jobTitle": title})

    got = teams.fetch("kr", ["ops"], "t0ken", httpx.Client(transport=httpx.MockTransport(graph)))
    assert not any("/channels/rel" in u for u in asked)
    live = [t["thread_id"] for t in teams.threads(got)]
    assert live == ["1731000000001", "1731000000101"]
    assert next(teams.threads(got))["participants"] == ["Field Operator", "Rotating Equipment Engineer"]


@pytest.fixture(scope="module")
def kestrel():
    from fuzer.ops import pipeline
    from fuzer.sources import ingest

    mp = pytest.MonkeyPatch()
    with db.connect(autocommit=True, url=make_conninfo(db.database_url(), dbname="postgres")) as a:
        a.execute(f'drop database if exists "{NAME}" with (force)')
        a.execute(f'create database "{NAME}"')
    url = make_conninfo(db.database_url(), dbname=NAME)
    mp.setenv("FUZER_DATABASE_URL", url)
    mp.delenv("FUZER_PROJECT", raising=False)
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()
    with db.connect(url=url) as conn:
        db.init_schema(conn)
        first = bundles.init(conn, KESTREL, by="test")
        mp.setenv("FUZER_DATA_DIR", str(first["data"]))
        pack = packs.current_pack(conn)
        ingest.ingest(conn, Path(first["data"]))
        pipeline.rebuild(conn, pack)
        conn.commit()
        # the IOC installs the built-in Teams pack from Sources: its work type and the open-thread rule (P5.8c)
        from fuzer.sources import packs as source_packs
        from fuzer.sources import workflows
        source_packs.install(conn, source_packs.builtin("teams"), "test", data_dir=first["data"])
        workflows.after_load(conn, ["teams_threads"])  # its rules run with the next load of the threads
        conn.commit()
        yield conn, pack
    mp.undo()
    packs.clear_cache()
    settings._cache.clear()
    settings._first.clear()


def _conversations(conn) -> dict:
    return {r["label"]: r for r in conn.execute(
        """
        select l.label, (select jsonb_object_agg(g.field, g.value) from golden g where g.fuzer_id = e.fuzer_id) as f,
               (select array_agg(tl.label order by tl.label) from edge m join entity_label tl on tl.fuzer_id = m.dst
                where m.src = e.fuzer_id and m.rel = 'mentions') as about
        from entity e join entity_label l using (fuzer_id) where e.kind = 'conversation'
        """)}


def test_threads_land_on_the_assets_they_name_with_a_link_to_teams(kestrel):
    conn, _ = kestrel
    conv = _conversations(conn)
    assert len(conv) == 4
    vib = next(c for c in conv.values() if (c["f"] or {}).get("subject", "").startswith("B1-K-2001"))
    assert "B1-K-2001" in " ".join(vib["about"] or [])
    assert vib["f"]["web_url"].startswith("https://teams.microsoft.com/") and vib["f"]["state"] == "resolved"
    stored = json.dumps([dict(r) for r in conn.execute(
        "select payload from source_record where source = 'teams'")], default=str)
    assert not any(n in stored for n in NAMES)  # what Fuzer holds of the chat names no one


def test_an_open_thread_about_an_asset_raises_one_routed_case_that_clears_when_fixed(kestrel):
    from fuzer.sources import workflows

    conn, _ = kestrel
    rows = conn.execute("select * from exception_record where type = 'conversation_report' and state <> 'cleared'").fetchall()
    assert len(rows) == 1  # the pump; the gate-code question names no asset, the two fixed threads are closed
    x = rows[0]
    assert x["title"] == "Reported in Teams: Glycol pump C1-P3002B won't prime after its service"
    labels = [r["label"] for r in conn.execute("select label from entity_label where fuzer_id = any(%s)", (list(x["subjects"]),))]
    assert any("P-3002B" in lb or "P3002B" in lb for lb in labels), labels
    items = {i["label"]: i for i in x["evidence"]["finding"]["items"]}
    assert items["Tried so far"]["value"].startswith("Bleed the suction")
    assert items["In teams"]["kind"] == "link" and items["In teams"]["value"].startswith("https://teams.microsoft.com/")
    assert x["item_id"] is not None  # routed: a case on the IOC's queue

    # the thread says it was fixed: the next load clears it
    conn.execute("""update source_record set payload = payload || '{"state": "resolved", "fix": "Swapped the check valve: primed."}'
                    where source = 'teams' and source_key = '1731000000201'""")
    assert workflows.evaluate(conn, ["teams_threads"])["teams/open_thread"]["cleared"] == 1
    conn.commit()



def test_an_assets_page_reads_its_threads_from_the_register(kestrel):
    """P5.8b (#745): what the asset page shows, from the API."""
    from fuzer.api.entities import entity_conversations

    conn, _ = kestrel
    tag = conn.execute("select fuzer_id from entity_label where label like '%%K-2001%%' or label like '%%K2001%%' "
                       "order by length(label) limit 1").fetchone()["fuzer_id"]
    [vib] = entity_conversations(tag, conn=conn)
    assert vib["subject"] == "B1-K-2001 high vibration after restart" and vib["state"] == "resolved"
    assert vib["fix"].endswith("That did it.") and vib["tried"][1].startswith("Alignment within spec")
    assert vib["web_url"] == "https://teams.microsoft.com/l/message/19:kr-ops@thread.tacv2/1731000000001"
    conn.rollback()
