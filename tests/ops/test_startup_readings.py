"""Startup enrichment uses the readers' content caches, never a per-process completion flag."""

import pytest
from psycopg.types.json import Jsonb
from test_llm_links import FakeClaude

from fuzer.core import db, llm
from fuzer.core.pack import load_pack
from fuzer.graph import llm_links
from fuzer.ops import pipeline
from fuzer.sources import llm_curves, llm_docs, llm_pids

PACK = load_pack("okan")


def _skip_other_readers(monkeypatch):
    for reader, key in [(llm_pids, "sheets"), (llm_docs, "sections"), (llm_curves, "datasheets")]:
        monkeypatch.setattr(reader, "run", lambda conn, pack, *, dry_run, key=key: {key: 0})
    monkeypatch.setattr(llm_curves, "queue_reviews", lambda conn, pack: 0)


def test_startup_reads_once_and_only_rereads_changed_text(okan_db, monkeypatch):
    conn = okan_db.conn
    conn.execute("delete from source_record where source = 'oplog' or "
                 "(source = 'ec' and record_type in ('daily_report', 'downtime'))")
    conn.execute("insert into source_record (source, record_type, source_key, payload) "
                 "values ('oplog', 'entry', 'BOOT-1', %s)",
                 (Jsonb({"text": "20-K-2200B tripped on high vibration", "timestamp": "2026-01-01T12:00:00"}),))
    conn.commit()
    fake = FakeClaude()
    create = fake.beta.messages.create
    scope = db.Scope("okan")

    def scoped_create(**kwargs):
        assert db.scope() == scope
        return create(**kwargs)

    fake.beta.messages.create = scoped_create
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "client", lambda: fake)
    _skip_other_readers(monkeypatch)
    rebuild = pipeline.rebuild
    builds = []

    def build(conn, pack):
        builds.append(True)
        return rebuild(conn, pack)

    monkeypatch.setattr(pipeline, "rebuild", build)
    with db.use(scope):
        pipeline.read_pending(conn, PACK, lambda msg: None)
    assert len(fake.requests) == 1 and len(builds) == 1
    assert conn.execute("select 1 from edge where rel = 'mentions' and 'claude' = any(sources)").fetchone()
    assert not pipeline.readings_newer(conn)

    with db.use(scope):
        pipeline.read_pending(conn, PACK, lambda msg: None)
    assert len(fake.requests) == 1 and len(builds) == 1

    conn.execute("update source_record set payload = payload || %s "
                 "where source = 'oplog' and source_key = 'BOOT-1'",
                 (Jsonb({"text": "20-K-2200B restarted after maintenance"}),))
    conn.commit()
    with db.use(scope):
        pipeline.read_pending(conn, PACK, lambda msg: None)
    assert len(fake.requests) == 2 and len(builds) == 2
    conn.commit()


@pytest.mark.parametrize("pending", [0, 1])
def test_keyless_startup_applies_cached_readings_without_model_calls(monkeypatch, pending):
    builds, reports, reviews = [], [], []
    _skip_other_readers(monkeypatch)
    monkeypatch.setattr(llm_curves, "queue_reviews", lambda conn, pack: reviews.append(pack))

    def read(conn, pack, *, dry_run):
        assert dry_run
        return {"entries": pending}

    monkeypatch.setattr(llm_links, "run", read)
    monkeypatch.setattr(llm, "configured", lambda: False)
    monkeypatch.setattr(pipeline, "readings_newer", lambda conn: True)
    monkeypatch.setattr(pipeline, "rebuild", lambda conn, pack: builds.append(pack))
    pipeline.read_pending(None, PACK, reports.append)
    assert builds == [PACK]
    assert reviews == [PACK]
    assert any("Rebuilding" in msg for msg in reports)
    assert any("Configure the provider API key" in msg for msg in reports) == bool(pending)


def test_startup_recovers_datasheet_reviews_after_interrupted_read(okan_db, monkeypatch):
    conn = okan_db.conn
    row = conn.execute("select source_key, payload from source_record where source = 'edms' "
                       "and record_type = 'document' and payload->>'doc_type' = 'datasheet' limit 1").fetchone()
    conn.execute("insert into llm_extraction (source, source_key, text_hash, model, prompt_version, result) "
                 "values (%s, %s, %s, %s, %s, '{}')",
                 (llm_curves.CACHE, row["source_key"], row["payload"]["file_sha256"][:16],
                  llm.DEFAULT_MODEL, llm_curves.PROMPT_VERSION))
    conn.commit()
    queue_reviews = llm_curves.queue_reviews
    _skip_other_readers(monkeypatch)
    monkeypatch.setattr(llm_curves, "queue_reviews", queue_reviews)
    monkeypatch.setattr(llm_links, "run", lambda conn, pack, *, dry_run: {"entries": 0})
    monkeypatch.setattr(pipeline, "readings_newer", lambda conn: False)
    monkeypatch.setattr(llm, "configured", lambda: False)
    pipeline.read_pending(conn, PACK, lambda msg: None)
    assert conn.execute("select 1 from review_item where kind = 'curve' and subject = %s",
                        (row["source_key"],)).fetchone()


def test_startup_reports_reader_failure_without_retry(monkeypatch):
    calls, reports = [], []
    _skip_other_readers(monkeypatch)
    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(pipeline, "readings_newer", lambda conn: False)

    def read(conn, pack, *, dry_run=False):
        calls.append(dry_run)
        return {"entries": 1, "errors": ["request rejected"]}

    monkeypatch.setattr(llm_links, "run", read)
    pipeline.read_pending(None, PACK, reports.append)
    assert calls == [True, False]
    assert any("Inspect recorded readings before a deliberate retry" in msg for msg in reports)
