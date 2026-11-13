"""A DEXPI sheet and its scanned twin (#63): Kestrel's flare and sales metering sheet ships both as DEXPI and as a
drawing. With the scan read too, the two are compared: where they agree nothing is asked, and each tag or line one
shows and the other doesn't becomes a "drawing disagrees" question. The scan's reading is kept apart, so it never
adds links of its own over the exact file's."""

from psycopg.types.json import Jsonb

from fuzer.core import bundles
from fuzer.sources import llm_pids

SHEET = "KRGP-H1-PID-001"


def _sheets(conn):
    return {r["source_key"]: r["payload"] for r in conn.execute(
        "select source_key, payload from source_record where source = 'pid' and record_type = 'sheet'")}


def _store(conn, sha, result):
    conn.execute("insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result) "
                 "values (%s, %s, %s, 'fake', %s, %s) on conflict (source, source_key) do update "
                 "set text_hash = excluded.text_hash, result = excluded.result",
                 (llm_pids.TWIN, SHEET, sha, llm_pids.PROMPT_VERSION, Jsonb(result)))


def test_a_dexpi_sheet_is_compared_with_its_scan_and_each_difference_is_asked(kestrel_db):
    conn, pack = kestrel_db, bundles.read(bundles.find("kestrel"))["pack"]
    sheets = _sheets(conn)
    p = sheets[SHEET]
    assert p["dexpi"] and p["image_sha256"]
    assert llm_pids.run(conn, pack, dry_run=True)["sheets"] == 0  # Kestrel's sheets are all smart: no scan is read
    assert llm_pids.run(conn, pack, dry_run=True, twins=True)["sheets"] == 1

    twin = {"mentions": [{"key": t} for t in p["tags"]],
            "connections": [{"from": c["from"], "to": c["to"]} for c in p["connections"]]}
    _store(conn, p["image_sha256"], twin)
    assert llm_pids.twin_differences(conn, sheets, pack)[SHEET]["same"]
    assert not [q for _, q in llm_pids.mismatches(conn, sheets, [], pack) if q["check"] == "twin"]

    missed = p["tags"][-1]
    _store(conn, p["image_sha256"], {**twin, "mentions": twin["mentions"][:-1]})
    asked = [q for _, q in llm_pids.mismatches(conn, sheets, [], pack) if q["check"] == "twin"]
    assert [q["tag"] for q in asked] == [missed]
    assert asked[0]["question"] == f"{missed} is on the DEXPI file of {SHEET} but the scan doesn't show it. Which is right?"
    assert asked[0]["finding"]  # shown with its evidence, like any other drawing disagreement

    assert SHEET not in llm_pids._readings(conn, sheets)  # never read as the sheet's own reading, so no links from it
    _store(conn, "an older scan", twin)
    assert SHEET not in llm_pids.twin_differences(conn, sheets, pack)  # a reading of a changed image doesn't count
    conn.rollback()
