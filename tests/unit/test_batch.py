"""Batch conversion (phase 7): manifest, idempotency, resume, isolation, report."""

import shutil
import sqlite3
from pathlib import Path

import pytest

from tomd.cli import EXIT_FAILED, EXIT_OK, main
from tomd.pipeline.batch import MANIFEST_NAME, REPORT_NAME, BatchOptions, discover, run_batch
from tomd.pipeline.manifest import Manifest

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
LISTS = CORPUS / "pdf" / "synthetic-lists" / "input.pdf"
HEADINGS = CORPUS / "pdf" / "synthetic-headings" / "input.pdf"
NOTES = CORPUS / "epub" / "synthetic-epub3-notes" / "input.epub"


def _inputs(tmp_path: Path, files: dict[str, Path | bytes]) -> Path:
    folder = tmp_path / "in"
    folder.mkdir()
    for name, source in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, Path):
            shutil.copyfile(source, target)
        else:
            target.write_bytes(source)
    return folder


def _rows(out: Path) -> dict[str, tuple[str, str | None]]:
    db = sqlite3.connect(out / MANIFEST_NAME)
    try:
        rows = db.execute("SELECT source, status, output FROM documents").fetchall()
    finally:
        db.close()
    return {Path(s).name: (st, o) for s, st, o in rows}


def _options(tmp_path: Path, **overrides: object) -> BatchOptions:
    return BatchOptions(out_dir=tmp_path / "out", ocr="never", **overrides)  # type: ignore[arg-type]


# --- Pure parts ----------------------------------------------------------------------------


def test_discover_folders_files_and_lists(tmp_path: Path) -> None:
    folder = _inputs(
        tmp_path, {"a.pdf": LISTS, "sub/b.epub": NOTES, "c.txt": b"x", ".hidden/d.pdf": LISTS}
    )
    listing = tmp_path / "list.txt"
    listing.write_text("# a list\nin/a.pdf\nin/missing.pdf\n", encoding="utf-8")
    files, missing = discover([folder, listing])
    assert [f.name for f in files] == ["a.pdf", "b.epub"]  # hidden folders and .txt skipped
    assert missing == ["in/missing.pdf"]


def test_manifest_plans_duplicates_once_and_claims_unique_names(tmp_path: Path) -> None:
    manifest = Manifest(tmp_path / "m.sqlite")
    tasks, skipped = manifest.plan(
        [("b.pdf", "s1"), ("a.pdf", "s1"), ("c.pdf", "s2")], "v1", retry_failed=False, force=False
    )
    assert [t.source for t in tasks] == ["a.pdf", "c.pdf"] and skipped["duplicate"] == 1
    assert manifest.claim_slug("title", "s1") == "title"
    assert manifest.claim_slug("title", "s2") == "title-s2"  # a different book, same title
    assert manifest.claim_slug("other", "s1") == "title"  # names never change once given
    manifest.close()


# --- Runs --------------------------------------------------------------------------------


def test_batch_is_idempotent_and_isolates_failures(tmp_path: Path) -> None:
    folder = _inputs(
        tmp_path,
        {"lists.pdf": LISTS, "copy-of-lists.pdf": LISTS, "notes.epub": NOTES,
         "broken.pdf": b"%PDF-1.4 truncated"},
    )  # fmt: skip
    summary = run_batch([folder], _options(tmp_path), progress=lambda line: None)
    assert summary.statuses == {"done": 2, "failed": 1}
    assert summary.skipped["duplicate"] == 1
    rows = _rows(tmp_path / "out")
    assert rows["broken.pdf"][0] == "failed"
    assert (tmp_path / "out" / "list-handling.md").is_file()
    assert (tmp_path / "out" / "the-synthetic-notes-book.md").is_file()
    report = (tmp_path / "out" / REPORT_NAME).read_text(encoding="utf-8")
    assert "## Fallos" in report and "broken.pdf" in report and "--retry-failed" in report

    again = run_batch([folder], _options(tmp_path), progress=lambda line: None)
    assert again.planned == 0 and again.skipped == {"done": 2, "failed": 1, "duplicate": 1}

    retry = run_batch([folder], _options(tmp_path, retry_failed=True), progress=lambda line: None)
    assert retry.planned == 1 and retry.statuses == {"failed": 1}


