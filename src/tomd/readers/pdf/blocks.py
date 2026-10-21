"""Document statistics (§1) and lines -> blocks (§3), including paragraphs that continue
across a page break."""

import re
from statistics import median

from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import DocStats, Line, TextBlock

LIST_MARKER = re.compile(r"^(?:[•◦▪‣▸►●○■□–\-*·]|\(?(?:\d{1,3}|[a-zA-Z]|[ivxlcdm]{1,6})[.)])\s")
# How many following lines (in baseline order) to scan for the next line of the same
# column; three columns put the next own-column line at most a few lines later.
_NEIGHBOUR_SCAN = 6
# Blocks set below this × S are small print (footnotes, notes at the foot of a column).
_SMALL_PRINT = 0.9
# A paragraph resuming below a float continues within this × L of its previous line.
_WRAP_GAP = 3.0
_FINAL_PUNCT = tuple(".!?:;…»”\"')")


def document_stats(lines: list[Line], cfg: PdfHeuristicsConfig) -> DocStats:
    """S = character-weighted mode of the rounded font size; L = the line spacing inside
    paragraphs, estimated from consecutive body-size lines of the same page.

    The plain median of those distances is not L: in documents made of short paragraphs or
    list items, most consecutive lines belong to *different* blocks, so the median measures
    paragraph spacing. L is the tightest gap that recurs (see `_leading`)."""
    weights: dict[float, int] = {}
    for line in lines:
        size = _round(line.size, cfg.size_rounding)
        weights[size] = weights.get(size, 0) + len(line.text.strip())
    if not weights:
        return DocStats(body_size=10.0, line_spacing=12.0)
    body = max(sorted(weights), key=lambda s: weights[s])
    # Small print (abstracts, footnotes) can outweigh the body on a single page; when a
    # larger size is nearly as frequent, the larger one is the body text.
    for size in sorted(weights):
        if size > body and weights[size] >= cfg.body_size_tie * weights[body]:
            body = size

    # Gap from each body line to the next body line *below it in the same column*
    # (horizontally overlapping): in multi-column pages, consecutive lines in page order
    # alternate columns and their baselines differ by a few points only.
    gaps: list[float] = []
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        if _round(line.size, cfg.size_rounding) == body:
            by_page.setdefault(line.page, []).append(line)
    for page_lines in by_page.values():
        ordered = sorted(page_lines, key=lambda ln: -ln.baseline)
        for i, line in enumerate(ordered):
            for below in ordered[i + 1 : i + 1 + _NEIGHBOUR_SCAN]:
                gap = line.baseline - below.baseline
                if gap <= 0 or min(line.x1, below.x1) <= max(line.x0, below.x0):
                    continue
                if gap < 3 * body:
                    gaps.append(gap)
                break
    spacing = _leading(gaps, cfg) if gaps else body * 1.2
    return DocStats(body_size=body, line_spacing=spacing)


def _leading(gaps: list[float], cfg: PdfHeuristicsConfig) -> float:
    """Median of the gaps around the smallest gap value that recurs often enough."""
    counts: dict[float, int] = {}
    for gap in gaps:
        key = _round(gap, cfg.leading_rounding)
        counts[key] = counts.get(key, 0) + 1
    needed = max(cfg.leading_min_count, cfg.leading_min_share * len(gaps))
    recurring = sorted(k for k, n in counts.items() if n >= needed)
    if not recurring:
        return median(gaps)
    tight = recurring[0]
    cluster = [g for g in gaps if tight - cfg.leading_rounding <= g <= tight * cfg.leading_cluster]
    return median(cluster)


def _round(value: float, step: float) -> float:
    return round(value / step) * step


def segment_blocks(
    lines: list[Line],
    stats: DocStats,
    cfg: PdfHeuristicsConfig,
    page_bounds: tuple[float, float] | None = None,
) -> list[TextBlock]:
    """Split lines of one region (already in reading order) into blocks.

    `page_bounds` (left, right) is the text area of the whole page, used to recognise
    centred lines; it defaults to the region's own extent.
    """
    if not lines:
        return []
    right = _text_right_edge(lines, stats)
    bounds = page_bounds or (min(ln.x0 for ln in lines), max(ln.x1 for ln in lines))
    blocks: list[TextBlock] = [TextBlock([lines[0]])]
    for line in lines[1:]:
        block = blocks[-1]
        if _continues(block, line, stats, right, bounds, cfg):
            block.lines.append(line)
        else:
            blocks.append(TextBlock([line]))
    return blocks


def _is_centred(
    line: Line, bounds: tuple[float, float], S: float, cfg: PdfHeuristicsConfig
) -> bool:
    left_room = line.x0 - bounds[0]
    right_room = bounds[1] - line.x1
    tolerance = cfg.block_indent_change * S
    return left_room > tolerance and abs(left_room - right_room) <= tolerance


