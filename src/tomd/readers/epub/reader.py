"""EPUB reader: spine XHTML -> IR through the shared HTML mapping (`html_common`).

EPUB-specific rules are handlers registered on the mapper, never a fork of the mapping:
- notes: `epub:type="noteref"` links, or EPUB 2 style links whose text is a note mark
  ("1", "[2]", "*"), become `FootnoteRef`s keyed `file#id`; their targets become
  `FootnoteDef`s, wherever they live, and back-links are removed. The final numbering is
  left to the `renumber_footnotes` transform;
- headings that are not `<h1>`-`<h6>`: an element the table of contents points to, or a
  short block whose CSS class names a heading (`chapter-title`, `h3`...);
- images: extracted to assets by hash; tiny decorative ones skipped;
- links: external ones kept; links between files of the book keep only their text;
- distributor boilerplate and print page markers removed, each with a diagnostic.
"""

import hashlib
import io
import mimetypes
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree, html
from PIL import Image as PILImage

from tomd.errors import UnreadableInputError
from tomd.ir import (
    Asset,
    Block,
    Document,
    DocumentMeta,
    FootnoteDef,
    FootnoteRef,
    Heading,
    Image,
    Inline,
    Link,
    OutlineEntry,
    Paragraph,
    Provenance,
    Text,
)
from tomd.ir import diagnostics as codes
from tomd.ir.diagnostics import Diagnostic, diagnose
from tomd.readers.epub.package import (
    EpubPackage,
    SpineItem,
    decode_xhtml,
    open_epub,
    resolve,
)
from tomd.readers.html_common import (
    BLOCK_TAGS,
    Element,
    Handler,
    HtmlMapper,
    classes,
    drop_handler,
    hoist_footnote_defs,
    tag_of,
    text_content,
)

BACKEND = "epub"
_EPUB_TYPE = "epub:type"
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# A note mark: "1", "[12]", "(3)", "*", "†"; letters only with a stronger cue (below).
_NOTE_MARK = re.compile(r"^\s*[\[(]?\s*(\d{1,3}|[*†‡§¶‖#]{1,3}|[a-z])\s*[\])]?\s*$")
_BACKLINK_TEXT = re.compile(r"^\s*(↩︎?|↑|\^|back|volver|regresar)\s*$", re.IGNORECASE)
# CSS class names that mean "heading", with the level they imply. Part/book above
# chapter, chapter above section; `normalize_headings` closes any gap.
_HEADING_CLASS = re.compile(
    r"^(?:h(?P<h>[1-6])|heading-?(?P<n>[1-6])"
    r"|(?P<part>part|book|volume)[-_]?(?:title|head|heading|name)?"
    r"|(?P<chapter>chapter|chap)[-_]?(?:title|head|heading|name)?"
    r"|(?P<section>section|sect)[-_]?(?:title|head|heading)"
    r"|(?P<sub>subsection|subchapter|subhead|subheading)"
    r"|(?P<title>title|titulo|chaptertitle))$",
    re.IGNORECASE,
)
# Headings are short; a long block with a heading-like class is a styled paragraph.
_MAX_HEADING_CHARS = 200
# Images under this many pixels a side are decoration (drop caps, ornaments, bullets).
_MIN_IMAGE_SIDE = 32
# An in-text table of contents: a list, table or block with at least this many links to
# the book (internal links), which hold at least this share of its text.
_TOC_MIN_LINKS = 3
# Outline limits for untrusted files (as in the PDF reader): real books nest 3-4 levels.
_OUTLINE_MAX_DEPTH = 6
_OUTLINE_MAX_ENTRIES = 5000
_TOC_LINK_SHARE = 0.8
# The short heading just before it ("Contents", "Capítulos:") goes with it.
_TOC_HEADING_CHARS = 40
# Semantic navigation (EPUB 3 structural semantics and ARIA roles).
_NAVIGATION_TYPES = frozenset(
    {"toc", "loi", "lot", "lov", "landmarks", "page-list", "doc-toc", "doc-pagelist"}
)
_TOC_CONTAINERS = frozenset({"table", "ul", "ol", "div", "p", "section"})
_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/svg+xml", "image/webp"})
_BOILERPLATE = frozenset({"pg-boilerplate", "pgheader", "pgfooter"})
_BOILERPLATE_IDS = frozenset({"pg-header", "pg-footer", "pg-machine-header"})
_HTML_PARSER = html.HTMLParser(no_network=True, remove_comments=True)


