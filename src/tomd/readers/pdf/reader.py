"""PDF reader: delegates to a `PdfBackend` (ADR-005)."""

from dataclasses import dataclass, replace
from pathlib import Path

from tomd.errors import UnsupportedSourceError
from tomd.ir import Document
from tomd.readers.pdf.backend import BACKENDS, PdfBackend
from tomd.readers.pdf.extract import open_pdf, read_outline
from tomd.readers.pdf.ocr import OcrMode

DEFAULT_BACKEND = "heuristic"


def get_backend(name: str = DEFAULT_BACKEND, ocr: OcrMode = OcrMode.AUTO) -> PdfBackend:
    try:
        return BACKENDS[name](ocr)
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise UnsupportedSourceError(f"Unknown PDF backend {name!r} (available: {known})") from None


@dataclass
class PdfReader:
    backend: str = DEFAULT_BACKEND
    ocr: OcrMode = OcrMode.AUTO

    def read(self, source: str) -> Document:
        path = Path(source)
        document = get_backend(self.backend, self.ocr).convert(path)
        # The outline is the publisher's, whatever backend read the pages (ADR-018).
        with open_pdf(path) as pdf:
            outline = read_outline(pdf)
        return replace(document, outline=outline)
