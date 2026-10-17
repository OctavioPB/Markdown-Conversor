"""OCR for scanned pages (§8, ADR-017): the per-page gate and Tesseract via pytesseract.

OCR output becomes `Line` objects in PDF coordinates, so blocks, headings, lists and
reading order work exactly as for digital pages. Each line carries Tesseract's mean word
confidence.
"""

import math
import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from statistics import median
from typing import Any

from lxml import html
from PIL import Image, ImageDraw, ImageOps

from tomd.readers.pdf.config import PdfHeuristicsConfig
from tomd.readers.pdf.model import Char, Line, PageInfo, PdfFigure, Ruling, Span


class OcrMode(Enum):
    AUTO = "auto"  # only pages the gate flags
    ALWAYS = "always"  # every page (legible but wrong text layers)
    NEVER = "never"


# Pages with fewer extracted characters than this have no usable text layer.
_MIN_TEXT_CHARS = 20
# Typical places for the engine and per-user language data (ADR-017).
_WINDOWS_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
# Width of the thumbnail used to estimate skew (enough to resolve text rows).
_SKEW_WIDTH = 600
# Grey level below which a pixel counts as ink (contrast is stretched first).
_DARK_LEVEL = 128
_INK = bytes([255])
_RUN_START = bytes([0, 255])  # paper then ink


def needs_ocr(
    info: PageInfo, figures: list[PdfFigure], cfg: PdfHeuristicsConfig, mode: OcrMode
) -> bool:
    """§8: no text layer under a page-sized image, or mostly garbage characters."""
    if mode is OcrMode.NEVER:
        return False
    if mode is OcrMode.ALWAYS:
        return True
    area = info.width * info.height or 1.0
    covered = max(
        (
            (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]) / area
            for f in figures
            if f.page == info.number
        ),
        default=0.0,
    )
    if info.char_count < _MIN_TEXT_CHARS and covered > cfg.ocr_min_image_cover:
        return True
    return bool(info.char_count) and info.garbage_count / info.char_count > cfg.garbage_ratio


@dataclass(frozen=True, slots=True)
class OcrEngine:
    command: str
    tessdata: str | None
    languages: str

    @classmethod
    def find(cls, languages: str) -> "OcrEngine | None":
        command = os.environ.get("TOMD_TESSERACT") or shutil.which("tesseract")
        if not command and _WINDOWS_TESSERACT.is_file():
            command = str(_WINDOWS_TESSERACT)
        if not command:
            return None
        return cls(command, _tessdata_dir(), languages)


def _tessdata_dir() -> str | None:
    explicit = os.environ.get("TOMD_TESSDATA")
    if explicit:
        return explicit
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "tomd" / "tessdata",
        Path.home() / ".local" / "share" / "tomd" / "tessdata",
    ]
    return next((str(c) for c in candidates if c.is_dir() and any(c.glob("*.traineddata"))), None)


class OcrFailedError(RuntimeError):
    """Tesseract could not run (missing language data, crashed engine)."""


@dataclass(frozen=True, slots=True)
class OcrPage:
    lines: list[Line]
    chars: list[Char]  # the same words as characters, to fill table grids
    rulings: list[Ruling]  # straight lines found in the bitmap (erased before OCR)
    width: float  # points, of the upright page (sideways scans swap width and height)
    height: float
    quarter_turns: int  # clockwise quarter turns applied to make the page upright
    skew: float  # degrees removed by deskewing
    dpi: int = 300  # rendering resolution (lower than configured for very large pages)


