"""A PDF's title, chosen among several witnesses (§6).

The largest text on page 1 is often not the title: a publisher's logo ("PEARSON"), a
scan group's watermark ("Team-Fly"), one line of a title set over two ("with Open Source
Tools"). The metadata title can be layout junk ("Book.ID_25782_979-8-..."). The file name
of a downloaded book usually holds the title ("Author - Title_ Subtitle (2020, CRC Press)
- libgen.li"). Each witness gives a candidate; implausible ones are discarded; the one
most witnesses agree with, and that the first pages contain, wins, the fuller one first.
"""

import re
import unicodedata
from dataclasses import dataclass

# Publishers, imprints and scan groups: a page-1 logo, never a title.
_NOT_TITLES = {
    "pearson", "wiley", "springer", "apress", "packt", "manning", "oreilly", "o reilly",
    "elsevier", "mcgraw hill", "addison wesley", "prentice hall", "no starch press",
    "crc press", "taylor francis", "routledge", "cambridge university press",
    "oxford university press", "mit press", "the mit press", "sage", "wiley vch",
    "team fly", "glossy", "ok glossy", "contents", "contributors", "preface", "foreword",
    "cover", "title page", "copyright", "index", "introduction", "untitled", "document",
    "microsoft word", "abstract", "chapter",
}  # fmt: skip
_JUNK = re.compile(
    r"\b(?:isbn|book\s*\.?\s*id|indd|\.pdf|\.docx?|\.tex|microsoft\s+word|untitled)\b"
    r"|\d{3}-\d{1,5}-\d{1,7}"  # ISBN-like groups
    r"|\d{9,}",  # catalogue numbers
    re.IGNORECASE,
)
# Pages around the title that name it in theirs: "Advance Praise for Head First...",
# "Also by the Author", "About the Author". Never the title itself.
_SECTION_TITLE = re.compile(
    r"^\s*(?:(?:advance\s+)?praise\s+(?:for|of)\b|also\s+by\b|other\s+books\s+by\b"
    r"|about\s+the\s+(?:authors?|editors?)\b|(?:sobre|acerca\s+de)\s+(?:el|los)\s+autor"
    r"|table\s+of\s+contents\b|copyright\b)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SMALL_WORDS = {
    "a", "an", "the", "of", "and", "or", "for", "to", "in", "on", "with", "at", "by",
    "from", "de", "la", "el", "los", "las", "y", "en", "del", "para", "con", "por",
}  # fmt: skip
# File names of downloaded books: sites and noise around the title.
_SITE = re.compile(
    r"\s*[-(]?\s*(?:libgen\.(?:li|rs|is)|z-?lib(?:rary)?(?:\.org)?|pdfdrive(?:\.com)?"
    r"|annas?-archive(?:\.org)?|dokumen\.pub|ebin\.pub|epdf\.pub|vdoc\.pub)\s*\)?",
    re.IGNORECASE,
)
_YEAR_PUBLISHER = re.compile(r"\s*[\[(][^)\]]*\b(?:1[89]|20)\d\d\b[^)\]]*[\])]")
_BRACKETED = re.compile(r"^\s*[\[(][^)\]]*[\])]\s*")  # "[Series] ...", "(Studies in ...)"
_LEADING_ID = re.compile(r"^\d{5,}[-_ ]+")
# "[Team-IRA]", "(MEAP V07)", "(Early Release)", "(1)": tags, not title words.
_TAGS = re.compile(
    r"\[[^\]]*\]|\((?:meap|early\s+release|rough\s+cuts|\d{1,2})[^)]*\)", re.IGNORECASE
)
# A publisher glued to the title with a bare hyphen: "...Python-CRC Press",
# "...TensorFlow-O'reilly", "...Data-Norton & Company Inc".
_PUBLISHER_WORDS = (
    r"press|publishing|publishers?|inc|ltd|company|media|books|springer|wiley|packt|apress"
    r"|manning|elsevier|pearson|o['’]?reilly|mcgraw[- ]hill|routledge|crc|norton|sage|cengage"
)
_GLUED_PUBLISHER = re.compile(
    rf"(?<=[^\s-])-(?=[^-]*\b(?:{_PUBLISHER_WORDS})\b)[^-]*$", re.IGNORECASE
)
_EDGE_PUBLISHER = re.compile(
    r"^(?:oreilly|o['’]reilly|packt|apress|manning|wiley|pearson)\s+"
    r"|\s+(?:oreilly|o['’]reilly|packt|apress|manning|wiley|pearson|springer)$",
    re.IGNORECASE,
)
_NUMBERS = re.compile(r"\b(?:97[89][-\d]{10,14}|\d{9,}[\dxX]?)\b")  # ISBNs, catalogue numbers
_EDITION = re.compile(
    r",?\s*\(?\b\d{1,2}(?:st|nd|rd|th)(?:\s+ed(?:ition)?\.?)?\)?\s*$", re.IGNORECASE
)
# Titles with at least this many content words count as equally full in the tie-break,
# so the document's own title is not beaten by a longer file name.
_FULL_TITLE_WORDS = 3
# A file name gives a title only with at least this many words.
_MIN_FILE_NAME_WORDS = 2
# A main title names a book alone with at least this many content words.
_MIN_MAIN_TITLE_WORDS = 2
_TRAILING_NOISE = re.compile(
    r"(?:[-_ ]+(?:\(\d+\)|\d{1,2}|v\d+|meap\s*v\d+|(?:jan|feb|mar|apr|may|jun|jul|aug|sep"
    r"|oct|nov|dec)[a-z]*[-_ ]\d{4}|\d{4}|espanol|english|ebook|pdf|epub))+\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    source: str  # "metadata", "cover" or "file name"


def choose_title(
    metadata: str | None,
    cover: str | None,
    file_stem: str,
    front_text: str,
    *,
    cover_from_ocr: bool = False,
) -> str | None:
    """The best title among the metadata title, the cover's largest text (lines joined)
    and the title in the file name; `front_text` is the text of the first pages. A cover
    read by OCR is a guess: it loses to a plausible metadata title unless another witness
    agrees with it."""
    candidates = [
        Candidate(clean(text), source)
        for text, source in (
            (metadata, "metadata"),
            (cover, "cover"),
            (title_from_file_name(file_stem), "file name"),
        )
        if text
    ]
    plausible = [
        c
        for c in candidates
        if is_plausible(c.text)
        # A file name of one word ("scan", "input", "document") names no book.
        and (c.source != "file name" or len(_words(c.text)) >= _MIN_FILE_NAME_WORDS)
    ]
    if cover_from_ocr and any(c.source == "metadata" for c in plausible):
        others = [_words(c.text) for c in plausible if c.source != "cover"]
        plausible = [
            c
            for c in plausible
            if c.source != "cover" or any(_agree(_words(c.text), o) for o in others)
        ]
    if not plausible:
        return None
    front = set(_words(front_text))

    def score(c: Candidate) -> tuple[int, int, int, int, int, int]:
        words = _words(c.text)
        support = sum(
            1 for other in plausible if other is not c and _agree(words, _words(other.text))
        )
        # The cover's text is on the first pages by construction: no evidence of itself.
        on_page = (
            c.source != "cover"
            and bool(words)
            and (sum(w in front for w in words) >= 0.8 * len(words))
        )
        content = len([w for w in words if w not in _SMALL_WORDS])
        order = -["metadata", "cover", "file name"].index(c.source)
        own = int(c.source != "file name")
        # Evidence first; then a one-word title loses to a fuller one; then the document's
        # own title beats its file name; then the fuller, then the order of the witnesses.
        return (support + int(on_page), min(content, _FULL_TITLE_WORDS), own, content,
                len(words), order)  # fmt: skip

    best = max(plausible, key=score)
    # A witness holding the whole of the best one and more ("with Open Source Tools" in
    # "Data Analysis with Open Source Tools") is the complete title: take it.
    fuller = [
        c for c in plausible
        if c is not best and set(_words(c.text)) > set(_words(best.text))
        and _agree(_words(c.text), _words(best.text))
    ]  # fmt: skip
    if fuller:
        best = max(fuller, key=score)
    if best.source == "file name":
        # The same title as printed in the document, with its own capitals.
        words = set(_words(best.text))
        for c in plausible:
            if c.source != "file name" and set(_words(c.text)) >= words:
                return c.text
    return best.text


def title_from_file_name(stem: str) -> str | None:
    """The title in a downloaded book's file name, or None. "[Series] A. Author, B. Author
    - Title_ Subtitle (2020, Publisher) - libgen.li" -> "Title: Subtitle"."""
    name = _SITE.sub(" ", stem)
    name = _YEAR_PUBLISHER.sub(" ", name)
    name = _TAGS.sub(" ", name)
    name = _LEADING_ID.sub("", name.strip())
    while _BRACKETED.match(name):
        name = _BRACKETED.sub("", name, count=1)
    name = _TRAILING_NOISE.sub("", name.strip())
    if " " not in name.strip():  # slug-like: "oreilly-data-analysis-with-open-source-tools"
        name = re.sub(r"[-_.]+", " ", name)
        name = _TRAILING_NOISE.sub("", name.strip())
    name = _GLUED_PUBLISHER.sub("", name)
    parts = [p.strip() for p in re.split(r"\s+-\s+", name) if p.strip()]
    if len(parts) > 1 and _looks_like_authors(parts[0]):
        parts = parts[1:]
    title = ": ".join(parts)
    title = re.sub(r"\s*_\s+", ": ", title).replace("_", " ")
    title = _NUMBERS.sub(" ", title)
    title = _EDITION.sub("", title)
    title = re.sub(r"\s+", " ", title).strip(" -:.,")
    title = _EDGE_PUBLISHER.sub("", title).strip(" -:.,")
    if title.islower():
        title = title[:1].upper() + title[1:]
    return title or None


def main_title(title: str) -> str:
    """The title without its subtitle, for naming files and folders: "Naked Statistics:
    Stripping the Dread from the Data" -> "Naked Statistics". Kept whole when the part
    before the colon is too short to name the book alone ("AI-Driven: ...")."""
    head = re.split(r"\s*[:–—]\s+|\s+-\s+|\.\s+(?=[A-Z])", title, maxsplit=1)[0]
    # Words as written ("AI-Driven" is one), not split at hyphens.
    content = [w for w in head.split() if w.casefold().strip(".,") not in _SMALL_WORDS]
    return head if len(content) >= _MIN_MAIN_TITLE_WORDS else title


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", " ".join(text.split()))
    return text.strip(" :;,-–—®™∗*†‡").strip()


def is_plausible(title: str) -> bool:
    """A title, not a logo, a watermark, a section name or layout junk."""
    words = _words(title)
    if not words or _JUNK.search(title) or _SECTION_TITLE.match(title):
        return False
    key = " ".join(words)
    if key in _NOT_TITLES:
        return False
    letters = sum(len(w) for w in words)
    if letters < 4 or (len(words) == 1 and len(words[0]) < 4):
        return False
    # Mostly letters: not a run of figures or symbols.
    return letters >= 0.6 * sum(1 for ch in title if not ch.isspace())


def _looks_like_authors(part: str) -> bool:
    """ "Elisa T. Lee, John Wang", "MEHTA, SHASHANK K.", "Neha Singh (editor)": names
    only, no function words a title would have."""
    part = re.sub(r"\((?:eds?|editors?|auth|author|trans)\.?\)", " ", part, flags=re.IGNORECASE)
    tokens = [t for t in re.split(r"[\s,;&]+", part) if t]
    if not tokens or len(tokens) > 16:
        return False
    for token in tokens:
        bare = token.strip(".")
        initial = len(bare) == 1 and token.endswith(".")  # "A.", "T."
        if not bare[:1].isupper():
            return False
        if not initial and bare.casefold() in _SMALL_WORDS - {"and", "y"}:
            return False
    return True


def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return [w.casefold() for w in _WORD.findall(plain)]


def _agree(a: list[str], b: list[str]) -> bool:
    """One title is mostly contained in the other ("Head First" / "Head First Data
    Analysis"): at least 60 % of the shorter one's words are in the longer one."""
    short, long_ = sorted((a, b), key=len)
    if not short:
        return False
    found = sum(1 for w in short if w in long_)
    return found >= 0.6 * len(short)
