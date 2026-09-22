"""Text extraction for TXT/Markdown/PDF. The PDF fixtures
(tests/rag_fixtures/*.pdf) are minimal, hand-built, genuinely valid PDFs
-- one with a real extractable text layer, one with none (simulating a
scanned/image-only PDF) -- not mocked pypdf calls, so this actually
exercises the real PDF parser CP-05 ships with.
"""

from pathlib import Path

import pytest

from app.services.extraction import ExtractionError, extract, kind_for_filename

FIXTURES = Path(__file__).parent.parent / "rag_fixtures"


def test_kind_for_filename_recognizes_supported_extensions():
    assert kind_for_filename("notes.txt") == "text"
    assert kind_for_filename("README.md") == "text"
    assert kind_for_filename("report.markdown") == "text"
    assert kind_for_filename("manual.pdf") == "pdf"


def test_kind_for_filename_rejects_unsupported_extensions():
    assert kind_for_filename("archive.zip") is None
    assert kind_for_filename("script.exe") is None
    assert kind_for_filename("no_extension") is None


def test_extract_txt():
    pages = extract("notes.txt", b"Hello, this is a plain text file.")
    assert len(pages) == 1
    assert pages[0].text == "Hello, this is a plain text file."
    assert pages[0].page_number is None


def test_extract_markdown_treated_as_text():
    pages = extract("README.md", b"# Title\n\nSome *markdown* content.")
    assert pages[0].text == "# Title\n\nSome *markdown* content."
    assert pages[0].page_number is None


def test_extract_empty_text_file_fails():
    with pytest.raises(ExtractionError, match="no text"):
        extract("empty.txt", b"")
    with pytest.raises(ExtractionError, match="no text"):
        extract("whitespace.txt", b"   \n\t  ")


def test_extract_non_utf8_text_fails():
    with pytest.raises(ExtractionError, match="UTF-8"):
        extract("binary.txt", b"\xff\xfe\x00\x01garbage")


def test_extract_real_pdf_with_text_layer():
    data = (FIXTURES / "sample.pdf").read_bytes()
    pages = extract("sample.pdf", data)
    assert len(pages) == 1
    assert "Hello from a real PDF fixture." in pages[0].text
    assert pages[0].page_number == 1  # 1-based, preserved for citations


def test_extract_scanned_pdf_with_no_text_layer_fails_honestly():
    data = (FIXTURES / "scanned_no_text.pdf").read_bytes()
    with pytest.raises(ExtractionError, match="no extractable text"):
        extract("scanned.pdf", data)


def test_extract_malformed_pdf_fails_honestly():
    with pytest.raises(ExtractionError, match="could not read PDF"):
        extract("fake.pdf", b"this is not actually a PDF file, just renamed")


def test_extract_unsupported_extension_raises():
    with pytest.raises(ExtractionError, match="unsupported file type"):
        extract("archive.zip", b"PK\x03\x04")
