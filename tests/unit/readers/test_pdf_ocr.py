"""OCR for scanned PDF pages (phase 6, ADR-017).

Most tests feed hand-written hOCR or a fake engine, so they run without Tesseract; the
`slow` ones run the real engine and skip when it is not installed.
"""

import io
from pathlib import Path

import pytest
from PIL import Image as PILImage
from PIL import ImageDraw
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from tests.builders import bare
from tomd.ir import Document, Heading, ListBlock, Paragraph, Text
from tomd.readers.pdf import backend as backend_module
from tomd.readers.pdf.backend import (
    PDF_NO_TEXT_LAYER,
    PDF_OCR_APPLIED,
    PDF_OCR_LOW_CONFIDENCE,
    PDF_OCR_UNAVAILABLE,
    HeuristicBackend,
)
from tomd.readers.pdf.config import DEFAULT_CONFIG
from tomd.readers.pdf.extract import iter_pages, open_pdf
from tomd.readers.pdf.model import Line, PageInfo, PdfFigure
from tomd.readers.pdf.ocr import (
    OcrEngine,
    OcrFailedError,
    OcrMode,
    OcrPage,
    estimate_skew,
    lines_from_hocr,
    needs_ocr,
)

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
PAGE = PageInfo(1, A4[0], A4[1], char_count=0, garbage_count=0)
FULL_PAGE = PdfFigure(1, (0.0, 0.0, A4[0], A4[1]), b"")
SCALE = 72 / 300  # points per pixel at 300 dpi


# --- Gate ---------------------------------------------------------------------------------


def test_gate_flags_a_page_sized_image_without_text() -> None:
    assert needs_ocr(PAGE, [FULL_PAGE], DEFAULT_CONFIG, OcrMode.AUTO)


def test_gate_ignores_a_small_image_and_digital_text() -> None:
    small = PdfFigure(1, (72.0, 300.0, 300.0, 500.0), b"")
    digital = PageInfo(1, A4[0], A4[1], char_count=2000, garbage_count=3)
    assert not needs_ocr(PAGE, [small], DEFAULT_CONFIG, OcrMode.AUTO)
    assert not needs_ocr(digital, [FULL_PAGE], DEFAULT_CONFIG, OcrMode.AUTO)  # text over a photo


def test_gate_flags_garbage_text_and_obeys_the_mode() -> None:
    garbage = PageInfo(1, A4[0], A4[1], char_count=500, garbage_count=400)
    digital = PageInfo(1, A4[0], A4[1], char_count=2000, garbage_count=0)
    assert needs_ocr(garbage, [], DEFAULT_CONFIG, OcrMode.AUTO)
    assert not needs_ocr(garbage, [], DEFAULT_CONFIG, OcrMode.NEVER)
    assert needs_ocr(digital, [], DEFAULT_CONFIG, OcrMode.ALWAYS)


def test_gate_never_flags_a_digital_corpus_page() -> None:
    """PLAN phase 6: no digital page of the corpus goes to OCR."""
    pdfs = sorted((CORPUS / "pdf").glob("*/input.pdf"))
    assert pdfs
    for path in pdfs:
        with open_pdf(path) as pdf:
            for content in iter_pages(pdf, DEFAULT_CONFIG.min_figure_area):
                flagged = needs_ocr(content.info, content.figures, DEFAULT_CONFIG, OcrMode.AUTO)
                assert not flagged, f"{path.parent.name} p{content.info.number}"


def test_gate_flags_every_scanned_corpus_page() -> None:
    pdfs = sorted((CORPUS / "pdf-ocr").glob("*/input.pdf"))
    assert len(pdfs) >= 5
    for path in pdfs:
        # scan-mixed: digital pages 1 and 3 around one scanned page.
        scanned = {2} if path.parent.name == "scan-mixed" else None
        with open_pdf(path) as pdf:
            for content in iter_pages(pdf, DEFAULT_CONFIG.min_figure_area):
                flagged = needs_ocr(content.info, content.figures, DEFAULT_CONFIG, OcrMode.AUTO)
                expected = scanned is None or content.info.number in scanned
                assert flagged == expected, f"{path.parent.name} p{content.info.number}"


# --- hOCR to lines ------------------------------------------------------------------------


