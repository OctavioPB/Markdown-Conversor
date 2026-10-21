"""Repeated headers and footers (§5), removed at line level before ordering."""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import DocStats, Line, PageInfo

_DIGITS = re.compile(r"\d+")
_NOT_WORD = re.compile(r"[\W_]+")
_ANY_PARITY = -1


@dataclass(frozen=True, slots=True)
class _Slot:
    parity: int
    edge: str  # "top" or "bottom"


@dataclass
class _Cluster:
    text: str  # first text seen, the representative
    distance: float  # to the page edge, of the first line seen
    pages: set[int] = field(default_factory=set)


def remove_furniture(
    lines: dict[int, list[Line]],
    pages: list[PageInfo],
    stats: DocStats,
    cfg: PdfHeuristicsConfig,
) -> tuple[dict[int, list[Line]], int]:
    """Drop lines repeated near the same page edge on many pages of the same parity.

    Odd and even pages are counted apart: books alternate the book title and the chapter
    title in their running heads. Texts are compared fuzzily, because the text layer of
    OCRed documents varies from page to page ("Pinilla" / "Pinillay"). Returns the
    remaining lines and how many were removed.
    """
    heights = {p.number: p.height for p in pages}
    unit = cfg.furniture_y_tolerance * stats.body_size
    clusters: dict[_Slot, list[_Cluster]] = {}
    membership: dict[tuple[int, int], list[_Cluster]] = {}
    for number, page_lines in lines.items():
        band = cfg.furniture_band * heights[number]
        for index, line in enumerate(page_lines):
            located = _locate(line, number, heights[number], band)
            if located is None:
                continue
            slot, distance, text = located
            # Counted per parity (alternating running heads) and over all pages (short
            # documents have too few pages of each parity).
            for key in (slot, _Slot(_ANY_PARITY, slot.edge)):
                group = clusters.setdefault(key, [])
                cluster = _find(group, text, distance, unit, cfg.furniture_text_similarity)
                cluster.pages.add(number)
                membership.setdefault((number, index), []).append(cluster)

    numbers = sorted(heights)
    per_parity = {parity: sum(1 for p in pages if p.number % 2 == parity) for parity in (0, 1)}
    per_parity[_ANY_PARITY] = len(pages)

    def is_furniture(cluster: _Cluster, parity: int) -> bool:
        count = len(cluster.pages)
        if count < cfg.furniture_min_pages:
            return False
        if count >= cfg.furniture_min_share * per_parity[parity]:
            return True
        # A running head of one chapter: dense over the pages it spans. Only with words:
        # lone numbers near an edge (the page numbers of a contents list) are not one.
        if not any(c.isalpha() for c in cluster.text):
            return False
        first, last = min(cluster.pages), max(cluster.pages)
        stretch = sum(
            1 for n in numbers if first <= n <= last and (parity == _ANY_PARITY or n % 2 == parity)
        )
        return count >= cfg.furniture_min_run_share * stretch

    furniture = {
        id(cluster)
        for slot, group in clusters.items()
        for cluster in group
        if is_furniture(cluster, slot.parity)
    }
    removed = 0
    kept: dict[int, list[Line]] = {}
    for number, page_lines in lines.items():
        kept[number] = []
        for index, line in enumerate(page_lines):
            members = membership.get((number, index), [])
            if any(id(member) in furniture for member in members):
                removed += 1
            else:
                kept[number].append(line)
    return kept, removed


def _locate(line: Line, page: int, height: float, band: float) -> tuple[_Slot, float, str] | None:
    if line.top >= height - band:
        edge, distance = "top", height - line.top
    elif line.bottom <= band:
        edge, distance = "bottom", line.bottom
    else:
        return None
    text = _NOT_WORD.sub("", _DIGITS.sub("#", line.text).lower())
    if not text:
        return None
    return _Slot(page % 2, edge), distance, text


def _find(
    group: list[_Cluster], text: str, distance: float, unit: float, threshold: float
) -> _Cluster:
    """The cluster at about the same distance from the edge with similar text. Distances
    are compared, not bucketed: scans and OCR place one running head a little higher or
    lower from page to page, and a bucket boundary would split it."""
    for cluster in group:
        if abs(cluster.distance - distance) > unit:
            continue
        if cluster.text == text or SequenceMatcher(None, cluster.text, text).ratio() >= threshold:
            return cluster
    cluster = _Cluster(text, distance)
    group.append(cluster)
    return cluster
