"""Characters -> lines (§2): baseline clustering, superscripts, spaces from gaps, and
splitting lines at gaps that indicate columns or table cells."""

from itertools import pairwise
from statistics import mean, median

from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import Char, Line, Span

# A raised glyph smaller than this × the line size is a superscript (note call, exponent).
_SUPERSCRIPT_SIZE = 0.9
# ... raised at least this × the line size above the baseline (subscripts and ordinary
# small text sit on or below it).
_RAISE = 0.15
# Lines with fewer glyph gaps than this use a zero typical gap (too few to measure).
_MIN_GAPS_FOR_MEDIAN = 4
# Typical script positions relative to the host baseline, in × host size.
_SUPERSCRIPT_OFFSET = 0.33
_SUBSCRIPT_OFFSET = 0.2


type Part = list[Char]
type Row = list[Part]  # one visual row, split at wide gaps, left to right


def group_lines(chars: list[Char], page: int, cfg: PdfHeuristicsConfig) -> list[Line]:
    """Group characters into lines, top to bottom then left to right."""
    return rows_to_lines(rejoin_rows(split_rows(chars, cfg), cfg), page, cfg)


def split_rows(chars: list[Char], cfg: PdfHeuristicsConfig) -> list[Row]:
    """Cluster characters into rows by baseline and split each row at wide gaps (columns,
    table cells, spaced-out names)."""
    rows: list[list[Char]] = []
    for ch in sorted(chars, key=lambda c: (-c.baseline, c.x0)):
        if rows and _same_row(rows[-1], ch, cfg):
            rows[-1].append(ch)
        else:
            rows.append([ch])
    rows = _attach_superscripts(rows)
    return [p for p in (_split_row(sorted(r, key=lambda c: c.x0), cfg) for r in rows) if p]


def rejoin_rows(rows: list[Row], cfg: PdfHeuristicsConfig) -> list[Row]:
    """Merge the parts of each row again unless the gap between them lies on a page gutter.

    Splitting at wide gaps is needed to find columns and table cells, but a single row of
    spaced-out author names or a formula with its number at the margin is one line. A
    gutter is an x position crossed by the gaps of many rows (a column gutter); gaps that
    do not cross one are rejoined.
    """
    gutters = _gutters(rows, cfg)
    out: list[Row] = []
    for row in rows:
        merged: Row = [list(row[0])]
        for part in row[1:]:
            left = max(c.x1 for c in merged[-1] if not c.is_space)
            right = min(c.x0 for c in part if not c.is_space)
            if any(left <= g <= right for g in gutters):
                merged.append(list(part))
            else:
                merged[-1].extend(part)
        out.append(merged)
    return out


def rows_to_lines(rows: list[Row], page: int, cfg: PdfHeuristicsConfig) -> list[Line]:
    lines: list[Line] = []
    for row in rows:
        for part in row:
            line = _make_line(part, page, cfg)
            if line is not None:
                lines.append(line)
    return lines


def _gutters(rows: list[Row], cfg: PdfHeuristicsConfig) -> list[float]:
    gaps: list[list[tuple[float, float]]] = []
    for row in rows:
        row_gaps = []
        for a, b in pairwise(row):
            left = max(c.x1 for c in a if not c.is_space)
            right = min(c.x0 for c in b if not c.is_space)
            row_gaps.append((left, right))
        if row_gaps:
            gaps.append(row_gaps)
    if not gaps:
        return []
    needed = max(cfg.gutter_min_rows, cfg.gutter_min_share * len(gaps))
    candidates = sorted((a + b) / 2 for row_gaps in gaps for a, b in row_gaps)
    gutters: list[float] = []
    for x in candidates:
        support = sum(1 for row_gaps in gaps if any(a <= x <= b for a, b in row_gaps))
        if support >= needed and not any(abs(x - g) < 1.0 for g in gutters):
            gutters.append(x)
    return gutters


def _row_size(row: list[Char]) -> float:
    visible = [c.size for c in row if not c.is_space]
    return max(visible) if visible else max(c.size for c in row)


def _row_baseline(row: list[Char]) -> float:
    size = _row_size(row)
    main = [c.baseline for c in row if not c.is_space and c.size >= size * _SUPERSCRIPT_SIZE]
    return main[0] if main else row[0].baseline


def _same_row(row: list[Char], ch: Char, cfg: PdfHeuristicsConfig) -> bool:
    size = max(_row_size(row), ch.size)
    return abs(_row_baseline(row) - ch.baseline) < cfg.same_line_baseline * size