def _word(text: str, box: tuple[int, int, int, int], conf: int = 95) -> str:
    return (
        f"<span class='ocrx_word' title='bbox {box[0]} {box[1]} {box[2]} {box[3]}; "
        f"x_wconf {conf}'>{text}</span>"
    )


def _line(words: list[str], box: tuple[int, int, int, int], offset: int) -> str:
    title = f"bbox {box[0]} {box[1]} {box[2]} {box[3]}; baseline 0 {offset}; x_size 42"
    return f"<span class='ocr_line' title=\"{title}\">{' '.join(words)}</span>"


def _hocr(*paragraphs: list[str]) -> bytes:
    body = "".join(f"<p class='ocr_par'>{''.join(lines)}</p>" for lines in paragraphs)
    return f"<html><body><div class='ocr_page'>{body}</div></body></html>".encode()


def test_hocr_lines_get_size_from_cap_height_not_descenders() -> None:
    # 15 pt heading without descenders ("2 Conclusions") and 10.5 pt body text: cap
    # heights of 45 and 32 px at 300 dpi.
    heading = _line(
        [_word("2", (300, 1000, 330, 1045)), _word("Conclusions", (350, 1000, 700, 1045))],
        (300, 1000, 700, 1045),
        0,
    )
    body = _line(
        [_word("plain", (300, 1100, 400, 1142)), _word("typography", (420, 1100, 640, 1142))],
        (300, 1100, 640, 1142),
        -10,
    )
    lines = lines_from_hocr(_hocr([heading], [body]), 1, A4[1], SCALE, DEFAULT_CONFIG)
    assert [ln.text for ln in lines] == ["2 Conclusions", "plain typography"]
    assert [ln.size for ln in lines] == [15.0, 10.5]
    assert lines[1].baseline == pytest.approx(A4[1] - 1132 * SCALE)
    assert lines[0].x0 == pytest.approx(300 * SCALE)
    assert lines[1].confidence == 0.95


def test_misread_bullet_with_noise_becomes_a_marker() -> None:
    words = [
        _word("e", (300, 1110, 312, 1122), conf=44),
        _word("-.", (330, 1100, 345, 1145), conf=77),  # a noise speckle after it
        _word("Second", (380, 1100, 520, 1132)),
        _word("point", (535, 1100, 640, 1142)),
    ]
    lines = lines_from_hocr(
        _hocr([_line(words, (300, 1100, 640, 1142), -10)]), 1, A4[1], SCALE, DEFAULT_CONFIG
    )
    assert lines[0].text == "• Second point"
    assert lines[0].size == 10.5  # neither the bullet nor the speckle sets the height


def test_a_one_letter_word_before_a_normal_space_is_not_a_bullet() -> None:
    words = [_word("e", (300, 1110, 318, 1132)), _word("Italia", (330, 1100, 450, 1132))]
    lines = lines_from_hocr(
        _hocr([_line(words, (300, 1100, 450, 1132), 0)]), 1, A4[1], SCALE, DEFAULT_CONFIG
    )
    assert lines[0].text == "e Italia"


def test_low_confidence_punctuation_is_dropped_as_a_speckle() -> None:
    words = [
        _word("text", (300, 1100, 380, 1132)),
        _word("'", (400, 1090, 405, 1095), conf=30),
        _word("here", (420, 1100, 500, 1132)),
    ]
    lines = lines_from_hocr(
        _hocr([_line(words, (300, 1090, 500, 1132), 0)]), 1, A4[1], SCALE, DEFAULT_CONFIG
    )
    assert lines[0].text == "text here"


def test_cap_height_follows_a_sloped_baseline() -> None:
    # Leftover skew: the baseline drops 0.02 px per px, so the right-hand word sits 12 px
    # lower than the left one; both are 32 px tall above the baseline under them.
    words = [
        _word("Left", (300, 1100, 400, 1142)),
        _word("Right", (900, 1112, 1000, 1154)),
    ]
    title = "bbox 300 1100 1000 1154; baseline 0.02 -23; x_size 42"
    line = f"<span class='ocr_line' title=\"{title}\">{' '.join(words)}</span>"
    lines = lines_from_hocr(_hocr([line]), 1, A4[1], SCALE, DEFAULT_CONFIG)
    assert lines[0].size == 10.5


