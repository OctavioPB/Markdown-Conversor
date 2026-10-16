"""Generate the synthetic PDF cases of the golden corpus (ADR-015).

Each case is described once, as data; the same description produces `input.pdf` (with
reportlab) and the `expected.md` draft. The expected text therefore comes from the source
content, never from tomd's output. Drafts still start with "# DRAFT" until reviewed.

    uv run python scripts/make_synthetic_pdfs.py            # (re)write all cases
    uv run python scripts/make_synthetic_pdfs.py --check    # fail if anything would change

Existing expected.md files without the DRAFT marker (already reviewed) are never touched.
"""

import argparse
import io
import random
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image as PILImage
from PIL import ImageChops, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable

ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus" / "pdf"
DRAFT = "# DRAFT — requiere revisión humana (generado desde la especificación del caso)\n\n"

TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=14)
H2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=15, leading=19, spaceBefore=10, spaceAfter=6
)
H3 = ParagraphStyle(
    "h3", fontName="Helvetica-Bold", fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=4
)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=14, spaceAfter=8)
CODE = ParagraphStyle("code", fontName="Courier", fontSize=9, leading=11, spaceAfter=8)
SANS = {"title": TITLE, "h2": H2, "h3": H3, "body": BODY}
SERIF = {
    "title": ParagraphStyle("s-title", parent=TITLE, fontName="Times-Bold"),
    "h2": ParagraphStyle("s-h2", parent=H2, fontName="Times-Bold"),
    "h3": ParagraphStyle("s-h3", parent=H3, fontName="Times-Bold"),
    "body": ParagraphStyle("s-body", parent=BODY, fontName="Times-Roman", fontSize=11),
}


# --- Content model (rendered both to PDF and to Markdown) ------------------------------


@dataclass(frozen=True)
class H:
    level: int  # 1 = document title
    text: str


@dataclass(frozen=True)
class P:
    markup: str  # reportlab mini-markup: <b>, <i> only


@dataclass(frozen=True)
class L:
    items: tuple["str | L", ...]  # nested L = sub-list of the previous item
    ordered: bool = False
    start: int = 1


@dataclass(frozen=True)
class C:
    code: str


@dataclass(frozen=True)
class T:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class Break:
    """Page break in the PDF; nothing in the Markdown."""


type Node = H | P | L | C | T | Break


def _md_inline(markup: str) -> str:
    return (
        markup.replace("<b>", "**").replace("</b>", "**").replace("<i>", "*").replace("</i>", "*")
    )


def to_markdown(nodes: list[Node]) -> str:
    out: list[str] = []
    for node in nodes:
        match node:
            case H(level, text):
                out.append("#" * level + " " + text)
            case P(markup):
                out.append(_md_inline(markup))
            case L():
                out.append("\n".join(_md_list(node, 0)))
            case C(code):
                out.append("```\n" + code + "\n```")
            case T(header, rows):
                table = [header, tuple("---" for _ in header), *rows]
                out.append("\n".join("| " + " | ".join(row) + " |" for row in table))
    return "\n\n".join(out) + "\n"


def _md_list(lst: L, depth: int) -> list[str]:
    lines: list[str] = []
    number = lst.start
    for item in lst.items:
        if isinstance(item, L):
            lines.extend(_md_list(item, depth + 1))
            continue
        marker = f"{number}." if lst.ordered else "-"
        indent = "   " * depth if lst.ordered else "  " * depth
        lines.append(f"{indent}{marker} {_md_inline(item)}")
        number += 1
    return lines


def _flowables(nodes: list[Node], styles: dict[str, ParagraphStyle] = SANS) -> list[Flowable]:
    out: list[Flowable] = []
    for node in nodes:
        match node:
            case H(level, text):
                out.append(Paragraph(text, styles[{1: "title", 2: "h2"}.get(level, "h3")]))
            case P(markup):
                out.append(Paragraph(markup, styles["body"]))
            case L():
                out.append(_list_flowable(node, styles))
            case C(code):
                out.append(Preformatted(code, CODE))
            case T(header, rows):
                grid = Table([list(header), *map(list, rows)], hAlign="LEFT")
                grid.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                            ("FONTSIZE", (0, 0), (-1, -1), 10.5),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ]
                    )
                )
                out.append(grid)
            case Break():
                out.append(PageBreak())
    return out


