"""Turn a job into the exact pages that go to each compartment.

A big job is split across compartments by sheet count. The split can fall in the
middle of a copy, so the agent builds one small PDF per part with exactly that
part's pages (plus blank backs where a duplex copy has an odd page count) and
prints each part as its own print job. The elevator then delivers that part.
"""
from __future__ import annotations

import io
import re

from pypdf import PdfReader, PdfWriter

BLANK = None  # marker for a blank page in a sequence

_PART = re.compile(r"^\s*(\d*)\s*(-?)\s*(\d*)\s*$")


def parse_page_range(spec: str | None, total: int) -> list[int]:
    """Same rules as the backend: '1-3,5,8-' -> [1,2,3,5,8..total]; empty = all."""
    if spec is None or not spec.strip():
        return list(range(1, total + 1))
    out: list[int] = []
    for raw in spec.split(","):
        m = _PART.match(raw)
        if not m or (not m.group(1) and not m.group(3)):
            raise ValueError(f"bad page range part: {raw!r}")
        a, dash, b = m.groups()
        start = int(a) if a else 1
        end = (int(b) if b else total) if dash else start
        if not 1 <= start <= end <= total:
            raise ValueError(f"page range {raw!r} out of bounds")
        out.extend(range(start, end + 1))
    return out


def page_sequence(total_pages: int, page_range: str | None, copies: int, duplex: bool) -> list[int | None]:
    """All physical page faces in print order. Each duplex copy starts on a new sheet."""
    pages = parse_page_range(page_range, total_pages)
    seq: list[int | None] = []
    for _ in range(copies):
        seq.extend(pages)
        if duplex and len(pages) % 2:
            seq.append(BLANK)
    return seq


def sheet_count(seq: list[int | None], duplex: bool) -> int:
    return (len(seq) + 1) // 2 if duplex else len(seq)


def split_sequence(seq: list[int | None], part_sheets: list[int], duplex: bool) -> list[list[int | None]]:
    """Cut the face sequence into parts holding `part_sheets` sheets each."""
    if sum(part_sheets) != sheet_count(seq, duplex):
        raise ValueError(
            f"parts add up to {sum(part_sheets)} sheets, document needs {sheet_count(seq, duplex)}"
        )
    faces_per_sheet = 2 if duplex else 1
    parts, pos = [], 0
    for sheets in part_sheets:
        n = sheets * faces_per_sheet
        parts.append(seq[pos:pos + n])
        pos += n
    return parts


def build_pdf(source: bytes, faces: list[int | None]) -> bytes:
    """New PDF containing exactly `faces` (1-based page numbers, None = blank A4 page)."""
    reader = PdfReader(io.BytesIO(source))
    if reader.is_encrypted:
        reader.decrypt("")
    writer = PdfWriter()
    for f in faces:
        if f is BLANK:
            box = writer.pages[-1].mediabox if writer.pages else reader.pages[0].mediabox
            writer.add_blank_page(width=float(box.width), height=float(box.height))
        else:
            writer.add_page(reader.pages[f - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
