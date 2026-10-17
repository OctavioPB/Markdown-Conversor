"""Intermediate layout objects of the heuristic backend (not IR: positions in points,
PDF coordinates with y growing upwards)."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Char:
    text: str
    x0: float
    x1: float
    top: float  # glyph box top (y up)
    bottom: float
    baseline: float
    size: float
    font: str
    bold: bool
    italic: bool
    mono: bool

    @property
    def is_space(self) -> bool:
        return self.text.isspace()


@dataclass(frozen=True, slots=True)
class Span:
    """A run of text with uniform style inside a line."""

    text: str
    bold: bool
    italic: bool
    mono: bool
    size: float
    superscript: bool = False
    # Footnote keys this span calls (set by footnote linking; the text is then dropped).
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Line:
    spans: tuple[Span, ...]
    x0: float
    x1: float
    top: float
    bottom: float
    baseline: float
    size: float  # dominant size (by characters)
    page: int  # 1-based
    confidence: float = 1.0  # OCR word confidence; 1.0 for digital text

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def bold(self) -> bool:
        visible = [s for s in self.spans if s.text.strip()]
        return bool(visible) and all(s.bold for s in visible)

    @property
    def mono(self) -> bool:
        visible = [s for s in self.spans if s.text.strip()]
        return bool(visible) and all(s.mono for s in visible)


@dataclass(slots=True)
class TextBlock:
    lines: list[Line] = field(default_factory=list)

    @property
    def page(self) -> int:
        return self.lines[0].page

    @property
    def x0(self) -> float:
        return min(line.x0 for line in self.lines)

    @property
    def x1(self) -> float:
        return max(line.x1 for line in self.lines)

    @property
    def top(self) -> float:
        return max(line.top for line in self.lines)

    @property
    def bottom(self) -> float:
        return min(line.bottom for line in self.lines)

    @property
    def size(self) -> float:
        weights: dict[float, int] = {}
        for line in self.lines:
            weights[line.size] = weights.get(line.size, 0) + len(line.text)
        return max(sorted(weights), key=lambda s: weights[s])

    @property
    def text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.bottom, self.x1, self.top)

    @property
    def confidence(self) -> float:
        return min(line.confidence for line in self.lines)


@dataclass(frozen=True, slots=True)
class PageInfo:
    number: int  # 1-based
    width: float
    height: float
    char_count: int
    garbage_count: int
    image_count: int = 0  # images lost: unreadable, or too large to decode safely
    has_struct_tree: bool = False
    rotated_count: int = 0
    unreadable: bool = False  # PDFium could not load the page; it contributes nothing
    empty: bool = False  # no page objects at all: blank, or an undecodable content stream
    small_image_count: int = 0  # images too small to be figures (icons, logos)


@dataclass(frozen=True, slots=True)
class DocStats:
    body_size: float  # S
    line_spacing: float  # L


@dataclass(frozen=True, slots=True)
class PdfFigure:
    page: int
    bbox: tuple[float, float, float, float]  # x0, bottom, x1, top
    data: bytes = field(repr=False)
    media_type: str = "image/png"

    @property
    def top(self) -> float:
        return self.bbox[3]

    @property
    def bottom(self) -> float:
        return self.bbox[1]


@dataclass(frozen=True, slots=True)
class Ruling:
    """A straight horizontal or vertical line segment drawn on the page."""

    horizontal: bool
    position: float  # y for horizontal rules, x for vertical ones
    start: float  # x0 / y0
    end: float  # x1 / y1
