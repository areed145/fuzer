"""Read documents into sections (heading, page, text) so they can be linked and searched.

Markdown splits on headings; PDFs split on numbered headings ("5 Troubleshooting", "5.2 Seals") and
keep page numbers; plain text splits on numbered headings. A PDF page with no text layer is flagged
for OCR (a scan) rather than guessed at.
"""

import re
from pathlib import Path

HEADING = re.compile(r"^(\d+(?:\.\d+)*)\s+([A-Z][^\n]{0,80})$")


def sections(path: Path) -> tuple[list[dict], dict]:
    """Sections of one file and facts about it (pages, whether OCR is needed)."""
    suffix = path.suffix.lower()
    if suffix == ".md":
        return _markdown(path.read_text()), {"format": "markdown"}
    if suffix == ".pdf":
        return _pdf(path)
    return _numbered([(None, path.read_text())]), {"format": "text"}


def _markdown(text: str) -> list[dict]:
    out, heading, body = [], None, []
    for line in text.splitlines():
        if m := re.match(r"^#{1,6}\s+(.*)", line):
            if heading or "".join(body).strip():
                out.append({"heading": heading or "Introduction", "page": None, "text": "\n".join(body).strip()})
            heading, body = m.group(1).strip(), []
        else:
            body.append(line)
    if heading or "".join(body).strip():
        out.append({"heading": heading or "Introduction", "page": None, "text": "\n".join(body).strip()})
    # A top-level title with no body of its own isn't a section.
    return [s for s in out if s["text"]]


def _pdf(path: Path) -> tuple[list[dict], dict]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]
    blank = sum(1 for _, t in pages if not t.strip())
    facts = {"format": "pdf", "pages": len(pages), **({"needs_ocr": True} if blank == len(pages) else {})}
    return _numbered(pages), facts


def _numbered(pages: list[tuple[int | None, str]]) -> list[dict]:
    out, heading, page_of_heading, body = [], None, None, []
    for page, text in pages:
        page_of_heading = page_of_heading or page
        for line in text.splitlines():
            if m := HEADING.match(line.strip()):
                if heading or "".join(body).strip():
                    out.append({"heading": heading or "Introduction", "page": page_of_heading,
                                "text": "\n".join(body).strip()})
                heading, page_of_heading, body = f"{m.group(1)} {m.group(2).strip()}", page, []
            else:
                body.append(line)
    if heading or "".join(body).strip():
        out.append({"heading": heading or "Introduction", "page": page_of_heading, "text": "\n".join(body).strip()})
    return [s for s in out if s["text"]]
