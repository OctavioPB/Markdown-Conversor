"""Text normalization for extracted PDF text (§7). Never NFKC: it would also turn `²`
into `2` and `½` into `1⁄2`."""

import unicodedata

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}
SOFT_HYPHEN = chr(0x00AD)
# PDFium reports a hyphen it recognises as a line-break hyphen with these code points.
PDFIUM_HYPHEN_MARKERS = frozenset({chr(0xFFFE), chr(0x02)})
# No-break, en/em, figure, thin, hair, narrow no-break and ideographic spaces.
_SPACE_CODES = (0x00A0, *range(0x2002, 0x200B), 0x202F, 0x3000)
_SPACES = {chr(code): " " for code in _SPACE_CODES}
_TRANSLATE = str.maketrans({**LIGATURES, **_SPACES, SOFT_HYPHEN: ""})
HYPHENS = frozenset({"-", chr(0x2010), chr(0x2011)})  # hyphen, non-breaking


def normalize_char(text: str) -> str:
    if text in PDFIUM_HYPHEN_MARKERS:
        return "-"
    return text.translate(_TRANSLATE)


def is_garbage(ch: str) -> bool:
    """Replacement, private-use and control characters: a missing ToUnicode map."""
    if ch == "�":
        return True
    code = ord(ch)
    if 0xE000 <= code <= 0xF8FF:
        return True
    return unicodedata.category(ch) == "Cc" and ch not in "\t\n\r"


def ends_with_hyphen(text: str) -> bool:
    """A line-final hyphen attached to its word (not a spaced dash)."""
    return len(text) > 1 and text[-1] in HYPHENS and not text[-2].isspace()


def join_lines(lines: list[str]) -> tuple[str, bool]:
    """Join the lines of a paragraph, repairing line-break hyphens.

    Without a dictionary (ADR-015) a hyphen at a line end is dropped when the next line
    starts in lowercase (a word cut in two) and kept otherwise. Returns the text and
    whether any join was a guess (lowers the block's confidence).
    """
    out = ""
    guessed = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        if ends_with_hyphen(out):
            if out[-2].isalpha() and line[0].islower():
                out = out[:-1] + line  # a word cut in two
                guessed = True
            else:
                out = out + line  # compound or range kept whole: "Norte-Sur", "3-4"
            continue
        out = out + " " + line
    return out, guessed
