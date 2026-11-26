"""Free-text messages become matched work (P2.8, #1254). The recorded Okan messages (fakeplant/messages.py: mail, radio
calls, Teams posts and field notes) are read against Okan's register and scored: which entity each case is about, that
the ones naming nothing for certain reach the data steward instead, and that "thanks" opens nothing. A steward's answer
makes the case; a field operator's note from the phone goes through the same reader and becomes routed work."""

import pytest
from fastapi.testclient import TestClient

from fakeplant import messages as recorded
from fuzer.core.pack import load_pack
from fuzer.graph import review
from fuzer.graph.llm_links import Register
from fuzer.ops import exceptions as engine
from fuzer.ops import messages

pytestmark = pytest.mark.sequence

KNOWN_MISSES = {"MSG-0023"}  # "lift gas header" is the pack's name for GGCP's header; the call is about ST1's


def _register() -> Register:
    reg = Register()
    for key in ("20-K-2200B", "10-PT-2001", "20-PT-2001", "20-V-2001"):
        reg.known[("tag", key)] = ""
        reg.by_short.setdefault(key.split("-", 1)[1], []).append(key)
    reg.known[("area", "OKAN-72")] = ""
    return reg


def test_the_reader_needs_no_database_and_no_model():
    pack, reg = load_pack("okan"), _register()
    assert messages.read("20-K-2200B vibration up", pack, reg).refs == ["tag:20-K-2200B"]
    assert messages.read("K-2200B vibration up", pack, reg).refs == ["tag:20-K-2200B"]  # one area has it
    short = messages.read("PT-2001 erratic", pack, reg)
    assert short.outcome == "steward" and short.candidates == ["tag:10-PT-2001", "tag:20-PT-2001"]
    assert messages.read("inlet separator carrying over", pack, reg).refs == ["tag:20-V-2001"]  # the pack's alias
    assert messages.read("20-P-9999 noisy", pack, reg).why == "“20-P-9999” is not in the register"
    assert messages.read("crane on J12 down", pack, reg).candidates == ["area:OKAN-72"]  # only a place
    assert messages.read("Thanks, received.", pack, reg).outcome == "handled"
    assert messages.read("noted", pack, reg, subject="Automatic reply: Out of office").outcome == "handled"
    # only says what was done, with nothing wrong or asked in it: a log entry, kept with what it names (#1363)
    logged = messages.read("Swapped 20-K-2200B to standby, isolated and drained.", pack, reg)
    assert (logged.outcome, logged.refs, logged.why) == ("handled", ["tag:20-K-2200B"], messages.LOG_ONLY)
    for news in ("20-K-2200B tripped, restarted and running", "Swapped 20-K-2200B, can you check the seal?",
                 "20-K-2200B isolated, gas smell at the flange"):
        assert messages.read(news, pack, reg).outcome == "case", news
    # an alias alone is 0.8 sure: a deployment that wants more asks the steward
    assert messages.read("inlet separator carrying over", pack, reg, min_confidence=0.9).outcome == "steward"


@pytest.fixture(scope="module")
def read(okan_db):
    conn = okan_db.conn
    assert conn.execute("select count(*) as n from source_record where source = 'messages'").fetchone()["n"] == len(
        recorded.MESSAGES)  # the export loaded with Okan's others
    out = messages.read_new(conn, load_pack("okan"))
    conn.commit()
    return conn, out


def _id(conn, ref: str) -> str:
    ids, _ = engine.match(conn, [ref])
    assert len(ids) == 1, ref
    return ids[0]


def test_the_recorded_okan_messages_are_matched_and_scored(read):
    conn, out = read
    got = {r["id"]: r for r in conn.execute("select * from message where id like 'MSG-%'")}
    assert set(got) == set(recorded.ANSWERS)
    cases = [i for i, r in got.items() if r["outcome"] == "case"]
    right = [i for i in cases if recorded.ANSWERS[i][0] == "case"
             and _id(conn, got[i]["refs"][0]) == _id(conn, recorded.ANSWERS[i][1])]
    assert set(cases) - set(right) == KNOWN_MISSES
    assert (len(right), len(cases)) == (10, 11)  # precision 10/11 = 0.91, as docs/eval-results.md records
    wanted = {i for i, a in recorded.ANSWERS.items() if a[0] == "case"}
    assert set(right) == wanted - KNOWN_MISSES  # recall 10/11 too
    for kind in ("steward", "handled"):
        assert {i for i, r in got.items() if r["outcome"] == kind} == {
            i for i, a in recorded.ANSWERS.items() if a[0] == kind}
    assert out == {"case": 11, "steward": 8, "handled": 4, "refused": 0}