@contextmanager
def _tesseract_env(engine: OcrEngine) -> Iterator[None]:
    # The data folder goes through the environment: pytesseract splits `config` with
    # Windows shell rules, which keep quotes around paths with spaces.
    saved = {key: os.environ.get(key) for key in ("TESSDATA_PREFIX", "OMP_THREAD_LIMIT")}
    if engine.tessdata:
        os.environ["TESSDATA_PREFIX"] = engine.tessdata
    os.environ["OMP_THREAD_LIMIT"] = "1"  # deterministic, one core per worker
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ocr_page(engine: OcrEngine, page: Any, number: int, cfg: PdfHeuristicsConfig) -> OcrPage:
    """Render one page, make it upright, level and contrasted, and return its OCR lines."""
    import pytesseract  # imported lazily: only scanned documents need it

    pytesseract.pytesseract.tesseract_cmd = engine.command
    width_pt, height_pt = page.get_size()
    dpi = min(
        float(cfg.ocr_dpi), 72 * math.sqrt(cfg.ocr_max_pixels / max(width_pt * height_pt, 1.0))
    )
    image = page.render(scale=dpi / 72, grayscale=True).to_pil().convert("L")
    try:
        with _tesseract_env(engine):
            # Faded ink on grey paper: stretch to the full range. A clean scan already
            # spans it, so this leaves it unchanged.
            image = _swap(image, ImageOps.autocontrast(image, cutoff=cfg.ocr_contrast_cutoff))
            turns = _quarter_turns(pytesseract, image, cfg)
            if turns:
                image = _swap(image, image.rotate(-90 * turns, expand=True))
            angle = estimate_skew(image, cfg)
            if abs(angle) >= cfg.ocr_min_skew:
                level = image.rotate(-angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
                image = _swap(image, level)
            scale = 72 / dpi
            # Table rules confuse Tesseract's layout analysis (rows of a grid go
            # missing): keep them as rulings for the grid finder, and erase them.
            rules = find_rule_boxes(
                image, cfg.ocr_rule_min_length / scale, cfg.ocr_rule_max_thickness / scale
            )
            if rules:
                image = _swap(image, _erase(image, rules))
            hocr = pytesseract.image_to_pdf_or_hocr(
                image, lang=engine.languages, config="--psm 3", extension="hocr"
            )
        width, height = image.width * scale, image.height * scale
        lines, chars = parse_hocr(hocr, number, height, scale, cfg, image)
        rulings = [_ruling(box, height, scale) for box in rules]
    except (pytesseract.TesseractError, OSError) as exc:
        raise OcrFailedError(str(exc).strip()) from exc
    finally:
        image.close()
    skew = angle if abs(angle) >= cfg.ocr_min_skew else 0.0
    return OcrPage(lines, chars, rulings, width, height, turns, skew, round(dpi))


RuleBox = tuple[bool, int, int, int, int]  # horizontal, x0, y0, x1, y1 (pixels, end exclusive)


def find_rule_boxes(image: Image.Image, min_length: float, max_thickness: float) -> list[RuleBox]:
    """Straight ink runs at least `min_length` pixels long and at most `max_thickness`
    thick: table rules and separators. Text strokes are far shorter; filled areas
    (photos, redactions) are thicker."""
    ink = image.point(lambda v: 255 if v < _DARK_LEVEL else 0)
    boxes = _row_runs(ink, int(min_length), max_thickness)
    turned = ink.transpose(Image.Transpose.TRANSPOSE)  # columns become rows
    boxes += [
        (False, y0, x0, y1, x1)
        for _, x0, y0, x1, y1 in _row_runs(turned, int(min_length), max_thickness)
    ]
    ink.close()
    turned.close()
    return boxes


def _row_runs(ink: Image.Image, min_length: int, max_thickness: float) -> list[RuleBox]:
    pattern = re.compile(re.escape(_INK) + b"{%d,}" % max(min_length, 1))
    data, width = ink.tobytes(), ink.width
    open_boxes: list[list[int]] = []  # x0, y0, x1, y1 of runs still growing downwards
    done: list[list[int]] = []
    for y in range(ink.height):
        row = data[y * width : (y + 1) * width]
        grown: list[list[int]] = []
        for m in pattern.finditer(row):
            for box in open_boxes:
                if box[3] == y and m.start() < box[2] and box[0] < m.end():
                    box[0], box[2], box[3] = min(box[0], m.start()), max(box[2], m.end()), y + 1
                    grown.append(box)
                    break
            else:
                grown.append([m.start(), y, m.end(), y + 1])
        done += [box for box in open_boxes if box[3] != y + 1]
        open_boxes = list({id(box): box for box in grown}.values())
    done += open_boxes
    return [
        (True, x0, y0, x1, y1)
        for x0, y0, x1, y1 in sorted(done, key=lambda b: (b[1], b[0]))
        if y1 - y0 <= max_thickness
    ]


def _erase(image: Image.Image, boxes: list[RuleBox]) -> Image.Image:
    clean = image.copy()
    draw = ImageDraw.Draw(clean)
    for _, x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 - 1, y0 - 1, x1, y1), fill=255)  # one pixel of anti-aliasing
    return clean


def _ruling(box: RuleBox, height: float, scale: float) -> Ruling:
    horizontal, x0, y0, x1, y1 = box
    if horizontal:
        return Ruling(True, height - (y0 + y1) / 2 * scale, x0 * scale, x1 * scale)
    return Ruling(False, (x0 + x1) / 2 * scale, height - y1 * scale, height - y0 * scale)


def _swap(old: Image.Image, new: Image.Image) -> Image.Image:
    if new is not old:
        old.close()
    return new


