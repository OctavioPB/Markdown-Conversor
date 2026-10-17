"""The EPUB container: ZIP -> META-INF/container.xml -> OPF (metadata, manifest, spine)
-> table of contents (EPUB 3 nav or EPUB 2 NCX). No XHTML content is mapped here.

The ZIP is never extracted to disk; members are read by name with a size budget, so a
zip bomb or a path such as `../../x` cannot do harm. DRM is detected here (ADR-009).
"""

import posixpath
import re
import urllib.parse
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from tomd.errors import DrmProtectedError, UnreadableInputError

# Members larger than this, or a ZIP declaring more than `_MAX_TOTAL` uncompressed, are
# refused: no real book comes close (the largest Standard Ebooks are under 50 MB), and the
# conversion must stay inside the worker's ~4 GB memory budget.
_MAX_MEMBER = 256 * 2**20
_MAX_TOTAL = 1024 * 2**20
_MAX_MEMBERS = 20_000
# What a damaged ZIP raises: bad headers, corrupt deflate streams, unknown versions or
# compression methods, sizes that lie.
_ZIP_ERRORS = (
    zipfile.BadZipFile, zlib.error, OSError, EOFError, RuntimeError, NotImplementedError,
    ValueError,
)  # fmt: skip

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "enc": "http://www.w3.org/2001/04/xmlenc#",
}
# Font obfuscation (IDPF and Adobe) scrambles embedded fonts only; it is not DRM.
_FONT_OBFUSCATION = frozenset(
    {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
)
# Files that only DRM schemes put in META-INF: Adobe ADEPT, Apple FairPlay, Readium LCP.
_DRM_FILES = {
    "META-INF/rights.xml": "Adobe ADEPT",
    "META-INF/sinf.xml": "Apple FairPlay",
    "META-INF/license.lcpl": "Readium LCP",
}
_XML_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, load_dtd=False, recover=True, huge_tree=False
)
XHTML_TYPES = frozenset({"application/xhtml+xml", "text/html"})


@dataclass(frozen=True, slots=True)
class ManifestItem:
    id: str
    path: str  # full path inside the ZIP
    media_type: str
    properties: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SpineItem:
    item: ManifestItem
    linear: bool


@dataclass(frozen=True, slots=True)
class TocEntry:
    path: str  # file inside the ZIP
    fragment: str | None
    depth: int  # 1 = top level
    label: str


@dataclass
class Metadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    language: str | None = None
    rights: str | None = None
    cover_id: str | None = None
    fixed_layout: bool = False
    version: str = "3.0"


@dataclass
class EpubPackage:
    zip: zipfile.ZipFile
    opf_path: str
    metadata: Metadata
    manifest: dict[str, ManifestItem]  # by id
    spine: list[SpineItem]
    toc: list[TocEntry] = field(default_factory=list)
    nav_path: str | None = None
    guide: dict[str, str] = field(default_factory=dict)  # EPUB 2 guide: type -> zip path
    _budget: int = _MAX_TOTAL

    def by_path(self, path: str) -> ManifestItem | None:
        return next((i for i in self.manifest.values() if i.path == path), None)

    def read(self, path: str) -> bytes | None:
        """A member's bytes, or None when it is missing from the ZIP."""
        try:
            info = self.zip.getinfo(path)
        except KeyError:
            return None
        if info.file_size > min(_MAX_MEMBER, self._budget):
            raise UnreadableInputError(
                f"{path} inside the EPUB is too large ({info.file_size} bytes) to read safely."
            )
        self._budget -= info.file_size
        try:
            return self.zip.read(info)
        except _ZIP_ERRORS as exc:
            raise UnreadableInputError(f"{path} inside the EPUB is damaged: {exc}") from exc

    def close(self) -> None:
        self.zip.close()


def resolve(base: str, href: str) -> tuple[str, str | None]:
    """A relative href (as in the OPF or an XHTML file at `base`) to (zip path, fragment)."""
    parts = urllib.parse.urlsplit(href)
    path = urllib.parse.unquote(parts.path)
    fragment = urllib.parse.unquote(parts.fragment) or None
    if not path:
        return base, fragment
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
    return joined.lstrip("/"), fragment


def open_epub(path: Path) -> EpubPackage:
    try:
        archive = zipfile.ZipFile(path)
    except _ZIP_ERRORS as exc:
        raise UnreadableInputError(
            f"{path.name} is not a valid EPUB (ZIP archive): {exc}. Obtain a fresh copy."
        ) from exc
    try:
        return _load(archive, path.name)
    except BaseException:
        archive.close()
        raise


