"""PDF backends (ADR-005). Everything backend-specific lives behind `PdfBackend`; the
`heuristic` backend chains the stages of the pdf-layout-heuristics skill."""

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from tomd.errors import UnreadableInputError
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
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import (
    PDF_FURNITURE_REMOVED,
    PDF_GARBAGE_TEXT,
    PDF_LOW_CONFIDENCE,
    PDF_NO_TEXT_LAYER,
    PDF_OCR_APPLIED,
    PDF_OCR_LOW_CONFIDENCE,
    PDF_OCR_UNAVAILABLE,
    PDF_PAGE_EMPTY,
    PDF_PAGE_UNREADABLE,
    PDF_PERMISSIONS_RESTRICTED,
    PDF_ROTATED_TEXT_DROPPED,
    PDF_STRUCT_TREE_IGNORED,
    diagnose,
)
from tomd.ir.walk import plain_text
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
from tomd.readers.pdf.figures import PlacedFloat, orphan_table_captions, place_floats
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
    TableSuspect,
    build_tables,
    find_aligned_tables,
    find_grids,
    find_rule_tables,
)
from tomd.readers.pdf.title import choose_title

# Tesseract languages, tried together (ADR-017); override with TOMD_OCR_LANG.
DEFAULT_OCR_LANGUAGES = "spa+eng"

# Pages with fewer extracted characters than this and at least one image are scans.
_MIN_TEXT_CHARS = 20
# A guessed hyphen join lowers a paragraph's confidence to this (ADR-015).
_GUESSED_HYPHEN_CONFIDENCE = 0.8
# The title is looked for on the first pages (a half-title page, then the title page).
_COVER_PAGES = 5
# Lines of a cover title share its size within this many points, and are at most this many.
_COVER_SIZE_TOLERANCE = 0.75
_COVER_MAX_LINES = 4
# Lists nest at most this deep; deeper items stay at the last level (Markdown readers and
# people stop following nesting long before).
_MAX_LIST_DEPTH = 8
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
    table_suspects: list[TableSuspect] = field(default_factory=list)
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
            aligned, rows, suspects = find_aligned_tables(rows, info.number, path.name, cfg)
            result.tables.extend([*ruled, *aligned])
            result.table_suspects.extend(suspects)
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
    result.classified = classify_blocks(
        result.blocks, result.stats, heights, result.ocr_confidence, cfg
    )
    return result


def classify_blocks(
    blocks: list[TextBlock],
    stats: DocStats,
    heights: dict[int, float],
    ocr_confidence: dict[int, float],
    cfg: PdfHeuristicsConfig,
) -> list[Classified]:
    """Classify every block (§6), then make the largest heading on page 1 the title.

    In a document with reliable pages, pages read by OCR with low confidence (a cover's
    artwork read as "OIE", "WX") take no part in the heading levels: their huge "letters"
    would take the top levels and push every real heading of the book down to H5. Their
    text stays, as paragraphs, under the page's pdf.ocr_low_confidence warning. A
    document that is all a poor scan keeps its headings: there is nothing better."""
    unreliable = {page for page, mean in ocr_confidence.items() if mean < cfg.ocr_min_confidence}
    if all(b.page in unreliable for b in blocks):
        unreliable = set()
    sizes = heading_sizes([b for b in blocks if b.page not in unreliable], stats, cfg)
    classified = [classify(b, stats, sizes, heights[b.page], cfg) for b in blocks]
    classified = [
        replace(c, kind=Kind.PARAGRAPH, level=0)
        if c.kind is Kind.HEADING and c.block.page in unreliable
        else c
        for c in classified
    ]
    return _promote_title(classified)


