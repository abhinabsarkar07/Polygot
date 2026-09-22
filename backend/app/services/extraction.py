"""Text extraction for the three required document types. No OCR --
a scanned PDF with no embedded text layer fails honestly
(:class:`ExtractionError`) rather than silently producing an empty
document or pretending text was recovered.

Kept isolated from HTTP routing on purpose: this module knows nothing
about ``UploadFile``, collections, or tenants -- it's a pure
``bytes -> list[ExtractedPage]`` transform, easy to unit test without a
database.
"""

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

TEXT_CONTENT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
PDF_CONTENT_TYPE = "application/pdf"

_EXTENSION_TO_KIND = {
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
    ".pdf": "pdf",
}


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class ExtractedPage:
    text: str
    page_number: int | None  # 1-based; None for non-paginated formats (TXT/Markdown)


def kind_for_filename(filename: str) -> str | None:
    """Returns "text", "pdf", or None for an unsupported extension.
    Extension-driven, not the client-declared content-type -- see
    ``app/api/collections.py`` for why (STEP 4: don't trust MIME alone)."""
    lowered = filename.lower()
    for ext, kind in _EXTENSION_TO_KIND.items():
        if lowered.endswith(ext):
            return kind
    return None


def extract(filename: str, data: bytes) -> list[ExtractedPage]:
    kind = kind_for_filename(filename)
    if kind == "text":
        return _extract_text(data)
    if kind == "pdf":
        return _extract_pdf(data)
    raise ExtractionError(f"unsupported file type for '{filename}'")


def _extract_text(data: bytes) -> list[ExtractedPage]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError("file is not valid UTF-8 text") from exc
    if not text.strip():
        raise ExtractionError("file contains no text")
    return [ExtractedPage(text=text, page_number=None)]


def _extract_pdf(data: bytes) -> list[ExtractedPage]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except (PdfReadError, ValueError) as exc:
        raise ExtractionError(f"could not read PDF: {exc}") from exc

    pages: list[ExtractedPage] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pypdf can raise a variety of parse errors per-page
            raise ExtractionError(f"could not extract text from page {index}: {exc}") from exc
        if text.strip():
            pages.append(ExtractedPage(text=text, page_number=index))

    if not pages:
        # The honest failure this module exists to produce: a real PDF
        # that opened fine but has no extractable text layer (e.g.
        # scanned pages with no OCR) is not silently treated as empty --
        # see app/services/ingestion.py for how this becomes
        # documents.status = 'failed'.
        raise ExtractionError("no extractable text found in PDF (scanned/image-only PDFs are not supported -- no OCR)")
    return pages