@dataclass
class EpubReader:
    def read(self, source: str) -> Document:
        return read_epub(Path(source))


def read_epub(path: Path) -> Document:
    package = open_epub(path)
    try:
        return _Conversion(package, path.name).run(_sha256(path))
    finally:
        package.close()


@dataclass
class _Note:
    key: str
    container: Element


@dataclass
class _Conversion:
    package: EpubPackage
    source: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    assets: dict[str, Asset] = field(default_factory=dict)
    roots: dict[str, Element] = field(default_factory=dict)  # spine path -> parsed body
    noterefs: dict[Element, str] = field(default_factory=dict)  # link -> note key
    notes: dict[Element, _Note] = field(default_factory=dict)  # container -> note
    toc_headings: dict[Element, int] = field(default_factory=dict)  # element -> level
    inline_tocs: set[Element] = field(default_factory=set)  # in-text TOCs to drop
    _image_sizes: dict[str, tuple[int, int] | None] = field(default_factory=dict)

    def report(self, code: str, message: str, ref: str | None = None) -> None:
        self.diagnostics.append(diagnose(code, message, Provenance(ref or self.source)))

    # --- Passes --------------------------------------------------------------------------

    def run(self, sha256: str) -> Document:
        meta = self.package.metadata
        if meta.fixed_layout:
            self.report(codes.EPUB_FIXED_LAYOUT, "Fixed-layout (pre-paginated) EPUB.")
        documents = self._parse_spine()
        self._find_notes()
        self._find_toc_headings()
        self._find_inline_tocs()
        blocks: list[Block] = []
        for item in documents:
            blocks.extend(self._map(item))
        title = meta.title
        doc_meta = DocumentMeta(
            title=title,
            source_type="epub",
            source=self.source,
            source_sha256=sha256,
            backend=BACKEND,
            language=meta.language,
            authors=meta.authors,
            license=meta.rights,
        )
        return Document(
            doc_meta, tuple(hoist_footnote_defs(blocks)), tuple(self.assets.values()),
            tuple(self.diagnostics), self._outline(),
        )  # fmt: skip

    def _outline(self) -> tuple[OutlineEntry, ...]:
        """The book's own table of contents (nav or NCX) for splitting it (ADR-018)."""
        entries: list[OutlineEntry] = []
        for entry in self.package.toc[:_OUTLINE_MAX_ENTRIES]:
            title = " ".join(entry.label.split())
            if not title or entry.depth > _OUTLINE_MAX_DEPTH:
                continue
            target = entry.path + (f"#{entry.fragment}" if entry.fragment else "")
            entries.append(OutlineEntry(title, entry.depth, target=target))
        return tuple(entries)

    def _parse_spine(self) -> list[SpineItem]:
        """Parse every spine document; leave out the TOC page and the cover page."""
        skip_toc = {self.package.nav_path, self.package.guide.get("toc")} - {None}
        kept: list[SpineItem] = []
        for entry in self.package.spine:
            path = entry.item.path
            if path in skip_toc:
                self.report(codes.EPUB_TOC_SKIPPED, f"Table of contents page {path} skipped.")
                continue
            data = self._member(path)
            if data is None:
                continue
            text, invalid = decode_xhtml(data)
            if invalid:
                self.report(
                    codes.TEXT_INVALID_UNICODE,
                    f"{invalid} undecodable byte(s) in {path} replaced by U+FFFD.",
                    path,
                )
            try:
                root = html.document_fromstring(text or "<html/>", parser=_HTML_PARSER)
            except (etree.ParserError, ValueError) as exc:
                raise UnreadableInputError(
                    f"{self.source}: {path} cannot be parsed: {exc}"
                ) from exc
            body = next((c for c in root if tag_of(c) == "body"), root)
            if self._is_cover_page(path, body):
                self.report(codes.EPUB_COVER_SKIPPED, f"Cover page {path} skipped.", path)
                continue
            self.roots[path] = body
            kept.append(entry)
        return kept

    def _member(self, path: str, *, report: bool = True) -> bytes | None:
        """A content file, or None when it is missing or damaged; the rest of the book
        still converts. Damage to the package itself (container, OPF) stays fatal."""
        try:
            data = self.package.read(path)
        except UnreadableInputError as exc:
            if report:
                self.report(codes.EPUB_MISSING_RESOURCE, f"{path} is damaged: {exc}", path)
            return None
        if data is None and report:
            self.report(codes.EPUB_MISSING_RESOURCE, f"{path} is not in the EPUB.", path)
        return data

    def _is_cover_page(self, path: str, body: Element) -> bool:
        """A page whose only content is the cover image (by OPF metadata or guide)."""
        if self.package.guide.get("cover") == path and not text_content(body).strip():
            return True
        cover_id = self.package.metadata.cover_id
        cover = self.package.manifest.get(cover_id or "")
        if cover is None or text_content(body).strip():
            return False
        images = [self._image_path(path, el) for el in body.iter() if _is_image(el)]
        return bool(images) and all(p == cover.path for p in images)

    def _ids(self) -> dict[tuple[str, str], Element]:
        ids: dict[tuple[str, str], Element] = {}
        for path, body in self.roots.items():
            for el in body.iter():
                if not isinstance(el.tag, str):
                    continue
                for name in ("id", "name") if tag_of(el) == "a" else ("id",):
                    value = el.get(name)
                    if value:
                        ids.setdefault((path, value), el)
        return ids

    def _find_notes(self) -> None:
        ids = self._ids()
        link_places: set[tuple[str, str]] = set()  # where note calls sit (back-link targets)
        for path, body in self.roots.items():
            for link in body.iter("a"):
                # Calls come before their notes; a link inside a note already found is
                # its back-link (EPUB 2 pairs link to each other), not another call.
                if link in self.notes or any(a in self.notes for a in link.iterancestors()):
                    continue
                target = self._note_target(path, link, ids)
                if target is None:
                    continue
                key, element = target
                container = _note_container(element)
                self.noterefs[link] = key
                self.notes.setdefault(container, _Note(key, container))
                for el in (link, link.getparent()):
                    if el is not None and el.get("id"):
                        link_places.add((path, el.get("id") or ""))
        # Notes marked as such but never called stay definitions (reported as orphans).
        for path, body in self.roots.items():
            for el in body.iter():
                note_id = el.get("id") if isinstance(el.tag, str) else None
                if note_id and _is_note_element(el) and el not in self.notes:
                    self.notes[el] = _Note(f"{path}#{note_id}", el)
        for note in self.notes.values():
            self._strip_backlinks(note, link_places)

    def _note_target(
        self, path: str, link: Element, ids: dict[tuple[str, str], Element]
    ) -> tuple[str, Element] | None:
        href = link.get("href")
        if not href or _is_external(href):
            return None
        target_path, fragment = resolve(path, href)
        if not fragment:
            return None
        target = ids.get((target_path, fragment))
        if target is None:
            return None
        types = _types(link)
        if not ({"noteref", "doc-noteref"} & types):
            text = text_content(link)
            mark = _NOTE_MARK.match(text)
            if mark is None or tag_of(target) in _HEADING_TAGS:
                return None
            cue = (
                _parent_tag(link) == "sup"
                or any(t in "".join(classes(link)).lower() for t in ("note", "fn"))
                or text.strip()[:1] in "[("
            )
            if not cue or (mark.group(1).isalpha() and _parent_tag(link) != "sup"):
                return None
            # A note does not sit in the paragraph that calls it (that is a cross-reference).
            if _block_ancestor(target) is _block_ancestor(link):
                return None
        return f"{target_path}#{fragment}", target

    def _strip_backlinks(self, note: _Note, link_places: set[tuple[str, str]]) -> None:
        path = note.key.split("#", 1)[0]
        for link in list(note.container.iter("a")):
            href = link.get("href") or ""
            back = resolve(path, href) if href and not _is_external(href) else None
            if (
                {"backlink", "doc-backlink"} & _types(link)
                or (back is not None and back[1] is not None and back in link_places)
                or _BACKLINK_TEXT.match(text_content(link))
                or _is_leading_label(note.container, link)
            ):
                _remove_keep_tail(link)

    def _find_toc_headings(self) -> None:
        """Elements the table of contents points at are headings, at the TOC depth, when
        they are not already <h1>-<h6> (or a container holding one)."""
        ids = self._ids()
        inferred = 0
        for entry in self.package.toc:
            element = ids.get((entry.path, entry.fragment)) if entry.fragment else None
            if element is None:
                continue
            block = element if tag_of(element) in BLOCK_TAGS else _block_ancestor(element)
            if block is None or block in self.toc_headings or not _heading_like(block):
                continue
            self.toc_headings[block] = min(entry.depth, 6)
            inferred += 1
        if inferred:
            self.report(
                codes.EPUB_HEADING_INFERRED,
                f"{inferred} heading(s) taken from the table of contents.",
            )

    def _find_inline_tocs(self) -> None:
        """Tables of contents typed into the text (EPUB 2 books often have one besides
        the NCX): they repeat the headings, and their links lead nowhere in Markdown.
        Either one container (list, table) of links, or a run of sibling blocks that are
        each one link ("Chapter I", "Chapter II"...). A short label just before it goes
        too."""
        for path, body in self.roots.items():
            for el in body.iter():
                if not isinstance(el.tag, str) or self._inside_toc_or_note(el):
                    continue
                if _NAVIGATION_TYPES & _types(el):  # marked as navigation by the book itself
                    self.inline_tocs.add(el)
                    continue
                if tag_of(el) in _TOC_CONTAINERS and self._links_only(path, el) >= (_TOC_MIN_LINKS):
                    self._mark_toc([el])
                    continue
                run: list[Element] = []
                for child in [*el, None]:
                    if child is not None and self._links_only(path, child) == 1:
                        run.append(child)
                        continue
                    if len(run) >= _TOC_MIN_LINKS:
                        self._mark_toc(run)
                    run = []

    def _inside_toc_or_note(self, el: Element) -> bool:
        chain = [el, *el.iterancestors()]
        return any(a in self.inline_tocs or a in self.notes for a in chain)

    def _links_only(self, path: str, el: Element) -> int:
        """How many links into the book `el` holds, when they make up nearly all of its
        text; else 0. Their targets need not exist: a damaged or partial book still has
        its table of contents."""
        if not isinstance(el.tag, str) or tag_of(el) not in _TOC_CONTAINERS | {"li", "td"}:
            return 0
        links = [
            a for a in el.iter("a")
            if a.get("href") and a not in self.noterefs
            and not _is_external(a.get("href") or "")
        ]  # fmt: skip
        total = len("".join(text_content(el).split()))
        linked = sum(len("".join(text_content(a).split())) for a in links)
        return len(links) if links and total and linked / total >= _TOC_LINK_SHARE else 0

    def _mark_toc(self, elements: list[Element]) -> None:
        self.inline_tocs.update(elements)
        label = elements[0].getprevious()
        while label is not None and _is_empty(label):  # spacers such as <p><br/></p>
            label = label.getprevious()
        if (
            label is not None
            and isinstance(label.tag, str)
            and not any(tag_of(d) == "a" and d.get("href") for d in label.iter())
            and 0 < len(text_content(label).strip()) <= _TOC_HEADING_CHARS
        ):
            self.inline_tocs.add(label)

    # --- Mapping -------------------------------------------------------------------------

    def _map(self, item: SpineItem) -> list[Block]:
        path = item.item.path
        mapper = HtmlMapper(source_ref=path, handlers=self._handlers(path))
        blocks = hoist_footnote_defs(mapper.blocks(self.roots[path]))
        self.diagnostics.extend(mapper.diagnostics)
        notes: list[Block] = [b for b in blocks if isinstance(b, FootnoteDef)]
        body = [b for b in blocks if not isinstance(b, FootnoteDef)]
        if notes and body and all(isinstance(b, Heading) for b in body):
            self.report(
                codes.EPUB_NOTES_HEADING_DROPPED,
                f"Notes section {path}: heading removed, notes became footnotes.",
                path,
            )
            return notes
        return blocks

    def _handlers(self, path: str) -> list[Handler]:
        def heading_block(el: Element, m: HtmlMapper) -> list[Block]:
            level = self.toc_headings.get(el) or _class_heading_level(el) or 1
            if el not in self.toc_headings:
                m.report_once(
                    codes.EPUB_HEADING_INFERRED, "Headings inferred from CSS class names."
                )
            return [Heading(level, tuple(m.inlines(el)), m.provenance)]

        def note_block(el: Element, m: HtmlMapper) -> list[Block]:
            note = self.notes[el]
            blocks = m.blocks(el) if tag_of(el) not in _HEADING_TAGS else []
            return [FootnoteDef(note.key, tuple(blocks), m.provenance)]

        def note_list(el: Element, m: HtmlMapper) -> list[Block]:
            return [b for li in el if isinstance(li.tag, str) for b in note_block(li, m)]

        def noteref(el: Element, m: HtmlMapper) -> list[Inline]:
            return [FootnoteRef(self.noterefs[el])]

        def link(el: Element, m: HtmlMapper) -> list[Inline]:
            children = m.inlines(el)
            href = el.get("href") or ""
            if _is_external(href):
                return [Link(tuple(children), href, el.get("title") or None)]
            if href:
                m.report_once(
                    codes.EPUB_INTERNAL_LINK_DROPPED, "Links inside the book kept as text."
                )
            return children

        def image_block(el: Element, m: HtmlMapper) -> list[Block]:
            image = self._image(path, el, m)
            return [image] if image is not None else []

        def image_inline(el: Element, m: HtmlMapper) -> list[Inline]:
            if self._is_small(path, el):
                return self._skip_small(path, m)
            alt = " ".join((el.get("alt") or "").split())
            m.report_once(
                codes.IMAGE_DROPPED, "Images inside headings or links kept as their alt text."
            )
            return [Text(alt)] if alt else []

        def figure(el: Element, m: HtmlMapper) -> list[Block]:
            caption_el = next((c for c in el.iter() if tag_of(c) == "figcaption"), None)
            caption = tuple(m.inlines(caption_el)) if caption_el is not None else ()
            out: list[Block] = []
            for child in el:
                if child is caption_el or not isinstance(child.tag, str):
                    continue
                if _is_image(child) or any(_is_image(d) for d in child.iter()):
                    for img in [d for d in child.iter() if _is_image(d)]:
                        image = self._image(path, img, m, caption if not out else ())
                        if image is not None:
                            out.append(image)
                else:
                    out.extend(m.block(child) if m.is_block(child) else m.blocks(child))
            if not out and caption:
                out.append(Paragraph(caption, m.provenance))
            return out

        return [
            drop_handler(
                "in-text-toc", lambda el: el in self.inline_tocs, codes.EPUB_TOC_SKIPPED
            ),
            Handler("noteref", lambda el: el in self.noterefs, inline=noteref),
            Handler(
                "note-list",
                lambda el: tag_of(el) in ("ol", "ul") and _all_notes(el, self.notes),
                block=note_list,
                is_block=lambda el: True,
            ),
            Handler("note", lambda el: el in self.notes, block=note_block,
                    is_block=lambda el: True),
            Handler("toc-heading", lambda el: el in self.toc_headings, block=heading_block,
                    is_block=lambda el: True),
            Handler("class-heading", _class_heading, block=heading_block,
                    is_block=lambda el: True),
            drop_handler("boilerplate", _is_boilerplate, codes.EPUB_BOILERPLATE_DROPPED),
            drop_handler("page-break", _is_page_marker, codes.EPUB_PAGE_MARKERS_DROPPED),
            Handler("figure", lambda el: tag_of(el) == "figure", block=figure,
                    is_block=lambda el: True),
            Handler("image", _is_image, block=image_block, inline=image_inline,
                    is_block=lambda el: not self._is_small(path, el)),
            Handler("link", lambda el: tag_of(el) == "a" and bool(el.get("href")), inline=link),
        ]  # fmt: skip

    # --- Images --------------------------------------------------------------------------

    def _image_path(self, base: str, el: Element) -> str:
        src = el.get("src") or el.get("xlink:href") or el.get("href") or ""
        return resolve(base, src)[0] if src else ""

    def _image(
        self, base: str, el: Element, m: HtmlMapper, caption: tuple[Inline, ...] = ()
    ) -> Image | None:
        path = self._image_path(base, el)
        if self._is_small(base, el):
            self._skip_small(base, m)
            return None
        data = self._member(path, report=False) if path else None
        item = self.package.by_path(path)
        media_type = item.media_type if item else (mimetypes.guess_type(path)[0] or "")
        if data is None or media_type not in _IMAGE_TYPES:
            m.report(codes.IMAGE_DROPPED, f"Image {path or '(no source)'} missing or unsupported.")
            return None
        asset = Asset.from_bytes(data, media_type)
        self.assets.setdefault(asset.sha256, asset)
        alt = " ".join((el.get("alt") or "").split())
        return Image(asset.sha256, alt, caption, m.provenance)

    def _is_small(self, base: str, el: Element) -> bool:
        size = self._size(self._image_path(base, el))
        return size is not None and min(size) < _MIN_IMAGE_SIDE

    def _size(self, path: str) -> tuple[int, int] | None:
        if path not in self._image_sizes:
            size = None
            data = self._member(path, report=False) if path and not path.endswith(".svg") else None
            if data is not None:
                try:
                    with PILImage.open(io.BytesIO(data)) as image:
                        size = image.size
                except (OSError, ValueError, PILImage.DecompressionBombError):
                    size = None
            self._image_sizes[path] = size
        return self._image_sizes[path]

    def _skip_small(self, base: str, m: HtmlMapper) -> list[Inline]:
        m.report_once(codes.IMAGE_SKIPPED_SMALL, "Small decorative images skipped.")
        return []


