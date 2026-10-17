from pathlib import Path

import pytest

from tomd.cli import EXIT_UNSUPPORTED, main
from tomd.errors import UnsupportedSourceError
from tomd.pipeline.dispatch import convert, read_source, slugify

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "corpus" / "wikipedia" / "en-maxwell-charge" / "input.html"


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Teorema de Bayes", "teorema-de-bayes"),
        ("Python (programming language)", "python-programming-language"),
        ("Ecuación de segundo grado", "ecuacion-de-segundo-grado"),
        ("¿?", "document"),
    ],
)
def test_slugify(title: str, slug: str) -> None:
    assert slugify(title) == slug


def test_recorded_parsoid_html_is_dispatched_to_wikipedia_reader() -> None:
    doc = read_source(str(FIXTURE))
    assert doc.meta.source_type == "wikipedia"
    assert doc.meta.revision_id is not None


@pytest.mark.parametrize("name", ["notes.txt", "page.html"])
def test_unsupported_files_raise(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"<html><body>x</body></html>")
    with pytest.raises(UnsupportedSourceError):
        read_source(str(path))


def test_convert_writes_deterministic_markdown(tmp_path: Path) -> None:
    first = convert(str(FIXTURE), tmp_path / "a")
    second = convert(str(FIXTURE), tmp_path / "b")
    assert first.markdown_path.name == "maxwell-s-equations.md"
    assert first.markdown_path.read_bytes() == second.markdown_path.read_bytes()
    text = first.markdown_path.read_text(encoding="utf-8")
    assert 'converter_version: "' in text
    assert str(tmp_path) not in text
    assert not list((tmp_path / "a").glob(".*.tmp"))


def test_cli_convert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", str(FIXTURE), "-o", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert out.out.strip().endswith("maxwell-s-equations.md")


def test_cli_reports_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["convert", str(tmp_path / "missing.txt"), "-o", str(tmp_path)]) == EXIT_UNSUPPORTED
    assert "no such file" in capsys.readouterr().err
