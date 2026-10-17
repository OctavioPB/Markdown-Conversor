"""Heuristic PDF backend on tiny PDFs generated with reportlab."""

import io
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from tests.builders import bare
from tomd.errors import DrmProtectedError, UnreadableInputError
from tomd.ir import (
    CodeBlock,
    Document,
    Emph,
    Heading,
    Image,
    ListBlock,
    Paragraph,
    Strong,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.readers.pdf.backend import (
    PDF_NO_TEXT_LAYER,
    PDF_PERMISSIONS_RESTRICTED,
    PDF_ROTATED_TEXT_DROPPED,
    HeuristicBackend,
)
from tomd.readers.pdf.text import is_garbage, join_lines, normalize_char

TOP = A4[1] - 72
LEFT = 72.0


def make_pdf(tmp_path: Path, draw: Callable[[Canvas], None], **canvas_args: object) -> Path:
    path = tmp_path / "doc.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1, **canvas_args)  # type: ignore[arg-type]
    draw(canvas)
    canvas.save()
    return path


def convert(path: Path) -> Document:
    doc = HeuristicBackend().convert(path)
    return Document(doc.meta, bare(doc.blocks), doc.assets, doc.diagnostics)  # type: ignore[arg-type]


def lines(
    canvas: Canvas, rows: list[tuple[str, str, float]], x: float = LEFT, leading: float = 14
) -> None:
    """Draw (font, text, size) rows top-down."""
    y = TOP
    for font, text, size in rows:
        canvas.setFont(font, size)
        canvas.drawString(x, y, text)
        y -= max(leading, size * 1.3)


def _codes(doc: Document) -> list[str]:
    return [d.code for d in doc.diagnostics]


# --- text.py ----------------------------------------------------------------------------


def test_ligatures_spaces_and_pdfium_hyphen_marker() -> None:
    assert normalize_char(chr(0xFB01)) == "fi"
    assert normalize_char(chr(0xFB03)) == "ffi"
    assert normalize_char(chr(0x00A0)) == " "
    assert normalize_char(chr(0x00AD)) == ""
    assert normalize_char(chr(0xFFFE)) == "-"
    assert normalize_char(chr(0x00B2)) == "²"  # never NFKC


def test_garbage_characters() -> None:
    assert is_garbage("�") and is_garbage("") and is_garbage("\x07")
    assert not is_garbage("ñ") and not is_garbage("∑")


@pytest.mark.parametrize(
    ("parts", "joined", "guessed"),
    [
        (["recons-", "truir"], "reconstruir", True),
        (["Norte-", "Sur"], "Norte-Sur", False),
        (["fin de línea", "siguiente"], "fin de línea siguiente", False),
        (["número 3-", "4"], "número 3-4", False),
    ],
)
def test_join_lines_hyphen_policy(parts: list[str], joined: str, guessed: bool) -> None:
    assert join_lines(parts) == (joined, guessed)


# --- Errors and diagnostics ------------------------------------------------------------


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    path = make_pdf(tmp_path, lambda c: c.drawString(LEFT, TOP, "secret"), encrypt="user-password")
    with pytest.raises(DrmProtectedError):
        HeuristicBackend().convert(path)


def test_permission_only_encryption_converts_with_diagnostic(tmp_path: Path) -> None:
    from reportlab.lib.pdfencrypt import StandardEncryption

    enc = StandardEncryption("", ownerPassword="owner", canPrint=0, canCopy=0)
    path = make_pdf(tmp_path, lambda c: c.drawString(LEFT, TOP, "Visible text."), encrypt=enc)
    doc = convert(path)
    assert PDF_PERMISSIONS_RESTRICTED in _codes(doc)
    assert doc.blocks == (Paragraph((Text("Visible text."),)),)


def test_corrupt_pdf_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\nnot really a pdf")
    with pytest.raises(UnreadableInputError):
        HeuristicBackend().convert(path)


def test_rotated_margin_stamp_is_dropped_with_diagnostic(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, TOP, "Body text stays in one paragraph.")
        c.saveState()
        c.translate(30, 300)
        c.rotate(90)
        c.setFont("Times-Roman", 20)
        c.drawString(0, 0, "arXiv:0000.00000v1 [cs.CL]")
        c.restoreState()

    doc = convert(make_pdf(tmp_path, draw))
    assert doc.blocks == (Paragraph((Text("Body text stays in one paragraph."),)),)
    assert PDF_ROTATED_TEXT_DROPPED in _codes(doc)


def test_page_without_text_layer_needs_ocr(tmp_path: Path) -> None:
    image = PILImage.new("RGB", (50, 50), "gray")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    def draw(c: Canvas) -> None:
        c.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 72, 300, 400, 400)

    doc = convert(make_pdf(tmp_path, draw))
    assert PDF_NO_TEXT_LAYER in _codes(doc)
    assert [type(b) for b in doc.blocks] == [Image]  # the scan itself is kept as a figure


