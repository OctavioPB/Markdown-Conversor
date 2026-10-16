"""Tables from ruling grids (§9, strategy 1).

Rules that intersect form a grid; a missing interior rule between two grid cells means a
spanned cell. Characters inside the grid fill its cells and leave the text flow. Tables
without rules are not detected here (§9 strategy 2 is not implemented): their text flows
as paragraphs, a documented limit of the heuristic backend.
"""

import re
from dataclasses import dataclass, field

from tomd.ir import Cell, Paragraph, Provenance, Row, Strong, Table
from tomd.readers.pdf.classify import block_inlines
from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.lines import group_lines
from tomd.readers.pdf.model import Char, Ruling, TextBlock

# A cell that is a number (with units, signs, percentages...).
_NUMERIC = re.compile(r"[-+±~≈<>]?\$?\d[\d.,\s]*(?:%|[a-zA-Zμ]{0,3})")
# A column is numeric when at least this share of its cells are numbers.
_NUMERIC_SHARE = 0.6


@dataclass(frozen=True, slots=True)
class GridCell:
    row: int
    col: int
    rowspan: int
    colspan: int
    bbox: tuple[float, float, float, float]  # x0, bottom, x1, top


@dataclass(frozen=True, slots=True)
class Grid:
    bbox: tuple[float, float, float, float]
    rows: int
    cols: int
    cells: tuple[GridCell, ...]


@dataclass(frozen=True, slots=True)
class PdfTable:
    page: int
    bbox: tuple[float, float, float, float]
    table: Table = field(repr=False)

    @property
    def top(self) -> float:
        return self.bbox[3]

    @property
    def bottom(self) -> float:
        return self.bbox[1]


def find_grids(rulings: list[Ruling], tol: float) -> list[Grid]:
    grids: list[Grid] = []
    for group in _connected(rulings, tol):
        horizontal = [r for r in group if r.horizontal]
        vertical = [r for r in group if not r.horizontal]
        ys = sorted(_cluster([r.position for r in horizontal], tol), reverse=True)
        xs = sorted(_cluster([r.position for r in vertical], tol))
        rows, cols = len(ys) - 1, len(xs) - 1
        if rows < 1 or cols < 1 or rows * cols < 2:
            continue  # a single box frames a figure or a note, it is not a table
        cells: list[GridCell] = []
        occupied: set[tuple[int, int]] = set()
        for r in range(rows):
            for c in range(cols):
                if (r, c) in occupied:
                    continue
                y_mid = (ys[r] + ys[r + 1]) / 2
                colspan = 1
                while c + colspan < cols and not _covers(vertical, xs[c + colspan], y_mid, tol):
                    colspan += 1
                x_mid = (xs[c] + xs[c + colspan]) / 2
                rowspan = 1
                while r + rowspan < rows and not _covers(horizontal, ys[r + rowspan], x_mid, tol):
                    rowspan += 1
                occupied |= {(r + i, c + j) for i in range(rowspan) for j in range(colspan)}
                bbox = (xs[c], ys[r + rowspan], xs[c + colspan], ys[r])
                cells.append(GridCell(r, c, rowspan, colspan, bbox))
        grids.append(Grid((xs[0], ys[-1], xs[-1], ys[0]), rows, cols, tuple(cells)))
    return grids


def build_tables(
    grids: list[Grid], chars: list[Char], page: int, source: str, cfg: PdfHeuristicsConfig
) -> tuple[list[PdfTable], list[Char]]:
    """Fill the grids with the characters inside them; return the tables and the
    characters left for the text flow."""
    remaining = list(chars)
    tables: list[PdfTable] = []
    for grid in grids:
        inside = [ch for ch in remaining if _contains(grid.bbox, ch)]
        if not inside:
            continue  # an empty grid is a drawing, not a table
        taken = {id(ch) for ch in inside}
        remaining = [ch for ch in remaining if id(ch) not in taken]
        rows: dict[int, list[Cell]] = {}
        header = True
        for cell in grid.cells:
            cell_chars = [ch for ch in inside if _contains(cell.bbox, ch)]
            lines = group_lines(cell_chars, page, cfg)
            blocks: tuple[Paragraph, ...] = ()
            if lines:
                inlines, _ = block_inlines(TextBlock(lines))
                blocks = (Paragraph(tuple(inlines)),)
            if (
                cell.row == 0
                and cell_chars
                and not all(ch.bold for ch in cell_chars if not ch.is_space)
            ):
                header = False
            rows.setdefault(cell.row, []).append(Cell(blocks, cell.rowspan, cell.colspan))
        all_rows = [Row(tuple(rows.get(r, []))) for r in range(grid.rows)]
        head = tuple(all_rows[:1]) if header else ()
        body = tuple(all_rows[1:]) if header else tuple(all_rows)
        prov = Provenance(source, page, grid.bbox)
        tables.append(PdfTable(page, grid.bbox, Table(head, body, (), prov)))
    return tables, remaining


