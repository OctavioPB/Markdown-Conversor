"""Malformed and hostile inputs (PLAN phase 8). Each test pins a finding of
`scripts/fuzz_inputs.py` or a resource guard: the input must convert (with diagnostics)
or fail with a `TomdError`, never with another exception."""

import contextlib
import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image as PILImage
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from tomd.errors import UnreadableInputError
from tomd.ir import diagnostics as codes
from tomd.readers.pdf.backend import (
    PDF_OCR_APPLIED,
    PDF_PAGE_EMPTY,
    PDF_PAGE_UNREADABLE,
    HeuristicBackend,
)
from tomd.readers.pdf.extract import iter_pages
from tomd.readers.pdf.ocr import OcrEngine, OcrMode
from tomd.readers.wikipedia.reader import read_parsoid_html

FIXTURES = Path(__file__).parent / "fixtures" / "fuzz"
CORPUS = Path(__file__).resolve().parents[1] / "corpus"

_PARSOID = (
    b'<!DOCTYPE html><html><head><meta property="mw:htmlVersion" content="2.8.0"/>'
    b'<title>Test</title></head><body class="parsoid-body" lang="en">'
)


def _codes(doc: object) -> set[str]:
    return {d.code for d in doc.diagnostics}  # type: ignore[attr-defined]


# --- HTML ------------------------------------------------------------------------------------


def test_invalid_utf8_is_replaced_and_reported() -> None:
    # Bytes 0xff and a cut multi-byte sequence used to raise UnicodeDecodeError deep in
    # the mapper, because lxml decodes text lazily.
    html = _PARSOID + b"<p>caf\xc3 au lait \xff ok</p></body></html>"
    doc = read_parsoid_html(html)
    assert codes.TEXT_INVALID_UNICODE in _codes(doc)


def test_nul_bytes_and_deep_nesting_do_not_crash() -> None:
    html = _PARSOID + b"<p>a\x00b</p>" + b"<div><span>" * 20_000 + b"deep</body></html>"
    read_parsoid_html(html)


def test_truncated_html_still_converts() -> None:
    data = (next((CORPUS / "wikipedia").glob("*/input.html"))).read_bytes()
    read_parsoid_html(data[: len(data) // 3])


# --- PDF -------------------------------------------------------------------------------------


def test_damaged_page_is_skipped_with_a_diagnostic() -> None:
    doc = HeuristicBackend(ocr=OcrMode.NEVER).convert(FIXTURES / "page-unreadable.pdf")
    # Page 2 cannot be loaded; page 1 loads, but its garbled content stream decodes to
    # nothing. Neither loss is silent.
    assert {PDF_PAGE_UNREADABLE, PDF_PAGE_EMPTY} <= _codes(doc)


def test_pdf_without_any_readable_page_is_rejected() -> None:
    with pytest.raises(UnreadableInputError, match="No page"):
        HeuristicBackend(ocr=OcrMode.NEVER).convert(FIXTURES / "all-pages-unreadable.pdf")


@pytest.mark.parametrize("fraction", [0.0, 0.1, 0.5, 0.9])
def test_truncated_pdf_converts_or_is_rejected(tmp_path: Path, fraction: float) -> None:
    data = (CORPUS / "pdf" / "synthetic-lists" / "input.pdf").read_bytes()
    path = tmp_path / "cut.pdf"
    path.write_bytes(data[: int(len(data) * fraction)])
    with contextlib.suppress(UnreadableInputError):
        HeuristicBackend(ocr=OcrMode.NEVER).convert(path)


def _pdf_with_image(tmp_path: Path, size: tuple[float, float], pixels: int) -> Path:
    image = PILImage.new("L", (pixels, pixels), 128)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "image.pdf"
    canvas = Canvas(str(path), pagesize=size, invariant=1)
    canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 0, 0, *size)
    canvas.save()
    return path


def test_oversized_image_is_skipped_before_decoding(tmp_path: Path) -> None:
    path = _pdf_with_image(tmp_path, (595.0, 842.0), 400)
    doc = pdfium.PdfDocument(str(path))
    try:
        (content,) = iter_pages(doc, 0.01, max_image_pixels=100_000)  # 400² = 160 000 px
    finally:
        doc.close()
    assert content.figures == [] and content.info.image_count == 1


@pytest.mark.slow
@pytest.mark.skipif(OcrEngine.find("eng") is None, reason="Tesseract is not installed")
def test_huge_scanned_page_is_read_at_a_lower_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A 200 x 200 inch page at 300 dpi would be 3.6 gigapixels; the render budget caps it.
    monkeypatch.setenv("TOMD_OCR_LANG", "eng")
    path = _pdf_with_image(tmp_path, (14_400.0, 14_400.0), 64)
    doc = HeuristicBackend().convert(path)
    applied = [d.message for d in doc.diagnostics if d.code == PDF_OCR_APPLIED]
    assert applied and "page too large" in applied[0]


# --- EPUB --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["epub-container-deflate-error.epub", "epub-zip-version.epub"])
def test_damaged_epub_archives_are_rejected(name: str) -> None:
    # Found by scripts/fuzz_inputs.py: a corrupt deflate stream in container.xml raised
    # zlib.error, an unknown ZIP version NotImplementedError.
    from tomd.readers.epub.reader import read_epub

    with pytest.raises(UnreadableInputError):
        read_epub(FIXTURES / name)


def test_a_damaged_chapter_loses_only_itself(tmp_path: Path) -> None:
    import zipfile

    from tests.unit.readers.test_epub import book
    from tomd.readers.epub.reader import read_epub

    path = book(
        tmp_path, {"a.xhtml": "<p>First chapter.</p>", "b.xhtml": "<p>" + "x" * 4000 + "</p>"}
    )
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("OEBPS/b.xhtml")
    data = bytearray(path.read_bytes())
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    for offset in range(start + 2, start + info.compress_size - 2, 7):
        data[offset] ^= 0xFF  # corrupt the deflate stream of b.xhtml only
    path.write_bytes(bytes(data))
    doc = read_epub(path)
    assert codes.EPUB_MISSING_RESOURCE in _codes(doc)
    assert any("First chapter." in repr(b) for b in doc.blocks)
