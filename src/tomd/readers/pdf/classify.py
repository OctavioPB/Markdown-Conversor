"""Block classification (§6): headings, lists, code and paragraphs, with confidence."""

import re
from dataclasses import dataclass, replace
from enum import Enum

from tomd.ir import Code, Emph, FootnoteRef, Inline, Strong, Text
from tomd.readers.pdf.blocks import LIST_MARKER
from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import DocStats, Span, TextBlock
from tomd.readers.pdf.text import ends_with_hyphen, join_lines

NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")
_ORDERED_ITEM = re.compile(r"^\(?(\d{1,3}|[a-zA-Z]|[ivxlcdm]{1,6})[.)]\s")
_PAGE_NUMBER = re.compile(
    r"^(?:[-–—]\s*)?(?:p(?:age|ágina|ag\.)?\s*)?\d{1,4}(?:\s*(?:/|of|de)\s*\d{1,4})?(?:\s*[-–—])?$",
    re.IGNORECASE,
)
_SENTENCE_END = tuple(".:;,")
# (bold, italic, mono, superscript, footnote keys)
type _RunStyle = tuple[bool, bool, bool, bool, tuple[str, ...]]
# Superscripts that are not footnote calls keep their meaning as Unicode superscripts
# ("mc²", affiliation marks "¹,²"); characters without one stay as they are.
_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
# Monospaced glyphs are 0.6 em wide in Courier and most code fonts; used to rebuild the
# leading spaces of code lines from their x offset.
MONO_ADVANCE = 0.6


class Kind(Enum):
    HEADING = "heading"
    LIST_ITEM = "list_item"
    CODE = "code"
    PARAGRAPH = "paragraph"
    PAGE_NUMBER = "page_number"


@dataclass(frozen=True, slots=True)
class Classified:
    block: TextBlock
    kind: Kind
    level: int = 0  # heading level, or list nesting depth (0-based)
    ordered: bool = False
    start: int = 1
    confidence: float = 1.0


def heading_sizes(
    blocks: list[TextBlock], stats: DocStats, cfg: PdfHeuristicsConfig
) -> list[float]:
    """Distinct heading sizes in the document, largest first (one level each)."""
    sizes: list[float] = []
    for block in blocks:
        if _heading_shape(block, cfg) and block.size >= cfg.heading_min_size * stats.body_size:
            size = round(block.size / cfg.heading_size_tolerance) * cfg.heading_size_tolerance
            if size not in sizes:
                sizes.append(size)
    return sorted(sizes, reverse=True)


def classify(
    block: TextBlock,
    stats: DocStats,
    sizes: list[float],
    page_height: float,
    cfg: PdfHeuristicsConfig,
) -> Classified:
    text = block.text.strip()
    if is_page_number(block, page_height, cfg):
        return Classified(block, Kind.PAGE_NUMBER)
    # Code blocks are multi-line monospaced text; a single monospaced line (an email, a
    # URL, a command) reads as a paragraph with inline code.
    if len(block.lines) > 1 and all(line.mono for line in block.lines):
        return Classified(block, Kind.CODE)

    S = stats.body_size
    if _heading_shape(block, cfg):
        ratio = block.size / S if S else 1.0
        numbered = NUMBERED_HEADING.match(text)
        bold = all(line.bold for line in block.lines)
        if ratio >= cfg.heading_min_size:
            size = round(block.size / cfg.heading_size_tolerance) * cfg.heading_size_tolerance
            # More than six heading sizes (a cover set in many sizes): the smaller ones
            # share the deepest level; normalize_headings compresses the rest (M07).
            level = min(sizes.index(size) + 1, 6) if size in sizes else 2
            if numbered:
                level = min(numbered.group(1).count(".") + 2, 6)
            # Barely above the threshold is a weak signal; well above it is strong.
            confidence = min(1.0, 0.55 + (ratio - cfg.heading_min_size) * 3)
            return Classified(block, Kind.HEADING, level, confidence=confidence)
        if bold and len(block.lines) == 1 and not text.endswith(_SENTENCE_END):
            level = min(numbered.group(1).count(".") + 2, 6) if numbered else len(sizes) + 2
            return Classified(
                block, Kind.HEADING, min(level, 6), confidence=0.9 if numbered else 0.6
            )

    marker = LIST_MARKER.match(text)
    if marker:
        ordered = _ORDERED_ITEM.match(text)
        start = _ordinal(ordered.group(1)) if ordered else 1
        return Classified(block, Kind.LIST_ITEM, ordered=bool(ordered), start=start)
    return Classified(block, Kind.PARAGRAPH)


