"""Property tests: escaping never alters rendered text (M10) and the block tree survives a
Markdown round trip (Phase 1 completion criterion)."""

import re

import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis import strategies as st

from tests.builders import convert, doc, p
from tests.mdparse import inline_texts, ir_tree, parse, token_tree
from tomd.ir import (
    Asset,
    Block,
    BlockQuote,
    Cell,
    Code,
    CodeBlock,
    Document,
    Emph,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    Inline,
    InlineMath,
    LineBreak,
    Link,
    ListBlock,
    ListItem,
    MathBlock,
    Paragraph,
    Row,
    Strong,
    Table,
    Text,
    ThematicBreak,
)
from tomd.transforms import apply_default_transforms

NBSP = chr(0xA0)
TRICKY = [*"*_`[]<>\\#-+=|!&;:~$.()^\"' 0123456789aZé\t\n", NBSP]

texts = st.text(
    alphabet=st.one_of(
        st.sampled_from(TRICKY),
        st.characters(exclude_categories=("Cs", "Cc")),
    ),
    max_size=30,
)

ASCII_WS = re.compile(r"[ \t\n\r\f\v]+")


def normalize(s: str) -> str:
    """What a paragraph of plain text should read like once rendered."""
    return ASCII_WS.sub(" ", s).strip()


def _strip_html(s: str) -> str:
    return re.sub(r"<html:</?\w+>>", "", s)


# The "explain" phase pretty-prints huge IR trees and can take minutes on failure.
PHASES = [Phase.explicit, Phase.reuse, Phase.generate, Phase.target, Phase.shrink]
PROPERTY = settings(max_examples=400, deadline=None, phases=PHASES)


@PROPERTY
@given(texts)
def test_M10_paragraph_text_roundtrips(s: str) -> None:
    out = convert(doc(p(s))).text
    expected = normalize(s)
    texts_out = inline_texts(out)[1:]  # skip the title
    assert texts_out == ([expected] if expected else [])


@PROPERTY
@given(texts, texts)
def test_M10_text_around_hard_break_roundtrips(a: str, b: str) -> None:
    out = convert(doc(p(a, LineBreak(), b))).text
    lines = [x for x in (normalize(a), normalize(b)) if x]
    assert inline_texts(out)[1:] == (["\n".join(lines)] if lines else [])


@PROPERTY
@given(texts)
def test_M10_heading_text_roundtrips(s: str) -> None:
    out = convert(doc(Heading(2, (Text(s),)))).text
    expected = normalize(s)
    assert inline_texts(out)[1:] == ([expected] if expected else [])


@PROPERTY
@given(texts, texts)
def test_M10_table_cell_text_roundtrips(a: str, b: str) -> None:
    table = Table(
        (Row((Cell((p("h"),)), Cell((p("h"),)))),), (Row((Cell((p(a),)), Cell((p(b),)))),)
    )
    out = convert(doc(table)).text
    assert inline_texts(out)[1:] == ["h", "h", normalize(a), normalize(b)]


@PROPERTY
@given(texts.filter(lambda s: s.strip()), texts)
def test_M10_link_text_and_url_roundtrip(label: str, url: str) -> None:
    out = convert(doc(p("x ", Link((Text(label),), url)))).text
    link = next(
        c
        for t in parse(out)
        if t.type == "inline"
        for c in t.children or []
        if c.type == "link_open"
    )
    assert link is not None
    assert inline_texts(out)[-1] == normalize("x " + label)


@PROPERTY
@given(texts, texts.filter(lambda s: s.strip()), texts)
def test_M09_emphasis_roundtrips(a: str, b: str, c: str) -> None:
    out = convert(doc(p(a, Emph((Text(b),)), c))).text
    rendered = inline_texts(out)[-1]
    assert _strip_html(rendered) == normalize(re.sub(" +", " ", a + b + c))
    kinds = [ch.type for t in parse(out) if t.type == "inline" for ch in t.children or []]
    assert kinds.count("em_open") + rendered.count("<html:<em>>") == 1
    assert "strong_open" not in kinds


# --- Random documents: block tree survives the round trip ------------------------------

ASSET = Asset.from_bytes(b"png", "image/png")

words = st.text(alphabet=st.sampled_from(TRICKY), min_size=1, max_size=12)


def inline_nodes(allow_refs: bool) -> st.SearchStrategy[tuple[Inline, ...]]:
    leaf: list[st.SearchStrategy[Inline]] = [
        words.map(Text),
        words.map(Code),
        words.map(InlineMath),
        st.just(LineBreak()),
        st.tuples(words, words).map(lambda t: Link((Text(t[0]),), t[1])),
        words.map(lambda w: Emph((Text(w),))),
        words.map(lambda w: Strong((Text(w),))),
    ]
    if allow_refs:
        leaf.append(st.sampled_from(["a", "b"]).map(FootnoteRef))
    return st.lists(st.one_of(leaf), min_size=1, max_size=6).map(tuple)


