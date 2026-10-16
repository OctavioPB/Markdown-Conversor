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
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
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


type Node = H | P | L | C


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


def _flowables(nodes: list[Node]) -> list[Flowable]:
    out: list[Flowable] = []
    for node in nodes:
        match node:
            case H(level, text):
                out.append(Paragraph(text, {1: TITLE, 2: H2}.get(level, H3)))
            case P(markup):
                out.append(Paragraph(markup, BODY))
            case L():
                out.append(_list_flowable(node))
            case C(code):
                out.append(Preformatted(code, CODE))
    return out


def _list_flowable(lst: L) -> ListFlowable:
    entries: list[list[Flowable]] = []
    for item in lst.items:
        if isinstance(item, L):
            entries[-1].append(_list_flowable(item))  # nested list under the previous item
        else:
            entries.append([Paragraph(item, BODY)])
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


def build_flow_pdf(nodes: list[Node]) -> bytes:
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
    doc.build(_flowables(nodes), onFirstPage=_page_number, onLaterPages=_page_number)
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
}


def write_case(name: str, check: bool) -> bool:
    build, phenomena = CASES[name]
    nodes, pdf = build()
    case_dir = ROOT / name
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