def _quarter_turns(pytesseract: Any, image: Image.Image, cfg: PdfHeuristicsConfig) -> int:
    """Clockwise quarter turns that make the page upright, from Tesseract's OSD. Pages
    too sparse for OSD, or a low-confidence answer, are left as they are."""
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0", output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0
    if float(osd.get("orientation_conf", 0.0)) < cfg.ocr_orientation_confidence:
        return 0
    return int(osd.get("rotate", 0)) // 90 % 4


def estimate_skew(image: Image.Image, cfg: PdfHeuristicsConfig) -> float:
    """Skew in degrees (counter-clockwise) by projection profile: rows of text are
    sharpest, so row darkness varies most, when the page is level."""
    small = image.resize(
        (_SKEW_WIDTH, max(1, image.height * _SKEW_WIDTH // image.width)),
        Image.Resampling.BOX,
    )
    steps = round(cfg.ocr_max_skew / cfg.ocr_skew_step)
    best, best_score = 0.0, -1.0
    for k in range(-steps, steps + 1):
        angle = k * cfg.ocr_skew_step
        level = small.rotate(-angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        rows = level.resize((1, level.height), Image.Resampling.BOX).tobytes()
        mean = sum(rows) / len(rows)
        score = sum((r - mean) ** 2 for r in rows)
        # Ties keep the angle closest to zero (k runs from negative to positive).
        if score > best_score + 1e-9 and (score > best_score or abs(angle) < abs(best)):
            best, best_score = angle, score
    small.close()
    return round(best, 2)


Word = tuple[str, int, int, int, int, float]  # text, left, top, right, bottom, confidence
_LINE_CLASSES = frozenset({"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"})
_BBOX = re.compile(r"bbox (-?\d+) (-?\d+) (-?\d+) (-?\d+)")
_BASELINE = re.compile(r"baseline (-?[\d.]+) (-?[\d.]+)")
_WCONF = re.compile(r"x_wconf (\d+)")


@dataclass(frozen=True, slots=True)
class _OcrLine:
    paragraph: int
    words: list[Word]
    baseline: float  # pixels from the top of the image, at the left edge of the line
    slope: float = 0.0  # baseline slope (pixels down per pixel right): leftover skew
    x0: int = 0  # left edge of the line, where `baseline` is measured

    def baseline_at(self, x: float) -> float:
        return self.baseline + self.slope * (x - self.x0)

    @property
    def cap_height(self) -> float:
        """Height of the tall words (capitals, ascenders) above the baseline under them.
        Only real words count (bullets and speckles sit at other heights), and the upper
        quartile rather than the tallest, so one word swollen by noise does not set the
        size."""
        text = [w for w in self.words if sum(ch.isalnum() for ch in w[0]) > 1] or self.words
        heights = sorted(self.baseline_at((w[1] + w[3]) / 2) - w[2] for w in text)
        return heights[-((1 - len(heights)) * 3 // 4)]  # ceiling: two words -> the taller


def lines_from_hocr(
    hocr: bytes,
    page: int,
    height: float,
    scale: float,
    cfg: PdfHeuristicsConfig,
    image: Image.Image | None = None,
) -> list[Line]:
    return parse_hocr(hocr, page, height, scale, cfg, image)[0]


def parse_hocr(
    hocr: bytes,
    page: int,
    height: float,
    scale: float,
    cfg: PdfHeuristicsConfig,
    image: Image.Image | None = None,
) -> tuple[list[Line], list[Char]]:
    """Tesseract hOCR (pixel boxes) to `Line`s and `Char`s in PDF points; `scale` is
    points per pixel. With the page `image`, lines with wider strokes than the page's
    text are bold."""
    root = html.fromstring(hocr)
    ocr_lines: list[_OcrLine] = []
    for p_index, paragraph in enumerate(root.iter("p")):
        for element in paragraph.iter("span"):
            if element.get("class") not in _LINE_CLASSES:
                continue
            words = _words(element)
            if not words:
                continue
            title = element.get("title", "")
            box = _BBOX.search(title)
            base = _BASELINE.search(title)
            bottom = int(box.group(4)) if box else max(w[4] for w in words)
            offset = float(base.group(2)) if base else 0.0
            slope = float(base.group(1)) if base else 0.0
            left = int(box.group(1)) if box else words[0][1]
            ocr_lines.append(_OcrLine(p_index, words, bottom + offset, slope, left))
    # Ascender-to-baseline height does not depend on whether a line has descenders
    # ("2 Conclusions" vs "Background"); the paragraph median discounts lines without
    # capitals or ascenders.
    caps: dict[int, list[float]] = {}
    for ln in ocr_lines:
        caps.setdefault(ln.paragraph, []).append(ln.cap_height)
    strokes = {id(ln): _stroke_width(image, ln.words) for ln in ocr_lines} if image else {}
    usual = median(strokes.values()) if strokes else 0.0
    lines: list[Line] = []
    chars: list[Char] = []
    for ln in ocr_lines:
        words = ln.words
        bold = usual > 0 and strokes[id(ln)] > usual * cfg.ocr_bold_stroke_ratio
        # Tesseract may put a heading in the paragraph of the text around it: a line
        # clearly taller than its paragraph keeps its own size.
        common = median(caps[ln.paragraph])
        own = ln.cap_height if ln.cap_height > common * cfg.ocr_own_size_ratio else common
        size_px = own / cfg.ocr_cap_height
        size = round(size_px * scale * 2) / 2 or 1.0
        # Markers first: a bullet often has the low confidence of a speckle.
        marker = _bullet_marker(words, size_px, cfg)
        kept = [w for w in words[marker:] if not _is_speckle(w[0], w[5], cfg)]
        texts = ["•", *(w[0] for w in kept)] if marker else [w[0] for w in kept]
        if not texts:
            continue
        baseline = height - ln.baseline * scale
        chars += _word_chars(kept, height, scale, baseline, size, bold)
        lines.append(
            Line(
                spans=(Span(" ".join(texts), bold, False, False, size),),
                x0=min(w[1] for w in words) * scale,
                x1=max(w[3] for w in words) * scale,
                top=height - min(w[2] for w in words) * scale,
                bottom=height - max(w[4] for w in words) * scale,
                baseline=baseline,
                size=size,
                page=page,
                confidence=round(sum(w[5] for w in words) / len(words), 3),
            )
        )
    return sorted(lines, key=lambda ln: (-ln.top, ln.x0)), chars


def _word_chars(
    words: list[Word], height: float, scale: float, baseline: float, size: float, bold: bool
) -> list[Char]:
    """Each word's letters spread evenly over its box, with a space between words."""
    out: list[Char] = []
    for i, (text, left, top, right, bottom, _) in enumerate(words):
        y_top, y_bottom = height - top * scale, height - bottom * scale
        if i:
            gap_start = words[i - 1][3] * scale
            out.append(
                Char(
                    " ",
                    gap_start,
                    left * scale,
                    y_top,
                    y_bottom,
                    baseline,
                    size,
                    "OCR",
                    bold,
                    False,
                    False,
                )
            )
        step = (right - left) * scale / len(text)
        for k, ch in enumerate(text):
            x0 = left * scale + k * step
            out.append(
                Char(ch, x0, x0 + step, y_top, y_bottom, baseline, size, "OCR", bold, False, False)
            )
    return out


def _stroke_width(image: Image.Image, words: list[Word]) -> float:
    """Mean horizontal stroke width in pixels inside the word boxes: dark pixels per dark
    run. Bold strokes are about 1.5 × wider; spacing and box padding do not count."""
    dark = runs = 0
    for _, left, top, right, bottom, _ in words:
        crop = image.crop((left, top, right, bottom)).point(lambda v: 255 if v < _DARK_LEVEL else 0)
        data = crop.tobytes()
        dark += data.count(_INK)
        runs += data.count(_RUN_START) + data.startswith(_INK)
    return dark / runs if runs else 0.0


def _words(line: Any) -> list[Word]:
    words: list[Word] = []
    for element in line.iter("span"):
        if element.get("class") != "ocrx_word":
            continue
        text = element.text_content().strip()
        title = element.get("title", "")
        box = _BBOX.search(title)
        conf = _WCONF.search(title)
        confidence = int(conf.group(1)) / 100 if conf else 0.0
        if not text or box is None:
            continue
        left, top, right, bottom = (int(g) for g in box.groups())
        words.append((text, left, top, right, bottom, confidence))
    return sorted(words, key=lambda w: w[1])


def _is_speckle(text: str, conf: float, cfg: PdfHeuristicsConfig) -> bool:
    """Noise read as a lone punctuation "word" (".", "-.", "'")."""
    return conf < cfg.ocr_speckle_confidence and not any(ch.isalnum() for ch in text)


def _bullet_marker(words: list[Word], size_px: float, cfg: PdfHeuristicsConfig) -> int:
    """Number of leading words that are a misread list bullet ("e", "«", "°", with noise
    such as "-." after it), or 0. A bullet is a lone non-digit glyph set apart from the
    first real word by much more than a word space."""
    first = words[0][0]
    if len(words) < 2 or len(first) != 1 or first.isdigit():
        return 0
    n = 1
    while n < len(words) - 1 and not any(ch.isalnum() for ch in words[n][0]):
        n += 1  # punctuation-only speckles between the bullet and the text
    return n if words[n][1] - words[0][3] > cfg.ocr_bullet_gap * size_px else 0
