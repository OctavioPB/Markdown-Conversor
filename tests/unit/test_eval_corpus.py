import importlib.util
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_corpus.py"


@pytest.fixture(scope="module")
def eval_corpus() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_corpus", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_corpus"] = module
    spec.loader.exec_module(module)
    return module


def _make_case(root: Path, case_id: str, expected: str | None) -> None:
    case_dir = root / case_id
    case_dir.mkdir(parents=True)
    (case_dir / "case.yaml").write_text("source: test\n", encoding="utf-8")
    if expected is not None:
        (case_dir / "expected.md").write_text(expected, encoding="utf-8")


def test_empty_corpus_emits_empty_report(
    eval_corpus: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert eval_corpus.main(["--corpus", str(tmp_path)]) == 0
    assert "No hay casos en el corpus." in capsys.readouterr().out


def test_cases_are_sorted_and_drafts_flagged(eval_corpus: ModuleType, tmp_path: Path) -> None:
    _make_case(tmp_path, "pdf/b-case", "# Title\n")
    _make_case(tmp_path, "epub/a-case", "# DRAFT — requiere revisión humana\n# Title\n")
    cases = eval_corpus.discover_cases(tmp_path)
    assert [c.case_id for c in cases] == ["epub/a-case", "pdf/b-case"]
    assert [c.is_draft for c in cases] == [True, False]


def test_unknown_case_fails(eval_corpus: ModuleType, tmp_path: Path) -> None:
    assert eval_corpus.main(["--corpus", str(tmp_path), "--case", "pdf/missing"]) == 2


def test_network_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("example.com", 80))
