"""PDF backends (ADR-005). Everything backend-specific lives behind `PdfBackend`; the
`heuristic` backend chains the stages of the pdf-layout-heuristics skill."""

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from tomd.ir import (
    Asset,
    Block,
    CodeBlock,
    Diagnostic,
    Document,
    DocumentMeta,
    FootnoteDef,
    Heading,
    Image,
    Inline,
    ListBlock,
    ListItem,
    Paragraph,
    Provenance,
    Severity,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.readers.pdf.blocks import document_stats, merge_across_pages, segment_blocks
from tomd.readers.pdf.classify import (
    NUMBERED_HEADING,
    Classified,
    Kind,
    block_inlines,
    classify,
    code_text,
    heading_sizes,
    heading_text,
    is_page_number,
)
from tomd.readers.pdf.config import DEFAULT_CONFIG, PdfHeuristicsConfig
from tomd.readers.pdf.extract import iter_pages, open_pdf, read_metadata
from tomd.readers.pdf.figures import PlacedFloat, place_floats
from tomd.readers.pdf.footnotes import link_footnotes
from tomd.readers.pdf.furniture import remove_furniture
from tomd.readers.pdf.lines import rejoin_rows, rows_to_lines, split_rows
from tomd.readers.pdf.model import DocStats, Line, PageInfo, PdfFigure, TextBlock
from tomd.readers.pdf.ocr import (
    OcrEngine,
    OcrFailedError,
    OcrMode,
    OcrPage,
    needs_ocr,
    ocr_page,
)
from tomd.readers.pdf.order import order_lines
from tomd.readers.pdf.tables import (
    PdfTable,
    build_tables,
    find_aligned_tables,
    find_grids,
    find_rule_tables,
)

PDF_GARBAGE_TEXT = "pdf.garbage_text"
PDF_NO_TEXT_LAYER = "pdf.no_text_layer"
PDF_STRUCT_TREE_IGNORED = "pdf.struct_tree_ignored"
PDF_PERMISSIONS_RESTRICTED = "pdf.permissions_restricted"
PDF_LOW_CONFIDENCE = "pdf.low_confidence"
PDF_ROTATED_TEXT_DROPPED = "pdf.rotated_text_dropped"
PDF_FURNITURE_REMOVED = "pdf.furniture_removed"
PDF_OCR_APPLIED = "pdf.ocr_applied"
PDF_OCR_LOW_CONFIDENCE = "pdf.ocr_low_confidence"
PDF_OCR_UNAVAILABLE = "pdf.ocr_unavailable"
# Tesseract languages, tried together (ADR-017); override with TOMD_OCR_LANG.
DEFAULT_OCR_LANGUAGES = "spa+eng"

# Pages with fewer extracted characters than this and at least one image are scans.
_MIN_TEXT_CHARS = 20
# A guessed hyphen join lowers a paragraph's confidence to this (ADR-015).
_GUESSED_HYPHEN_CONFIDENCE = 0.8
# Generator defaults and file names that PDF producers leave in /Title.
_IMPLAUSIBLE_TITLE = re.compile(
    r"^(untitled|sin título|document\d*|microsoft (word|powerpoint) - .*"
    r"|.*\.(docx?|pdf|tex|odt|rtf))$",
    re.IGNORECASE,
)
_IMPLAUSIBLE_AUTHOR = frozenset({"anonymous", "unknown", "user", "admin", "administrator"})


class PdfBackend(Protocol):
    name: str

    def convert(self, path: Path) -> Document: ...


@dataclass
class LayoutResult:
    """Everything the heuristic stages infer; also consumed by `tomd debug-pdf`."""

    pages: list[PageInfo] = field(default_factory=list)
    lines: dict[int, list[Line]] = field(default_factory=dict)
    blocks: list[TextBlock] = field(default_factory=list)
    classified: list[Classified] = field(default_factory=list)
    stats: DocStats = field(default_factory=lambda: DocStats(10.0, 12.0))
    furniture_removed: int = 0
    uncertain_pages: list[int] = field(default_factory=list)
    figures: list[PdfFigure] = field(default_factory=list)
    tables: list[PdfTable] = field(default_factory=list)
    ocr_confidence: dict[int, float] = field(default_factory=dict)  # page -> mean confidence
    ocr_pages: dict[int, OcrPage] = field(default_factory=dict)
    ocr_missing: list[int] = field(default_factory=list)  # flagged pages left without OCR


def analyze_layout(
    path: Path,
    cfg: PdfHeuristicsConfig = DEFAULT_CONFIG,
    ocr: OcrMode = OcrMode.AUTO,
    engine: OcrEngine | None = None,
) -> LayoutResult:
    result = LayoutResult()
    with open_pdf(path) as pdf:
        for content in iter_pages(pdf, cfg.min_figure_area):
            info = content.info
            result.pages.append(info)
            if needs_ocr(info, content.figures, cfg, ocr):
                scanned = _ocr(pdf, info, engine, cfg) if engine else None
                if scanned is None:
                    result.ocr_missing.append(info.number)
                else:
                    grids = find_grids(scanned.rulings, cfg.table_snap_tolerance)
                    tables, _ = build_tables(grids, scanned.chars, info.number, path.name, cfg)
                    result.tables.extend(tables)
                    lines = [ln for ln in scanned.lines if not _in_tables(ln, tables)]
                    if scanned.quarter_turns % 2:  # read sideways: the page is portrait now
                        result.pages[-1] = replace(info, width=scanned.width, height=scanned.height)
                    result.ocr_pages[info.number] = scanned
                    result.lines[info.number] = lines
                    result.ocr_confidence[info.number] = (
                        round(sum(ln.confidence for ln in lines) / len(lines), 3) if lines else 0.0
                    )
                    # The scan itself is now text, not a figure.
                    area = info.width * info.height or 1.0
                    kept = [
                        f
                        for f in content.figures
                        if _area(f.bbox) / area <= cfg.ocr_min_image_cover
                    ]
                    if scanned.quarter_turns and kept:
                        # Their boxes are in the sideways frame: counted as dropped images.
                        result.pages[-1] = replace(
                            result.pages[-1], image_count=info.image_count + len(kept)
                        )
                        kept = []
                    result.figures.extend(kept)
                    continue
            result.figures.extend(content.figures)
            grids = find_grids(content.rulings, cfg.table_snap_tolerance)
            tables, chars = build_tables(grids, content.chars, info.number, path.name, cfg)
            result.tables.extend(tables)
            ruled, chars = find_rule_tables(content.rulings, chars, info.number, path.name, cfg)
            rows = split_rows(chars, cfg)
            aligned, rows = find_aligned_tables(rows, info.number, path.name, cfg)
            result.tables.extend([*ruled, *aligned])
            result.lines[info.number] = rows_to_lines(rejoin_rows(rows, cfg), info.number, cfg)
    all_lines = [line for page in result.pages for line in result.lines[page.number]]
    result.stats = document_stats(all_lines, cfg)
    heights = {p.number: p.height for p in result.pages}
    result.lines, result.furniture_removed = remove_furniture(
        result.lines, result.pages, result.stats, cfg
    )
    blocks: list[TextBlock] = []
    for page in result.pages:
        page_lines = result.lines[page.number]
        order = order_lines(page_lines, result.stats, cfg)
        page_bounds = (
            (min(ln.x0 for ln in page_lines), max(ln.x1 for ln in page_lines))
            if page_lines
            else None
        )
        if order.uncertain:
            result.uncertain_pages.append(page.number)
        for region in order.regions:
            for block in segment_blocks(region, result.stats, cfg, page_bounds):
                # Page furniture goes first, or a footer page number would sit between
                # the two halves of a paragraph that crosses the page break.
                if not is_page_number(block, heights[page.number], cfg):
                    blocks.append(block)
    result.blocks = merge_across_pages(blocks, result.stats)
    sizes = heading_sizes(result.blocks, result.stats, cfg)
    result.classified = _promote_title(
        [classify(b, result.stats, sizes, heights[b.page], cfg) for b in result.blocks]
    )
    return result


def _in_tables(line: Line, tables: list[PdfTable]) -> bool:
    x, y = (line.x0 + line.x1) / 2, (line.top + line.bottom) / 2
    return any(t.bbox[0] <= x <= t.bbox[2] and t.bbox[1] <= y <= t.bbox[3] for t in tables)


def _area(bbox: tuple[float, float, float, float]) -> float:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _ocr(pdf: Any, info: PageInfo, engine: OcrEngine, cfg: PdfHeuristicsConfig) -> OcrPage | None:
    """OCR of one page, or None when the engine fails (reported as unavailable)."""
    page = pdf[info.number - 1]
    try:
        return ocr_page(engine, page, info.number, cfg)
    except OcrFailedError:
        return None
    finally:
        page.close()


def _promote_title(classified: list[Classified]) -> list[Classified]:
    """The largest heading on page 1 is the document title (H1): a larger font elsewhere
    (a figure label, a cover badge) must not push the title down a level."""
    first_page = [
        i
        for i, c in enumerate(classified)
        if c.block.page == 1
        and c.kind is Kind.HEADING
        and not NUMBERED_HEADING.match(c.block.text.strip())  # "2 Methods" is a section
    ]
    if not first_page:
        return classified
    best = max(first_page, key=lambda i: (classified[i].block.size, -i))
    out = list(classified)
    out[best] = replace(out[best], level=1)
    return out


@dataclass
class HeuristicBackend:
    config: PdfHeuristicsConfig = DEFAULT_CONFIG
    name: str = "heuristic"
    ocr: OcrMode = OcrMode.AUTO

    def convert(self, path: Path) -> Document:
        diagnostics: list[Diagnostic] = []
        with open_pdf(path) as pdf:
            meta = read_metadata(pdf)
        engine = None
        if self.ocr is not OcrMode.NEVER:
            engine = OcrEngine.find(os.environ.get("TOMD_OCR_LANG") or DEFAULT_OCR_LANGUAGES)
        layout = analyze_layout(path, self.config, self.ocr, engine)
        source = path.name

        def report(code: str, severity: Severity, message: str, page: int | None = None) -> None:
            diagnostics.append(Diagnostic(code, severity, message, Provenance(source, page=page)))

        if meta.encrypted:
            report(PDF_PERMISSIONS_RESTRICTED, "info", "PDF has permission restrictions.")
        images = 0
        for page in layout.pages:
            images += page.image_count
            if page.number in layout.ocr_confidence:
                confidence = layout.ocr_confidence[page.number]
                scanned = layout.ocr_pages[page.number]
                fixes = []
                if scanned.quarter_turns:
                    fixes.append(f"turned {90 * scanned.quarter_turns}° clockwise")
                if scanned.skew:
                    fixes.append(f"deskewed by {scanned.skew:.1f}°")
                report(
                    PDF_OCR_APPLIED,
                    "info",
                    f"Text recognised by OCR (mean confidence {confidence:.2f}"
                    + "".join(f"; {fix}" for fix in fixes)
                    + ").",
                    page.number,
                )
                if confidence < self.config.ocr_min_confidence:
                    report(
                        PDF_OCR_LOW_CONFIDENCE,
                        "warning",
                        f"OCR confidence {confidence:.2f} is low; check the text.",
                        page.number,
                    )
                continue
            if page.number in layout.ocr_missing and self.ocr is not OcrMode.NEVER:
                report(
                    PDF_OCR_UNAVAILABLE,
                    "warning",
                    "Page needs OCR but Tesseract or its language data is missing (ADR-017).",
                    page.number,
                )
            has_image = page.image_count or any(f.page == page.number for f in layout.figures)
            if page.char_count < _MIN_TEXT_CHARS and has_image:
                report(
                    PDF_NO_TEXT_LAYER, "warning", "Page has no text layer; needs OCR.", page.number
                )
            elif (
                page.char_count and page.garbage_count / page.char_count > self.config.garbage_ratio
            ):
                report(
                    PDF_GARBAGE_TEXT,
                    "warning",
                    "Extracted text is garbage; needs OCR.",
                    page.number,
                )
        rotated = sum(p.rotated_count for p in layout.pages)
        if rotated:
            report(
                PDF_ROTATED_TEXT_DROPPED,
                "info",
                f"{rotated} rotated character(s) dropped (margin stamps, rotated labels).",
            )
        if layout.furniture_removed:
            report(
                PDF_FURNITURE_REMOVED,
                "info",
                f"{layout.furniture_removed} repeated header/footer line(s) removed.",
            )
        for number in layout.uncertain_pages:
            report(
                codes.PDF_READING_ORDER_UNCERTAIN,
                "warning",
                "Ambiguous column layout; reading order is a best guess.",
                number,
            )
        if images:
            report(
                codes.IMAGE_DROPPED,
                "info",
                f"{images} image(s) too small to be figures, or unreadable, dropped.",
            )
        if any(p.has_struct_tree for p in layout.pages):
            report(PDF_STRUCT_TREE_IGNORED, "info", "Tagged PDF structure tree not used yet.")

        heights = {p.number: p.height for p in layout.pages}
        body, notes, note_diagnostics = link_footnotes(
            layout.classified, layout.stats, heights, source
        )
        diagnostics.extend(note_diagnostics)
        floats: list[PdfFigure | PdfTable] = [*layout.figures, *layout.tables]
        flow = place_floats(body, floats, layout.stats, self.config)
        blocks, title, assets = _build_blocks(flow, source, self.config, diagnostics)
        for note in notes:
            inlines, _ = block_inlines(note.block)
            if inlines:
                blocks.append(FootnoteDef(note.key, (Paragraph(tuple(inlines)),), note.provenance))
        if not title and meta.title and not _IMPLAUSIBLE_TITLE.match(meta.title):
            title = meta.title
        authors: tuple[str, ...] = ()
        if meta.author and meta.author.strip().lower() not in _IMPLAUSIBLE_AUTHOR:
            authors = (meta.author.strip(),)
        doc_meta = DocumentMeta(
            title=title,
            source_type="pdf",
            source=source,
            source_sha256=_sha256(path),
            backend=self.name,
            language=meta.language,
            authors=authors,
        )
        return Document(doc_meta, tuple(blocks), tuple(assets), tuple(diagnostics))


def _build_blocks(
    flow: list[Classified | PlacedFloat],
    source: str,
    cfg: PdfHeuristicsConfig,
    diagnostics: list[Diagnostic],
) -> tuple[list[Block], str | None, list[Asset]]:
    out: list[Block] = []
    title: str | None = None
    items: list[Classified] = []
    assets: dict[str, Asset] = {}  # by sha256, in order of first appearance

    def flush_list() -> None:
        if items:
            out.append(_list_block(items, source))
            items.clear()

    for c in flow:
        if isinstance(c, PlacedFloat):
            flush_list()
            caption: tuple[Inline, ...] = ()
            if c.caption is not None:
                caption = tuple(block_inlines(c.caption)[0])
            if isinstance(c.item, PdfTable):
                out.append(replace(c.item.table, caption=caption))
                continue
            asset = Asset.from_bytes(c.item.data, c.item.media_type)
            assets.setdefault(asset.sha256, asset)
            prov = Provenance(source, c.item.page, c.item.bbox)
            out.append(Image(asset.sha256, "", caption, prov))
            continue
        if c.kind is Kind.LIST_ITEM:
            items.append(c)
            continue
        flush_list()
        prov = _provenance(c, source)
        match c.kind:
            case Kind.PAGE_NUMBER:
                continue
            case Kind.HEADING:
                text = heading_text(c.block)
                if c.level == 1 and title is None and c.block.page == 1:
                    title = text
                out.append(Heading(c.level, (Text(text),), prov))
            case Kind.CODE:
                out.append(CodeBlock(code_text(c.block), None, prov))
            case Kind.PARAGRAPH:
                inlines, guessed = block_inlines(c.block)
                if guessed:
                    prov = Provenance(source, prov.page, prov.bbox, _GUESSED_HYPHEN_CONFIDENCE)
                out.append(Paragraph(tuple(inlines), prov))
        if prov.confidence < cfg.min_confidence:
            diagnostics.append(
                Diagnostic(PDF_LOW_CONFIDENCE, "info", f"Low-confidence {c.kind.value}.", prov)
            )
    flush_list()
    return out, title, list(assets.values())


def _list_block(items: list[Classified], source: str) -> ListBlock:
    """Nest list items by the x position of their markers (§6)."""
    first = items[0]
    tolerance = first.block.size * DEFAULT_CONFIG.list_level_tolerance

    def build(start: int, x: float) -> tuple[ListBlock, int]:
        entries: list[ListItem] = []
        head = items[start]
        i = start
        while i < len(items):
            item = items[i]
            ix = item.block.x0
            if ix < x - tolerance:
                break
            if ix > x + tolerance and entries:
                sub, i = build(i, ix)
                last = entries[-1]
                entries[-1] = ListItem((*last.blocks, sub), last.provenance)
                continue
            inlines: list[Inline]
            inlines, _ = block_inlines(item.block, drop_marker=True)
            entries.append(ListItem((Paragraph(tuple(inlines)),), _provenance(item, source)))
            i += 1
        return ListBlock(tuple(entries), head.ordered, head.start, _provenance(head, source)), i

    lst, consumed = build(0, first.block.x0)
    if consumed < len(items):  # a shallower item after the first: start a sibling list
        rest = _list_block(items[consumed:], source)
        return ListBlock(lst.items + rest.items, lst.ordered, lst.start, lst.provenance)
    return lst


def _provenance(c: Classified, source: str) -> Provenance:
    # OCR lines carry the recogniser's confidence; digital lines are 1.0.
    confidence = c.confidence * c.block.confidence
    return Provenance(source, c.block.page, c.block.bbox, round(confidence, 3))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _docling(ocr: OcrMode) -> PdfBackend:  # docling runs its own OCR models
    from tomd.readers.pdf.docling_backend import DoclingBackend

    return DoclingBackend()


def _heuristic(ocr: OcrMode) -> PdfBackend:
    return HeuristicBackend(ocr=ocr)


BACKENDS: dict[str, Callable[[OcrMode], PdfBackend]] = {
    "heuristic": _heuristic,
    "docling": _docling,
}
