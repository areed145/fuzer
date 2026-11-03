"""Generated reference pages (#134) match the code: regenerate with `uv run python -m fuzer.reference`."""

import pytest

from fuzer import reference
from fuzer.resources import ROOT


@pytest.mark.parametrize("page", sorted(reference.PAGES))
def test_reference_page_is_current(page):
    assert (ROOT / "docs" / page).read_text() == reference.PAGES[page](), f"docs/{page} is stale"
