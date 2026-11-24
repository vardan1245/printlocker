import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_pdf(pages: int = 1) -> bytes:
    """Build a small valid PDF with `pages` blank pages (pypdf only)."""
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=595, height=842)  # A4 in points
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture
def pdf_bytes():
    return make_pdf
