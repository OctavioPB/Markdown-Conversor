from tests.builders import doc, p
from tomd.ir import (
    BlockQuote,
    Code,
    CodeBlock,
    Emph,
    FootnoteDef,
    FootnoteRef,
    Heading,
    LineBreak,
    ListBlock,
    ListItem,
    Paragraph,
    Row,
    Table,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.transforms import (
    drop_empty,
    merge_adjacent_text,
    normalize_headings,
    renumber_footnotes,
)


def _codes(d: object) -> list[str]:
    return [x.code for x in d.diagnostics]  # type: ignore[attr-defined]


# --- drop_empty ------------------------------------------------------------------------


def test_drop_empty_removes_blank_blocks_and_cascades_to_containers() -> None:
    d = doc(
        p("  \n "),
        Paragraph((Emph((Text(" "),)),)),
        CodeBlock("\n\n"),
        ListBlock((ListItem((p(" "),)),)),
        BlockQuote((p(""),)),
        p("ok"),
    )
    assert drop_empty(d).blocks == (p("ok"),)


def test_drop_empty_trims_edge_line_breaks_and_empty_inlines() -> None:
    d = doc(Paragraph((LineBreak(), Text(""), Code(""), Text("a"), LineBreak())))
    assert drop_empty(d).blocks == (p("a"),)


def test_drop_empty_replaces_empty_table_by_its_caption() -> None:
    d = doc(Table((), (), caption=(Text("Tabla"),)), Table((), ()))
    out = drop_empty(d)
    assert out.blocks == (Paragraph((Emph((Text("Tabla"),)),)),)
    assert _codes(out) == [codes.TABLE_EMPTY_DROPPED] * 2


def test_drop_empty_keeps_tables_with_rows() -> None:
    t = Table((Row(()),), ())
    assert drop_empty(doc(t)).blocks == (t,)


# --- merge_adjacent_text ---------------------------------------------------------------


def test_merge_adjacent_text_recurses_into_emphasis() -> None:
    d = doc(p("a", "b", Emph((Text("c"), Text("d"))), "e"))
    assert merge_adjacent_text(d).blocks == (p("ab", Emph((Text("cd"),)), "e"),)


# --- normalize_headings ----------------------------------------------------------------


def _levels(d: object) -> list[int]:
    return [b.level for b in d.blocks if isinstance(b, Heading)]  # type: ignore[attr-defined]


def test_headings_start_at_h2_and_compress_gaps() -> None:
    d = doc(*(Heading(n, (Text(str(n)),)) for n in (3, 5, 5, 4, 3, 6)))
    assert _levels(normalize_headings(d)) == [2, 3, 3, 3, 2, 3]


def test_several_h1_are_demoted() -> None:
    d = doc(Heading(1, (Text("Cap 1"),)), Heading(2, (Text("s"),)), Heading(1, (Text("Cap 2"),)))
    assert _levels(normalize_headings(d)) == [2, 3, 2]


def test_h1_matching_title_is_merged_case_and_space_insensitive() -> None:
    d = doc(Heading(1, (Text(" the  TITLE "),)), Heading(2, (Text("s"),)), title="The title")
    out = normalize_headings(d)
    assert _levels(out) == [2]
    assert out.meta.title == "The title"


def test_first_h1_becomes_title() -> None:
    d = doc(p("x"), Heading(1, (Emph((Text("Libro"),)),)), Heading(1, (Text("Otro"),)), title=None)
    out = normalize_headings(d)
    assert out.meta.title == "Libro"
    assert _levels(out) == [2]


def test_missing_title_is_an_error_diagnostic() -> None:
    out = normalize_headings(doc(p("x"), title=None))
    assert _codes(out) == [codes.DOC_TITLE_MISSING]
    assert out.diagnostics[0].severity == "error"
    assert out.meta.title is None


def test_deep_headings_are_clamped_with_diagnostic() -> None:
    d = doc(*(Heading(n, (Text(str(n)),)) for n in (1, 2, 3, 4, 5, 6)))
    out = normalize_headings(d)
    assert _levels(out) == [2, 3, 4, 5, 6, 6]
    assert _codes(out) == [codes.HEADING_LEVEL_CLAMPED]


def test_headings_inside_containers_are_normalized_in_document_order() -> None:
    d = doc(Heading(2, (Text("a"),)), BlockQuote((Heading(5, (Text("b"),)),)))
    quote = normalize_headings(d).blocks[1]
    assert isinstance(quote, BlockQuote)
    assert quote.blocks[0] == Heading(3, (Text("b"),))


# --- renumber_footnotes ----------------------------------------------------------------


def test_footnotes_numbered_by_first_reference_and_moved_to_end() -> None:
    d = doc(
        FootnoteDef("x", (p("X"),)),
        p("a", FootnoteRef("y"), FootnoteRef("x"), FootnoteRef("y")),
        FootnoteDef("y", (p("Y"),)),
    )
    out = renumber_footnotes(d)
    assert out.blocks == (
        p("a", FootnoteRef("1"), FootnoteRef("2"), FootnoteRef("1")),
        FootnoteDef("1", (p("Y"),)),
        FootnoteDef("2", (p("X"),)),
    )
    assert out.diagnostics == ()


def test_references_inside_definitions_are_numbered_after_the_body() -> None:
    d = doc(
        p("a", FootnoteRef("outer")),
        p("b", FootnoteRef("later")),
        FootnoteDef("outer", (p("see", FootnoteRef("inner")),)),
        FootnoteDef("inner", (p("I"),)),
        FootnoteDef("later", (p("L"),)),
    )
    out = renumber_footnotes(d)
    keys = [b.key for b in out.blocks if isinstance(b, FootnoteDef)]
    assert keys == ["1", "2", "3"]
    assert out.blocks[2] == FootnoteDef("1", (p("see", FootnoteRef("3")),))


def test_orphan_missing_and_duplicate_footnotes_emit_diagnostics() -> None:
    d = doc(
        p("a", FootnoteRef("ghost"), FootnoteRef("ok")),
        FootnoteDef("ok", (p("1"),)),
        FootnoteDef("ok", (p("dup"),)),
        FootnoteDef("orphan", (p("o"),)),
    )
    out = renumber_footnotes(d)
    assert out.blocks == (p("a", FootnoteRef("1")), FootnoteDef("1", (p("1"),)))
    assert _codes(out) == [
        codes.FOOTNOTE_DUPLICATE_DEFINITION,
        codes.FOOTNOTE_MISSING_DEFINITION,
        codes.FOOTNOTE_ORPHAN_DEFINITION,
    ]


def test_transforms_are_pure() -> None:
    d = doc(Heading(3, (Text("a"),)), p("x", FootnoteRef("k")), FootnoteDef("k", (p("n"),)))
    snapshot = repr(d)
    for transform in (drop_empty, merge_adjacent_text, normalize_headings, renumber_footnotes):
        transform(d)
    assert repr(d) == snapshot
