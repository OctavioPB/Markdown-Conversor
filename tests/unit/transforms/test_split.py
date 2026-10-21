"""Splitting a book by chapters (ADR-018): where it cuts, what each chapter carries, and
when it refuses to cut."""

from dataclasses import replace

from tomd.ir import (
    Asset,
    Block,
    Document,
    DocumentMeta,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    OutlineEntry,
    Paragraph,
    Provenance,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.transforms.split import SplitConfig, split_document

SRC = "book.pdf"
LONG = "This paragraph is long enough to count as the text of a real chapter of a book. " * 3


def h(level: int, text: str, page: int, *extra: object) -> Heading:
    return Heading(level, (Text(text), *extra), Provenance(SRC, page))  # type: ignore[arg-type]


def p(text: str, page: int, *extra: object) -> Paragraph:
    return Paragraph((Text(text), *extra), Provenance(SRC, page))  # type: ignore[arg-type]


def book(*blocks: Block, outline: tuple[OutlineEntry, ...] = (), **meta: object) -> Document:
    fields: dict[str, object] = {"title": "Networks", "source_type": "pdf", "source": SRC}
    fields.update(meta)
    return Document(DocumentMeta(**fields), blocks, outline=outline)  # type: ignore[arg-type]


def chapters_of(doc: Document, **kwargs: object) -> list[Document]:
    result, _ = split_document(doc, mode="chapters", **kwargs)  # type: ignore[arg-type]
    assert result is not None
    return [c.document for c in result.chapters]


THREE = (
    p("Copyright and dedication.", 1),
    h(2, "Chapter 1 Basics", 2),
    p(LONG, 2),
    h(3, "1.1 Setup", 3),
    p(LONG, 3),
    h(2, "Chapter 2 Routing", 4),
    p(LONG, 4),
    h(2, "Chapter 3 Security", 6),
    p(LONG, 6),
)
OUTLINE = (
    OutlineEntry("Chapter 1  Basics", 1, page=2),
    OutlineEntry("1.1 Setup", 2, page=3),
    OutlineEntry("Chapter 2 Routing", 1, page=4),
    OutlineEntry("Chapter 3 Security", 1, page=5),  # points at the page before the heading
)


def test_outline_entries_anchor_to_their_headings() -> None:
    result, _ = split_document(book(*THREE, outline=OUTLINE), mode="chapters")
    assert result is not None
    titles = [c.document.meta.title for c in result.chapters]
    assert titles == ["Chapter 1 Basics", "Chapter 2 Routing", "Chapter 3 Security"]
    first = result.chapters[0].document
    assert (first.meta.book, first.meta.chapter, first.meta.chapters) == ("Networks", 1, 3)
    # The chapter heading is the title; its sections move up one level (H3 -> H2).
    assert first.blocks[1] == h(2, "1.1 Setup", 3)
    # A short text before chapter 1 is no file of its own: it stays on the book's page.
    assert result.book.blocks == (p("Copyright and dedication.", 1),)
    assert result.book.meta.chapters == 3


def test_long_front_text_is_a_part_of_its_own() -> None:
    doc = book(h(2, "Preface", 1), p(LONG, 1), *THREE[1:], outline=OUTLINE, language="es")
    (front, *_rest) = split_document(doc, mode="chapters")[0].chapters  # type: ignore[union-attr]
    assert front.number is None and front.document.meta.title == "Preliminares"
    assert front.document.meta.chapter is None and front.document.meta.chapters == 3


def test_without_outline_chapter_headings_are_the_boundaries() -> None:
    blocks = (
        h(2, "Prólogo", 1),
        p(LONG, 1),
        h(2, "Capítulo 1. El comienzo", 2),
        p(LONG, 2),
        h(2, "Capítulo 2. El final", 3),
        p(LONG, 3),
        h(2, "Agradecimientos", 4),  # not a chapter: stays in chapter 2
        p("Gracias.", 4),
    )
    titles = [d.meta.title for d in chapters_of(book(*blocks))]
    assert titles == ["Front matter", "Capítulo 1. El comienzo", "Capítulo 2. El final"]


def test_numbered_and_roman_headings_are_chapters_sections_are_not() -> None:
    numbered = (h(2, "1 Redes", 1), p(LONG, 1), h(3, "1.1 Subredes", 1), p(LONG, 1),
                h(2, "2 Rutas", 2), p(LONG, 2))  # fmt: skip
    assert [d.meta.title for d in chapters_of(book(*numbered))] == ["1 Redes", "2 Rutas"]
    roman = (h(2, "I. Platero", 1), p(LONG, 1), h(2, "II. El loco", 2), p(LONG, 2))
    assert len(chapters_of(book(*roman))) == 2


def test_footnotes_go_with_the_chapter_that_cites_them() -> None:
    blocks = (
        h(2, "Chapter 1", 1),
        p(LONG, 1, FootnoteRef("1")),
        h(2, "Chapter 2", 2),
        p(LONG, 2, FootnoteRef("2"), FootnoteRef("3")),
        # Notes collected at the end of the book.
        FootnoteDef("1", (Paragraph((Text("First note."),)),)),
        FootnoteDef("2", (Paragraph((Text("Second note."),)),)),
        FootnoteDef("3", (Paragraph((Text("Third note."),)),)),
    )
    one, two = chapters_of(book(*blocks))
    assert [b.key for b in one.blocks if isinstance(b, FootnoteDef)] == ["1"]
    # Numbered 1..n within each chapter.
    defs = [b for b in two.blocks if isinstance(b, FootnoteDef)]
    assert [d.key for d in defs] == ["1", "2"]
    assert defs[0].blocks == (Paragraph((Text("Second note."),)),)
    assert not [d for d in two.diagnostics if d.code.startswith("footnote.")]


def test_a_note_call_in_a_chapter_title_is_kept() -> None:
    blocks = (
        h(2, "Chapter 1", 1, FootnoteRef("1")),
        p(LONG, 1),
        h(2, "Chapter 2", 2),
        p(LONG, 2),
        FootnoteDef("1", (Paragraph((Text("Written in 1914."),)),)),
    )
    one, _ = chapters_of(book(*blocks))
    assert one.meta.title == "Chapter 1"
    assert one.blocks[0] == Paragraph((FootnoteRef("1"),))
    assert isinstance(one.blocks[-1], FootnoteDef)


def test_images_and_diagnostics_follow_their_chapter() -> None:
    picture = Asset.from_bytes(b"png-bytes", "image/png")
    blocks = (
        h(2, "Chapter 1", 1),
        p(LONG, 1),
        h(2, "Chapter 2", 2),
        Image(picture.sha256, "", (), Provenance(SRC, 3)),
        p(LONG, 3),
    )
    page_3 = diagnose(codes.PDF_GARBAGE_TEXT, "Page 3.", Provenance(SRC, 3))
    whole = diagnose(codes.PDF_FURNITURE_REMOVED, "Headers removed.")
    doc = replace(book(*blocks), assets=(picture,), diagnostics=(page_3, whole))
    result, _ = split_document(doc, mode="chapters")
    assert result is not None
    one, two = (c.document for c in result.chapters)
    assert one.assets == () and two.assets == (picture,)
    assert two.diagnostics == (page_3,) and one.diagnostics == ()
    assert result.book.diagnostics == (whole,)


def test_a_part_title_page_joins_the_next_chapter() -> None:
    blocks = (
        h(2, "Chapter 1", 1),
        p(LONG, 1),
        h(2, "Part II", 2),  # a title page, no text
        h(2, "Chapter 2", 3),
        p(LONG, 3),
    )
    outline = (
        OutlineEntry("Chapter 1", 1, page=1),
        OutlineEntry("Part II", 1, page=2),
        OutlineEntry("Chapter 2", 1, page=3),
    )
    _one, two = chapters_of(book(*blocks, outline=outline))
    assert two.meta.title == "Chapter 2" and two.meta.chapter == 2
    assert two.blocks[0] == h(2, "Part II", 2)  # kept, at the top of the chapter


def test_chapters_nested_in_a_careless_outline_still_count() -> None:
    blocks = tuple(
        b for n in range(1, 5) for b in (h(2, f"{'I' * n}. Title", n), p(LONG, n))
    )  # I., II., III., IIII.
    outline = (
        OutlineEntry("I. Title", 1, page=1),
        OutlineEntry("II. Title", 1, page=2),
        OutlineEntry("III. Title", 2, page=3),  # nested under II by mistake
        OutlineEntry("IIII. Title", 2, page=4),
    )
    assert len(chapters_of(book(*blocks, outline=outline))) == 4


def test_when_not_to_split() -> None:
    small = book(*THREE, outline=OUTLINE)
    # auto: below the size threshold nothing is tried, and nothing is reported.
    assert split_document(small, mode="auto") == (None, small)
    assert split_document(small, mode="auto", cfg=SplitConfig(auto_min_chars=10))[0]
    assert split_document(small, mode="none") == (None, small)
    wiki = replace(small, meta=replace(small.meta, source_type="wikipedia"))
    assert split_document(wiki, mode="chapters")[0] is None
    # Asked for, but no structure: one file, and why.
    plain = book(p(LONG, 1), p(LONG, 2))
    result, doc = split_document(plain, mode="chapters")
    assert result is None
    assert [d.code for d in doc.diagnostics] == [codes.DOC_SPLIT_SKIPPED]


def test_lopsided_or_foreign_outlines_are_refused() -> None:
    # One chapter with almost all the text: the outline missed the structure.
    lopsided = book(
        h(2, "Chapter 1", 1), p(LONG * 20, 1), h(2, "Chapter 2", 2), p("Short.", 2),
        outline=(OutlineEntry("Chapter 1", 1, page=1), OutlineEntry("Chapter 2", 1, page=2)),
    )  # fmt: skip
    assert split_document(lopsided, mode="chapters")[0] is None
    # An outline of another edition: its entries are not in this text, and the headings
    # are no chapters either.
    foreign = book(
        h(2, "Intro", 1), p(LONG, 1), h(2, "Body", 2), p(LONG, 2),
        outline=tuple(OutlineEntry(f"Lesson {n}", 1, page=n) for n in range(1, 6)),
    )  # fmt: skip
    assert split_document(foreign, mode="chapters")[0] is None


def test_epub_outline_targets_files() -> None:
    def ep(block: Block, ref: str) -> Block:
        return replace(block, provenance=Provenance(ref))  # type: ignore[call-arg]

    blocks = (
        ep(h(2, "One", 1), "OEBPS/c1.xhtml"),
        ep(p(LONG, 1), "OEBPS/c1.xhtml"),
        ep(p(LONG, 1), "OEBPS/c2.xhtml"),  # a chapter whose first block is no heading
        ep(p(LONG, 1), "OEBPS/c2.xhtml"),
    )
    outline = (
        OutlineEntry("One", 1, target="OEBPS/c1.xhtml#start"),
        OutlineEntry("Two", 1, target="OEBPS/c2.xhtml"),
    )
    one, two = chapters_of(book(*blocks, outline=outline, source_type="epub"))
    assert (one.meta.title, two.meta.title) == ("One", "Two")
    assert len(two.blocks) == 2


def test_parts_with_their_own_contents_join_their_first_chapter() -> None:
    """Front matter entries open no file; a "Part" groups chapters and joins the first one
    even with a short contents list of its own; a title set in pieces ("CHAPTER", "2") is
    taken off, before and after the heading the outline anchors to."""
    contents = "Chapter 2: Types of Architects. Chapter 3: Career Planning for Architects."
    blocks = (
        p("Cover text and copyright notice of the book, all rights reserved.", 1),
        h(2, "P a r t", 3),
        p("2", 3),
        h(2, "In This Part", 3),
        p(contents, 3),
        h(2, "CHAPTER", 4),
        p("2", 4),
        h(2, "Types of Architects", 4),
        p(LONG, 4),
        h(2, "CHAPTER", 6),
        p("3", 6),
        h(2, "Career Planning for Architects", 6),
        p(LONG, 6),
    )
    outline = (
        OutlineEntry("Cover", 1, page=1),
        OutlineEntry("Copyright Page", 1, page=1),
        OutlineEntry("Part 2 Career Planning", 1, page=3),
        OutlineEntry("Chapter 2 Types of Architects", 2, page=4),
        OutlineEntry("Chapter 3 Career Planning for Architects", 2, page=6),
        OutlineEntry("Part 3 Toolbox", 1, page=8),  # not in this text: one entry missed
    )
    result, _ = split_document(book(*blocks, outline=outline), mode="chapters")
    assert result is not None
    two, three = (c.document for c in result.chapters)
    assert two.meta.title == "Chapter 2 Types of Architects"
    assert two.blocks[:3] == (
        Heading(2, (Text("Part 2 Career Planning"),), Provenance(SRC, 3)),
        h(2, "In This Part", 3),
        p(contents, 3),
    )
    assert two.blocks[3] == p(LONG, 4)  # "CHAPTER", "2" and the title are gone
    assert three.blocks == (p(LONG, 6),)
    assert result.book.blocks[0] == blocks[0]  # the cover text stays on the book's page
