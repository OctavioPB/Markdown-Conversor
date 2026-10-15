"""M13-M16: lists, code, math and quotes."""

from tests.builders import body, convert, doc, p
from tests.mdparse import ir_tree, parse, token_tree
from tomd.ir import (
    BlockQuote,
    CodeBlock,
    ListBlock,
    ListItem,
    MathBlock,
    ThematicBreak,
)
from tomd.transforms import apply_default_transforms


def _same_tree(d: object) -> None:
    from tomd.ir import Document

    assert isinstance(d, Document)
    transformed = apply_default_transforms(d)
    assert token_tree(parse(convert(d).text)) == ir_tree(transformed)


def test_M13_bullets_use_dash_and_ordered_respect_start() -> None:
    d = doc(
        ListBlock((ListItem((p("a"),)), ListItem((p("b"),)))),
        p("x"),
        ListBlock((ListItem((p("c"),)), ListItem((p("d"),))), ordered=True, start=7),
    )
    assert body(convert(d).text) == "- a\n- b\n\nx\n\n7. c\n8. d\n"
    _same_tree(d)


def test_M13_children_align_with_parent_content() -> None:
    inner = ListBlock((ListItem((p("y"),)),))
    d = doc(
        ListBlock(
            (ListItem((p("x"), inner)), ListItem((p("z"),))),
            ordered=True,
            start=9,
        )
    )
    assert body(convert(d).text) == "9. x\n   - y\n10. z\n"
    _same_tree(d)


def test_M13_loose_items_with_several_blocks() -> None:
    d = doc(ListBlock((ListItem((p("a"), p("b"))), ListItem((CodeBlock("c"),)))))
    assert body(convert(d).text) == "- a\n\n  b\n\n- ```\n  c\n  ```\n"
    _same_tree(d)


def test_M13_adjacent_lists_of_same_kind_are_separated() -> None:
    d = doc(
        ListBlock((ListItem((p("a"),)),), ordered=True),
        ListBlock((ListItem((p("b"),)),), ordered=True, start=5),
    )
    assert "<!-- -->" in convert(d).text
    _same_tree(d)


def test_M13_nested_ordered_list_not_starting_at_one() -> None:
    d = doc(ListBlock((ListItem((p("a"), ListBlock((ListItem((p("b"),)),), True, 3))),)))
    _same_tree(d)


def test_M13_item_starting_with_thematic_break() -> None:
    _same_tree(doc(ListBlock((ListItem((ThematicBreak(), p("a"))),))))


def test_M14_fenced_code_with_language() -> None:
    out = body(convert(doc(CodeBlock("print(1)\n", "python"))).text)
    assert out == "```python\nprint(1)\n```\n"


def test_M14_fence_widens_when_content_has_backticks() -> None:
    d = doc(CodeBlock("```\nx\n```"))
    assert body(convert(d).text).startswith("````\n")
    fence = next(t for t in parse(convert(d).text) if t.type == "fence")
    assert fence.content == "```\nx\n```\n"


def test_M15_block_math() -> None:
    d = doc(MathBlock("\\int_0^1 x\\,dx"))
    assert body(convert(d).text) == "$$\n\\int_0^1 x\\,dx\n$$\n"
    _same_tree(d)


def test_M16_quotes_prefix_every_line_including_blank() -> None:
    d = doc(BlockQuote((p("a"), p("b"))))
    assert body(convert(d).text) == "> a\n>\n> b\n"
    _same_tree(d)


def test_M16_adjacent_quotes_stay_separate() -> None:
    _same_tree(doc(BlockQuote((p("a"),)), BlockQuote((p("b"),))))