def _report_lost_tables(
    flow: list[Classified | PlacedFloat],
    suspects: list[TableSuspect],
    source: str,
    diagnostics: list[Diagnostic],
) -> None:
    """§9 strategy 3: a table caption with no table, or aligned rows that were not
    rebuilt, means a table left as text. One diagnostic per page, at the first place."""
    places: dict[int, list[tuple[tuple[float, float, float, float], str]]] = {}
    for caption in orphan_table_captions(flow):
        label = (caption.text.strip().splitlines() or [""])[0][:40]
        places.setdefault(caption.page, []).append((caption.bbox, f"caption «{label}»"))
    for suspect in suspects:
        places.setdefault(suspect.page, []).append(
            (suspect.bbox, f"{suspect.rows} rows aligned in columns")
        )
    for page in sorted(places):
        found = sorted(places[page], key=lambda p: -p[0][3])  # top of the page first
        what = "; ".join(cue for _, cue in found)
        diagnostics.append(
            diagnose(
                codes.PDF_TABLE_NOT_RECONSTRUCTED,
                f"Page {page}: a table was left as text ({what}).",
                Provenance(source, page, found[0][0]),
            )
        )


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

        def report(code: str, message: str, page: int | None = None) -> None:
            diagnostics.append(diagnose(code, message, Provenance(source, page=page)))

        if meta.encrypted:
            report(PDF_PERMISSIONS_RESTRICTED, "Permission flags (copy, print) ignored.")
        unreadable = [p.number for p in layout.pages if p.unreadable]
        if unreadable and len(unreadable) == len(layout.pages):
            raise UnreadableInputError(
                f"No page of {source} could be read: the file is damaged. Re-export it from "
                "its source or repair it (`qpdf in.pdf out.pdf`)."
            )
        for number in (p.number for p in layout.pages if p.empty and not p.unreadable):
            report(PDF_PAGE_EMPTY, f"Page {number} has no content.", number)
        for number in unreadable:
            report(
                PDF_PAGE_UNREADABLE, f"Page {number} could not be loaded; it is missing.", number
            )
        lost_images = small_images = 0
        for page in layout.pages:
            lost_images += page.image_count
            small_images += page.small_image_count
            if page.number in layout.ocr_confidence:
                confidence = layout.ocr_confidence[page.number]
                scanned = layout.ocr_pages[page.number]
                fixes = []
                if scanned.quarter_turns:
                    fixes.append(f"turned {90 * scanned.quarter_turns}° clockwise")
                if scanned.skew:
                    fixes.append(f"deskewed by {scanned.skew:.1f}°")
                if scanned.dpi < self.config.ocr_dpi:
                    fixes.append(f"page too large, read at {scanned.dpi} dpi")
                report(
                    PDF_OCR_APPLIED,
                    f"Page {page.number} read by OCR (mean confidence {confidence:.2f}"
                    + "".join(f"; {fix}" for fix in fixes)
                    + ").",
                    page.number,
                )
                if confidence < self.config.ocr_min_confidence:
                    report(
                        PDF_OCR_LOW_CONFIDENCE,
                        f"Page {page.number}: mean OCR confidence {confidence:.2f} "
                        f"(threshold {self.config.ocr_min_confidence:.2f}).",
                        page.number,
                    )
                continue
            if page.number in layout.ocr_missing and self.ocr is not OcrMode.NEVER:
                report(
                    PDF_OCR_UNAVAILABLE,
                    f"Page {page.number} needs OCR, but Tesseract or its language data "
                    f"({os.environ.get('TOMD_OCR_LANG') or DEFAULT_OCR_LANGUAGES}) is missing.",
                    page.number,
                )
            has_image = (
                page.image_count
                or page.small_image_count
                or any(f.page == page.number for f in layout.figures)
            )
            if page.char_count < _MIN_TEXT_CHARS and has_image:
                report(
                    PDF_NO_TEXT_LAYER,
                    f"Page {page.number} is an image without a text layer; its text is missing.",
                    page.number,
                )
            elif (
                page.char_count and page.garbage_count / page.char_count > self.config.garbage_ratio
            ):
                share = page.garbage_count / page.char_count
                report(
                    PDF_GARBAGE_TEXT,
                    f"Page {page.number}: {share:.0%} of the extracted characters are garbage.",
                    page.number,
                )
        rotated = sum(p.rotated_count for p in layout.pages)
        if rotated:
            report(PDF_ROTATED_TEXT_DROPPED, f"{rotated} rotated character(s) dropped.")
        if layout.furniture_removed:
            report(
                PDF_FURNITURE_REMOVED,
                f"{layout.furniture_removed} repeated header/footer line(s) removed.",
            )
        for number in layout.uncertain_pages:
            report(
                codes.PDF_READING_ORDER_UNCERTAIN,
                f"Page {number}: ambiguous columns; the reading order is a best guess.",
                number,
            )
        if lost_images:
            report(
                codes.IMAGE_DROPPED,
                f"{lost_images} image(s) unreadable or too large to decode safely, dropped.",
            )
        if small_images:
            report(codes.IMAGE_SKIPPED_SMALL, f"{small_images} small image(s) skipped.")
        if any(p.has_struct_tree for p in layout.pages):
            report(PDF_STRUCT_TREE_IGNORED, "Structure tree (tags) ignored; layout inferred.")

        heights = {p.number: p.height for p in layout.pages}
        body, notes, note_diagnostics = link_footnotes(
            layout.classified, layout.stats, heights, source
        )
        diagnostics.extend(note_diagnostics)
        floats: list[PdfFigure | PdfTable] = [*layout.figures, *layout.tables]
        flow = place_floats(body, floats, layout.stats, self.config)
        _report_lost_tables(flow, layout.table_suspects, source, diagnostics)
        blocks, title, assets = _build_blocks(flow, source, self.config, diagnostics)
        for note in notes:
            inlines, _ = block_inlines(note.block)
            if inlines:
                blocks.append(FootnoteDef(note.key, (Paragraph(tuple(inlines)),), note.provenance))
        # The title among three witnesses (title.py): the metadata, the cover's largest
        # lines and the file name. Artwork read as letters on a scanned cover ("AP") never
        # gets here: low-confidence OCR pages give no headings (classify_blocks), and
        # what remains is checked for plausibility.
        plausible = bool(meta.title) and not _IMPLAUSIBLE_TITLE.match(meta.title or "")
        cover = _cover_title(layout.classified)
        front = " ".join(c.block.text for c in layout.classified if c.block.page <= _COVER_PAGES)
        chosen = choose_title(
            meta.title if plausible else None, cover, path.stem, front,
            cover_from_ocr=1 in layout.ocr_pages,
        )  # fmt: skip
        if chosen and title and _key(chosen) != _key(title):
            # The page-1 heading that is not the title (a logo, one line of the title)
            # stays as text, not as a second H1 that would push every heading down.
            blocks = [
                Paragraph(b.children, b.provenance)
                if isinstance(b, Heading) and b.level == 1 and heading_text_of(b) == title
                else b
                for b in blocks
            ]
        title = chosen or title
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
                diagnose(
                    PDF_LOW_CONFIDENCE,
                    f"Page {prov.page}: {c.kind.value} classified with confidence "
                    f"{prov.confidence:.2f}.",
                    prov,
                )
            )
    flush_list()
    return out, title, list(assets.values())


