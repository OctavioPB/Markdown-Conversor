"""Floats (figures and tables) and their captions (§6, §9): place each one in the reading
order by position and attach the "Figure N" / "Table N" block next to it as its caption."""

import re
from dataclasses import dataclass

from tomd.readers.pdf.classify import Classified, Kind
from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import DocStats, PdfFigure, TextBlock
from tomd.readers.pdf.tables import PdfTable

FIGURE_CAPTION = re.compile(
    r"^(?:fig(?:ure|ura)?s?\.?|imagen|gr[aá]fico|ilustraci[oó]n|l[aá]mina|plate|chart)\s*\d+",
    re.IGNORECASE,
)
# A block at most this × its font size away from a float, beside it, wraps around it.
_BESIDE_GAP = 2.5
TABLE_CAPTION = re.compile(r"^(?:tab(?:le|la)|cuadro)\.?\s*\d+", re.IGNORECASE)

type Float = PdfFigure | PdfTable


@dataclass(frozen=True, slots=True)
class PlacedFloat:
    item: Float
    caption: TextBlock | None


def place_floats(
    classified: list[Classified],
    floats: list[Float],
    stats: DocStats,
    cfg: PdfHeuristicsConfig,
) -> list[Classified | PlacedFloat]:
    max_distance = cfg.caption_max_distance * stats.line_spacing
    captions: dict[int, int] = {}  # float index -> classified index
    used: set[int] = set()
    for f_index, item in enumerate(floats):
        pattern = TABLE_CAPTION if isinstance(item, PdfTable) else FIGURE_CAPTION
        best: tuple[float, int] | None = None
        for c_index, c in enumerate(classified):
            if c_index in used or c.block.page != item.page or c.kind is Kind.CODE:
                continue
            if not pattern.match(c.block.text.lstrip()) or not _near(c.block, item):
                continue
            distance = min(abs(item.bottom - c.block.top), abs(c.block.bottom - item.top))
            if distance <= max_distance and (best is None or distance < best[0]):
                best = (distance, c_index)
        if best is not None:
            captions[f_index] = best[1]
            used.add(best[1])

    anchors: dict[int, list[int]] = {}
    for f_index, item in enumerate(floats):
        anchors.setdefault(_anchor(classified, item, used), []).append(f_index)

    out: list[Classified | PlacedFloat] = []
    for c_index in range(len(classified) + 1):
        for f_index in sorted(anchors.get(c_index, []), key=lambda i: -floats[i].top):
            caption = classified[captions[f_index]].block if f_index in captions else None
            out.append(PlacedFloat(floats[f_index], caption))
        if c_index < len(classified) and c_index not in used:
            out.append(classified[c_index])
    return out


def _anchor(classified: list[Classified], item: Float, used: set[int]) -> int:
    """Where the float goes in the reading order.

    A float beside the text (paragraphs wrapping around it) goes right after the first
    block it sits next to. Otherwise it goes before the first block below it that overlaps
    it horizontally, or after the last block of its page.
    """
    after = 0
    for index, c in enumerate(classified):
        if index in used:
            continue
        if c.block.page < item.page:
            after = index + 1
        elif c.block.page == item.page:
            if _beside(c.block, item):
                return index + 1
            if c.block.top <= item.bottom and _overlaps(c.block, item):
                return index
            after = index + 1
    return after


def _beside(block: TextBlock, item: Float) -> bool:
    """The block runs alongside the float: vertical overlap, small horizontal gap."""
    x0, y0, x1, y1 = item.bbox
    vertical = min(block.top, y1) - max(block.bottom, y0) > 0
    gap = max(x0 - block.x1, block.x0 - x1)
    return vertical and 0 <= gap <= _BESIDE_GAP * block.size


def _near(block: TextBlock, item: Float) -> bool:
    """Captions are often left-aligned under centred floats: accept a caption that
    overlaps the float widened by its own width on each side (not the other column)."""
    x0, _, x1, _ = item.bbox
    width = x1 - x0
    return min(block.x1, x1 + width) > max(block.x0, x0 - width)


def _overlaps(block: TextBlock, item: Float) -> bool:
    x0, _, x1, _ = item.bbox
    return min(block.x1, x1) > max(block.x0, x0)
