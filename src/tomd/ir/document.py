"""Root of the IR tree."""

from dataclasses import dataclass

from tomd.ir.diagnostics import Diagnostic
from tomd.ir.nodes import Asset, Block, DocumentMeta


@dataclass(frozen=True, slots=True)
class Document:
    meta: DocumentMeta
    blocks: tuple[Block, ...]
    assets: tuple[Asset, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