def _list_flowable(lst: L, styles: dict[str, ParagraphStyle] = SANS) -> ListFlowable:
    entries: list[list[Flowable]] = []
    for item in lst.items:
        if isinstance(item, L):
            entries[-1].append(_list_flowable(item, styles))  # nested under the previous item
        else:
            entries.append([Paragraph(item, styles["body"])])
    # The reportlab stubs do not model ListItem as a nestable flowable.
    items: list[Any] = [ListItem(flowables) for flowables in entries]
    if lst.ordered:
        return ListFlowable(
            items, bulletType="1", start=lst.start, bulletFormat="%s.", leftIndent=18
        )
    return ListFlowable(items, bulletType="bullet", start="•", leftIndent=18)


def _page_number(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    canvas.setFont("Helvetica", 9)
    canvas.drawCentredString(A4[0] / 2, 1.2 * cm, str(doc.page))


def build_flow_pdf(nodes: list[Node], styles: dict[str, ParagraphStyle] = SANS) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        title="",
        author="",
        invariant=1,
    )
    doc.build(_flowables(nodes, styles), onFirstPage=_page_number, onLaterPages=_page_number)
    return buffer.getvalue()


# --- Cases ---------------------------------------------------------------------------------

LOREM_ES = (
    "El texto de relleno sirve para comprobar que los párrafos largos se reconstruyen "
    "como una sola línea, sin cortes arbitrarios ni espacios duplicados entre palabras, "
    "y que los acentos y la eñe se conservan tal cual aparecen en la página original."
)
LOREM_EN = (
    "This filler paragraph checks that long paragraphs are rebuilt as a single line, "
    "with no arbitrary breaks and no duplicated spaces between words, across several "
    "lines of justified or ragged text in a single-column layout."
)


def case_headings() -> tuple[list[Node], bytes]:
    nodes: list[Node] = [H(1, "Manual de prueba de títulos")]
    for i in range(1, 4):
        nodes.append(H(2, f"{i} Sección número {i}"))
        nodes.append(P(LOREM_ES))
        for j in range(1, 3):
            nodes.append(H(3, f"{i}.{j} Subsección {i}.{j}"))
            nodes.append(P(LOREM_ES))
    return nodes, build_flow_pdf(nodes)


def case_lists() -> tuple[list[Node], bytes]:
    nodes: list[Node] = [
        H(1, "List handling"),
        P("A bulleted list with a nested level:"),
        L(("First item", "Second item", L(("Nested one", "Nested two")), "Third item")),
        P("An ordered list that starts at one:"),
        L(("Prepare the input", "Run the converter", "Review the output"), ordered=True),
        P(LOREM_EN),
    ]
    return nodes, build_flow_pdf(nodes)


def case_styles_and_code() -> tuple[list[Node], bytes]:
    nodes: list[Node] = [
        H(1, "Inline styles and code"),
        P("Some words are <b>bold</b>, others are <i>italic</i>, and the rest are plain."),
        P("A code sample follows; its indentation must survive:"),
        C("def factorial(n):\n    if n < 2:\n        return 1\n    return n * factorial(n - 1)"),
        P(LOREM_EN),
    ]
    return nodes, build_flow_pdf(nodes)


def case_page_break() -> tuple[list[Node], bytes]:
    nodes: list[Node] = [H(1, "Párrafos a través de páginas")]
    nodes.extend(P(LOREM_ES) for _ in range(9))
    nodes.append(P(" ".join([LOREM_ES] * 4)))  # long paragraph likely to cross a page
    nodes.extend(P(LOREM_ES) for _ in range(3))
    return nodes, build_flow_pdf(nodes)


def case_hyphenation() -> tuple[list[Node], bytes]:
    """Lines drawn by hand so that words are cut with a hyphen at the line end."""
    lines = [
        "La conversión de documentos requiere recons-",
        "truir los párrafos a partir de líneas sueltas; las pala-",
        "bras partidas deben unirse. Un compuesto como Norte-",
        "Sur conserva su guion porque continúa en mayúscula.",
    ]
    nodes: list[Node] = [
        H(1, "Guiones de corte"),
        P(
            "La conversión de documentos requiere reconstruir los párrafos a partir de "
            "líneas sueltas; las palabras partidas deben unirse. Un compuesto como "
            "Norte-Sur conserva su guion porque continúa en mayúscula."
        ),
    ]
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(2.5 * cm, A4[1] - 3 * cm, "Guiones de corte")
    canvas.setFont("Helvetica", 10.5)
    y = A4[1] - 4.2 * cm
    for line in lines:
        canvas.drawString(2.5 * cm, y, line)
        y -= 14
    canvas.showPage()
    canvas.save()
    return nodes, buffer.getvalue()


