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


def test_reset_output_forgets_and_optionally_deletes(tmp_path: Path) -> None:
    from tomd.pipeline.batch import MANIFEST_NAME, BatchOptions, run_batch
    from tomd.pipeline.dispatch import reset_output

    source = Path(__file__).resolve().parents[1] / "corpus/pdf/synthetic-page-break/input.pdf"
    out = tmp_path / "out"
    run_batch([source], BatchOptions(out_dir=out, ocr="never"))
    written = sorted(p.name for p in out.glob("*.md") if p.name != "report.md")
    assert reset_output(out, delete_files=False) == 0
    assert not (out / MANIFEST_NAME).exists() and (out / written[0]).is_file()
    run_batch([source], BatchOptions(out_dir=out, ocr="never"))
    assert reset_output(out, delete_files=True) == 0
    assert not list(out.glob("*.md")) and not (out / MANIFEST_NAME).exists()
    assert reset_output(tmp_path / "never-used", delete_files=True) == 0  # nothing to do


def test_reset_deletes_only_files_with_tomds_front_matter(tmp_path: Path) -> None:
    from tomd.pipeline.dispatch import reset_output

    out = tmp_path / "out"
    (out / "book").mkdir(parents=True)
    (out / "notes").mkdir()
    tomd_head = '---\ntitle: "X"\nsource_type: pdf\nsource: "x.pdf"\nsource_sha256: "ab"\n'
    tomd_head += 'converter_version: "0.0.0"\n---\n\n# X\n'
    (out / "old.md").write_text(tomd_head, encoding="utf-8")  # forgotten by an earlier reset
    (out / "old.assets").mkdir()
    (out / "book" / "index.md").write_text(tomd_head, encoding="utf-8")
    (out / "mine.md").write_text("# My notes\n", encoding="utf-8")
    (out / "blog.md").write_text('---\ntitle: "My post"\n---\n', encoding="utf-8")
    (out / "notes" / "index.md").write_text("# Not a book\n", encoding="utf-8")
    reset_output(out, delete_files=True)
    assert not (out / "old.md").exists() and not (out / "old.assets").exists()
    assert not (out / "book").exists()
    assert (out / "mine.md").exists() and (out / "blog.md").exists()
    assert (out / "notes" / "index.md").exists()
