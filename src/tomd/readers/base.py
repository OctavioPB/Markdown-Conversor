"""Reader contract: a source goes in, an IR Document comes out. Readers never emit
Markdown."""

from typing import Protocol

from tomd.ir import Document


class Reader(Protocol):
    def read(self, source: str) -> Document: ...