def test_a_case_shows_the_message_as_its_source(read):
    conn, _ = read
    from fuzer.ops import worklist

    ex = conn.execute("select * from exception_record where type = 'message_report' and key = 'MSG-0002'").fetchone()
    assert ex["source"] == "messages" and ex["subjects"] == [_id(conn, "tag:10-P-1001B")]
    assert ex["item_id"]  # routed: a case in the ops lead's queue, or the open case about the pump it joined
    m = ex["evidence"]["message"]
    assert (m["channel_label"], m["sender"]) == ("Radio call", "PP CRO") and m["text"].startswith("Field op reports")
    labels = {i["label"]: i["value"] for i in ex["evidence"]["finding"]["items"]}
    assert labels["Channel"] == "Radio call" and labels["From"] == "PP CRO" and "10-P-1001B" in labels["Matched by"]
    rows = worklist.rows(conn, kind="message_report")["rows"]
    row = next(r for r in rows if r["key"] == "MSG-0002" or any(j["key"] == "MSG-0002" for j in r.get("joined") or []))
    assert row["kind_label"] == "Reported in a message" or row["joined"]


def test_a_case_is_titled_by_a_short_summary_of_its_message():
    assert messages.title("Morning IOC, 20-K-2200B vibration has crept up since the night shift, about 6 mm/s on the "
                          "local gauge now. Can someone have it looked at before it trips again?") == \
        "20-K-2200B vibration has crept up since the night shift"
    assert messages.title("Swapped 10-P-1001A to 10-P-1001C for the seal job, A is isolated and drained.") == \
        "Swapped 10-P-1001A to 10-P-1001C for the seal job"
    assert messages.title("pump B noisy") == "Pump B noisy"


def test_a_message_opens_its_own_case_and_never_joins_an_older_one(read):
    """A field note about a machine with a case open on it already (a trip two days ago) is its own case, titled by
    what it says, not a second report under the trip's title (#1363, tour N1)."""
    conn, _ = read
    pack = load_pack("okan")
    first = messages.receive(conn, pack, "femi", "20-K-2300A tripped on high discharge temp, restarted, running")
    then = messages.receive(conn, pack, "femi", "20-K-2300A lube oil pressure dropping, 28 psig, was 35")
    assert first["outcome"] == then["outcome"] == "case" and first["item_id"] and then["item_id"]
    assert then["item_id"] != first["item_id"]
    ex = conn.execute("select * from exception_record where id = %s", (then["exception_id"],)).fetchone()
    assert ex["joined_to"] is None and ex["title"] == "20-K-2300A lube oil pressure dropping, 28 psig, was 35"
    assert conn.execute("select title from work_item where id = %s", (then["item_id"],)).fetchone()["title"] == ex["title"]
    assert then["said"] == f"Sent to the IOC as case {then['item_id']} about 20-K-2300A."  # the phone links it
    conn.rollback()


def test_a_message_case_closes_as_log_only_and_no_other_case_does(read):
    conn, _ = read
    from fuzer.ops import disposition

    pack = load_pack("okan")
    note = messages.receive(conn, pack, "femi", "Put 20-K-2300A on load, running fine")
    assert note["outcome"] == "case"  # nothing in the reader says it is only news: the IOC closes it as log only
    form = disposition.form(conn, pack, note["item_id"])
    assert form["message"] and form["vocabulary"]["dispositions"]["log_only"] == "Log only, no action"
    row = disposition.record(conn, pack, note["item_id"], "ana", "log_only")
    assert row["disposition"] == "log_only" and row["recheck_at"] is None
    other = conn.execute("select item_id from exception_record where type <> 'message_report' and item_id is not null "
                         "and state = 'open' limit 1").fetchone()["item_id"]
    assert not disposition.form(conn, pack, other)["message"]
    with pytest.raises(ValueError, match="log only is for a message"):
        disposition.record(conn, pack, other, "ana", "log_only")
    conn.rollback()


def test_noise_opens_nothing_and_reading_again_reads_nothing_new(read):
    conn, _ = read
    for i in ("MSG-0010", "MSG-0013", "MSG-0019", "MSG-0021"):
        m = conn.execute("select * from message where id = %s", (i,)).fetchone()
        assert m["outcome"] == "handled" and m["exception_id"] is None and m["review_item_id"] is None
        assert not conn.execute("select 1 from exception_record where key = %s", (i,)).fetchone()
    assert messages.read_new(conn, load_pack("okan")) == {"case": 0, "steward": 0, "handled": 0, "refused": 0}
    conn.commit()