def _load(archive: zipfile.ZipFile, name: str) -> EpubPackage:
    infos = archive.infolist()
    if len(infos) > _MAX_MEMBERS or sum(i.file_size for i in infos) > _MAX_TOTAL:
        raise UnreadableInputError(
            f"{name} declares {len(infos)} files and "
            f"{sum(i.file_size for i in infos)} uncompressed bytes: too large to read safely."
        )
    names = {i.filename for i in infos}
    _check_drm(archive, names, name)
    package = EpubPackage(archive, "", Metadata(), {}, [])
    container = package.read("META-INF/container.xml")
    if container is None:
        raise UnreadableInputError(f"{name} has no META-INF/container.xml: not an EPUB.")
    root = _xml(container, name)
    rootfile = root.find(".//c:rootfile", _NS)
    opf_path = (rootfile.get("full-path") or "") if rootfile is not None else ""
    opf_data = package.read(opf_path) if opf_path else None
    if opf_data is None:
        raise UnreadableInputError(f"{name}: the package document (OPF) is missing.")
    package.opf_path = opf_path
    opf = _xml(opf_data, name)
    package.metadata = _metadata(opf)
    package.manifest = _manifest(opf, opf_path)
    package.spine = _spine(opf, package.manifest)
    if not package.spine:
        raise UnreadableInputError(f"{name}: the spine lists no readable content documents.")
    package.nav_path, package.toc = _toc(package, opf)
    package.guide = _guide(opf, opf_path)
    if package.metadata.cover_id is None:  # EPUB 3 marks the cover in the manifest
        cover = next((i for i in package.manifest.values() if "cover-image" in i.properties), None)
        package.metadata.cover_id = cover.id if cover else None
    return package


def _guide(opf: etree._Element, opf_path: str) -> dict[str, str]:
    guide: dict[str, str] = {}
    for ref in opf.iterfind("opf:guide/opf:reference", _NS):
        kind, href = (ref.get("type") or "").lower(), ref.get("href")
        if kind and href:
            guide.setdefault(kind, resolve(opf_path, href)[0])
    return guide