# --- Element predicates ------------------------------------------------------------------


def _types(el: Element) -> set[str]:
    return set((el.get(_EPUB_TYPE) or "").split()) | set((el.get("role") or "").split())


def _is_external(href: str) -> bool:
    return urllib.parse.urlsplit(href).scheme in ("http", "https", "mailto", "ftp")


def _is_note_element(el: Element) -> bool:
    return bool(
        {"footnote", "endnote", "rearnote", "note", "doc-footnote", "doc-endnote"} & _types(el)
    )


def _is_leading_label(container: Element, link: Element) -> bool:
    """The note's own number ("[1] The note...") opening it: the footnote syntax numbers
    the note, so the label would repeat it, wherever the label links."""
    if not _NOTE_MARK.match(text_content(link)):
        return False
    before = []
    for node in container.iter():
        if node is link:
            break
        before.append(node.text or "")
        if node is not container:
            before.append(node.tail or "")
    return not "".join(before).strip()


def _parent_tag(el: Element) -> str:
    parent = el.getparent()
    return tag_of(parent) if parent is not None else ""


def _block_ancestor(el: Element) -> Element | None:
    for ancestor in el.iterancestors():
        if tag_of(ancestor) in BLOCK_TAGS and tag_of(ancestor) != "body":
            return ancestor
    return None


