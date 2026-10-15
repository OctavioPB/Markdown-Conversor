"""Pure IR -> IR passes, independent of the source format."""

from collections.abc import Callable

from tomd.ir import Document
from tomd.transforms.drop_empty import drop_empty
from tomd.transforms.footnotes import renumber_footnotes
from tomd.transforms.headings import normalize_headings
from tomd.transforms.merge_text import merge_adjacent_text

DEFAULT_TRANSFORMS: tuple[Callable[[Document], Document], ...] = (
    drop_empty,
    merge_adjacent_text,
    normalize_headings,
    renumber_footnotes,
)


def apply_default_transforms(doc: Document) -> Document:
    for transform in DEFAULT_TRANSFORMS:
        doc = transform(doc)
    return doc


__all__ = [
    "DEFAULT_TRANSFORMS",
    "apply_default_transforms",
    "drop_empty",
    "merge_adjacent_text",
    "normalize_headings",
    "renumber_footnotes",
]