def case_two_columns() -> tuple[list[Node], bytes]:
    """Full-width title and abstract, then two columns over three pages with running heads.

    Every paragraph is numbered and distinct, so the reading order can be measured.
    """
    nodes: list[Node] = [
        H(1, "Two-column layout test"),
        P(
            "Abstract. This synthetic article checks that a full-width title and abstract "
            "come first and that the two columns below are read left column first, then "
            "right column, page after page."
        ),
    ]
    sentences = (
        "It carries enough words to wrap over several lines of a narrow column, so that "
        "lines of the left and right columns share baselines across the whole page."
    )
    for section in range(1, 9):  # long enough for 3+ pages: running heads need 3
        nodes.append(H(2, f"{section} Section {section}"))
        for k in range(1, 5):
            nodes.append(P(f"Paragraph {section}.{k}. {sentences} {sentences}"))

    buffer = io.BytesIO()
    margin, gutter = 2 * cm, 0.8 * cm
    width, height = A4
    column = (width - 2 * margin - gutter) / 2
    top_height = 5 * cm

    def running_head(canvas: Canvas, doc: BaseDocTemplate) -> None:
        canvas.setFont("Helvetica", 8)
        canvas.drawString(margin, height - 1.2 * cm, "Synthetic Journal of Layout Tests, vol. 1")
        canvas.drawCentredString(width / 2, 1.2 * cm, str(doc.page))

    body_top = height - margin - top_height
    first = PageTemplate(
        "first",
        [
            Frame(margin, body_top, width - 2 * margin, top_height, id="top"),
            Frame(margin, margin, column, body_top - margin, id="l1"),
            Frame(margin + column + gutter, margin, column, body_top - margin, id="r1"),
        ],
        onPage=running_head,
    )
    later = PageTemplate(
        "later",
        [
            Frame(margin, margin, column, height - 2 * margin, id="l"),
            Frame(margin + column + gutter, margin, column, height - 2 * margin, id="r"),
        ],
        onPage=running_head,
    )
    doc = BaseDocTemplate(buffer, pagesize=A4, pageTemplates=[first, later], invariant=1)
    flow: list[Flowable] = [Paragraph(nodes[0].text, TITLE), Paragraph(nodes[1].markup, BODY)]  # type: ignore[union-attr]
    flow += [FrameBreak(), NextPageTemplate("later")]
    flow += _flowables(nodes[2:])
    doc.build(flow)
    return nodes, buffer.getvalue()


# --- Scanned pages (phase 6, ADR-017) -----------------------------------------------------
# A digital page rendered to a bitmap, degraded like a real scan, and wrapped in an
# image-only PDF. Text is plain (OCR does not recover bold or italic).


@dataclass(frozen=True)
class Scan:
    dpi: int = 300
    noise: float = 0.0  # share of pixels flipped to black or white (salt and pepper)
    skew: float = 0.0  # degrees, counter-clockwise
    jpeg_quality: int | None = None  # None = lossless
    blur: float = 0.0  # Gaussian radius in pixels (out-of-focus scanner, photocopy)
    background: int = 0  # darkening across the page, 0-255 (uneven lighting, old paper)
    shadow: int = 0  # darkening at the left edge, 0-255 (book gutter)
    contrast: tuple[int, int] = (0, 255)  # grey levels of ink and paper (faded print)
    quarter_turns: int = 0  # page fed sideways: 1 = 90° counter-clockwise
    pages: tuple[int, ...] | None = None  # 1-based pages to scan; the rest stay digital
    bilevel: bool = False  # black and white at a fixed threshold (office document scanners)


PHOTOCOPY = Scan(
    dpi=200, skew=1.5, noise=0.002, blur=0.8, background=40, shadow=140, jpeg_quality=70
)