def test_a_steward_question_carries_the_message_and_its_answer_makes_the_case(read):
    conn, _ = read
    q = conn.execute("select * from review_item where kind = 'message' and subject = 'MSG-0007'").fetchone()
    assert q["payload"]["message"]["text"].startswith("PT-2001") and len(q["payload"]["candidates"]) == 5
    work = conn.execute("select * from exception_record where type = 'message_to_match' and key = 'message:MSG-0007' "
                        "and state = 'open'").fetchone()
    assert work and work["title"].startswith("Which entity is this about? Radio call from ST1 CRO")

    chosen = _id(conn, "tag:30-PT-2001")  # the ST1 CRO called it: ST1's separator
    review.decide(conn, q["id"], "choose", chosen, "ana")
    with pytest.raises(review.ReviewError):
        review.decide(conn, conn.execute("select id from review_item where kind = 'message' and subject = 'MSG-0016'")
                      .fetchone()["id"], "accept", None, "ana")
    noise = conn.execute("select id from review_item where kind = 'message' and subject = 'MSG-0017'").fetchone()["id"]
    review.decide(conn, noise, "reject", None, "ana")
    assert messages.sync_answers(conn) == {"case": 1, "handled": 1}
    conn.commit()
    m = conn.execute("select * from message where id = 'MSG-0007'").fetchone()
    assert m["outcome"] == "case" and m["refs"] == [chosen] and m["why"] == "chosen by ana"
    ex = conn.execute("select * from exception_record where id = %s", (m["exception_id"],)).fetchone()
    assert ex["type"] == "message_report" and ex["subjects"] == [chosen] and ex["item_id"] and ex["joined_to"] is None
    answer = messages.answer_of(conn, q["id"])  # the steward's confirmation links the case and the tag (#1382)
    assert answer["case"] == ex["item_id"] and answer["about"] == [{"fuzer_id": chosen, "label": "30-PT-2001"}]
    # who chose it is kept as their actor, for the page to name (#1383): never "Matched by chosen by user:demo"
    items = {i["label"]: i["value"] for i in ex["evidence"]["finding"]["items"]}
    assert items["Chosen by"] == "ana" and "Matched by" not in items
    assert conn.execute("select state from exception_record where type = 'message_to_match' and key = "
                        "'message:MSG-0007'").fetchone()["state"] == "cleared"  # the steward's case closed
    assert conn.execute("select outcome from message where id = 'MSG-0017'").fetchone()["outcome"] == "handled"
    # each steward case closes as what the answer was (tour D11): a choice "Matched", no work "No work needed"
    closed = {r["key"]: r["disposition"] for r in conn.execute(
        "select x.key, d.disposition from exception_record x join case_disposition d on d.item_id = x.item_id "
        "where x.type = 'message_to_match' and x.key in ('message:MSG-0007', 'message:MSG-0017')")}
    assert closed == {"message:MSG-0007": "matched", "message:MSG-0017": "no_work"}, closed


def test_a_message_is_matched_only_to_what_work_can_be_about(read):
    """The steward's choices for a message are the entities work can be about, each with its kind, description and
    place; a log entry or a work order is refused on the server (#1368, tour N9: a log entry was accepted as the
    asset)."""
    conn, _ = read
    from fuzer import api
    from fuzer.graph import queries
    from fuzer.graph.hierarchy import WORK_KINDS

    qid = conn.execute("select id from review_item where kind = 'message' and subject = 'MSG-0020'").fetchone()["id"]
    [item] = queries.reviews(conn, kind="message", item=qid)
    cands = item["payload"]["candidates"]
    assert len(cands) == 5 and item["payload"]["kinds"] == list(WORK_KINDS)
    assert all(c["kind"] == "tag" and c["label"].endswith("V-2002") and c["parent_label"] for c in cands), cands
    assert len({c["parent_label"] for c in cands}) == 5  # five areas: the place tells them apart
    place = conn.execute("select id from review_item where kind = 'message' and subject = 'MSG-0009'").fetchone()["id"]
    assert queries.reviews(conn, kind="message", item=place)[0]["payload"]["candidates"] == []  # an area is no asset
    record = conn.execute("select fuzer_id, kind from entity where kind <> all(%s) and kind not in ('area', 'facility') "
                          "and merged_into is null and retired_at is null order by kind limit 1",
                          (list(WORK_KINDS),)).fetchone()
    with pytest.raises(review.ReviewError, match=f"not a {record['kind'].replace('_', ' ')}"):
        review.decide(conn, qid, "choose", record["fuzer_id"], "ana")
    conn.rollback()
    with TestClient(api.app) as client:
        r = client.post(f"/review/{qid}", json={"decision": "choose", "value": record["fuzer_id"], "by": "ana"})
        assert r.status_code == 400 and "work is about a" in r.text
    assert conn.execute("select status from review_item where id = %s", (qid,)).fetchone()["status"] == "open"