def _text_right_edge(lines: list[Line], stats: DocStats) -> float:
    body = [line.x1 for line in lines if abs(line.size - stats.body_size) <= 0.5]
    return max(body) if body else max(line.x1 for line in lines)


def _continues(
    block: TextBlock,
    line: Line,
    stats: DocStats,
    right: float,
    bounds: tuple[float, float],
    cfg: PdfHeuristicsConfig,
) -> bool:
    prev = block.lines[-1]
    S, L = stats.body_size, stats.line_spacing
    scale = max(prev.size, line.size) / S if S else 1.0
    if prev.baseline - line.baseline > cfg.block_gap * L * max(scale, 1.0):
        return False
    if abs(prev.size - line.size) > cfg.block_size_change * S:
        return False
    if prev.mono != line.mono or prev.bold != line.bold:
        return False
    if prev.mono and line.mono:
        # Code: indentation and short lines are content, not block boundaries.
        return True
    if LIST_MARKER.match(line.text.lstrip()):
        return False
    first = block.lines[0]
    if LIST_MARKER.match(first.text.lstrip()):
        # Hanging indent: continuation lines align with the item text, right of the marker.
        if line.x0 <= first.x0 + cfg.block_indent_change * S * 0.25:
            return False
    elif abs(line.x0 - prev.x0) > cfg.block_indent_change * S:
        # Accepted shifts: a first-line indent (line 1 indented, line 2 at the margin) and
        # centred lines sharing an axis (multi-line titles, author blocks).
        first_line_indent = len(block.lines) == 1 and line.x0 < prev.x0
        # Centred lines leave about as much room on the left as on the right of the page's
        # text area; a paragraph's short last line hugs the left margin instead.
        centred = _is_centred(prev, bounds, S, cfg) and _is_centred(line, bounds, S, cfg)
        # A title set flush right ("Software Architect Capability" / "Model" on a chapter
        # opening page): heading-sized lines that share their right edge. Only headings:
        # ragged-left body text is too rare to risk paragraph boundaries for.
        flush_right = (
            min(prev.size, line.size) >= cfg.heading_min_size * S
            and abs(line.x1 - prev.x1) <= cfg.block_indent_change * S * 0.25
        )
        if not (first_line_indent or centred or flush_right):
            return False
    ends_paragraph = prev.text.rstrip().endswith(_FINAL_PUNCT)
    if _opens_with_run_in_label(line) and (ends_paragraph or _opens_with_run_in_label(prev)):
        return False  # "**Code:** …", "**Key words.** …": a new paragraph or entry
    short = prev.x1 < block.x0 + cfg.short_line_ratio * (right - block.x0)
    return not (ends_paragraph and short)


def _opens_with_run_in_label(line: Line) -> bool:
    """A bold lead-in followed by regular text on the same line."""
    visible = [s for s in line.spans if s.text.strip()]
    return len(visible) >= 2 and visible[0].bold and not all(s.bold for s in visible)


def merge_across_pages(blocks: list[TextBlock], stats: DocStats) -> list[TextBlock]:
    """Join a paragraph cut by a page or column break: the block before the break does
    not end in final punctuation, the one after starts in lowercase, same size and style.

    A column break shows as the next block (in reading order) starting higher on the
    same page than the previous one ended. Small print at the foot of the column or page
    (footnotes, below 0.9 × S) is held back while the interrupted paragraph continues,
    then emitted after it.
    """
    out: list[TextBlock] = []
    held: list[TextBlock] = []  # small print waiting for the paragraph to resume
    for block in blocks:
        body = out[-1] if out else None
        if body is not None and _is_small_print(block, stats) and _open_ended(body):
            held.append(block)
            continue
        if body is not None and _continues_across_break(body, block, stats):
            body.lines.extend(block.lines)
            continue
        out.extend(held)
        held = []
        out.append(block)
    out.extend(held)
    return out


def _is_small_print(block: TextBlock, stats: DocStats) -> bool:
    return block.size < _SMALL_PRINT * stats.body_size


def _open_ended(block: TextBlock) -> bool:
    return not block.lines[-1].text.rstrip().endswith(_FINAL_PUNCT)


def _continues_across_break(prev: TextBlock, block: TextBlock, stats: DocStats) -> bool:
    last, first = prev.lines[-1], block.lines[0]
    text = first.text.lstrip()
    next_page = block.page == prev.page + 1
    next_column = block.page == prev.page and first.top > last.top
    # Text wrapping around a float (a table or figure beside the paragraph) ends one
    # region and resumes in the region just below it.
    just_below = (
        block.page == prev.page
        and first.top <= last.bottom
        and last.baseline - first.baseline <= _WRAP_GAP * stats.line_spacing
    )
    return (
        (next_page or next_column or just_below)
        and last.bold == first.bold
        and _open_ended(prev)
        and text[:1].islower()
        and abs(last.size - first.size) <= 0.5
        and last.mono == first.mono
        and not LIST_MARKER.match(text)
    )