# --- Lines -----------------------------------------------------------------------------


def test_spaces_are_inferred_when_the_pdf_has_no_space_glyphs(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        x = LEFT
        for word in ["Words", "without", "space", "glyphs."]:
            c.drawString(x, TOP, word)
            x += c.stringWidth(word, "Helvetica", 11) + c.stringWidth(" ", "Helvetica", 11)

    doc = convert(make_pdf(tmp_path, draw))
    assert doc.blocks == (Paragraph((Text("Words without space glyphs."),)),)


def test_superscript_stays_on_its_line(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, TOP, "Energy is mc")
        c.setFont("Helvetica", 7)
        c.drawString(LEFT + c.stringWidth("Energy is mc", "Helvetica", 11), TOP + 4, "2")
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, TOP - 14, "and that is all.")

    doc = convert(make_pdf(tmp_path, draw))
    assert len(doc.blocks) == 1
    para = doc.blocks[0]
    assert isinstance(para, Paragraph)
    text = "".join(n.value for n in para.children if isinstance(n, Text))
    # Not a footnote call: the superscript keeps its meaning as a Unicode superscript.
    assert text.startswith("Energy is mc" + chr(0xB2)) and text.endswith("and that is all.")


# --- Blocks and classification ---------------------------------------------------------


def test_paragraphs_headings_and_inline_styles(tmp_path: Path) -> None:
    rows = [
        ("Helvetica-Bold", "Document Title", 20),
        ("Helvetica-Bold", "1 Introduction", 14),
        ("Helvetica", "First paragraph line that is quite long and fills the", 11),
        ("Helvetica", "measure, then ends here.", 11),
    ]

    def draw(c: Canvas) -> None:
        lines(c, rows)
        y = TOP - 4 * 26 - 20
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, y, "A ")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(LEFT + c.stringWidth("A ", "Helvetica", 11), y, "bold")
        c.setFont("Helvetica-Oblique", 11)
        c.drawString(LEFT + 40, y, "italic")

    doc = convert(make_pdf(tmp_path, draw))
    assert doc.meta.title == "Document Title"
    assert doc.blocks[0] == Heading(1, (Text("Document Title"),))
    assert doc.blocks[1] == Heading(2, (Text("1 Introduction"),))
    assert doc.blocks[2] == Paragraph(
        (Text("First paragraph line that is quite long and fills the measure, then ends here."),)
    )
    last = doc.blocks[3]
    assert isinstance(last, Paragraph)
    assert any(isinstance(n, Strong) for n in last.children)
    assert any(isinstance(n, Emph) for n in last.children)


