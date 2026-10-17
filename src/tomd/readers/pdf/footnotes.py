"""Footnotes (§10): pair small-print notes at the foot of a page with superscript calls on
the same page, so the writer can emit `[^n]` references and definitions.

A note is a small-print block (below 0.9 × S) in the lower half of the page whose lines
start with a marker: a superscript number, or a symbol (*, †, ‡, §, ⋆…). A call is a
superscript span in the body with the same marker on the same page. Symbol markers link
every call (several authors share ⋆); a number links only its last call on the page, as
earlier occurrences are usually author affiliation numbers. Unpaired notes stay as text
with a `pdf.footnote_unmatched` diagnostic.
"""

import re
from dataclasses import dataclass, replace

from tomd.ir import Diagnostic, Provenance
from tomd.ir.diagnostics import (
    PDF_FOOTNOTE_UNMATCHED,
    diagnose,
)
from tomd.readers.pdf.classify import Classified, Kind
from tomd.readers.pdf.model import DocStats, Line, Span, TextBlock

SYMBOLS = "*†‡§¶‖⋆∗#"
# Notes are set smaller than the body text.
_SMALL_PRINT = 0.9
# ... and sit in the lower half of the page.
_LOWER_PART = 0.5
_SYMBOL_START = re.compile(r"^\s*([" + re.escape(SYMBOLS) + r"]+)\s*")
# The same mark set in different fonts: math asterisk U+2217 in the call, ASCII "*" in the note.
_SAME_MARK = {"∗": "*"}
_TOKENS = re.compile(r"\d{1,3}|[" + re.escape(SYMBOLS) + r"]")


@dataclass(frozen=True, slots=True)
class PdfNote:
    key: str
    page: int
    block: TextBlock  # marker removed
    provenance: Provenance


def link_footnotes(
    classified: list[Classified], stats: DocStats, page_heights: dict[int, float], source: str
) -> tuple[list[Classified], list[PdfNote], list[Diagnostic]]:
    """Return the body (calls rewritten, paired notes removed), the notes and diagnostics."""
    candidates: dict[int, list[tuple[str, TextBlock]]] = {}
    for index, c in enumerate(classified):
        notes = _split_notes(c, stats, page_heights)
        if notes:
            candidates[index] = notes

    # Calls: superscript spans in body blocks, in reading order.
    calls: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for b_index, c in enumerate(classified):
        if b_index in candidates or c.kind in (Kind.CODE, Kind.PAGE_NUMBER):
            continue
        for l_index, line in enumerate(c.block.lines):
            for s_index, span in enumerate(line.spans):
                if span.superscript:
                    for token in _tokens(span.text):
                        calls.setdefault((c.block.page, token), []).append(
                            (b_index, l_index, s_index)
                        )

    paired: dict[tuple[int, int, int], list[str]] = {}
    notes_out: list[PdfNote] = []
    diagnostics: list[Diagnostic] = []
    kept_candidates: set[int] = set()
    for b_index, notes in sorted(candidates.items()):
        page = classified[b_index].block.page
        any_paired = False
        for marker, block in notes:
            where = calls.get((page, marker), [])
            chosen = where if not marker.isdigit() else where[-1:]
            if not chosen:
                continue
            key = f"p{page}-{marker}"
            for position in chosen:
                paired.setdefault(position, []).append(key)
            prov = Provenance(source, page, block.bbox)
            notes_out.append(PdfNote(key, page, block, prov))
            any_paired = True
        if not any_paired:
            kept_candidates.add(b_index)
            diagnostics.append(
                diagnose(
                    PDF_FOOTNOTE_UNMATCHED,
                    "Footnote-like text without a matching call; kept as text.",
                    Provenance(source, page, classified[b_index].block.bbox),
                )
            )

    body: list[Classified] = []
    for b_index, c in enumerate(classified):
        if b_index in candidates and b_index not in kept_candidates:
            continue
        body.append(_with_calls(c, b_index, paired) if any(k[0] == b_index for k in paired) else c)
    return body, notes_out, diagnostics


def _tokens(text: str) -> list[str]:
    return [_SAME_MARK.get(tok, tok) for tok in _TOKENS.findall(text)]


def _split_notes(
    c: Classified, stats: DocStats, page_heights: dict[int, float]
) -> list[tuple[str, TextBlock]]:
    block = c.block
    if c.kind not in (Kind.PARAGRAPH, Kind.LIST_ITEM):
        return []
    if block.size >= _SMALL_PRINT * stats.body_size:
        return []
    if block.bottom > _LOWER_PART * page_heights.get(block.page, 0.0):
        return []
    notes: list[tuple[str, TextBlock]] = []
    for line in block.lines:
        started = _note_start(line)
        if started is not None:
            marker, stripped = started
            notes.append((marker, TextBlock([stripped])))
        elif notes:
            notes[-1][1].lines.append(line)
        else:
            return []  # text before the first marker: not a footnote block
    return notes


def _note_start(line: Line) -> tuple[str, Line] | None:
    """(marker, line without it) when the line opens a note."""
    spans = list(line.spans)
    if not spans:
        return None
    first = spans[0]
    if first.superscript and _tokens(first.text) and not first.text.strip(SYMBOLS + "0123456789 "):
        marker = _tokens(first.text)[0]
        return marker, replace(line, spans=tuple(spans[1:]) or (replace(first, text=""),))
    m = _SYMBOL_START.match(first.text)
    if m and len(m.group(1)) == 1:
        rest = replace(first, text=first.text[m.end() :])
        return _SAME_MARK.get(m.group(1), m.group(1)), replace(line, spans=(rest, *spans[1:]))
    return None


def _with_calls(
    c: Classified, b_index: int, paired: dict[tuple[int, int, int], list[str]]
) -> Classified:
    lines: list[Line] = []
    for l_index, line in enumerate(c.block.lines):
        spans: list[Span] = []
        for s_index, span in enumerate(line.spans):
            keys = paired.get((b_index, l_index, s_index))
            # Only a span whose markers are all paired becomes references; a mixed one
            # ("1,2" where only 2 is a call) keeps its text.
            if keys and len(keys) == len(_tokens(span.text)):
                spans.append(replace(span, notes=tuple(keys)))
            else:
                spans.append(span)
        lines.append(replace(line, spans=tuple(spans)))
    return replace(c, block=TextBlock(lines))
