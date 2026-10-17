"""Wikipedia articles saved as files for the batch (no network: a fake client)."""

from pathlib import Path

import pytest

from tomd.errors import UnsupportedSourceError
from tomd.pipeline.fetch import CACHE_DIR, fetch_article
from tomd.readers.wikipedia.client import ArticleRef


class FakeClient:
    def __init__(self) -> None:
        self.asked: list[tuple[ArticleRef, int]] = []

    def latest_revision(self, ref: ArticleRef) -> int:
        return 42

    def revision_html(self, ref: ArticleRef, revision_id: int) -> bytes:
        self.asked.append((ref, revision_id))
        return b"<html>article</html>"


def test_article_is_saved_under_the_hidden_cache(tmp_path: Path) -> None:
    client = FakeClient()
    path = fetch_article("https://es.wikipedia.org/wiki/Lenguaje_de_marcado", tmp_path,
                         client=client)  # type: ignore[arg-type]  # fmt: skip
    assert path == tmp_path / CACHE_DIR / "es-Lenguaje_de_marcado-42.html"
    assert path.read_bytes() == b"<html>article</html>"
    pinned = fetch_article("en:Markdown", tmp_path, revision=7, client=client)  # type: ignore[arg-type]
    assert pinned.name == "en-Markdown-7.html" and client.asked[-1][1] == 7


def test_not_an_article_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedSourceError):
        fetch_article("https://example.com/page", tmp_path, client=FakeClient())  # type: ignore[arg-type]
