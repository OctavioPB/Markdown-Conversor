"""Thresholds of the heuristic PDF backend.

Every distance is relative to a character size `s`, the document body size `S` or the
median line spacing `L` (pdf-layout-heuristics skill): absolute points work for one
document and fail on the next. Initial values come from the skill's reference and are
calibrated against the PDF corpus; each comment says why the value is what it is.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PdfHeuristicsConfig:
    # --- Lines (§2) ---
    # Characters share a line when their baselines differ by less than this × size;
    # tighter splits accented capitals, looser merges consecutive lines of small print.
    same_line_baseline: float = 0.3
    # Insert a space when the gap between glyphs exceeds the line's typical (median) gap
    # by this × mean glyph width; many PDFs have no space glyphs at all. 0.25 merged
    # "Stand for" in an italic ACL title (word gap 0.93 pt, mean glyph width 7.5 pt).
    space_gap: float = 0.12
    # A gap wider than this × size inside a line means two columns or table cells.
    # ACL proceedings have a ~17 pt gutter at 11 pt (1.5 em); justified word spaces stay
    # well below 1 em. The skill's initial 2.0 merged ACL columns line by line.
    split_line_gap: float = 1.2

    # Gaps that cross the same x on at least this many rows (and this share of the rows
    # with gaps) are a column gutter; other wide gaps in a row (spaced-out author names, a
    # formula and its number) are rejoined into one line.
    gutter_min_rows: int = 4
    gutter_min_share: float = 0.25

    # --- Blocks (§3) ---
    # A vertical gap above this × L starts a new block (paragraph spacing is usually
    # 1.5-2 × L, plain line spacing 1 × L).
    block_gap: float = 1.4
    # A font size change above this × S starts a new block.
    block_size_change: float = 0.1
    # A left-edge shift above this × S starts a new block (except first-line indents).
    # ACL proceedings indent paragraphs by exactly 1 em, so the threshold must sit below
    # 1.0; justified lines start exactly at the margin, so 0.75 em is still safe.
    block_indent_change: float = 0.75
    # A line ending before this fraction of the block width, in final punctuation,
    # closes a paragraph in justified text.
    short_line_ratio: float = 0.8

    # --- Classification (§6) ---
    # Heading by size: at least this × S.
    heading_min_size: float = 1.15
    # Blocks longer than this (words) or with more lines are not headings, however big:
    # abstracts and pull quotes are large but long.
    heading_max_words: int = 15
    heading_max_lines: int = 2
    # Heading sizes closer than this (points, after rounding) are the same level.
    heading_size_tolerance: float = 0.5
    # List nesting: bullet x0 positions within this × S are the same level.
    list_level_tolerance: float = 0.5
    # Blocks whose confidence falls below this get a diagnostic.
    min_confidence: float = 0.5

    # --- Reading order (§4) ---
    # Lines at least this fraction of the page's text width span all columns (titles,
    # abstracts, wide figures) and are ordered as bands before the XY-cut.
    full_width_ratio: float = 0.7
    # A vertical valley at least this × S wide, crossing the whole region, separates
    # columns. Gutters are 1.5-3 em; 1.2 keeps ACL (1.5 em) clearly above the threshold.
    xy_min_column_gap: float = 1.2
    # A horizontal valley at least this × L tall separates rows of the region.
    xy_min_row_gap: float = 0.5
    # Valleys at least this × L tall are cut before looking for columns (running heads
    # and footers sit well apart from the body; synthetic-two-columns has its page
    # number centred in the gutter).
    xy_wide_row_gap: float = 2.0
    # A column cut leaving fewer lines than this on one side is doubtful (margin notes,
    # figure labels) and reported as pdf.reading_order_uncertain.
    xy_min_column_lines: int = 3

    # --- Page furniture (§5) ---
    # Headers, footers and page numbers are looked for in this top/bottom fraction.
    furniture_band: float = 0.08
    # A header/footer text (digits normalized) repeated on at least this share of the
    # pages of the same parity, at a similar height, is furniture.
    furniture_min_share: float = 0.4
    # ... and on at least this many pages, so a two-page document keeps its first lines.
    furniture_min_pages: int = 3
    # Heights within this × S count as "the same position".
    furniture_y_tolerance: float = 1.0
    # Header texts this similar (difflib ratio, letters and digits only) are the same
    # header; OCR layers vary between pages (RCED 1997 article: "Pinilla"/"Pinillay").
    furniture_text_similarity: float = 0.85

    # --- Figures (§6) ---
    # Images smaller than this fraction of the page area are decoration (logos, bullets,
    # rules drawn as images) and are dropped with a diagnostic, not extracted.
    min_figure_area: float = 0.01
    # A caption ("Figure 3", "Fig. 3", "Figura 3", "Tabla 3"...) belongs to an image when
    # it starts within this × L above or below it and overlaps it horizontally.
    caption_max_distance: float = 2.5

    # --- Tables (§9) ---
    # Rules closer than this (points) are the same grid line. This is drawing precision
    # (rounding in the producer), not typography, so it is absolute.
    table_snap_tolerance: float = 2.0

    # Tables without rules (§9.2): at least this many consecutive rows split into at least
    # two short parts with shared column gaps. Short means narrower than this fraction of
    # the page text width, so two-column body text (each line ~45 %) is never a table.
    aligned_min_rows: int = 3
    aligned_cell_max_width: float = 0.35
    # Column gaps inside a table are at least this × the character size.
    table_min_column_gap: float = 0.8
    # Rows further apart than this × their size end an aligned-table run.
    aligned_max_row_gap: float = 2.5
    # An aligned run this long that is not rebuilt as a table is reported as a possible
    # table left as text (§9 strategy 3). Higher than `aligned_min_rows` so that short
    # pairs of lines (an address beside a date, two names) are not flagged.
    table_suspect_min_rows: int = 4

    # --- Text quality (§7, §8) ---
    # Pages whose share of garbage characters exceeds this need OCR.
    garbage_ratio: float = 0.3

    # --- OCR (§8, ADR-017) ---
    # A page whose largest image covers more than this fraction, with no text layer, is
    # a scan.
    ocr_min_image_cover: float = 0.5
    # Rendering resolution for Tesseract (its recommended minimum for body text).
    ocr_dpi: int = 300
    # Ascender (and capital) height above the baseline, as a share of the font size, in
    # common text faces (measured: 32 px for 10.5 pt and 46 px for 15 pt at 300 dpi).
    ocr_cap_height: float = 0.72
    # A line whose cap height exceeds its OCR paragraph's median by this factor keeps its
    # own size (heading sizes start at about 1.15 × body).
    ocr_own_size_ratio: float = 1.12
    # A line whose mean stroke width exceeds the page median by this factor is bold (OCR
    # has no font names). Measured on the scan corpus: regular lines up to 1.15 × the
    # median, bold headings from 1.55 ×.
    ocr_bold_stroke_ratio: float = 1.35
    # Pages whose mean OCR confidence is below this get pdf.ocr_low_confidence.
    ocr_min_confidence: float = 0.6
    # A lone glyph followed by a gap wider than this × the font size is a list bullet that
    # Tesseract misread (word spaces are about 0.25 em; bullet indents about 1 em).
    ocr_bullet_gap: float = 0.6
    # Skew search range and step in degrees; hand-fed scans are rarely off by more than 3°.
    ocr_max_skew: float = 3.0
    ocr_skew_step: float = 0.1
    # Below this angle deskewing is not worth the resampling blur.
    ocr_min_skew: float = 0.2
    # Pixel budget for one rendered page: larger pages (posters, plans) render at a lower
    # resolution. 40 MP is A2 at 300 dpi; Tesseract then needs about 1 GB.
    ocr_max_pixels: int = 40_000_000
    # Straight ink runs at least this long (points) and at most this thick are ruled
    # lines; the longest text strokes (dashes) stay far below half an inch.
    ocr_rule_min_length: float = 36.0
    ocr_rule_max_thickness: float = 2.5
    # Share (percent) of darkest and lightest pixels ignored when stretching contrast, so
    # a few noise specks do not hold the range open.
    ocr_contrast_cutoff: float = 0.1
    # Tesseract OSD confidence below which the page orientation is left unchanged (its
    # answers on upright text pages score 10-20; sparse pages score below 2).
    ocr_orientation_confidence: float = 2.0
    # Punctuation-only "words" under this confidence are noise speckles, dropped.
    ocr_speckle_confidence: float = 0.6

    # --- Document statistics (§1) ---
    # Font sizes are rounded to this step (points) before taking the mode.
    size_rounding: float = 0.5
    # A larger font size with at least this share of the mode's characters is the body
    # size instead (page 1 of a SIAM paper: 8 pt abstract and notes vs 10 pt body).
    body_size_tie: float = 0.8
    # Line spacing L: baseline gaps are rounded to this step, and the smallest value seen
    # at least `leading_min_count` times and in `leading_min_share` of the gaps is the
    # paragraph leading (synthetic-lists: median gave 22 pt, the real leading is 14 pt).
    leading_rounding: float = 0.5
    leading_min_count: int = 2
    leading_min_share: float = 0.05
    # Gaps up to this × the tight value belong to the same leading cluster.
    leading_cluster: float = 1.15


DEFAULT_CONFIG = PdfHeuristicsConfig()