def _note_container(target: Element) -> Element:
    """The block that holds a note: the target itself when it is a block (EPUB 3
    `<aside>`/`<li>`), else the block around the anchor (EPUB 2 `<p><a id>`)."""
    if tag_of(target) in BLOCK_TAGS:
        return target
    ancestor = _block_ancestor(target)
    return ancestor if ancestor is not None else target


def _all_notes(el: Element, notes: dict[Element, _Note]) -> bool:
    items = [li for li in el if isinstance(li.tag, str)]
    return bool(items) and all(li in notes for li in items)


def _heading_like(el: Element) -> bool:
    if tag_of(el) in _HEADING_TAGS or any(tag_of(d) in _HEADING_TAGS for d in el.iter()):
        return False  # already a heading, or a section holding one
    if any(tag_of(d) in BLOCK_TAGS - {"span"} for d in el.iterdescendants()):
        return False
    text = " ".join(text_content(el).split())
    return 0 < len(text) <= _MAX_HEADING_CHARS


def _class_heading_level(el: Element) -> int | None:
    for name in sorted(classes(el)):
        m = _HEADING_CLASS.match(name)
        if m is None:
            continue
        if m.group("h") or m.group("n"):
            return int(m.group("h") or m.group("n"))
        if m.group("part") or m.group("title"):
            return 1
        if m.group("chapter"):
            return 2
        if m.group("section"):
            return 3
        return 4
    return None


def _class_heading(el: Element) -> bool:
    return tag_of(el) in ("p", "div") and _class_heading_level(el) is not None and _heading_like(el)


def _is_boilerplate(el: Element) -> bool:
    return bool(classes(el) & _BOILERPLATE) or (el.get("id") or "") in _BOILERPLATE_IDS


def _is_page_marker(el: Element) -> bool:
    return bool({"pagebreak", "doc-pagebreak"} & _types(el))


def _is_empty(el: Element) -> bool:
    return (
        isinstance(el.tag, str)
        and tag_of(el) != "hr"
        and not text_content(el).strip()
        and not any(_is_image(d) for d in el.iter())
    )


def _is_image(el: Element) -> bool:
    return tag_of(el) == "img" or (tag_of(el) == "image" and bool(el.get("xlink:href")))


def _remove_keep_tail(el: Element) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail or ""
    previous = el.getprevious()
    if tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