def _xml(data: bytes, name: str) -> etree._Element:
    try:
        root = etree.fromstring(data, _XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise UnreadableInputError(f"{name}: damaged package XML: {exc}") from exc
    if root is None:
        raise UnreadableInputError(f"{name}: empty package XML.")
    return root


def _check_drm(archive: zipfile.ZipFile, names: set[str], name: str) -> None:
    for drm_file, scheme in _DRM_FILES.items():
        if drm_file in names:
            raise DrmProtectedError(
                f"{name} is protected with {scheme} DRM; tomd does not remove DRM (ADR-009). "
                "Convert a DRM-free copy."
            )
    if "META-INF/encryption.xml" not in names:
        return
    info = archive.getinfo("META-INF/encryption.xml")
    if info.file_size > _MAX_MEMBER:
        raise UnreadableInputError(f"{name}: META-INF/encryption.xml is too large.")
    try:
        data = archive.read(info)
    except _ZIP_ERRORS as exc:
        raise UnreadableInputError(f"{name}: META-INF/encryption.xml is damaged: {exc}") from exc
    root = _xml(data, name)
    for method in root.iterfind(".//enc:EncryptionMethod", _NS):
        if (method.get("Algorithm") or "") not in _FONT_OBFUSCATION:
            raise DrmProtectedError(
                f"{name} has encrypted content (DRM); tomd does not remove DRM (ADR-009). "
                "Convert a DRM-free copy."
            )


def _text(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    value = " ".join("".join(str(t) for t in el.itertext()).split())
    return value or None


def _metadata(opf: etree._Element) -> Metadata:
    meta = Metadata(version=opf.get("version") or "3.0")
    block = opf.find("opf:metadata", _NS)
    if block is None:
        return meta
    refines = _refines(block)
    titles = block.findall("dc:title", _NS)
    main = next(
        (t for t in titles if refines.get((t.get("id") or "", "title-type")) == "main"), None
    )
    meta.title = _text(main if main is not None else (titles[0] if titles else None))
    authors: list[str] = []
    for creator in block.findall("dc:creator", _NS):
        role = creator.get("{http://www.idpf.org/2007/opf}role") or refines.get(
            (creator.get("id") or "", "role")
        )
        if role in (None, "aut"):
            name = _text(creator)
            if name and name not in authors:
                authors.append(name)
    meta.authors = tuple(authors)
    meta.language = _text(block.find("dc:language", _NS))
    meta.rights = _text(block.find("dc:rights", _NS))
    for m in block.findall("opf:meta", _NS):
        if m.get("name") == "cover":
            meta.cover_id = m.get("content")
        if m.get("property") == "rendition:layout" and _text(m) == "pre-paginated":
            meta.fixed_layout = True
    return meta


def _refines(block: etree._Element) -> dict[tuple[str, str], str]:
    """EPUB 3 <meta refines="#id" property="..."> values, keyed by (id, property)."""
    out: dict[tuple[str, str], str] = {}
    for m in block.findall("opf:meta", _NS):
        target, prop = (m.get("refines") or "").lstrip("#"), m.get("property") or ""
        value = _text(m)
        if target and prop and value:
            out[(target, prop)] = value
    return out


def _manifest(opf: etree._Element, opf_path: str) -> dict[str, ManifestItem]:
    items: dict[str, ManifestItem] = {}
    for el in opf.iterfind("opf:manifest/opf:item", _NS):
        item_id, href = el.get("id"), el.get("href")
        if not item_id or not href:
            continue
        path, _ = resolve(opf_path, href)
        items[item_id] = ManifestItem(
            item_id,
            path,
            (el.get("media-type") or "").lower(),
            frozenset((el.get("properties") or "").split()),
        )
    return items


def _spine(opf: etree._Element, manifest: dict[str, ManifestItem]) -> list[SpineItem]:
    spine: list[SpineItem] = []
    for ref in opf.iterfind("opf:spine/opf:itemref", _NS):
        item = manifest.get(ref.get("idref") or "")
        if item is not None and item.media_type in XHTML_TYPES:
            spine.append(SpineItem(item, (ref.get("linear") or "yes") != "no"))
    return spine


def _toc(package: EpubPackage, opf: etree._Element) -> tuple[str | None, list[TocEntry]]:
    nav = next((i for i in package.manifest.values() if "nav" in i.properties), None)
    if nav is not None:
        data = package.read(nav.path)
        if data is not None:
            entries = _nav_entries(data, nav.path)
            if entries:
                return nav.path, entries
    spine = opf.find("opf:spine", _NS)
    ncx_id = spine.get("toc") if spine is not None else None
    ncx = package.manifest.get(ncx_id or "") or next(
        (i for i in package.manifest.values() if i.media_type == "application/x-dtbncx+xml"), None
    )
    if ncx is not None:
        data = package.read(ncx.path)
        if data is not None:
            return (nav.path if nav else None), _ncx_entries(data, ncx.path)
    return (nav.path if nav else None), []


_EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"


def _nav_entries(data: bytes, base: str) -> list[TocEntry]:
    try:
        root = etree.fromstring(data, _XML_PARSER)
    except etree.XMLSyntaxError:
        return []
    if root is None:
        return []
    toc_nav = next(
        (n for n in root.iter("{*}nav") if "toc" in (n.get(_EPUB_TYPE) or "").split()), None
    )
    if toc_nav is None:
        return []
    entries: list[TocEntry] = []

    def walk(ol: etree._Element, depth: int) -> None:
        for li in ol.iterfind("{*}li"):
            link = li.find("{*}a")
            if link is not None and link.get("href"):
                path, fragment = resolve(base, link.get("href") or "")
                entries.append(TocEntry(path, fragment, depth, _text(link) or ""))
            for sub in li.iterfind("{*}ol"):
                walk(sub, depth + 1)

    for ol in toc_nav.iterfind("{*}ol"):
        walk(ol, 1)
    return entries


def _ncx_entries(data: bytes, base: str) -> list[TocEntry]:
    try:
        root = etree.fromstring(data, _XML_PARSER)
    except etree.XMLSyntaxError:
        return []
    if root is None:
        return []
    entries: list[TocEntry] = []

    def walk(parent: etree._Element, depth: int) -> None:
        for point in parent.iterfind("ncx:navPoint", _NS):
            content = point.find("ncx:content", _NS)
            label = _text(point.find("ncx:navLabel", _NS)) or ""
            if content is not None and content.get("src"):
                path, fragment = resolve(base, content.get("src") or "")
                entries.append(TocEntry(path, fragment, depth, label))
            walk(point, depth + 1)

    nav_map = root.find("ncx:navMap", _NS)
    if nav_map is not None:
        walk(nav_map, 1)
    return entries


_XML_DECL = re.compile(rb"^\s*<\?xml[^>]*?encoding=[\"']([\w.-]+)[\"'][^>]*\?>", re.IGNORECASE)


def decode_xhtml(data: bytes) -> tuple[str, int]:
    """XHTML bytes to text using the declared encoding (UTF-8 by default), and the number
    of undecodable bytes that were replaced by U+FFFD."""
    declared = _XML_DECL.match(data)
    encoding = declared.group(1).decode("ascii") if declared else "utf-8"
    try:
        text = data.decode(encoding)
        invalid = 0
    except (UnicodeDecodeError, LookupError):
        text = data.decode("utf-8", errors="replace")
        invalid = text.count("�") - data.count("�".encode())
    if text.startswith("﻿"):
        text = text[1:]
    # lxml refuses an encoding declaration in an already decoded string.
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    return text.replace("\x00", "�"), max(invalid, 0)
