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


def test_text_similarity_ignores_front_matter_and_whitespace(eval_corpus: ModuleType) -> None:
    a = "---\ntitle: A\n---\n\n# T\n\nHola   mundo\n"
    b = "---\ntitle: B\nx: 1\n---\n\n# T\n\nHola mundo\n"
    assert eval_corpus.text_similarity(a, b) == 1.0
    assert eval_corpus.text_similarity("abcd", "abxd") == 0.75


def test_heading_f1_matches_level_and_text(eval_corpus: ModuleType) -> None:
    out = "# T\n\n## Uno\n\n```\n# not a heading\n```\n\n### Dos\n"
    exp = "# T\n\n## Uno\n\n## Dos\n"
    assert eval_corpus.headings(out) == [(1, "t"), (2, "uno"), (3, "dos")]
    assert eval_corpus.multiset_f1(eval_corpus.headings(out), eval_corpus.headings(exp)) == (2 / 3)


def test_math_recall_counts_formulas(eval_corpus: ModuleType) -> None:
    exp = "Sea $x^2$ y\n\n$$\na + b\n$$\n"
    assert eval_corpus.math_recall("Sea $x^2$ y `a + b`", exp) == 0.5
    assert eval_corpus.math_recall("nada", "sin fórmulas") is None


def test_reading_order_is_kendall_tau_over_matched_paragraphs(eval_corpus: ModuleType) -> None:
    paras = [f"Paragraph number {i} with enough text to be matched." for i in range(4)]
    expected = "\n\n".join(paras)
    assert eval_corpus.reading_order(expected, expected) == 1.0
    assert eval_corpus.reading_order("\n\n".join(reversed(paras)), expected) == -1.0
    swapped = "\n\n".join([paras[0], paras[2], paras[1], paras[3]])
    assert eval_corpus.reading_order(swapped, expected) == (5 - 1) / 6
    assert eval_corpus.reading_order("short", "short") is None


def test_reading_order_pairs_near_duplicates_by_position(eval_corpus: ModuleType) -> None:
    """An OCR slip ("lt" for "It") makes paragraph 2 as similar to expected 3 as to its own
    expected 2; content cannot tell them apart, so it must not count as a swap."""
    body = "carries enough words to wrap over several lines of a narrow column."
    paras = [f"Paragraph {i}. It {body}" for i in range(1, 5)]
    output = "\n\n".join([paras[0], f"Paragraph 2. lt {body}", paras[2], paras[3]])
    assert eval_corpus.reading_order(output, "\n\n".join(paras)) == 1.0
