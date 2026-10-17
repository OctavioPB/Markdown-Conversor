"""M09-M12: inline syntax and escaping."""

import pytest

from tests.builders import body, convert, doc, p
from tests.mdparse import inline_texts, parse
from tomd.ir import Code, Emph, Inline, InlineMath, LineBreak, Link, Strong, Text


def _render_para(*children: Inline | str) -> str:
    return body(convert(doc(p(*children))).text)


def _tokens(markdown: str) -> list[str]:
    for tok in parse(markdown):
        if tok.type == "inline":
            return [c.type for c in tok.children or []]
    return []


def test_M09_emphasis_strong_and_code() -> None:
    out = _render_para(Emph((Text("e"),)), " ", Strong((Text("s"),)), " ", Code("c"))
    assert out == "*e* **s** `c`\n"


def test_M09_code_fence_widens_with_backticks() -> None:
    assert _render_para(Code("a`b")) == "``a`b``\n"
    assert _render_para(Code("`x")) == "`` `x ``\n"
    assert inline_texts(_render_para(Code("``x`")))[-1] == "``x`"


def test_M09_emphasis_falls_back_to_html_when_delimiters_cannot_flank() -> None:
    # "a*(b)*c": the closing * is preceded by punctuation and followed by a letter.
    out = _render_para("a", Emph((Text("(b)"),)), "c")
    assert "<em>" in out
    assert inline_texts(out)[-1] == "a<html:<em>>(b)<html:</em>>c"


def test_M09_whitespace_is_moved_out_of_emphasis() -> None:
    assert _render_para("a", Emph((Text(" b "),)), "c") == "a *b* c\n"


def test_M09_nested_emphasis_does_not_merge_delimiters() -> None:
    out = _render_para(Emph((Emph((Text("x"),)),)))
    assert "em_open" in _tokens(out) or "html_inline" in _tokens(out)
    assert "strong_open" not in _tokens(out)


@pytest.mark.parametrize(
    "text",
    [
        "*no emphasis*",
        "_x_",
        "snake_case_name",
        "[not a link](x)",
        "<b>raw html</b>",
        "a \\ b \\* c",
        "&amp; &#123; & ;",
        "precio: $5 y $6",
        "~~tachado~~",
        "C:\\Users\\x",
        "!",
    ],
)
def test_M10_escaping_preserves_text(text: str) -> None:
    out = _render_para(text)
    assert inline_texts(out)[-1] == text


@pytest.mark.parametrize(
    "text",
    ["# no heading", "- no list", "+ no list", "1. no list", "2) no list", "> no quote", "---"],
)
def test_M10_line_start_constructs_are_escaped(text: str) -> None:
    out = _render_para(text)
    assert _tokens(out) == ["text"]
    assert inline_texts(out)[-1] == text


@pytest.mark.parametrize("text", ["===", "---", "-|-", ":-", "| a |", "- x", "1. y"])
def test_M10_continuation_lines_cannot_open_blocks(text: str) -> None:
    out = _render_para("a | b", LineBreak(), text)
    assert inline_texts(out)[-1] == f"a | b\n{text}"
    assert [t.type for t in parse(out)] == ["paragraph_open", "inline", "paragraph_close"]


def test_M10_escaping_is_minimal() -> None:
    assert _render_para("snake_case, 3.5 - 2 + 1 # x") == "snake_case, 3.5 - 2 + 1 # x\n"


def test_M10_dashes_followed_by_emphasis_are_not_escaped() -> None:
    # "--*Hacer*" (a dialogue dash, Platero y yo) cannot be a list item or a rule: the
    # line holds more than dashes. Alone, "--" is still left alone and "---" escaped.
    out = _render_para("--", Emph((Text("Hacer"),)), " + infinitivo.")
    assert out == "--*Hacer* + infinitivo.\n"
    assert _tokens(out) == ["text", "em_open", "text", "em_close", "text"]  # a paragraph
    assert _render_para("---") == "\\---\n"


def test_M11_inline_links() -> None:
    assert _render_para(Link((Text("t"),), "https://x.org/a", "T")) == (
        '[t](https://x.org/a "T")\n'
    )


def test_M11_urls_with_spaces_or_parens_use_angle_brackets() -> None:
    out = _render_para(Link((Text("t"),), "https://x.org/a b(c)"))
    assert out == "[t](<https://x.org/a b(c)>)\n"
    assert _tokens(out) == ["link_open", "text", "link_close"]


def test_M11_link_title_with_quotes() -> None:
    out = _render_para(Link((Text("t"),), "u", 'say "hi"'))
    link = next(c for t in parse(out) if t.type == "inline" for c in t.children or [])
    assert link.attrs["title"] == 'say "hi"'


def test_M12_inline_math_without_inner_spaces() -> None:
    out = _render_para("Sea ", InlineMath(" x^2 + 1 "), " real.")
    assert out == "Sea $x^2 + 1$ real.\n"
    assert "math_inline" in _tokens(out)
