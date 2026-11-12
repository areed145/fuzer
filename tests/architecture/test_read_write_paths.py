"""R.7 design note (docs/ot-security.md): collectors only read. The one POST each live collector makes is a read: PI
Web API's /batch with GET sub-requests only, ADX's query endpoint (never /v1/rest/mgmt). A new write call in the
collectors fails here, so it has to go through the approved write-back queue (P5.7) instead."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
SOURCES = Path(__file__).parents[2] / "src/fuzer/sources"
WRITE = re.compile(r"\.(post|put|patch|delete)\(")
# The reads that travel as POST, each by the file and the text of its call.
READS_BY_POST = {
    "pi_web_api.py": 'self.http.post(f"{self.url}/batch",',
    "adx.py": 'self.http.post(f"{self.url}/v1/rest/query", json={"db": self.database, "csl": csl})',
}


def test_collectors_make_no_write_calls():
    calls = {(p.name, line.strip()) for p in SOURCES.rglob("*.py") for line in p.read_text().splitlines()
             if WRITE.search(line) and "http" in line}
    assert calls == {(f, c if c.startswith("r = ") else f"r = {c}") for f, c in READS_BY_POST.items()}, (
        "a collector writes to a source system: send it through the write-back queue (docs/ot-security.md)")


def test_the_pi_batch_carries_only_gets_and_adx_never_calls_management():
    pi = (SOURCES / "pi_web_api.py").read_text()
    assert '{"Method": "GET", "Resource": res}' in pi and not re.search(r'"Method": "(POST|PUT|PATCH|DELETE)"', pi)
    assert "/v1/rest/mgmt" not in (SOURCES / "adx.py").read_text()