def test_wide_strokes_mark_a_line_bold() -> None:
    image = PILImage.new("L", (1200, 400), 255)
    draw = ImageDraw.Draw(image)
    rows = {100: 3, 200: 3, 300: 6}  # top -> stroke width in px; the last row is "bold"
    for top, stroke in rows.items():
        for x in range(300, 600, 12):
            draw.rectangle((x, top, x + stroke - 1, top + 32), fill=0)
    lines_html = [
        _line([_word(f"word{top}", (300, top, 600, top + 32))], (300, top, 600, top + 32), 0)
        for top in rows
    ]
    lines = lines_from_hocr(_hocr(lines_html), 1, 400 * SCALE, SCALE, DEFAULT_CONFIG, image)
    assert [ln.bold for ln in lines] == [False, False, True]


def test_rule_boxes_find_grid_lines_but_not_text_or_filled_areas() -> None:
    from tomd.readers.pdf.ocr import find_rule_boxes

    image = PILImage.new("L", (1000, 1000), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 899, 102), fill=0)  # horizontal rule, 3 px thick
    draw.rectangle((100, 100, 102, 599), fill=0)  # vertical rule
    draw.rectangle((300, 700, 899, 900), fill=0)  # a filled block (photo, redaction)
    draw.rectangle((150, 300, 180, 330), fill=0)  # a glyph-sized blot
    boxes = find_rule_boxes(image, min_length=150, max_thickness=10)
    assert sorted(boxes) == [(False, 100, 100, 103, 600), (True, 100, 100, 900, 103)]


def test_skew_estimate_finds_the_rotation() -> None:
    image = PILImage.new("L", (1200, 1600), 255)
    draw = ImageDraw.Draw(image)
    for y in range(200, 1400, 40):  # text rows as dark bars
        draw.rectangle((150, y, 1050, y + 18), fill=0)
    assert estimate_skew(image, DEFAULT_CONFIG) == 0.0
    assert estimate_skew(image.rotate(1.5, fillcolor=255), DEFAULT_CONFIG) == pytest.approx(
        1.5, abs=0.15
    )


# --- Backend integration ------------------------------------------------------------------


def _scan_pdf(tmp_path: Path, text_rows: list[tuple[str, int]]) -> Path:
    """Image-only page: rows of (text, pixel size) painted on a 150 dpi bitmap."""
    image = PILImage.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    y = 150
    for text, size in text_rows:
        draw.text((150, y), text, fill=0, font_size=size)
        y += int(size * 1.6)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    path = tmp_path / "scan.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 0, 0, *A4)
    canvas.save()
    return path


def _fake_line(text: str, top: float, size: float, confidence: float) -> Line:
    from tomd.readers.pdf.model import Span

    return Line(
        (Span(text, False, False, False, size),),
        72.0,
        72.0 + len(text) * size * 0.5,
        top,
        top - size * 1.2,
        top - size,
        size,
        1,
        confidence,
    )


def _convert(path: Path, mode: OcrMode = OcrMode.AUTO) -> Document:
    doc = HeuristicBackend(ocr=mode).convert(path)
    return Document(doc.meta, bare(doc.blocks), doc.assets, doc.diagnostics)  # type: ignore[arg-type]


def test_ocr_lines_flow_through_the_layout_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [
        _fake_line("Scanned report", 770.0, 20.0, 0.6),
        _fake_line("Recognised body text goes here.", 720.0, 10.5, 0.3),
    ]
    monkeypatch.setattr(OcrEngine, "find", classmethod(lambda cls, lang: cls("t", None, lang)))
    page = OcrPage(lines, [], [], A4[0], A4[1], 0, 0.0)
    monkeypatch.setattr(backend_module, "ocr_page", lambda *args: page)
    raw = HeuristicBackend().convert(_scan_pdf(tmp_path, [("x", 20)]))
    doc = Document(raw.meta, bare(raw.blocks), raw.assets, raw.diagnostics)  # type: ignore[arg-type]
    assert doc.blocks == (
        Heading(1, (Text("Scanned report"),)),
        Paragraph((Text("Recognised body text goes here."),)),
    )
    assert not doc.assets  # the scan became text; it is not also kept as a figure
    codes = [d.code for d in doc.diagnostics]
    assert PDF_OCR_APPLIED in codes and PDF_OCR_LOW_CONFIDENCE in codes
    paragraph = raw.blocks[1]
    assert isinstance(paragraph, Paragraph) and paragraph.provenance is not None
    assert paragraph.provenance.confidence == 0.3  # OCR confidence carried to the IR


