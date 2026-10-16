"""`tomd debug-pdf`: SVG overlay of one page at one stage of the heuristic pipeline.

Debugging layout without seeing it is guessing (pdf-layout-heuristics skill). Boxes are
drawn in page coordinates; open the SVG next to the PDF page.
"""

from html import escape
from pathlib import Path

from tomd.readers.pdf.backend import analyze_layout
from tomd.readers.pdf.config import DEFAULT_CONFIG

STAGES = ("lines", "blocks", "order", "classify")
_COLORS = {
    "heading": "#d62728",
    "list_item": "#2ca02c",
    "code": "#9467bd",
    "paragraph": "#1f77b4",
    "page_number": "#7f7f7f",
}


def render_debug_svg(path: Path, page: int, stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    layout = analyze_layout(path, DEFAULT_CONFIG)
    info = next((p for p in layout.pages if p.number == page), None)
    if info is None:
        raise ValueError(f"page {page} out of range (1-{len(layout.pages)})")
    width, height = info.width, info.height

    def rect(box: tuple[float, float, float, float], color: str, label: str) -> str:
        x0, y0, x1, y1 = box
        y = height - y1
        return (
            f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="0.8"/>'
            f'<text x="{x0:.1f}" y="{y - 1:.1f}" font-size="6" fill="{color}">'
            f"{escape(label)}</text>"
        )

    shapes: list[str] = []
    if stage == "lines":
        for i, line in enumerate(layout.lines.get(page, [])):
            label = f"{i} s={line.size:g}{' B' if line.bold else ''}{' M' if line.mono else ''}"
            shapes.append(rect((line.x0, line.bottom, line.x1, line.top), "#1f77b4", label))
    else:
        index = 0
        for c in layout.classified:
            if c.block.page != page:
                continue
            index += 1
            kind = c.kind.value
            if stage == "blocks":
                label, color = f"{index}", "#1f77b4"
            elif stage == "order":
                label, color = f"#{index}", "#ff7f0e"
            else:
                level = f" h{c.level}" if c.kind.value == "heading" else ""
                label, color = f"{kind}{level} c={c.confidence:.2f}", _COLORS[kind]
            shapes.append(rect(c.block.bbox, color, label))
    stats = layout.stats
    header = (
        f"<!-- {escape(path.name)} page {page} stage {stage}: "
        f"S={stats.body_size:g} L={stats.line_spacing:.2f} -->"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.1f} {height:.1f}">{header}'
        f'<rect width="100%" height="100%" fill="white"/>' + "".join(shapes) + "</svg>\n"
    )