def test_numbered_headings_set_levels(tmp_path: Path) -> None:
    rows = [
        ("Helvetica-Bold", "2 Methods", 14),
        ("Helvetica", "Body text.", 11),
        ("Helvetica-Bold", "2.1 Data", 11),
        ("Helvetica", "More body text.", 11),
        ("Helvetica-Bold", "2.1.1 Sources", 11),
        ("Helvetica", "Even more body text.", 11),
    ]
    doc = convert(make_pdf(tmp_path, lambda c: lines(c, rows, leading=24)))
    # Numbering fixes the level: "2" -> H2, "2.1" -> H3, "2.1.1" -> H4 (H1 is the title).
    assert [b.level for b in doc.blocks if isinstance(b, Heading)] == [2, 3, 4]


def test_first_line_indent_does_not_split_but_new_indent_does(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        rows = [
            (LEFT + 20, "Indented first line of the first paragraph which"),
            (LEFT, "continues at the margin."),
            (LEFT + 20, "Second paragraph starts indented again."),
        ]
        for i, (x, text) in enumerate(rows):
            c.drawString(x, TOP - 14 * i, text)

    doc = convert(make_pdf(tmp_path, draw))
    assert len(doc.blocks) == 2


def test_bullets_with_hanging_indent_and_nesting(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        rows = [
            (LEFT, "Intro paragraph."),
            (LEFT, "• First item that wraps onto"),
            (LEFT + 10, "a second line"),
            (LEFT + 20, chr(0x2013) + " Nested item"),  # en dash bullet
            (LEFT, "• Second item"),
        ]
        for i, (x, text) in enumerate(rows):
            c.drawString(x, TOP - 16 * i, text)

    doc = convert(make_pdf(tmp_path, draw))
    lst = doc.blocks[1]
    assert isinstance(lst, ListBlock) and not lst.ordered
    assert len(lst.items) == 2
    first = lst.items[0]
    assert first.blocks[0] == Paragraph((Text("First item that wraps onto a second line"),))
    assert isinstance(first.blocks[1], ListBlock)


def test_ordered_list_keeps_start(tmp_path: Path) -> None:
    rows = [("Helvetica", "3. Third", 11), ("Helvetica", "4. Fourth", 11)]
    doc = convert(make_pdf(tmp_path, lambda c: lines(c, rows, leading=16)))
    (lst,) = doc.blocks
    assert isinstance(lst, ListBlock) and lst.ordered and lst.start == 3


def test_monospaced_block_is_code_with_indentation(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Courier", 10)
        c.drawString(LEFT, TOP, "def f(x):")
        c.drawString(LEFT + 4 * 6, TOP - 12, "return x")

    doc = convert(make_pdf(tmp_path, draw))
    assert doc.blocks == (CodeBlock("def f(x):\n    return x"),)


def test_paragraph_across_pages_and_page_numbers(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, 80, "This sentence starts on the first page and")
        c.drawCentredString(A4[0] / 2, 30, "1")
        c.showPage()
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, TOP, "ends on the second one.")
        c.drawCentredString(A4[0] / 2, 30, "2")

    doc = convert(make_pdf(tmp_path, draw))
    assert doc.blocks == (
        Paragraph((Text("This sentence starts on the first page and ends on the second one."),)),
    )


# --- Phase 5: reading order and furniture ------------------------------------------------


def _paragraph_texts(doc: Document) -> list[str]:
    out = []
    for block in doc.blocks:
        if isinstance(block, Paragraph):
            out.append("".join(n.value for n in block.children if isinstance(n, Text)))
    return out


def test_two_columns_are_read_left_then_right(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        for side, x in (("left", LEFT), ("right", 320.0)):
            for i in range(6):
                c.drawString(x, TOP - 13 * i, f"{side} column line {i}")

    texts = _paragraph_texts(convert(make_pdf(tmp_path, draw)))
    joined = " ".join(texts)
    assert joined.index("left column line 5") < joined.index("right column line 0")


def test_full_width_title_comes_before_columns(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 10)
        c.drawString(
            LEFT, TOP, "A full width introduction line that spans across both of the columns."
        )
        for side, x in (("left", LEFT), ("right", 320.0)):
            for i in range(5):
                c.drawString(x, TOP - 40 - 13 * i, f"{side} {i}")

    texts = _paragraph_texts(convert(make_pdf(tmp_path, draw)))
    assert texts[0].startswith("A full width introduction")


def test_running_heads_repeated_on_three_pages_are_removed(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        for page in range(1, 4):
            c.setFont("Helvetica", 8)
            c.drawString(LEFT, A4[1] - 30, "Journal of Tests, vol. 1")
            c.setFont("Helvetica", 11)
            # Body text must dominate the character count, as in any real page.
            c.drawString(LEFT, TOP - 40, f"Body text of page {page}, long enough to be body.")
            c.showPage()

    doc = convert(make_pdf(tmp_path, draw))
    expected = [f"Body text of page {n}, long enough to be body." for n in (1, 2, 3)]
    assert _paragraph_texts(doc) == expected
    assert "pdf.furniture_removed" in _codes(doc)


def test_running_head_that_drifts_a_little_is_still_removed(tmp_path: Path) -> None:
    """Scans place the head a few points higher or lower on each page: distances to the
    edge are compared with a tolerance, not rounded into buckets that could split them."""

    def draw(c: Canvas) -> None:
        for page, drift in enumerate((0, 6, 9), start=1):
            c.setFont("Helvetica", 8)
            c.drawString(LEFT, A4[1] - 27 - drift, "Journal of Tests, vol. 1")
            c.setFont("Helvetica", 11)
            c.drawString(LEFT, TOP - 40, f"Body text of page {page}, long enough to be body.")
            c.showPage()

    doc = convert(make_pdf(tmp_path, draw))
    expected = [f"Body text of page {n}, long enough to be body." for n in (1, 2, 3)]
    assert _paragraph_texts(doc) == expected


def test_header_on_only_two_pages_is_kept(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        for page in range(1, 3):
            c.setFont("Helvetica", 8)
            c.drawString(LEFT, A4[1] - 30, "Same line on top")
            c.setFont("Helvetica", 11)
            c.drawString(LEFT, TOP - 40, f"Body {page}.")
            c.showPage()

    assert "Same line on top" in " ".join(_paragraph_texts(convert(make_pdf(tmp_path, draw))))


def _png(color: str) -> ImageReader:
    buffer = io.BytesIO()
    PILImage.new("RGB", (60, 40), color).save(buffer, format="PNG")
    return ImageReader(io.BytesIO(buffer.getvalue()))


def test_figure_is_extracted_placed_and_captioned(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica", 11)
        c.drawString(LEFT, TOP, "Text before the figure, long enough to be body text.")
        c.drawImage(_png("red"), LEFT, TOP - 220, 300, 200)
        c.drawString(LEFT, TOP - 240, "Figure 1: A red rectangle.")
        c.drawString(LEFT, TOP - 280, "Text after the figure, also long enough to be body text.")
        c.drawImage(_png("blue"), 500, 60, 12, 12)  # decoration: too small to be a figure

    doc = convert(make_pdf(tmp_path, draw))
    kinds = [type(b) for b in doc.blocks]
    assert kinds == [Paragraph, Image, Paragraph]
    figure = doc.blocks[1]
    assert isinstance(figure, Image)
    assert figure.caption == (Text("Figure 1: A red rectangle."),)
    (asset,) = doc.assets
    assert asset.sha256 == figure.src_asset and asset.media_type == "image/png"
    assert codes.IMAGE_SKIPPED_SMALL in _codes(doc)  # the small one


def test_identical_images_are_stored_once(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        for page in range(2):
            c.setFont("Helvetica", 11)
            c.drawString(LEFT, TOP, f"Page {page} body text that is long enough.")
            c.drawImage(_png("green"), LEFT, TOP - 220, 300, 200)
            c.showPage()

    doc = convert(make_pdf(tmp_path, draw))
    assert sum(isinstance(b, Image) for b in doc.blocks) == 2
    assert len(doc.assets) == 1


# --- Tables from ruling grids ----------------------------------------------------------


def _table_pdf(tmp_path: Path, spans: bool, bold_header: bool = True) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph as RLParagraph
    from reportlab.platypus import SimpleDocTemplate, Spacer, TableStyle
    from reportlab.platypus import Table as RLTable

    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=13)
    data = [["Name", "Value", "Unit"], ["dim", "4096", "-"], ["layers", "32", "n"]]
    if spans:
        data.append(["Merged", "", "x"])
    style = [("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONT", (0, 1), (-1, -1), "Helvetica")]
    style.append(("FONT", (0, 0), (-1, 0), "Helvetica-Bold" if bold_header else "Helvetica"))
    if spans:
        style.append(("SPAN", (0, 3), (1, 3)))
    table = RLTable(data)
    table.setStyle(TableStyle(style))
    path = tmp_path / "table.pdf"
    # Multi-line paragraphs, so the document has a measurable line spacing L.
    filler = " ".join(["Body text that wraps over several lines of the page."] * 6)
    flow = [
        RLParagraph(filler, body),
        Spacer(1, 12),
        RLParagraph("Table 1: Model parameters.", body),
        Spacer(1, 4),
        table,
        Spacer(1, 12),
        RLParagraph(filler, body),
    ]
    SimpleDocTemplate(str(path), pagesize=A4, invariant=1).build(flow)
    return path


def test_ruled_table_with_header_caption_and_span(tmp_path: Path) -> None:
    from tomd.ir import Table

    doc = convert(_table_pdf(tmp_path, spans=True))
    kinds = [type(b) for b in doc.blocks]
    assert kinds == [Paragraph, Table, Paragraph]
    table = doc.blocks[1]
    assert isinstance(table, Table)
    assert table.caption == (Text("Table 1: Model parameters."),)
    assert len(table.header_rows) == 1 and len(table.body_rows) == 3
    header = table.header_rows[0]
    assert [c.blocks[0] for c in header.cells] == [
        Paragraph((Strong((Text(t),)),)) for t in ("Name", "Value", "Unit")
    ]
    merged = table.body_rows[2].cells[0]
    assert merged.colspan == 2 and merged.blocks == (Paragraph((Text("Merged"),)),)
    assert len(table.body_rows[2].cells) == 2


def test_simple_ruled_table_renders_as_gfm(tmp_path: Path) -> None:
    from tomd.pipeline.dispatch import render_document

    path = _table_pdf(tmp_path, spans=False, bold_header=False)
    _, result, _ = render_document(HeuristicBackend().convert(path))
    assert "| Name | Value | Unit |\n| --- | --- | --- |\n| dim | 4096 | - |" in result.text
    assert codes.TABLE_HEADER_PROMOTED in [d.code for d in result.diagnostics]


# --- Footnotes (§10) --------------------------------------------------------------------


def _sup(c: Canvas, x: float, y: float, text: str) -> float:
    """Draw a raised 7 pt mark; return the x after it."""
    c.setFont("Helvetica", 7)
    c.drawString(x, y + 4, text)
    return x + c.stringWidth(text, "Helvetica", 7)


def _body(c: Canvas, x: float, y: float, text: str) -> float:
    c.setFont("Helvetica", 11)
    c.drawString(x, y, text)
    return x + c.stringWidth(text, "Helvetica", 11)


FILLER = "Body text that is long enough to be the dominant size of the page here."


def test_numbered_call_is_linked_to_its_note() -> None:
    import tempfile

    def draw(c: Canvas) -> None:
        for i in range(4):
            _body(c, LEFT, TOP - 14 * i, FILLER)
        x = _body(c, LEFT, TOP - 70, "A claim with a source")
        x = _sup(c, x, TOP - 70, "1")
        _body(c, x, TOP - 70, " and more text.")
        c.setFont("Helvetica", 8)
        c.drawString(LEFT + 6, 80, "The source of the claim.")
        c.setFont("Helvetica", 6)
        c.drawString(LEFT, 84, "1")

    from tomd.ir import FootnoteDef, FootnoteRef

    doc = convert(make_pdf(Path(tempfile.mkdtemp()), draw))
    para = doc.blocks[1]
    assert isinstance(para, Paragraph)
    assert any(isinstance(n, FootnoteRef) for n in para.children)
    (note,) = [b for b in doc.blocks if isinstance(b, FootnoteDef)]
    assert note.blocks == (Paragraph((Text("The source of the claim."),)),)


def test_affiliation_number_does_not_steal_the_call(tmp_path: Path) -> None:
    from tomd.ir import FootnoteDef, FootnoteRef

    def draw(c: Canvas) -> None:
        x = _body(c, LEFT, TOP, "Ann Author")
        _sup(c, x, TOP, "1")  # affiliation mark
        for i in range(4):
            _body(c, LEFT, TOP - 30 - 14 * i, FILLER)
        x = _body(c, LEFT, TOP - 100, "The real call")
        _sup(c, x, TOP - 100, "1")
        c.setFont("Helvetica", 6)
        c.drawString(LEFT, 84, "1")
        c.setFont("Helvetica", 8)
        c.drawString(LEFT + 6, 80, "Note text.")

    doc = convert(make_pdf(tmp_path, draw))
    author = doc.blocks[0]
    assert isinstance(author, Paragraph)
    assert not any(isinstance(n, FootnoteRef) for n in author.children)
    assert author.children[-1] == Text(chr(0xB9))  # "¹" kept as text
    assert len([b for b in doc.blocks if isinstance(b, FootnoteDef)]) == 1


def test_shared_symbol_links_every_call(tmp_path: Path) -> None:
    from tomd.ir import FootnoteDef, FootnoteRef

    star = chr(0x2020)  # dagger: available in the standard Helvetica encoding

    def draw(c: Canvas) -> None:
        x = _body(c, LEFT, TOP, "Ann")
        x = _sup(c, x, TOP, star)
        x = _body(c, x + 20, TOP, "Bob")
        _sup(c, x, TOP, star)
        for i in range(4):
            _body(c, LEFT, TOP - 30 - 14 * i, FILLER)
        c.setFont("Helvetica", 8)
        c.drawString(LEFT, 80, star + " Equal contribution.")

    doc = convert(make_pdf(tmp_path, draw))
    refs = [n for b in doc.blocks if isinstance(b, Paragraph) for n in b.children]
    assert sum(isinstance(n, FootnoteRef) for n in refs) == 2
    assert len([b for b in doc.blocks if isinstance(b, FootnoteDef)]) == 1


def test_unmatched_note_stays_as_text_with_diagnostic(tmp_path: Path) -> None:
    from tomd.readers.pdf.footnotes import PDF_FOOTNOTE_UNMATCHED

    def draw(c: Canvas) -> None:
        for i in range(4):
            _body(c, LEFT, TOP - 14 * i, FILLER)
        c.setFont("Helvetica", 8)
        c.drawString(LEFT, 80, chr(0x2020) + " A note nobody calls.")

    doc = convert(make_pdf(tmp_path, draw))
    assert PDF_FOOTNOTE_UNMATCHED in _codes(doc)
    assert any("A note nobody calls." in str(b) for b in doc.blocks)


# --- §9.2 tables without vertical rules, author rows, nested forms ----------------------


def _filler(c: Canvas, y: float, lines: int = 4) -> None:
    for i in range(lines):
        _body(c, LEFT, y - 14 * i, FILLER)


def test_booktabs_table_with_header_rule(tmp_path: Path) -> None:
    from tomd.ir import Table

    def draw(c: Canvas) -> None:
        _filler(c, TOP)
        c.setLineWidth(0.8)
        for y in (600, 584, 530):  # top rule, mid rule (under the header), bottom rule
            c.line(200, y, 400, y)
        c.setFont("Helvetica", 10)
        c.drawString(210, 588, "Parameter")
        c.drawString(330, 588, "Value")
        for i, (k, v) in enumerate([("dim", "4096"), ("layers", "32"), ("heads", "8")]):
            c.drawString(210, 570 - 14 * i, k)
            c.drawString(330, 570 - 14 * i, v)
        _filler(c, 480)

    doc = convert(make_pdf(tmp_path, draw))
    tables = [b for b in doc.blocks if isinstance(b, Table)]
    assert len(tables) == 1
    table = tables[0]
    assert len(table.header_rows) == 1 and len(table.body_rows) == 3
    assert table.body_rows[0].cells[1].blocks == (Paragraph((Text("4096"),)),)


def test_aligned_table_without_rules_needs_a_table_cue(tmp_path: Path) -> None:
    from tomd.ir import Table

    def draw(c: Canvas) -> None:
        _filler(c, TOP)
        c.setFont("Helvetica", 10)
        rows = [("Spain", "47.4"), ("France", "68.2"), ("Italy", "58.9"), ("Portugal", "10.3")]
        for i, (name, value) in enumerate(rows):
            c.drawString(LEFT, 600 - 14 * i, name)
            c.drawString(LEFT + 120, 600 - 14 * i, value)
        _filler(c, 500)

    doc = convert(make_pdf(tmp_path, draw))
    (table,) = [b for b in doc.blocks if isinstance(b, Table)]
    assert len(table.body_rows) + len(table.header_rows) == 4


def test_spaced_author_names_stay_one_line(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        c.setFont("Helvetica-Bold", 18)
        c.drawString(LEFT, TOP + 40, "A Paper Title")
        c.setFont("Helvetica-Bold", 11)
        for x, name in ((120, "Ann Author"), (250, "Bob Writer"), (380, "Cy Scholar")):
            c.drawString(x, TOP, name)
        _filler(c, TOP - 40)

    doc = convert(make_pdf(tmp_path, draw))
    # The three names are one line, in reading order (not three blocks).
    names = doc.blocks[1]
    assert "Ann Author Bob Writer Cy Scholar" in str(names)


def test_mark_between_lines_attaches_as_superscript_of_the_line_below(tmp_path: Path) -> None:
    def draw(c: Canvas) -> None:
        _body(c, LEFT, TOP, "First line of text that is long enough to be body.")
        x = _body(c, LEFT, TOP - 12, "Second line ends with MUST")
        _sup(c, x, TOP - 12, "1")
        _filler(c, TOP - 40)

    doc = convert(make_pdf(tmp_path, draw))
    text = " ".join(_paragraph_texts(doc))
    assert "MUST" + chr(0xB9) in text and "text" + chr(0xB9) not in text


def test_figure_inside_form_xobject_gets_page_coordinates(tmp_path: Path) -> None:
    from reportlab.lib.utils import ImageReader

    def draw(c: Canvas) -> None:
        _filler(c, TOP)
        c.beginForm("fig")
        c.drawImage(_png("red"), 0, 0, 300, 150)
        c.endForm()
        c.saveState()
        c.translate(150, 400)
        c.doForm("fig")
        c.restoreState()
        _filler(c, 360)

    path = make_pdf(tmp_path, draw)
    from tomd.readers.pdf.backend import analyze_layout

    (figure,) = analyze_layout(path).figures
    x0, y0, x1, y1 = figure.bbox
    assert abs(x0 - 150) < 2 and abs(y0 - 400) < 2 and abs(x1 - 450) < 2 and abs(y1 - 550) < 2
    _ = ImageReader