def test_missing_engine_reports_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(OcrEngine, "find", classmethod(lambda cls, lang: None))
    codes = [d.code for d in _convert(_scan_pdf(tmp_path, [("x", 20)])).diagnostics]
    assert PDF_OCR_UNAVAILABLE in codes and PDF_NO_TEXT_LAYER in codes


def test_engine_failure_reports_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> list[Line]:
        raise OcrFailedError("Failed loading language 'spa'")

    monkeypatch.setattr(OcrEngine, "find", classmethod(lambda cls, lang: cls("t", None, lang)))
    monkeypatch.setattr(backend_module, "ocr_page", fail)
    codes = [d.code for d in _convert(_scan_pdf(tmp_path, [("x", 20)])).diagnostics]
    assert PDF_OCR_UNAVAILABLE in codes


def test_ocr_never_skips_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object) -> list[Line]:
        raise AssertionError("OCR must not run")

    monkeypatch.setattr(backend_module, "ocr_page", boom)
    codes = [d.code for d in _convert(_scan_pdf(tmp_path, [("x", 20)]), OcrMode.NEVER).diagnostics]
    assert PDF_NO_TEXT_LAYER in codes and PDF_OCR_UNAVAILABLE not in codes


# --- Real engine --------------------------------------------------------------------------

needs_tesseract = pytest.mark.skipif(
    OcrEngine.find("eng") is None, reason="Tesseract is not installed"
)


@pytest.mark.slow
@needs_tesseract
def test_tesseract_reads_a_scan_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOMD_OCR_LANG", "eng")
    path = _scan_pdf(
        tmp_path,
        [("Quarterly Report", 64), ("The results were better than expected.", 32)],
    )
    first, second = _convert(path), _convert(path)
    assert first == second
    assert first.blocks[0] == Heading(1, (Text("Quarterly Report"),))
    assert first.blocks[1] == Paragraph((Text("The results were better than expected."),))
    assert PDF_OCR_APPLIED in [d.code for d in first.diagnostics]


@pytest.mark.slow
@needs_tesseract
def test_scanned_list_is_rebuilt(tmp_path: Path) -> None:
    case = CORPUS / "pdf-ocr" / "scan-clean-300dpi" / "input.pdf"
    doc = _convert(case)
    assert any(isinstance(b, ListBlock) and len(b.items) == 3 for b in doc.blocks)


def test_text_over_a_full_page_photo_is_not_ocred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object) -> OcrPage:
        raise AssertionError("a page with a text layer must not go to OCR")

    monkeypatch.setattr(backend_module, "ocr_page", boom)
    photo = PILImage.new("L", (620, 877), 200)
    buffer = io.BytesIO()
    photo.save(buffer, format="PNG")
    path = tmp_path / "poster.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 0, 0, *A4)
    canvas.setFont("Helvetica", 11)
    canvas.drawString(72, 700, "A caption printed over the photograph, as on a poster.")
    canvas.save()
    doc = _convert(path)
    assert (
        Paragraph((Text("A caption printed over the photograph, as on a poster."),)) in doc.blocks
    )
    assert PDF_OCR_APPLIED not in [d.code for d in doc.diagnostics]


def test_installed_app_uses_its_bundled_tesseract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the packaged app (frozen), Tesseract and its data next to the executable win
    over anything installed on the machine."""
    import sys

    bundled = tmp_path / "tesseract"
    (bundled / "tessdata").mkdir(parents=True)
    (bundled / "tesseract.exe").write_bytes(b"")
    (bundled / "tessdata" / "spa.traineddata").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "tomd.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("TOMD_TESSERACT", raising=False)
    monkeypatch.delenv("TOMD_TESSDATA", raising=False)
    engine = OcrEngine.find("spa")
    assert engine is not None
    assert Path(engine.command) == bundled / "tesseract.exe"
    assert engine.tessdata == str(bundled / "tessdata")