def _cover_title(classified: list[Classified]) -> str | None:
    """The cover's title: on the first page with headings among the first pages, the
    lines set in its largest size that follow one another, joined ("Data Analysis" +
    "with Open Source Tools"). It ends at the first other block: a formula or an author
    line of the same size further down is not part of it."""
    for page in range(1, _COVER_PAGES + 1):
        on_page = [c for c in classified if c.block.page == page]
        heads = [c for c in on_page if c.kind is Kind.HEADING]
        if not heads:
            continue
        floor = max(c.block.size for c in heads) - _COVER_SIZE_TOLERANCE
        lines: list[str] = []
        for c in on_page:
            if c.kind is Kind.HEADING and c.block.size >= floor:
                lines.append(c.block.text)
                if len(lines) == _COVER_MAX_LINES:
                    break
            elif lines:
                break
        return " ".join(" ".join(lines).split()) or None
    return None


def heading_text_of(block: Heading) -> str:
    return " ".join(plain_text(block.children).split())


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def _list_block(items: list[Classified], source: str) -> ListBlock:
    """Nest list items by the x position of their markers (§6)."""
    first = items[0]
    tolerance = abs(first.block.size) * DEFAULT_CONFIG.list_level_tolerance

    def build(start: int, x: float, depth: int) -> tuple[ListBlock, int]:
        entries: list[ListItem] = []
        head = items[start]
        i = start
        while i < len(items):
            item = items[i]
            ix = item.block.x0
            if ix < x - tolerance:
                break
            # Deeper than Markdown nesting is useful: the item stays at this level (and
            # the recursion stays bounded on hostile or odd layouts).
            if ix > x + tolerance and entries and depth < _MAX_LIST_DEPTH:
                sub, i = build(i, ix, depth + 1)
                last = entries[-1]
                entries[-1] = ListItem((*last.blocks, sub), last.provenance)
                continue
            inlines: list[Inline]
            inlines, _ = block_inlines(item.block, drop_marker=True)
            entries.append(ListItem((Paragraph(tuple(inlines)),), _provenance(item, source)))
            i += 1
        return ListBlock(tuple(entries), head.ordered, head.start, _provenance(head, source)), i

    # A shallower item after the first starts a sibling run, joined to the same list. A
    # loop, not recursion: a book's index can be a list of thousands of runs.
    lst, consumed = build(0, first.block.x0, 1)
    entries = list(lst.items)
    while consumed < len(items):
        rest, used = build(consumed, items[consumed].block.x0, 1)
        entries.extend(rest.items)
        # Past the run's first item at least, whatever the geometry: never stuck.
        consumed = max(used, consumed + 1)
    return ListBlock(tuple(entries), lst.ordered, lst.start, lst.provenance)


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