def test_same_title_different_books_get_distinct_files(tmp_path: Path) -> None:
    # Two different files with the same title: one name each, images linked correctly.
    other = LISTS.read_bytes() + b"\n% a different file with the same title\n"
    folder = _inputs(tmp_path, {"a.pdf": LISTS, "b.pdf": other})
    run_batch([folder], _options(tmp_path), progress=lambda line: None)
    outputs = {o or "" for _, o in _rows(tmp_path / "out").values()}
    assert "list-handling.md" in outputs  # the first one keeps the plain name
    assert any(o.startswith("list-handling-") and o.endswith(".md") for o in outputs)
    assert len(list((tmp_path / "out").glob("list-handling*.md"))) == 2


def test_cli_batch_exit_codes(tmp_path: Path) -> None:
    good = _inputs(tmp_path, {"lists.pdf": LISTS})
    assert main(["batch", str(good), "-o", str(tmp_path / "o1"), "--ocr", "never"]) == EXIT_OK
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "x.pdf").write_bytes(b"not a pdf")
    assert main(["batch", str(bad), "-o", str(tmp_path / "o2"), "--ocr", "never"]) == EXIT_FAILED


@pytest.mark.slow
def test_timeouts_are_recorded_and_the_batch_goes_on(tmp_path: Path) -> None:
    folder = _inputs(tmp_path, {"a.pdf": LISTS, "b.pdf": HEADINGS})
    summary = run_batch([folder], _options(tmp_path, timeout_s=0.001), progress=lambda line: None)
    assert summary.statuses == {"timeout": 2}
    assert {status for status, _ in _rows(tmp_path / "out").values()} == {"timeout"}


@pytest.mark.slow
def test_memory_limit_is_enforced_per_worker(tmp_path: Path) -> None:
    big = CORPUS / "pdf" / "arxiv-mistral-7b" / "input.pdf"
    folder = _inputs(tmp_path, {"big.pdf": big, "small.pdf": LISTS})
    summary = run_batch([folder], _options(tmp_path, memory_mb=40), progress=lambda line: None)
    # The big paper needs far more than 40 MB: it fails on memory (Python's MemoryError,
    # or native code aborting), and a fresh worker takes the next document.
    rows = _rows(tmp_path / "out")
    assert rows["big.pdf"][0] in ("memory", "crashed")
    assert rows["small.pdf"][0] in ("done", "memory", "crashed")
    assert sum(summary.statuses.values()) == 2


@pytest.mark.slow
def test_interrupted_batch_resumes_without_redoing_work(tmp_path: Path) -> None:
    folder = _inputs(
        tmp_path, {f"doc{i}.pdf": LISTS.read_bytes() + f"\n% copy {i}\n".encode() for i in range(6)}
    )
    finished: list[str] = []

    def interrupt_after_two(line: str) -> None:
        if line.startswith("["):
            finished.append(line)
            if len(finished) == 2:
                raise KeyboardInterrupt  # as Ctrl+C would, once

    first = run_batch([folder], _options(tmp_path), progress=interrupt_after_two)
    assert first.interrupted and first.statuses == {"done": 2}
    assert {s for s, _ in _rows(tmp_path / "out").values()} == {"done", "pending"}
    second = run_batch([folder], _options(tmp_path), progress=lambda line: None)
    assert second.skipped["done"] == 2 and second.statuses == {"done": 4}
    documents = [p for p in (tmp_path / "out").glob("*.md") if p.name != REPORT_NAME]
    assert len(documents) == 6  # each converted exactly once, none twice