def test_a_field_operators_note_from_the_phone_becomes_routed_work(read):
    conn, _ = read
    from fuzer import api

    with TestClient(api.app) as client:
        r = client.post("/messages", json={"by": "femi", "text": "20-K-2100C seal gas vent flow high, smells of gas"})
        assert r.status_code == 200, r.text
        note = r.json()
        assert note["outcome"] == "case" and note["channel"] == "phone" and note["sender"] == "femi"
        assert note["said"] == f"Sent to the IOC as case {note['item_id']} about 20-K-2100C.", note["said"]
        vague = client.post("/messages", json={"by": "femi", "text": "Something rattling near the stairs"}).json()
        assert vague["outcome"] == "steward" and vague["review_item_id"]
        assert vague["said"].startswith("Sent to the IOC. The data steward says which entity")
        assert client.post("/messages", json={"by": "femi", "text": "   "}).status_code == 400
    ex = conn.execute("select * from exception_record where id = %s", (note["exception_id"],)).fetchone()
    assert ex["subjects"] == [_id(conn, "tag:20-K-2100C")] and ex["item_id"]
    assert ex["evidence"]["message"]["channel_label"] == "Field note"
    conn.rollback()


def test_the_reader_eval_scores_what_docs_eval_results_records(read, okan_db):
    """`fuzer eval --messages`: the same reading, scored against the answer key the generator wrote, and the run
    recorded in evals/runs/ (docs/eval-results.md) is what the reader does now."""
    from fuzer.evals import message_eval, runs

    conn, _ = read
    res = message_eval.run(conn, load_pack("okan"), "okan", okan_db.data)
    assert res["passes"] and (res["right"], res["cases"], res["precision"]) == (10, 11, 0.909)
    assert res["wrong"] == ["MSG-0023: case tag:20-PT-3003, not case tag:30-PT-3003"]
    assert res["to_steward"] == res["noise_handled"] == 1.0
    [last] = [r for r in runs.load() if r["kind"] == "messages" and r["bundle"] == "okan"][-1:]
    assert {k: last[k] for k in runs.MESSAGES} == {k: res[k] for k in runs.MESSAGES}


def test_a_wrong_match_is_changed_on_the_message_case(read):
    """#1382 (tour C6): a message matched to the wrong entity couldn't be redone; "Correct it" changed only the
    close-out reason. Change the entities on the case: the event, the case and the message say the new ones, the
    history says the old, and a record or an unknown case is refused."""
    conn, _ = read
    from fuzer import api

    with TestClient(api.app) as client:
        note = client.post("/messages", json={"by": "femi", "text": "20-K-2100C seal oil pot level low"}).json()
        right = [_id(conn, "tag:20-K-2100B"), _id(conn, "tag:20-K-2100A")]
        r = client.post(f"/exceptions/{note['exception_id']}/message/entities", json={"by": "ana", "fuzer_ids": right})
        assert r.status_code == 200, r.text
        assert r.json() == {"case": note["item_id"], "about": [{"fuzer_id": right[0], "label": "20-K-2100B"},
                                                               {"fuzer_id": right[1], "label": "20-K-2100A"}]}
        record = conn.execute("select fuzer_id from entity where kind = 'work_order' limit 1").fetchone()
        if record:
            bad = client.post(f"/exceptions/{note['exception_id']}/message/entities",
                              json={"by": "ana", "fuzer_ids": [record["fuzer_id"]]})
            assert bad.status_code == 400 and "work is about a" in bad.text
        assert client.post("/exceptions/999999/message/entities",
                           json={"by": "ana", "fuzer_ids": right}).status_code == 404
    ex = conn.execute("select subjects from exception_record where id = %s", (note["exception_id"],)).fetchone()
    assert ex["subjects"] == right
    case = conn.execute("select subjects from work_item where id = %s", (note["item_id"],)).fetchone()
    assert case["subjects"] == right
    said = [r["t"] for r in conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = "
                                         "'commented'", (note["item_id"],))]
    assert "Changed what it is about from 20-K-2100C to 20-K-2100B, 20-K-2100A" in said, said
    m = conn.execute("select refs, why from message where exception_id = %s", (note["exception_id"],)).fetchone()
    assert (m["refs"], m["why"]) == (right, "changed by ana")
    conn.rollback()


