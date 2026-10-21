"""Deleting what a conversion wrote, and nothing else."""

from pathlib import Path

from tomd.pipeline.dispatch import remove_output
from tomd.pipeline.fetch import CACHE_DIR


def test_markdown_assets_and_downloaded_article_go(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "doc.assets").mkdir(parents=True)
    (out / "doc.md").write_text("# Doc\n", encoding="utf-8")
    (out / "doc.assets" / "a.png").write_bytes(b"png")
    (out / "other.md").write_text("# Other\n", encoding="utf-8")
    (out / CACHE_DIR).mkdir()
    article = out / CACHE_DIR / "es-Doc-1.html"
    article.write_text("<html/>", encoding="utf-8")
    removed = remove_output(out, "doc.md", str(article))
    assert len(removed) == 3
    assert not (out / "doc.md").exists() and not (out / "doc.assets").exists()
    assert not article.exists() and (out / "other.md").exists()


def test_the_original_and_paths_outside_are_never_touched(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    original = tmp_path / "book.pdf"
    original.write_bytes(b"%PDF")
    outside = tmp_path / "escape.md"
    outside.write_text("x", encoding="utf-8")
    assert remove_output(out, "../escape.md", str(original)) == []
    assert original.exists() and outside.exists()
    assert remove_output(out, None, "") == []  # nothing written (a failed conversion)
