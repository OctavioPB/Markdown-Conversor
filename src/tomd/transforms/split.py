"""Split a book into chapters (ADR-018): a pure IR -> IR pass over a transformed document.

Boundaries come from the source's own outline (PDF bookmarks, EPUB table of contents)
anchored to the headings of the IR or, without a usable outline, from chapter-like
headings ("Capítulo 3", "Chapter III", "4 Redes"). Each chapter becomes a document of its
own: its heading is its title, its footnotes are numbered within it, it carries its images
and the diagnostics of its pages. When the split is not plausible the book stays one
document with `doc.split_skipped`: a wrong split is worse than none.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from tomd.ir import (
    Block,
    Code,
    CodeBlock,
    Diagnostic,
    Document,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    Inline,
    OutlineEntry,
    Paragraph,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.ir.walk import iter_inlines, map_block_children, plain_text
from tomd.transforms.footnotes import renumber_footnotes
from tomd.transforms.headings import FIRST_BODY_LEVEL, normalize_headings

SPLIT_MODES = ("auto", "chapters", "none")


@dataclass(frozen=True, slots=True)
class SplitConfig:
    # `auto` splits books with at least this much text: ~80 pages of ~1 900 characters
    # (ADR-018). Text, not pages, because an EPUB has no pages.
    auto_min_chars: int = 150_000
    # A split needs at least two chapters, and at most this many: more is a section-level
    # outline or a damaged one, not a book's chapters.
    min_parts: int = 2
    max_parts: int = 300
    # No chapter may hold more than this share of the text: one giant part means the
    # boundaries missed the structure (only the appendices bookmarked, say).
    max_part_share: float = 0.9
    # A part with less text than this is a title page ("Part II", an empty contents entry):
    # it joins the next chapter instead of becoming a file. Low on purpose: real chapters
    # can be short (the prose poems of Platero y yo run 300-600 characters).
    min_part_chars: int = 100
    # Outline entries that could not be anchored in the text, as a share of the level
    # used: above it the outline does not describe this text (another edition, damage).
    max_unanchored_share: float = 0.34
    # Levels below the requested one an outline may be read at when that level has too few
    # entries or only groups parts (a root entry with the book title, "Part I"...).
    max_level_descent: int = 2


SPLIT_CONFIG = SplitConfig()

# Headings that open a chapter-sized unit, in the languages of the corpus (es, en).
_CHAPTER_HEADING = re.compile(
    r"^\s*(?:cap[ií]tulo|chapter|chap\.|ap[eé]ndice|appendix|anexo|annex|lecci[oó]n|lesson"
    r"|unidad|unit|tema)\s+(?:\d{1,3}|[ivxlcdm]{1,7}|[a-z])\b"
    r"|^\s*\d{1,3}\.?\s+[^\d\s.]",  # "4 Redes", "4. Redes"; not "4.1 Subredes"
    re.IGNORECASE,
)
# "XXVIII. El alba": a Roman numeral and a dot. Upper case only, so that "c. 1900" or an
# initial ("C. Smith") is not taken for one.
_ROMAN_HEADING = re.compile(r"^\s*[IVXLCDM]{1,7}\.\s+\S")
# Outline entries that group chapters rather than being one.
_PART_TITLE = re.compile(r"^\s*(?:parte|part|libro|book|secci[oó]n|section)\b", re.IGNORECASE)
_FRONT_TITLE = {"es": "Preliminares", "en": "Front matter"}
_FRONT_MATTER = re.compile(
    r"^\s*(?:(?:front\s+)?cover|half[-\s]title(?:\s+page)?|title\s+page|copyright(?:\s+page)?"
    r"|(?:table\s+of\s+)?contents(?:\s+at\s+a\s+glance)?|dedication|portada|portadilla"
    r"|cr[eé]ditos|[ií]ndice(?:\s+general)?|sumario|dedicatoria|p[aá]gina\s+legal)\s*$",
    re.IGNORECASE,
)
_KEY = re.compile(r"[\W_]+")
_MIN_KEY = 4  # shorter titles ("1", "I") only match exactly
# A heading matches a title it is part of only when it covers this much of it: "CHAPTER"
# alone is not "Chapter 1 Software Architect Capability Model".
_MIN_TITLE_COVER = 0.6
# The title of a part set in pieces: at most this many leading blocks, each short.
_MAX_TITLE_PIECES = 6
_MAX_TITLE_PIECE_CHARS = 120


@dataclass(frozen=True, slots=True)
class Chapter:
    number: int | None  # None: the part before the first chapter (preface, foreword...)
    document: Document


@dataclass(frozen=True, slots=True)
class SplitBook:
    """`book`: the whole book's metadata, the text before the first chapter when it is too
    short for a file of its own, and the diagnostics that belong to no chapter."""

    book: Document
    chapters: tuple[Chapter, ...]


@dataclass(frozen=True, slots=True)
class _Bound:
    index: int  # first block of the part in the body
    title: str
    from_outline: bool  # the publisher's title, rather than a heading of the text


@dataclass(frozen=True, slots=True)
class _Part:
    title: str
    blocks: tuple[Block, ...]  # without the title itself
    start: int  # index in the body, for placing diagnostics
    heading: Heading  # the title as a heading, for when the part joins another
    group: bool  # a "Part II" that groups the chapters after it


def split_document(
    doc: Document, *, mode: str = "auto", level: int = 1, cfg: SplitConfig = SPLIT_CONFIG
) -> tuple[SplitBook | None, Document]:
    """Split `doc` (already through the default transforms) by chapters, or not. Returns
    the book, or None and the document (with `doc.split_skipped` when a split was asked
    for and no reliable boundaries were found)."""
    if mode == "none" or doc.meta.source_type == "wikipedia":
        return None, doc
    body = tuple(b for b in doc.blocks if not isinstance(b, FootnoteDef))
    total = _chars(body)
    if mode == "auto" and total < cfg.auto_min_chars:
        return None, doc
    places = _places(body)
    bounds = _outline_bounds(doc.outline, body, places, level, cfg) or _heading_bounds(body, cfg)
    parts = _parts(body, bounds, cfg) if bounds else None
    front: tuple[Block, ...] = body[: bounds[0].index] if bounds else ()
    if parts is None or not _plausible(parts, total, cfg):
        reason = "no usable outline or chapter headings" if not bounds else "implausible parts"
        skipped = diagnose(codes.DOC_SPLIT_SKIPPED, f"Not split by chapters ({reason}).")
        return None, replace(doc, diagnostics=(*doc.diagnostics, skipped))
    return _book(doc, front, parts, places, cfg), doc


# --- Boundaries --------------------------------------------------------------------------


def _outline_bounds(
    outline: tuple[OutlineEntry, ...],
    body: tuple[Block, ...],
    places: list[tuple[int | None, str | None]],
    level: int,
    cfg: SplitConfig,
) -> list[_Bound] | None:
    entries: list[OutlineEntry] = []
    for depth in range(level, level + cfg.max_level_descent + 1):
        entries = [e for e in outline if e.level == depth]
        deeper = [e for e in outline if e.level == depth + 1]
        only_parts = entries and all(_PART_TITLE.match(e.title) for e in entries)
        if len(entries) >= cfg.min_parts and not (only_parts and len(deeper) > len(entries)):
            break
    else:
        return None
    if len(entries) < cfg.min_parts:
        return None
    # Chapters one level down are chapters too when this level groups them in parts
    # ("Part 2" holding chapters 2-4: a file per chapter, not per part) or is itself made
    # of chapters (a careless outline nests a run of them under the one before).
    # "3.1 Subredes" is not chapter-like, so sections stay inside their chapter.
    parts = sum(bool(_PART_TITLE.match(e.title)) for e in entries)
    if parts >= cfg.min_parts or sum(_chapter_like(e.title) for e in entries) * 2 >= len(entries):
        depth = entries[0].level
        entries = [
            e
            for e in outline
            if e.level == depth or (e.level == depth + 1 and _chapter_like(e.title))
        ]
    # Leading front-matter entries (cover, title page, contents) open no file of their
    # own: their text goes to the front part.
    while entries and _FRONT_MATTER.match(entries[0].title):
        entries = entries[1:]
    if len(entries) < cfg.min_parts:
        return None
    bounds: list[_Bound] = []
    missed = 0
    start = 0
    for entry in entries:
        index = _anchor(entry, body, places, start)
        if index is None:
            missed += 1
            continue
        bounds.append(_Bound(index, " ".join(entry.title.split()), from_outline=True))
        start = index + 1
    if missed > cfg.max_unanchored_share * len(entries):
        return None
    return bounds


def _anchor(
    entry: OutlineEntry,
    body: tuple[Block, ...],
    places: list[tuple[int | None, str | None]],
    start: int,
) -> int | None:
    """Where an outline entry starts in the body: the heading with its title on its page
    (or in its file), else the first block of that page (or file) for an entry that
    points to a whole page or file."""
    path = entry.target.split("#")[0] if entry.target else None

    def in_place(index: int) -> bool:
        page, ref = places[index]
        if entry.page is not None:
            # Bookmarks often point at the page before the heading's (a part title page).
            return page is not None and entry.page <= page <= entry.page + 1
        return path is None or ref == path

    key = _key(entry.title)
    for index in range(start, len(body)):
        page = places[index][0]
        if entry.page is not None and page is not None and page > entry.page + 1:
            break
        block = body[index]
        if (
            isinstance(block, Heading)
            and in_place(index)
            and _same_title(key, _key(plain_text(block.children)))
        ):
            # The pieces of the title set before it ("CHAPTER", "2") start the part too.
            while index > start and places[index - 1][0] == places[index][0]:
                before = body[index - 1]
                if not isinstance(before, Heading | Paragraph):
                    break
                text = " ".join(plain_text(before.children).split())
                if not _key(text) or _key(text) not in key or len(text) > _MAX_TITLE_PIECE_CHARS:
                    break
                index -= 1
            return index
    if entry.page is not None:
        return next((i for i in range(start, len(body)) if places[i][0] == entry.page), None)
    if path is not None and entry.target == path:  # the whole file, no fragment
        return next((i for i in range(start, len(body)) if places[i][1] == path), None)
    return None


def _heading_bounds(body: tuple[Block, ...], cfg: SplitConfig) -> list[_Bound] | None:
    headings = [(i, b) for i, b in enumerate(body) if isinstance(b, Heading)]
    for depth in sorted({h.level for _, h in headings}):
        found = [
            _Bound(i, _title_text(h), from_outline=False)
            for i, h in headings
            if h.level == depth and _chapter_like(plain_text(h.children))
        ]
        if len(found) >= cfg.min_parts:
            return found
    return None


def _chapter_like(title: str) -> bool:
    return bool(_CHAPTER_HEADING.match(title) or _ROMAN_HEADING.match(title))


# --- Parts -------------------------------------------------------------------------------


def _parts(body: tuple[Block, ...], bounds: list[_Bound], cfg: SplitConfig) -> list[_Part]:
    raw: list[_Part] = []
    for n, bound in enumerate(bounds):
        end = bounds[n + 1].index if n + 1 < len(bounds) else len(body)
        segment = body[bound.index : end]
        title = bound.title
        if not bound.from_outline and isinstance(segment[0], Heading):
            title = _title_text(segment[0]) or title
        opening, rest = _take_title(segment, title)
        prov = getattr(segment[0], "provenance", None)
        heading = Heading(FIRST_BODY_LEVEL, (Text(title),), prov)
        group = bool(_PART_TITLE.match(title))
        raw.append(_Part(title, (*opening, *rest), bound.index, heading, group))
    # A part that groups chapters ("Part II", often with its own short contents) or has
    # too little text for a file opens the next chapter instead, its title kept as a
    # heading; a short last part closes the previous one.
    merged: list[_Part] = []
    carry: tuple[Block, ...] = ()
    carry_start: int | None = None
    for n, part in enumerate(raw):
        last = n == len(raw) - 1
        if (part.group or _chars(part.blocks) < cfg.min_part_chars) and not last:
            carry += _with_heading(part)
            carry_start = part.start if carry_start is None else carry_start
            continue
        if carry:
            part = replace(part, blocks=(*carry, *part.blocks), start=carry_start or part.start)
            carry, carry_start = (), None
        if last and merged and _chars(part.blocks) < cfg.min_part_chars:
            previous = merged.pop()
            part = replace(previous, blocks=(*previous.blocks, *_with_heading(part)))
        merged.append(part)
    return merged


def _take_title(
    segment: tuple[Block, ...], title: str
) -> tuple[tuple[Block, ...], tuple[Block, ...]]:
    """Take the title off the start of a part. Books often set it in pieces ("CHAPTER",
    "1", "Software Architect Capability", "Model"): leading headings and short lines whose
    words are all in the title go. Note calls in them cannot go in the front matter: they
    open the chapter, so their notes are kept. Returns (opening, the rest)."""
    key = _key(title)
    calls: list[Inline] = []
    taken = 0
    for block in segment[:_MAX_TITLE_PIECES]:
        if not isinstance(block, Heading | Paragraph):
            break
        words = tuple(n for n in block.children if not isinstance(n, FootnoteRef))
        text = " ".join(plain_text(words).split())
        piece = _key(text)
        if not piece or piece not in key or len(text) > _MAX_TITLE_PIECE_CHARS:
            break
        calls += [n for n in block.children if isinstance(n, FootnoteRef)]
        taken += 1
    opening: tuple[Block, ...] = (Paragraph(tuple(calls)),) if calls else ()
    return opening, segment[taken:]


def _with_heading(part: _Part) -> tuple[Block, ...]:
    return (part.heading, *part.blocks)


def _plausible(parts: list[_Part], total: int, cfg: SplitConfig) -> bool:
    if not cfg.min_parts <= len(parts) <= cfg.max_parts:
        return False
    return max(_chars(p.blocks) for p in parts) <= cfg.max_part_share * max(total, 1)


# --- The book ----------------------------------------------------------------------------


def _book(
    doc: Document,
    front: tuple[Block, ...],
    parts: list[_Part],
    places: list[tuple[int | None, str | None]],
    cfg: SplitConfig,
) -> SplitBook:
    notes = {b.key: b for b in doc.blocks if isinstance(b, FootnoteDef)}
    language = (doc.meta.language or "").split("-")[0].lower()
    units: list[tuple[int | None, str, tuple[Block, ...], int]] = []
    book_blocks: tuple[Block, ...] = ()
    if _chars(front) >= cfg.min_part_chars:
        units.append((None, _FRONT_TITLE.get(language, _FRONT_TITLE["en"]), front, 0))
    else:
        book_blocks = front
    units += [(n, p.title, p.blocks, p.start) for n, p in enumerate(parts, start=1)]

    starts = [start for *_, start in units]
    by_unit: list[list[Diagnostic]] = [[] for _ in units]
    left: list[Diagnostic] = []
    for diagnostic in doc.diagnostics:
        unit = _unit_of(diagnostic, starts, places)
        (left if unit is None else by_unit[unit]).append(diagnostic)

    chapters: list[Chapter] = []
    for (number, title, blocks, _), diagnostics in zip(units, by_unit, strict=True):
        own_notes = _notes_for(blocks, notes)
        images = _images(blocks + own_notes)
        meta = replace(
            doc.meta, title=title, book=doc.meta.title, chapter=number, chapters=len(parts)
        )
        part = Document(
            meta,
            blocks + own_notes,
            tuple(a for a in doc.assets if a.sha256 in images),
            tuple(diagnostics),
        )
        chapters.append(Chapter(number, renumber_footnotes(normalize_headings(part))))
    book_meta = replace(doc.meta, chapters=len(parts))
    book = Document(book_meta, book_blocks, (), tuple(left), doc.outline)
    return SplitBook(book, tuple(chapters))


def _unit_of(
    diagnostic: Diagnostic, starts: list[int], places: list[tuple[int | None, str | None]]
) -> int | None:
    """The part a diagnostic belongs to: the one holding its page (PDF) or its file (EPUB).
    Diagnostics about the whole book stay with the book."""
    prov = diagnostic.provenance
    if prov is None or (prov.page is None and not prov.source_ref):
        return None
    found: int | None = None
    for unit, start in enumerate(starts):
        end = starts[unit + 1] if unit + 1 < len(starts) else len(places)
        for page, ref in places[start:end]:
            if (prov.page is not None and page == prov.page) or (
                prov.page is None and ref == prov.source_ref
            ):
                return unit
        first_page = places[start][0]
        if prov.page is not None and first_page is not None and first_page <= prov.page:
            found = unit  # a page with no block of its own: the part running there
    return found


def _notes_for(blocks: tuple[Block, ...], notes: dict[str, FootnoteDef]) -> tuple[Block, ...]:
    """The definitions of the footnotes a chapter cites (and those they cite), in order of
    the book: notes collected at the end of a book go back to their chapter."""
    wanted: list[str] = []
    pending = list(_note_keys(blocks))
    while pending:
        key = pending.pop(0)
        if key in notes and key not in wanted:
            wanted.append(key)
            pending.extend(_note_keys(notes[key].blocks))
    return tuple(notes[k] for k in notes if k in wanted)


def _note_keys(blocks: Iterable[Block]) -> list[str]:
    return [n.key for n in iter_inlines(tuple(blocks)) if isinstance(n, FootnoteRef)]


def _images(blocks: tuple[Block, ...]) -> set[str]:
    found: set[str] = set()

    def visit(bs: tuple[Block, ...]) -> tuple[Block, ...]:
        for b in bs:
            if isinstance(b, Image):
                found.add(b.src_asset)
            else:
                map_block_children(b, visit, lambda nodes: nodes)
        return bs

    visit(blocks)
    return found


# --- Helpers -----------------------------------------------------------------------------


def _places(body: tuple[Block, ...]) -> list[tuple[int | None, str | None]]:
    """Page and source file of each block; a block without provenance takes the last
    known place (a list after a paragraph sits where the paragraph was)."""
    out: list[tuple[int | None, str | None]] = []
    page: int | None = None
    ref: str | None = None
    for block in body:
        prov = getattr(block, "provenance", None)
        if prov is not None:
            page = prov.page if prov.page is not None else page
            ref = prov.source_ref or ref
        out.append((page, ref))
    return out


def _chars(blocks: tuple[Block, ...]) -> int:
    count = sum(len(n.value) for n in iter_inlines(blocks) if isinstance(n, Text | Code))
    return count + sum(len(b.code) for b in blocks if isinstance(b, CodeBlock))


def _title_text(heading: Heading) -> str:
    text = plain_text(tuple(n for n in heading.children if not isinstance(n, FootnoteRef)))
    return " ".join(text.split())


def _key(title: str) -> str:
    return _KEY.sub("", title).casefold()


def _same_title(a: str, b: str) -> bool:
    if a == b:
        return bool(a)
    shorter, longer = sorted((a, b), key=len)
    return (
        len(shorter) >= _MIN_KEY
        and len(shorter) >= _MIN_TITLE_COVER * len(longer)
        and shorter in longer
    )