def test_a_steward_can_say_a_message_is_about_several_entities(read):
    """#1382 (tour C9): "It applies to all three gas turbine drivers" could be matched to only one."""
    conn, _ = read
    qid = conn.execute("select id from review_item where kind = 'message' and status = 'open' "
                       "order by id limit 1").fetchone()["id"]
    three = [_id(conn, f"tag:20-GT-2000{x}") for x in "ABC"]
    review.decide(conn, qid, "choose", three, "ana")
    messages.sync_answers(conn)
    answer = messages.answer_of(conn, qid)
    assert answer["case"] and [a["label"] for a in answer["about"]] == ["20-GT-2000A", "20-GT-2000B", "20-GT-2000C"]
    conn.rollback()


def test_the_picker_search_counts_what_it_does_not_show_and_takes_a_trailing_hyphen(read):
    """#1382 (tour C9): "20-P-" found nothing while "20-P" worked, and the picker stopped at 12 with no "more"."""
    conn, _ = read
    from fuzer.graph import queries

    pack = load_pack("okan")
    typed = queries.search(conn, pack, "20-P-", "tag,equipment", 12, total=True)
    bare = queries.search(conn, pack, "20-P", "tag,equipment", 12, total=True)
    assert typed == bare and len(bare["hits"]) == 12 and bare["total"] > 12
    assert queries.search(conn, pack, "-", None, 5)  # nothing but a separator is still a search


def test_a_steward_choice_kept_before_it_was_a_fact_reads_as_one(read):
    """Tour D8: cases a steward placed before #1383 kept "Matched by: chosen by user:demo", printed on the page as
    "Matched by chosen by user:demo". The schema rewrites them to the "Chosen by" fact every new case carries."""
    from psycopg.types.json import Jsonb

    from fuzer.resources import PACKAGE

    conn, _ = read
    evidence = {"finding": {"v": 1, "origin": "external", "detector": "messages.reader", "items": [
        {"kind": "text", "label": "Message", "value": "Anyone know why train C tripped last night?"},
        {"kind": "text", "label": "Matched by", "value": "chosen by user:demo"}]}}
    ex = conn.execute("insert into exception_record (type, key, title, source, evidence) values "
                      "('message_report', 'MSG-legacy', 'Train C', 'messages', %s) returning id", (Jsonb(evidence),)
                      ).fetchone()["id"]
    sql = (PACKAGE / "ops" / "schema.sql").read_text()
    start = sql.index("update exception_record e set evidence")
    conn.execute(sql[start:sql.index(";", start) + 1])
    items = conn.execute("select evidence from exception_record where id = %s", (ex,)).fetchone()["evidence"]
    assert items["finding"]["items"][1] == {"kind": "text", "label": "Chosen by", "value": "user:demo"}
    assert items["finding"]["items"][0]["label"] == "Message"
    conn.rollback()


def test_what_a_message_case_is_about_reads_equipment_by_its_tag(read):
    """Tour D8: changing a case to three machines wrote "S/N 76031, S/N 80853, S/N 85143" on its history."""
    conn, _ = read
    from fuzer import api

    machines = [r["src"] for r in conn.execute(
        "select x.src from edge x join entity e on e.fuzer_id = x.src join entity_label t on t.fuzer_id = x.dst "
        "where x.rel = 'installed_at' and x.valid_to is null and e.kind = 'equipment' and t.label like '20-GT-2000%%' "
        "order by t.label limit 3")]
    assert machines
    with TestClient(api.app) as client:
        note = client.post("/messages", json={"by": "femi", "text": "20-K-2100C seal oil pot level low"}).json()
        r = client.post(f"/exceptions/{note['exception_id']}/message/entities", json={"by": "ana", "fuzer_ids": machines})
        assert r.status_code == 200, r.text
    about = [a["label"] for a in r.json()["about"]]
    assert all(a.startswith("20-GT-2000") and "S/N" not in a for a in about), about
    said = [x["t"] for x in conn.execute("select data->>'text' as t from work_event where item_id = %s and kind = "
                                         "'commented'", (note["item_id"],))]
    assert any("20-GT-2000" in t for t in said) and not any("S/N" in t for t in said), said
    conn.rollback()