def _contains(bbox: tuple[float, float, float, float], ch: Char) -> bool:
    x0, y0, x1, y1 = bbox
    cx = (ch.x0 + ch.x1) / 2
    cy = (ch.top + ch.bottom) / 2
    return x0 <= cx <= x1 and y0 <= cy <= y1


def _covers(rulings: list[Ruling], position: float, at: float, tol: float) -> bool:
    """Whether some rule lies at `position` and passes through coordinate `at`."""
    return any(
        abs(r.position - position) <= tol and r.start - tol <= at <= r.end + tol for r in rulings
    )


def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[float] = []
    for value in sorted(values):
        if out and value - out[-1] <= tol:
            continue
        out.append(value)
    return out


def _connected(rulings: list[Ruling], tol: float) -> list[list[Ruling]]:
    """Groups of rules that touch each other (union-find)."""
    parent = list(range(len(rulings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(rulings):
        for j in range(i + 1, len(rulings)):
            if _touch(a, rulings[j], tol):
                parent[find(i)] = find(j)
    groups: dict[int, list[Ruling]] = {}
    for i, ruling in enumerate(rulings):
        groups.setdefault(find(i), []).append(ruling)
    return list(groups.values())


def _touch(a: Ruling, b: Ruling, tol: float) -> bool:
    if a.horizontal != b.horizontal:
        h, v = (a, b) if a.horizontal else (b, a)
        return (
            h.start - tol <= v.position <= h.end + tol
            and v.start - tol <= h.position <= v.end + tol
        )
    # Collinear pieces of the same rule (tables drawn cell by cell).
    return abs(a.position - b.position) <= tol and min(a.end, b.end) >= max(a.start, b.start) - tol


# --- Tables without vertical rules (§9.2) ------------------------------------------------


def find_rule_tables(
    rulings: list[Ruling],
    chars: list[Char],
    page: int,
    source: str,
    cfg: PdfHeuristicsConfig,
) -> tuple[list[PdfTable], list[Char]]:
    """Booktabs-style tables: two or more horizontal rules of the same extent, without
    vertical rules, enclose a block whose text splits into aligned columns."""
    tol = cfg.table_snap_tolerance
    vertical = [r for r in rulings if not r.horizontal]
    free = [r for r in rulings if r.horizontal and not any(_touch(r, v, tol) for v in vertical)]
    groups: list[list[Ruling]] = []
    for rule in sorted(free, key=lambda r: (r.start, r.end, -r.position)):
        for group in groups:
            if (
                abs(group[0].start - rule.start) <= 2 * tol
                and abs(group[0].end - rule.end) <= 2 * tol
            ):
                group.append(rule)
                break
        else:
            groups.append([rule])
    tables: list[PdfTable] = []
    remaining = list(chars)
    for group in groups:
        if len(group) < 2:
            continue
        ys = sorted({round(r.position, 1) for r in group}, reverse=True)
        top, bottom = ys[0], ys[-1]
        x0 = min(r.start for r in group) - tol
        x1 = max(r.end for r in group) + tol
        inside = [
            c
            for c in remaining
            if x0 <= (c.x0 + c.x1) / 2 <= x1 and bottom < (c.top + c.bottom) / 2 < top
        ]
        rows = _char_rows(inside, cfg)
        if len(rows) < 2:
            continue
        mids = ys[1:-1]
        header = sum(1 for row in rows if mids and _row_mid(row) > mids[0])
        table = _table_from_rows(rows, header, page, (x0, bottom, x1, top), source, cfg, 0.9)
        if table is None:
            continue
        taken = {id(c) for c in inside}
        remaining = [c for c in remaining if id(c) not in taken]
        tables.append(table)
    return tables, remaining


def find_aligned_tables(
    rows: "list[list[list[Char]]]",
    page: int,
    source: str,
    cfg: PdfHeuristicsConfig,
) -> tuple[list[PdfTable], "list[list[list[Char]]]"]:
    """Tables without any rule: runs of consecutive rows split into short parts that share
    column gaps (§9.2). Two-column body text never qualifies: its lines are long."""
    visible = [c for row in rows for part in row for c in part if not c.is_space]
    if not visible:
        return [], rows
    width = max(c.x1 for c in visible) - min(c.x0 for c in visible)

    def candidate(row: list[list[Char]]) -> bool:
        if len(row) < 2:
            return False
        return all(
            _extent(part)[1] - _extent(part)[0] < cfg.aligned_cell_max_width * width for part in row
        )

    tables: list[PdfTable] = []
    keep: list[list[list[Char]]] = []
    i = 0
    while i < len(rows):
        run = [rows[i]] if candidate(rows[i]) else []
        j = i + 1
        while run and j < len(rows) and candidate(rows[j]) and _close_rows(run[-1], rows[j], cfg):
            run.append(rows[j])
            j += 1
        if len(run) >= cfg.aligned_min_rows:
            flat = [[c for part in row for c in part] for row in run]
            header = 1 if all(c.bold for c in flat[0] if not c.is_space) else 0
            if not (header or _looks_tabular(run)):
                keep.append(rows[i])
                i += 1
                continue
            xs = [c for row in flat for c in row]
            bbox = (
                min(c.x0 for c in xs),
                min(c.bottom for c in xs),
                max(c.x1 for c in xs),
                max(c.top for c in xs),
            )
            table = _table_from_rows(flat, header, page, bbox, source, cfg, 0.7)
            if table is not None:
                tables.append(table)
                i = j
                continue
        keep.append(rows[i])
        i += 1
    return tables, keep


def _looks_tabular(run: list[list[list[Char]]]) -> bool:
    """Alignment alone also matches two columns of short lines; a table needs one more
    cue: three or more columns, or a column of numbers."""
    if max(len(row) for row in run) >= 3:
        return True
    for column in range(min(len(row) for row in run)):
        cells = ["".join(c.text for c in row[column]).strip() for row in run]
        numeric = sum(1 for text in cells if _NUMERIC.fullmatch(text))
        if numeric >= _NUMERIC_SHARE * len(cells):
            return True
    return False


def _extent(part: list[Char]) -> tuple[float, float]:
    visible = [c for c in part if not c.is_space] or part
    return min(c.x0 for c in visible), max(c.x1 for c in visible)


def _close_rows(a: list[list[Char]], b: list[list[Char]], cfg: PdfHeuristicsConfig) -> bool:
    size = max(c.size for part in a for c in part)
    return (
        _row_mid([c for p in a for c in p]) - _row_mid([c for p in b for c in p])
        <= cfg.aligned_max_row_gap * size
    )


def _row_mid(row: list[Char]) -> float:
    return sum((c.top + c.bottom) / 2 for c in row) / len(row)


def _char_rows(chars: list[Char], cfg: PdfHeuristicsConfig) -> list[list[Char]]:
    """Characters clustered into rows by baseline, top to bottom."""
    rows: list[list[Char]] = []
    for ch in sorted(chars, key=lambda c: (-c.baseline, c.x0)):
        if rows and abs(rows[-1][0].baseline - ch.baseline) < cfg.same_line_baseline * ch.size:
            rows[-1].append(ch)
        else:
            rows.append([ch])
    return [r for r in rows if any(not c.is_space for c in r)]


def _columns(rows: list[list[Char]], cfg: PdfHeuristicsConfig) -> list[tuple[float, float]]:
    """Column intervals: x ranges separated by gaps that no row crosses."""
    spans = sorted((c.x0, c.x1, c.size) for row in rows for c in row if not c.is_space)
    columns: list[tuple[float, float]] = []
    start, reach, _ = spans[0]
    for x0, x1, size in spans[1:]:
        if x0 - reach >= cfg.table_min_column_gap * size:
            columns.append((start, reach))
            start = x0
        reach = max(reach, x1)
    columns.append((start, reach))
    return columns


def _table_from_rows(
    rows: list[list[Char]],
    header: int,
    page: int,
    bbox: tuple[float, float, float, float],
    source: str,
    cfg: PdfHeuristicsConfig,
    confidence: float,
) -> PdfTable | None:
    columns = _columns(rows, cfg)
    if len(columns) < 2:
        return None
    grid: list[list[list[Char]]] = []
    for row in rows:
        cells: list[list[Char]] = [[] for _ in columns]
        for ch in row:
            centre = (ch.x0 + ch.x1) / 2
            index = next((k for k, (a, b) in enumerate(columns) if a - 1 <= centre <= b + 1), None)
            if index is not None:
                cells[index].append(ch)
        filled = sum(1 for cell in cells if any(not c.is_space for c in cell))
        # A row with an empty first cell continues the row above (a wrapped cell).
        if grid and len(grid) > header and not any(not c.is_space for c in cells[0]) and filled:
            for k, cell in enumerate(cells):
                grid[-1][k].extend(cell)
            continue
        grid.append(cells)
    if len(grid) < 2 or sum(1 for row in grid if sum(1 for c in row if c) >= 2) < 2:
        return None  # not tabular enough: leave the text to the paragraph flow
    out_rows = [
        Row(tuple(_cell(cell, page, cfg, header=r < header) for cell in row))
        for r, row in enumerate(grid)
    ]
    prov = Provenance(source, page, bbox, confidence)
    return PdfTable(page, bbox, Table(tuple(out_rows[:header]), tuple(out_rows[header:]), (), prov))


def _cell(chars: list[Char], page: int, cfg: PdfHeuristicsConfig, *, header: bool = False) -> Cell:
    lines = group_lines(chars, page, cfg) if chars else []
    if not lines:
        return Cell(())
    inlines, _ = block_inlines(TextBlock(lines))
    if header:  # bold is what marks the header row; in a header cell it is redundant
        inlines = [
            n for node in inlines for n in (node.children if isinstance(node, Strong) else (node,))
        ]
    return Cell((Paragraph(tuple(inlines)),))
