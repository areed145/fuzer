"""The same documents under another folder name aren't offered (and loaded) a second time (#458)."""

import json

from fuzer.sources import connectors


def test_a_copied_document_folder_is_not_offered_again(tmp_path):
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "sop-1.md").write_text("# SOP 1\n\nStart the pump.\n")
    (tmp_path / "documents.json").write_text(json.dumps([{"doc_number": "SOP-1", "revision": "A",
                                                         "file": "documents/sop-1.md"}]))
    (tmp_path / "docs_copy").mkdir()
    (tmp_path / "docs_copy" / "SOP one.md").write_text("# SOP 1\n\nStart the pump.\n")  # same content, renamed
    (tmp_path / "manuals").mkdir()
    (tmp_path / "manuals" / "k-2100.md").write_text("# K-2100 manual\n\nOil level.\n")
    (tmp_path / "manuals_backup").mkdir()
    (tmp_path / "manuals_backup" / "k-2100.md").write_text("# K-2100 manual\n\nOil level.\n")
    (tmp_path / "wells.csv").write_text("well\nA-1\n")

    assert connectors.datasets(tmp_path) == ["manuals", "wells"]


def test_a_document_saved_twice_in_one_folder_is_read_once(tmp_path):
    (tmp_path / "a.md").write_text("# Guide\n\n## Step\nDo it.\n")
    (tmp_path / "a (copy).md").write_text("# Guide\n\n## Step\nDo it.\n")
    rows = list(connectors.DocumentFolder(tmp_path).rows())
    assert {r["file"] for r in rows} == {"a (copy).md"} or {r["file"] for r in rows} == {"a.md"}
    assert sum(r["section"] == 0 for r in rows) == 1