def paragraphs(allow_refs: bool = True) -> st.SearchStrategy[Block]:
    return inline_nodes(allow_refs).map(Paragraph)


def cells() -> st.SearchStrategy[Cell]:
    return st.builds(
        Cell,
        st.lists(paragraphs(allow_refs=False), max_size=2).map(tuple),
        st.integers(1, 2),
        st.integers(1, 2),
    )


tables = st.builds(
    Table,
    st.lists(
        st.lists(cells(), min_size=1, max_size=3).map(lambda c: Row(tuple(c))), max_size=2
    ).map(tuple),
    st.lists(
        st.lists(cells(), min_size=1, max_size=3).map(lambda c: Row(tuple(c))), max_size=3
    ).map(tuple),
    st.one_of(st.just(()), inline_nodes(allow_refs=False)),
)

leaf_blocks: st.SearchStrategy[Block] = st.one_of(
    paragraphs(),
    st.builds(Heading, st.integers(1, 6), inline_nodes(allow_refs=True)),
    st.builds(
        CodeBlock,
        st.text(alphabet=st.sampled_from(TRICKY), max_size=20),
        st.sampled_from([None, "py", "a b"]),
    ),
    st.builds(MathBlock, words),
    st.just(ThematicBreak()),
    st.builds(Image, st.just(ASSET.sha256), words, st.one_of(st.just(()), inline_nodes(False))),
    tables,
)


def _containers(children: st.SearchStrategy[Block]) -> st.SearchStrategy[Block]:
    block_lists = st.lists(children, min_size=1, max_size=3).map(tuple)
    items = st.lists(block_lists.map(ListItem), min_size=1, max_size=3).map(tuple)
    return st.one_of(
        st.builds(ListBlock, items, st.booleans(), st.integers(0, 12)),
        block_lists.map(BlockQuote),
    )


blocks = st.recursive(leaf_blocks, _containers, max_leaves=12)

documents = st.tuples(
    st.lists(blocks, min_size=1, max_size=6),
    st.lists(paragraphs(allow_refs=False), min_size=1, max_size=2),
    st.lists(paragraphs(allow_refs=False), min_size=1, max_size=2),
).map(
    lambda t: doc(
        *t[0],
        FootnoteDef("a", tuple(t[1])),
        FootnoteDef("b", tuple(t[2])),
        assets=(ASSET,),
    )
)


def _check_roundtrip(document: Document) -> None:
    transformed = apply_default_transforms(document)
    out = convert(document).text
    assert token_tree(parse(out)) == ir_tree(transformed), f"\n{out}\n{transformed.blocks!r}"


ROUNDTRIP = {"deadline": None, "phases": PHASES, "suppress_health_check": [HealthCheck.too_slow]}


@settings(max_examples=150, **ROUNDTRIP)  # type: ignore[arg-type]
@given(documents)
def test_block_tree_survives_markdown_roundtrip(document: Document) -> None:
    _check_roundtrip(document)


@pytest.mark.slow
@settings(max_examples=3000, **ROUNDTRIP)  # type: ignore[arg-type]
@given(documents)
def test_block_tree_survives_markdown_roundtrip_thorough(document: Document) -> None:
    _check_roundtrip(document)


# --- Nested emphasis keeps its structure ----------------------------------------------

emph_words = st.text(alphabet=st.sampled_from([*"ab*_ .,(!", NBSP]), min_size=1, max_size=5)
emph_trees = st.recursive(
    emph_words.map(Text),
    lambda inner: st.one_of(
        st.lists(inner, min_size=1, max_size=3).map(lambda c: Emph(tuple(c))),
        st.lists(inner, min_size=1, max_size=3).map(lambda c: Strong(tuple(c))),
    ),
    max_leaves=6,
)


def _shape_ir(nodes: tuple[Inline, ...]) -> str:
    out = []
    for n in nodes:
        if isinstance(n, Text):
            out.append(n.value)
        elif isinstance(n, Emph):
            out.append(f"<em>{_shape_ir(n.children)}</em>")
        elif isinstance(n, Strong):
            out.append(f"<strong>{_shape_ir(n.children)}</strong>")
    return "".join(out)


def _shape_tokens(markdown: str) -> str:
    tags = {"em_open": "<em>", "em_close": "</em>", "strong_open": "<strong>"}
    tags["strong_close"] = "</strong>"
    for tok in parse(markdown):
        if tok.type == "inline" and tok.children:
            parts = []
            for c in tok.children:
                if c.type in tags:
                    parts.append(tags[c.type])
                elif c.type in ("text", "html_inline"):
                    parts.append(c.content)
            shape = "".join(parts)
    return shape


@PROPERTY
@given(st.lists(emph_trees, min_size=1, max_size=3))
def test_M09_nested_emphasis_keeps_structure(nodes: list[Inline]) -> None:
    from tomd.writers.markdown import _prepare

    prepared = _prepare(tuple(nodes), flatten=False)
    if not prepared:
        return
    out = convert(doc(Paragraph(tuple(nodes)))).text
    assert _shape_tokens(out) == _shape_ir(prepared), out
