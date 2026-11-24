"""PDF upload checks."""
from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 50 * 1024 * 1024


class InvalidPdf(ValueError):
    pass


def count_pdf_pages(data: bytes, max_bytes: int = MAX_PDF_BYTES) -> int:
    """Validate an uploaded PDF and return its page count.

    Rejects: empty files, files over the size limit, files that are not PDFs,
    damaged PDFs, password-protected PDFs and PDFs with zero pages.
    """
    if not data:
        raise InvalidPdf("file is empty")
    if len(data) > max_bytes:
        raise InvalidPdf(f"file is larger than {max_bytes // (1024 * 1024)} MB")
    if not data.lstrip()[:5] == b"%PDF-":
        raise InvalidPdf("file is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Some PDFs are "encrypted" with an empty password (print-only restrictions).
            try:
                ok = reader.decrypt("")
            except Exception as exc:  # unsupported encryption algorithm, etc.
                raise InvalidPdf("PDF is password protected") from exc
            if not ok:
                raise InvalidPdf("PDF is password protected")
        pages = len(reader.pages)
    except InvalidPdf:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise InvalidPdf("PDF is damaged or unreadable") from exc
    if pages < 1:
        raise InvalidPdf("PDF has no pages")
    return pages