def _heading_shape(block: TextBlock, cfg: PdfHeuristicsConfig) -> bool:
    words = len(block.text.split())
    if not 0 < words <= cfg.heading_max_words or len(block.lines) > cfg.heading_max_lines:
        return False
    # A lone number or symbol (page numbers, figure labels, footnote marks) is never a
    # heading, however large.
    return any(ch.isalpha() for ch in block.text)


def is_page_number(block: TextBlock, page_height: float, cfg: PdfHeuristicsConfig) -> bool:
    """A lone page number in the top or bottom band of the page (§5)."""
    if len(block.lines) != 1 or not _PAGE_NUMBER.match(block.text.strip()):
        return False
    band = cfg.furniture_band * page_height
    return block.top >= page_height - band or block.bottom <= band


def _ordinal(token: str) -> int:
    if token.isdigit():
        return int(token)
    if len(token) == 1 and token.isalpha() and token.lower() not in "ivxlcdm":
        return ord(token.lower()) - ord("a") + 1
    return 1


# --- Inline content ----------------------------------------------------------------------


def strip_marker(text: str) -> str:
    m = LIST_MARKER.match(text.lstrip())
    return text.lstrip()[m.end() :] if m else text


def block_inlines(block: TextBlock, *, drop_marker: bool = False) -> tuple[list[Inline], bool]:
    """Inline content of a block: lines joined with hyphen repair, styles kept.

    Returns the inlines and whether a hyphen join was guessed (lowers confidence).
    """
    runs: list[tuple[str, _RunStyle]] = []
    guessed = False
    for index, line in enumerate(block.lines):
        spans = list(line.spans)
        if index == 0 and drop_marker and spans:
            spans = _drop_marker(spans)
        for span_index, span in enumerate(spans):
            text = span.text
            if span_index == 0:
                text = text.lstrip()
                if runs:
                    # Line break: same rule as text.join_lines (ADR-015).
                    prev_text, prev_style = runs[-1][0].rstrip(), runs[-1][1]
                    if ends_with_hyphen(prev_text):
                        if prev_text[-2].isalpha() and text[:1].islower():
                            runs[-1] = (prev_text[:-1], prev_style)
                            guessed = True
                        else:
                            runs[-1] = (prev_text, prev_style)
                    else:
                        runs[-1] = (prev_text + " ", prev_style)
            if text:
                style = (span.bold, span.italic, span.mono, span.superscript, span.notes)
                runs.append((text, style))
    if runs:
        runs[-1] = (runs[-1][0].rstrip(), runs[-1][1])
    return _to_inlines(runs), guessed


def _drop_marker(spans: list[Span]) -> list[Span]:
    first = spans[0]
    stripped = strip_marker(first.text)
    rest = spans[1:]
    if stripped.strip():
        return [replace(first, text=stripped), *rest]
    if rest:
        return [replace(rest[0], text=rest[0].text.lstrip()), *rest[1:]]
    return []


def _to_inlines(runs: list[tuple[str, _RunStyle]]) -> list[Inline]:
    merged: list[tuple[str, _RunStyle]] = []
    for text, style in runs:
        if merged and merged[-1][1] == style and not style[4]:
            merged[-1] = (merged[-1][0] + text, style)
        elif merged and not text.strip() and not merged[-1][1][4]:
            merged[-1] = (merged[-1][0] + text, merged[-1][1])
        else:
            merged.append((text, style))
    out: list[Inline] = []
    for text, (bold, italic, mono, superscript, notes) in merged:
        if notes:  # footnote call: the marker text is replaced by references
            # A call attaches to the preceding word ("SkyPilot²", not "SkyPilot ²").
            if out and isinstance(out[-1], Text) and out[-1].value != out[-1].value.rstrip():
                out[-1] = Text(out[-1].value.rstrip())
            out.extend(FootnoteRef(key) for key in notes)
            if text != text.rstrip():
                out.append(Text(" "))
            continue
        if superscript:
            out.append(Text(text.translate(_SUPERSCRIPT)))
            continue
        if mono:
            node: Inline = Code(text)
        else:
            node = Text(text)
            if italic:
                node = Emph((node,))
            if bold:
                node = Strong((node,))
        out.append(node)
    return out


def heading_text(block: TextBlock) -> str:
    text, _ = join_lines([line.text for line in block.lines])
    return " ".join(text.split())


def code_text(block: TextBlock) -> str:
    left = block.x0
    lines = []
    for line in block.lines:
        indent = round((line.x0 - left) / (MONO_ADVANCE * line.size)) if line.size else 0
        lines.append(" " * max(indent, 0) + line.text.rstrip())
    return "\n".join(lines)
