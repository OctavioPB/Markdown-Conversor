"""«Para RAG» (ADR-019): leave out the parts of a book that are not its content (cover,
copyright, about the author, acknowledgments, contents and index, bibliography, lists of
figures, back cover...). A pure IR -> IR pass, after the default transforms and before a
book is split. Off unless asked for; every section left out is reported.
"""

import re
from dataclasses import replace

from tomd.ir import (
    Block,
    Diagnostic,
    Document,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    OutlineEntry,
    Provenance,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import diagnose
from tomd.ir.walk import iter_inlines, map_block_children, plain_text
from tomd.transforms.footnotes import renumber_footnotes

# Titles of sections that are not content, matched whole (a chapter called "References in
# Java" stays). An optional number in front ("10 References", "A.2 Bibliography").
NON_CONTENT_TITLE = re.compile(
    r"""^\s*(?:(?:\d+(?:\.\d+)*\.?|[A-Z](?:\.\d+)+\.?|[A-Z]\.)\s+)?(?:
      (?:front\s+|back\s+)?cover | cubierta | contracubierta | portada | contraportada
    | portadilla | half[-\s]title(?:\s+page)? | title\s+page | p[aá]gina\s+de\s+t[ií]tulo
    | copyright(?:\s+page)? | p[aá]gina\s+legal | cr[eé]ditos | credits
    | dedication | dedicatoria
    | (?:table\s+of\s+)?contents(?:\s+at\s+a\s+glance)? | (?:brief|detailed)\s+contents
    | contenidos? | sumario | index | [ií]ndice(?:\s+(?:general|alfab[eé]tico|anal[ií]tico
      |tem[aá]tico|onom[aá]stico|de\s+contenidos?))?
    | about\s+(?:the\s+)?(?:authors?|editors?|technical\s+editors?|contributing\s+editors?
      |contributors?|reviewers?|technical\s+reviewers?)
    | (?:sobre|acerca\s+de)\s+(?:el|la|los|las)\s+(?:autor(?:es|as?)?|editor(?:es|as?)?
      |colaborador(?:es|as?)?|revisor(?:es|as?)?)
    | contributors | colaboradores
    | acknowledge?ments? | agradecimientos
    | bibliograph(?:y|ies) | bibliograf[ií]a | references | referencias(?:\s+bibliogr[aá]ficas)?
    | works\s+cited | further\s+reading | lecturas\s+(?:recomendadas|adicionales)
    | list\s+of\s+(?:figures|tables|illustrations) | lista\s+de\s+(?:figuras|tablas)
    | [ií]ndice\s+de\s+(?:figuras|tablas|ilustraciones)
    | praise\s+for\b.* | also\s+by\b.* | other\s+books\s+by\b.* | del\s+mismo\s+autor
    | advertisements? | publicidad | colophon | colof[oó]n
    | eula | end[-\s]user\s+licen[cs]e\s+agreement
    )\s*[.:]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def drop_non_content(doc: Document) -> Document:
    """The document without the sections that are not content, each one reported."""
    body = [b for b in doc.blocks if not isinstance(b, FootnoteDef)]
    notes = [b for b in doc.blocks if isinstance(b, FootnoteDef)]
    pages, outline, outline_titles = _outline_pages(doc.outline)
    kept: list[Block] = []
    dropped: list[Block] = []
    reports: list[tuple[str, Provenance | None]] = []
    skip_level: int | None = None
    for block in body:
        if isinstance(block, Heading):
            if skip_level is not None and block.level <= skip_level:
                skip_level = None
            title = " ".join(plain_text(block.children).split())
            if skip_level is None and NON_CONTENT_TITLE.match(title):
                skip_level = block.level
                reports.append((title, block.provenance))
        page = _page(block)
        if skip_level is not None or (page is not None and page in pages):
            dropped.append(block)
        else:
            kept.append(block)
    seen = {title.casefold() for title, _ in reports}
    for title, first_page in outline_titles:  # pages without a heading of their own
        if title.casefold() not in seen and any(_page(b) == first_page for b in dropped):
            reports.append((title, Provenance(doc.meta.source, first_page)))
    if not dropped:
        return replace(doc, outline=outline)

    gone = {r.key for r in iter_inlines(tuple(dropped)) if isinstance(r, FootnoteRef)}
    still = {r.key for r in iter_inlines(tuple(kept)) if isinstance(r, FootnoteRef)}
    notes = [n for n in notes if n.key not in gone or n.key in still]
    images = _images(tuple(kept) + tuple(notes))
    diagnostics: list[Diagnostic] = list(doc.diagnostics)
    diagnostics += [
        diagnose(codes.DOC_SECTION_DROPPED, f"Section «{title}» left out (RAG mode).", prov)
        for title, prov in reports
    ]
    result = replace(
        doc,
        blocks=(*kept, *notes),
        assets=tuple(a for a in doc.assets if a.sha256 in images),
        diagnostics=tuple(diagnostics),
        outline=outline,
    )
    return renumber_footnotes(result)  # 1..n again, now that some notes went


def _outline_pages(
    outline: tuple[OutlineEntry, ...],
) -> tuple[set[int], tuple[OutlineEntry, ...], list[tuple[str, int]]]:
    """Pages that outline entries for non-content sections cover (from their page to the
    next entry's), the outline without those entries, and (title, first page) of each.

    Only whole pages: an entry of the outline's top level (cover, copyright, the index)
    that no other entry shares its first page with. A section starting mid-page ("Further
    Reading" after a chapter's text) shares its page with real content: its heading alone
    marks it, and the heading rule takes it out."""
    paged = [e for e in outline if e.page is not None]
    top = min((e.level for e in paged), default=1)
    pages: set[int] = set()
    titles: list[tuple[str, int]] = []
    for n, entry in enumerate(paged):
        if entry.level != top or not NON_CONTENT_TITLE.match(entry.title):
            continue
        start = entry.page or 0
        if any(e.page == start and e is not entry for e in paged):
            continue
        following = [e.page for e in paged[n + 1 :] if e.page is not None and e.page > start]
        end = following[0] if following else start + 1
        pages.update(range(start, end))
        titles.append((entry.title, start))
    kept = tuple(e for e in outline if not NON_CONTENT_TITLE.match(e.title))
    return pages, kept, titles


def _page(block: Block) -> int | None:
    provenance = getattr(block, "provenance", None)
    return provenance.page if provenance is not None else None


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