def _degrade(image: PILImage.Image, scan: Scan, rng: random.Random) -> PILImage.Image:
    if scan.skew:
        image = image.rotate(scan.skew, resample=PILImage.Resampling.BILINEAR, fillcolor=255)
    if scan.noise:
        pixels = image.load()
        assert pixels is not None
        width, height = image.size
        for _ in range(int(width * height * scan.noise)):
            pixels[rng.randrange(width), rng.randrange(height)] = rng.choice((0, 255))
    if scan.blur:
        image = image.filter(ImageFilter.GaussianBlur(scan.blur))
    if scan.contrast != (0, 255):
        ink, paper = scan.contrast
        image = image.point(lambda v: ink + v * (paper - ink) // 255)
    if scan.background or scan.shadow:
        width, height = image.size
        band = width // 12
        row = [
            255
            - scan.background * x // width
            - (scan.shadow * (band - x) // band if x < band else 0)
            for x in range(width)
        ]
        light = PILImage.new("L", (width, 1))
        light.putdata(row)
        image = ImageChops.multiply(image, light.resize((width, height)))
    if scan.quarter_turns:
        image = image.rotate(90 * scan.quarter_turns, expand=True)
    if scan.bilevel:
        image = image.point(lambda v: 0 if v < 160 else 255).convert("1")
    return image


def scan_pdf(digital: bytes, scan: Scan, seed: int) -> bytes:
    rng = random.Random(seed)
    src = pdfium.PdfDocument(digital)
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    for index in range(len(src)):
        page = src[index]
        image = page.render(scale=scan.dpi / 72, grayscale=True).to_pil().convert("L")
        page.close()
        image = _degrade(image, scan, rng)
        data = io.BytesIO()
        if scan.jpeg_quality is None:
            image.save(data, format="PNG", optimize=True)
        else:
            image.save(data, format="JPEG", quality=scan.jpeg_quality)
        size = landscape(A4) if scan.quarter_turns % 2 else A4
        canvas.setPageSize(size)
        canvas.drawImage(ImageReader(io.BytesIO(data.getvalue())), 0, 0, *size)
        canvas.showPage()
    src.close()
    canvas.save()
    if scan.pages is None:
        return buffer.getvalue()
    return _mix_pages(digital, buffer.getvalue(), scan.pages)


def _mix_pages(digital: bytes, scanned: bytes, pages: tuple[int, ...]) -> bytes:
    """The digital document with the given pages replaced by their scans."""
    src, scans = pdfium.PdfDocument(digital), pdfium.PdfDocument(scanned)
    out = pdfium.PdfDocument.new()
    for index in range(len(src)):
        out.import_pages(scans if index + 1 in pages else src, [index])
    buffer = io.BytesIO()
    out.save(buffer)
    for doc in (out, scans, src):
        doc.close()
    # PDFium stamps the current time and a random /ID; pin both (same lengths, so the
    # xref offsets stay valid) to keep the file byte-for-byte reproducible.
    data = re.sub(rb"D:\d{14}", b"D:20000101000000", buffer.getvalue())
    return re.sub(
        rb"/ID\[<[0-9A-F]{32}><[0-9A-F]{32}>\]",
        b"/ID[<" + b"0" * 32 + b"><" + b"0" * 32 + b">]",
        data,
    )


def _scan_nodes(lang: str) -> list[Node]:
    if lang == "es":
        return [
            H(1, "Informe escaneado de prueba"),
            P(LOREM_ES),
            H(2, "1 Antecedentes"),
            P(LOREM_ES),
            L(("Primer punto de la lista", "Segundo punto de la lista", "Tercer punto")),
            H(2, "2 Conclusiones"),
            P(LOREM_ES),
        ]
    return [
        H(1, "Scanned test report"),
        P(LOREM_EN),
        H(2, "1 Background"),
        P(LOREM_EN),
        L(("First point of the list", "Second point of the list", "Third point")),
        H(2, "2 Conclusions"),
        P(LOREM_EN),
    ]


def scan_case(lang: str, scan: Scan, seed: int) -> Callable[[], tuple[list[Node], bytes]]:
    return scan_of(lambda: _scan_nodes(lang), scan, seed)


def scan_of(
    content: Callable[[], list[Node]],
    scan: Scan,
    seed: int,
    styles: dict[str, ParagraphStyle] = SANS,
) -> Callable[[], tuple[list[Node], bytes]]:
    def build() -> tuple[list[Node], bytes]:
        nodes = content()
        return nodes, scan_pdf(build_flow_pdf(nodes, styles), scan, seed)

    return build


def scan_two_columns() -> tuple[list[Node], bytes]:
    nodes, digital = case_two_columns()
    return nodes, scan_pdf(digital, Scan(dpi=200, skew=-0.5, noise=0.0002, bilevel=True), 6)


def _survey() -> list[Node]:
    return [
        H(1, "Field survey notes"),
        P(
            "The survey team visited eleven sites along the northern ridge during the spring "
            "season. Each site was photographed, measured and described in a short report "
            "written on the same day."
        ),
        H(2, "1 Method"),
        P(
            "Measurements were taken with a calibrated tape and a handheld level. Where the "
            "ground was uneven, the team repeated every reading twice and kept the average."
        ),
        L(
            (
                "Mark the corners of the plot",
                "Record the slope and the soil type",
                "Photograph the plot from the south",
            ),
            ordered=True,
        ),
        H(2, "2 Findings"),
        P(
            "Most plots showed moderate erosion on the western side. Two sites near the river "
            "had been flooded recently and were excluded from the final count."
        ),
    ]


_ES_SENTENCES = (
    "La biblioteca amplió su horario durante los meses de exámenes.",
    "¿Cuántos lectores usan la sala de consulta cada día?",
    "Según el registro, la media superó los doscientos visitantes.",
    "¡El fondo de cartografía antigua se digitalizó por completo!",
    "Los niños del barrio participaron en el taller de encuadernación.",
    "El año próximo se prevé renovar la señalización y el mobiliario.",
)


def _library_report() -> list[Node]:
    def paragraph(k: int) -> P:
        picked = [_ES_SENTENCES[(k + i) % len(_ES_SENTENCES)] for i in range(5)]
        return P(f"Párrafo {k}. " + " ".join(picked))

    nodes: list[Node] = [H(1, "Memoria anual de la biblioteca"), H(2, "1 Introducción")]
    nodes += [paragraph(k) for k in range(1, 5)]
    nodes += [H(3, "1.1 Fondos antiguos"), *(paragraph(k) for k in range(5, 9))]
    nodes += [H(2, "2 Servicios y señalización"), *(paragraph(k) for k in range(9, 13))]
    nodes.append(P(" ".join(paragraph(k).markup for k in range(13, 17))))  # crosses a page
    nodes += [H(3, "2.1 Año próximo"), paragraph(17)]
    return nodes


def _inventory() -> list[Node]:
    return [
        H(1, "Inventory of spare parts"),
        P(
            "This page was fed into the scanner sideways, as often happens with loose sheets "
            "in a batch. The converter must detect the orientation before reading it."
        ),
        H(2, "1 Storage"),
        P(
            "Parts are stored by size in the north warehouse. Small items such as bolts and "
            "washers are kept in labelled drawers near the entrance."
        ),
        L(("Bolts and washers", "Belts and pulleys", "Filters and seals")),
    ]


def _minutes() -> list[Node]:
    return [
        H(1, "Acta de la reunión de vecinos"),
        P(
            "La reunión comenzó a las siete de la tarde en el salón del portal, con la "
            "asistencia de catorce propietarios y la administradora de la finca."
        ),
        H(2, "1 Orden del día"),
        L(("Reparación de la cubierta", "Cambio del ascensor", "Ruegos y preguntas")),
        H(2, "2 Acuerdos"),
        P(
            "Se aprobó por mayoría pedir tres presupuestos para la cubierta. La decisión sobre "
            "el ascensor se aplaza hasta la próxima junta, prevista para el mes de octubre."
        ),
    ]


def _mixed() -> list[Node]:
    def page(title: str, body: str, second: str) -> list[Node]:
        return [H(2, title), P(body), P(second)]

    nodes: list[Node] = [H(1, "Expediente con una página escaneada")]
    nodes += page(
        "1 Solicitud",
        "La primera página es digital: su texto se extrae directamente del archivo y no "
        "debe pasar por el reconocimiento óptico de caracteres.",
        "El solicitante adjunta un documento firmado a mano, que aparece en la página "
        "siguiente tal como se escaneó.",
    )
    nodes.append(Break())
    nodes += page(
        "2 Documento adjunto",
        "Esta hoja se imprimió, se firmó a mano y se volvió a escanear antes de unirla al "
        "expediente, así que solo existe como imagen.",
        "Su contenido debe recuperarse con reconocimiento óptico y quedar en el mismo orden "
        "que el resto del expediente.",
    )
    nodes.append(Break())
    nodes += page(
        "3 Resolución",
        "La resolución vuelve a ser texto digital, con el mismo formato que la solicitud inicial.",
        "Se concede lo solicitado en todos sus términos.",
    )
    return nodes


def _sales() -> list[Node]:
    return [
        H(1, "Quarterly sales summary"),
        P("The table below lists units sold per region in each quarter of the year."),
        T(
            ("Region", "Q1", "Q2", "Q3", "Q4"),
            (
                ("North", "120", "135", "150", "160"),
                ("South", "98", "102", "110", "125"),
                ("East", "143", "139", "151", "149"),
                ("West", "87", "95", "104", "118"),
            ),
        ),
        P("Totals rose in every region during the second half of the year."),
    ]


CASES: dict[str, tuple[Callable[[], tuple[list[Node], bytes]], list[str]]] = {
    "synthetic-two-columns": (
        case_two_columns,
        ["two_column", "full_width_band", "running_heads", "lang_en"],
    ),
    "synthetic-headings": (
        case_headings,
        ["numbered_headings", "heading_levels", "page_numbers", "lang_es"],
    ),
    "synthetic-lists": (case_lists, ["bullet_lists", "nested_lists", "ordered_lists", "lang_en"]),
    "synthetic-styles-code": (case_styles_and_code, ["inline_styles", "code_blocks", "lang_en"]),
    "synthetic-page-break": (
        case_page_break,
        ["paragraph_across_pages", "page_numbers", "lang_es"],
    ),
    "synthetic-hyphenation": (case_hyphenation, ["line_break_hyphens", "lang_es"]),
    "scan-clean-300dpi": (scan_case("es", Scan(), 1), ["ocr", "scan_clean", "lang_es"]),
    "scan-noise": (scan_case("en", Scan(noise=0.003), 2), ["ocr", "scan_noise", "lang_en"]),
    "scan-skew-1deg": (scan_case("es", Scan(skew=1.0), 3), ["ocr", "scan_skew", "lang_es"]),
    "scan-jpeg-q25": (
        scan_case("en", Scan(jpeg_quality=25), 4),
        ["ocr", "scan_jpeg", "lang_en"],
    ),
    "scan-150dpi": (scan_case("es", Scan(dpi=150), 5), ["ocr", "scan_low_dpi", "lang_es"]),
    "scan-photocopy": (
        scan_of(_survey, PHOTOCOPY, 7),
        ["ocr", "scan_photocopy", "ordered_lists", "lang_en"],
    ),
    "scan-two-columns": (scan_two_columns, ["ocr", "two_column", "running_heads", "lang_en"]),
    "scan-serif-es": (
        scan_of(_library_report, Scan(dpi=200, skew=0.3, bilevel=True), 8, SERIF),
        ["ocr", "serif", "numbered_headings", "paragraph_across_pages", "lang_es"],
    ),
    "scan-rotated-90": (
        scan_of(_inventory, Scan(quarter_turns=1), 9),
        ["ocr", "scan_rotated", "lang_en"],
    ),
    "scan-mixed": (
        scan_of(_mixed, Scan(noise=0.001, pages=(2,)), 10),
        ["ocr", "mixed_digital_scan", "lang_es"],
    ),
    "scan-table": (scan_of(_sales, Scan(), 11), ["ocr", "simple_tables", "lang_en"]),
    "scan-faint": (
        scan_of(_minutes, Scan(contrast=(150, 215)), 12),
        ["ocr", "scan_low_contrast", "lang_es"],
    ),
}


def write_case(name: str, check: bool) -> bool:
    build, phenomena = CASES[name]
    nodes, pdf = build()
    case_dir = (ROOT.parent / "pdf-ocr" if name.startswith("scan-") else ROOT) / name
    files = {
        "input.pdf": pdf,
        "case.yaml": (
            f'source: "Synthetic, generated by scripts/make_synthetic_pdfs.py (public domain)"\n'
            f"phenomena: [{', '.join(phenomena)}]\n"
            "known_limits: []\n"
            # PLAN phase 5: reading order >= 0.90 on the two-column corpus.
            + ("thresholds:\n  reading_order: 0.90\n" if "two_column" in phenomena else "")
        ).encode(),
    }
    expected = case_dir / "expected.md"
    if not expected.exists() or expected.read_text(encoding="utf-8").startswith("# DRAFT"):
        files["expected.md"] = (DRAFT + to_markdown(nodes)).encode()
    changed = False
    for filename, data in files.items():
        path = case_dir / filename
        if path.exists() and path.read_bytes() == data:
            continue
        changed = True
        if not check:
            case_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if any file would change")
    args = parser.parse_args(argv)
    changed = [name for name in CASES if write_case(name, args.check)]
    for name in changed:
        print(("would change: " if args.check else "wrote: ") + name, file=sys.stderr)
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