def _attach_superscripts(rows: list[list[Char]]) -> list[list[Char]]:
    """A row of small glyphs whose baseline falls inside the vertical span of the next or
    previous row belongs to that row (exponents and note calls sit above the baseline)."""
    out: list[list[Char]] = []
    pending = [list(r) for r in rows]
    for i, row in enumerate(pending):
        above = out[-1] if out else None
        below = pending[i + 1] if i + 1 < len(pending) else None
        hosts = [h for h in (above, below) if h is not None and _is_raised_row(row, h)]
        if not hosts:
            out.append(row)
            continue
        # A mark between two lines can be a superscript of the line below or a subscript of
        # the line above: take the host it sits closest to the typical position for.
        host = min(hosts, key=lambda h: _script_deviation(row, h))
        host.extend(row)
    return out


def _script_deviation(row: list[Char], host: list[Char]) -> float:
    size = _row_size(host)
    offset = _row_baseline(row) - _row_baseline(host)
    expected = _SUPERSCRIPT_OFFSET * size if offset > 0 else -_SUBSCRIPT_OFFSET * size
    return abs(offset - expected)


def _is_raised_row(row: list[Char], host: list[Char]) -> bool:
    size, host_size = _row_size(row), _row_size(host)
    if size >= host_size * _SUPERSCRIPT_SIZE:
        return False
    host_top = max(c.top for c in host)
    host_bottom = min(c.bottom for c in host)
    baseline = _row_baseline(row)
    # Superscripts sit above the host baseline, subscripts slightly below its bottom.
    return host_bottom - size * 0.5 <= baseline <= host_top


def _split_row(row: list[Char], cfg: PdfHeuristicsConfig) -> list[list[Char]]:
    parts: list[list[Char]] = [[]]
    last: Char | None = None
    for ch in row:
        gap = ch.x0 - last.x1 if last is not None else 0.0
        if (
            last is not None
            and not ch.is_space
            and gap > cfg.split_line_gap * max(ch.size, last.size)
        ):
            parts.append([])
        parts[-1].append(ch)
        if not ch.is_space:
            last = ch
    return [p for p in parts if any(not c.is_space for c in p)]


def _make_line(chars: list[Char], page: int, cfg: PdfHeuristicsConfig) -> Line | None:
    visible = [c for c in chars if not c.is_space]
    if not visible:
        return None
    widths = [c.x1 - c.x0 for c in visible if c.x1 > c.x0]
    mean_width = mean(widths) if widths else visible[0].size * 0.5
    space_at = _space_threshold(chars, mean_width, cfg)
    size = _dominant_size(visible)
    baseline = _row_baseline(chars)

    spans: list[Span] = []
    text: list[str] = []
    style: tuple[bool, bool, bool, float, bool] | None = None
    prev: Char | None = None
    for ch in chars:
        if ch.is_space:
            if text and not text[-1].endswith(" "):
                text.append(" ")
            prev = None
            continue
        gap_is_space = prev is not None and ch.x0 - prev.x1 > space_at
        if gap_is_space and text and not text[-1].endswith(" "):
            text.append(" ")
        raised = ch.size <= _SUPERSCRIPT_SIZE * size and ch.baseline > baseline + _RAISE * size
        ch_style = (ch.bold, ch.italic, ch.mono, ch.size, raised)
        if style is not None and ch_style != style and text:
            spans.append(Span("".join(text), *style))
            text = []
        style = ch_style
        text.append(ch.text)
        prev = ch
    if text and style is not None:
        spans.append(Span("".join(text), *style))
    return Line(
        spans=tuple(spans),
        x0=min(c.x0 for c in visible),
        x1=max(c.x1 for c in visible),
        top=max(c.top for c in visible),
        bottom=min(c.bottom for c in visible),
        baseline=baseline,
        size=size,
        page=page,
    )


def _space_threshold(chars: list[Char], mean_width: float, cfg: PdfHeuristicsConfig) -> float:
    """Gap above which two glyphs are separate words.

    Measured from the line's own typical gap (the median, which is the gap inside words),
    so italics whose overhangs eat into word gaps and letter-spaced text both work.
    """
    visible = [c for c in chars if not c.is_space]
    gaps = [b.x0 - a.x1 for a, b in pairwise(visible)]
    typical = median(gaps) if len(gaps) >= _MIN_GAPS_FOR_MEDIAN else 0.0
    return max(typical, 0.0) + cfg.space_gap * mean_width


def _dominant_size(chars: list[Char]) -> float:
    counts: dict[float, int] = {}
    for c in chars:
        counts[c.size] = counts.get(c.size, 0) + 1
    return max(sorted(counts), key=lambda s: counts[s])
