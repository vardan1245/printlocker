"""Page ranges, sheet counting and splitting a job across compartments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

COMPARTMENT_CAPACITY_SHEETS = 500
MAX_COPIES = 50

_PART = re.compile(r"^\s*(\d*)\s*(-?)\s*(\d*)\s*$")


class InvalidPrintOptions(ValueError):
    pass


def parse_page_range(spec: str | None, total_pages: int) -> list[int]:
    """Turn '1-3,5,8-' into a list of 1-based page numbers.

    Empty / None means all pages. Open ends are allowed ('-3', '8-').
    Order is kept as written; a page may appear more than once.
    """
    if total_pages < 1:
        raise InvalidPrintOptions("document has no pages")
    if spec is None or not spec.strip():
        return list(range(1, total_pages + 1))

    pages: list[int] = []
    for raw in spec.split(","):
        m = _PART.match(raw)
        if not m or (not m.group(1) and not m.group(3)):
            raise InvalidPrintOptions(f"bad page range part: '{raw.strip()}'")
        start_s, dash, end_s = m.groups()
        if not dash:
            start = end = int(start_s)
        else:
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else total_pages
        if start < 1 or end < 1:
            raise InvalidPrintOptions("page numbers start at 1")
        if start > end:
            raise InvalidPrintOptions(f"range {start}-{end} is backwards")
        if end > total_pages:
            raise InvalidPrintOptions(f"page {end} does not exist (document has {total_pages})")
        pages.extend(range(start, end + 1))
    return pages


def sheets_needed(selected_pages: int, copies: int, duplex: bool) -> int:
    """Physical sheets for the whole job. Each copy starts on a new sheet."""
    if selected_pages < 1:
        raise InvalidPrintOptions("no pages selected")
    if not 1 <= copies <= MAX_COPIES:
        raise InvalidPrintOptions(f"copies must be between 1 and {MAX_COPIES}")
    per_copy = math.ceil(selected_pages / 2) if duplex else selected_pages
    return per_copy * copies


def compartments_needed(sheets: int, capacity: int = COMPARTMENT_CAPACITY_SHEETS) -> int:
    if sheets < 1:
        raise InvalidPrintOptions("job has no sheets")
    return math.ceil(sheets / capacity)


def split_sheets(sheets: int, capacity: int = COMPARTMENT_CAPACITY_SHEETS) -> list[int]:
    """Split sheets into balanced parts, each <= capacity (700 -> [350, 350])."""
    n = compartments_needed(sheets, capacity)
    base, extra = divmod(sheets, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


@dataclass(frozen=True)
class JobPlan:
    total_pages: int
    selected_pages: int
    copies: int
    duplex: bool
    sheets: int
    parts: list[int]

    @property
    def compartments(self) -> int:
        return len(self.parts)


def plan_job(
    total_pages: int,
    page_range: str | None,
    copies: int,
    duplex: bool,
    capacity: int = COMPARTMENT_CAPACITY_SHEETS,
    max_sheets: int | None = None,
) -> JobPlan:
    pages = parse_page_range(page_range, total_pages)
    sheets = sheets_needed(len(pages), copies, duplex)
    if max_sheets is not None and sheets > max_sheets:
        raise InvalidPrintOptions(f"job needs {sheets} sheets, the limit is {max_sheets}")
    return JobPlan(
        total_pages=total_pages,
        selected_pages=len(pages),
        copies=copies,
        duplex=duplex,
        sheets=sheets,
        parts=split_sheets(sheets, capacity),
    )
