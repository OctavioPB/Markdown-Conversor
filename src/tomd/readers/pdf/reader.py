"""PDF reader: delegates to a `PdfBackend` (ADR-005)."""

from dataclasses import dataclass
from pathlib import Path

from tomd.errors import UnsupportedSourceError
from tomd.ir import Document
from tomd.readers.pdf.backend import BACKENDS, PdfBackend

DEFAULT_BACKEND = "heuristic"


def get_backend(name: str = DEFAULT_BACKEND) -> PdfBackend:
    try:
        return BACKENDS[name]()
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise UnsupportedSourceError(f"Unknown PDF backend {name!r} (available: {known})") from None


@dataclass
class PdfReader:
    backend: str = DEFAULT_BACKEND

    def read(self, source: str) -> Document:
        return get_backend(self.backend).convert(Path(source))
