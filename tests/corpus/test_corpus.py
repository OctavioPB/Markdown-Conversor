"""Golden corpus: every reviewed case must meet its thresholds (docs/TESTING.md).

Cases whose expected.md is still a DRAFT are skipped, never compared.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tomd.readers.pdf.ocr import OcrEngine

ROOT = Path(__file__).resolve().parents[2]


def _eval_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "eval_corpus", ROOT / "scripts" / "eval_corpus.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_corpus"] = module
    spec.loader.exec_module(module)
    return module


EVAL = _eval_module()
CASES = EVAL.discover_cases(ROOT / "tests" / "corpus")


def test_every_case_has_an_input_and_metadata() -> None:
    for case in CASES:
        assert case.input_path.is_file(), case.case_id
        config = case.config
        assert config.get("source"), f"{case.case_id}: case.yaml needs a source with license"
        assert config.get("phenomena"), f"{case.case_id}: case.yaml needs phenomena"


# Scanned cases run Tesseract: slow, and only where it is installed (ADR-017).
_OCR_SOURCE = "pdf-ocr"


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.slow) if c.source == _OCR_SOURCE else c for c in CASES],
    ids=[c.case_id for c in CASES],
)
def test_case_meets_thresholds(case: object) -> None:
    if case.is_draft:  # type: ignore[attr-defined]
        pytest.skip("expected.md is a DRAFT pending human review")
    if case.source == _OCR_SOURCE and OcrEngine.find("eng") is None:  # type: ignore[attr-defined]
        pytest.skip("Tesseract is not installed")
    result = EVAL.evaluate(case)
    assert not result.failures, "; ".join(result.failures)
