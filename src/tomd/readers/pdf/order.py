"""Reading order (§4): full-width bands, then a recursive XY-cut over line boxes.

Ordering works on lines, before block segmentation: lines are grouped page-wide by
baseline, so in a two-column page consecutive lines alternate between columns until the
columns are separated here.
"""

from dataclasses import dataclass, field

from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import DocStats, Line

# Gutters narrower than this × the minimum column gap make the column split doubtful.
_NARROW_GUTTER = 1.2


@dataclass
class OrderResult:
    regions: list[list[Line]] = field(default_factory=list)  # in reading order
    uncertain: bool = False


def order_lines(lines: list[Line], stats: DocStats, cfg: PdfHeuristicsConfig) -> OrderResult:
    result = OrderResult()
    if not lines:
        return result
    left = min(line.x0 for line in lines)
    right = max(line.x1 for line in lines)
    full_width = cfg.full_width_ratio * (right - left)

    band: list[Line] = []
    for line in sorted(lines, key=lambda ln: (-ln.top, ln.x0)):
        if line.x1 - line.x0 >= full_width:
            if band:
                _xy_cut(band, stats, cfg, result)
                band = []
            result.regions.append([line])
        else:
            band.append(line)
    if band:
        _xy_cut(band, stats, cfg, result)
    return _merge_consecutive(result)


def _xy_cut(
    lines: list[Line], stats: DocStats, cfg: PdfHeuristicsConfig, result: OrderResult
) -> None:
    if len(lines) > 1:
        # Wide horizontal valleys first (header and footer bands, section breaks): a
        # page number centred in the column gutter would otherwise block the column cut.
        rows = _horizontal_split(lines, cfg.xy_wide_row_gap * stats.line_spacing)
        if len(rows) > 1:
            for row in rows:
                _xy_cut(row, stats, cfg, result)
            return
        min_gap = cfg.xy_min_column_gap * stats.body_size
        cut = _vertical_cut(lines, min_gap)
        if cut is not None:
            x, width = cut
            left = [ln for ln in lines if (ln.x0 + ln.x1) / 2 < x]
            right = [ln for ln in lines if (ln.x0 + ln.x1) / 2 >= x]
            # A "column" of one or two lines is an equation number or a margin note,
            # not a column: no cut. A gutter that barely clears the threshold is doubtful.
            if min(len(left), len(right)) >= cfg.xy_min_column_lines:
                if width < min_gap * _NARROW_GUTTER:
                    result.uncertain = True
                _xy_cut(left, stats, cfg, result)
                _xy_cut(right, stats, cfg, result)
                return
        rows = _horizontal_split(lines, cfg.xy_min_row_gap * stats.line_spacing)
        if len(rows) > 1:
            for row in rows:
                _xy_cut(row, stats, cfg, result)
            return
    result.regions.append(sorted(lines, key=lambda ln: (-ln.baseline, ln.x0)))


def _vertical_cut(lines: list[Line], min_gap: float) -> tuple[float, float] | None:
    """(x, width) of the widest gutter (empty vertical strip) crossing the region."""
    spans = sorted((ln.x0, ln.x1) for ln in lines)
    reach = spans[0][1]
    best: tuple[float, float] | None = None
    for x0, x1 in spans[1:]:
        gap = x0 - reach
        if gap >= min_gap and (best is None or gap > best[1]):
            best = ((reach + x0) / 2, gap)
        reach = max(reach, x1)
    return best


def _horizontal_split(lines: list[Line], min_gap: float) -> list[list[Line]]:
    ordered = sorted(lines, key=lambda ln: -ln.top)
    rows: list[list[Line]] = [[ordered[0]]]
    floor = ordered[0].bottom
    for line in ordered[1:]:
        if floor - line.top >= min_gap:
            rows.append([line])
        else:
            rows[-1].append(line)
        floor = min(floor, line.bottom)
    return rows


def _merge_consecutive(result: OrderResult) -> OrderResult:
    """Adjacent regions that are plain vertical continuations (same left edge) are one
    region again, so paragraphs split by row cuts can be re-joined by block segmentation."""
    merged: list[list[Line]] = []
    for region in result.regions:
        if merged and _continues(merged[-1], region):
            merged[-1].extend(region)
        else:
            merged.append(list(region))
    return OrderResult(merged, result.uncertain)


def _continues(above: list[Line], below: list[Line]) -> bool:
    last, first = above[-1], below[0]
    if first.top > last.bottom:  # not below (e.g. the top of the next column)
        return False
    # Below and horizontally overlapping: the same column continues. Indentation (code,
    # quotes) may move the left edge; block segmentation still splits on gaps.
    a0, a1 = min(ln.x0 for ln in above), max(ln.x1 for ln in above)
    b0, b1 = min(ln.x0 for ln in below), max(ln.x1 for ln in below)
    return min(a1, b1) > max(a0, b0)
