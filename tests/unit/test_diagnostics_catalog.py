"""The diagnostic catalog is complete, used, and the only source of severities."""

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

from tomd.cli import EXIT_DRM, EXIT_NOT_FOUND, EXIT_UNREADABLE, EXIT_UNSUPPORTED, exit_code, main
from tomd.errors import (
    DrmProtectedError,
    SourceNotFoundError,
    UnreadableInputError,
    UnsupportedSourceError,
)
from tomd.ir import diagnostics

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "tomd"
CODE = re.compile(r"^[a-z]+\.[a-z_]+$")


def _sources() -> list[Path]:
    return [p for p in sorted(SRC.rglob("*.py")) if p != SRC / "ir" / "diagnostics.py"]


def test_every_code_constant_is_in_the_catalog() -> None:
    constants = {
        value
        for name, value in vars(diagnostics).items()
        if name.isupper() and isinstance(value, str) and CODE.match(value)
    }
    assert constants == set(diagnostics.CATALOG)


def test_no_code_is_defined_outside_the_catalog() -> None:
    # Only the catalog's namespaces: "manifest.sqlite" is a file name, not a code.
    namespaces = "|".join(sorted({code.split(".")[0] for code in diagnostics.CATALOG}))
    literal = re.compile(rf'^[A-Z_]+ = "(?:{namespaces})\.[a-z_]+"', re.MULTILINE)
    offenders = [
        str(p.relative_to(ROOT)) for p in _sources() if literal.search(p.read_text("utf-8"))
    ]
    assert offenders == []


def test_every_catalog_code_is_emitted_somewhere() -> None:
    names = {
        v: k
        for k, v in vars(diagnostics).items()
        if isinstance(v, str) and v in diagnostics.CATALOG
    }
    text = "\n".join(p.read_text(encoding="utf-8") for p in _sources())
    unused = sorted(code for code, name in names.items() if not re.search(rf"\b{name}\b", text))
    assert unused == []


def test_severities_come_only_from_the_catalog() -> None:
    """Emitters call diagnose(); building a Diagnostic by hand could give one code two
    severities."""
    offenders = []
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Diagnostic":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_actions_are_given_for_warnings_and_errors_that_users_can_fix() -> None:
    for code, info in diagnostics.CATALOG.items():
        assert info.meaning.endswith("."), code
        assert not info.action or info.action.endswith("."), code


def test_diagnostics_doc_is_up_to_date() -> None:
    spec = importlib.util.spec_from_file_location(
        "gen_doc", ROOT / "scripts" / "gen_diagnostics_doc.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_doc"] = module
    spec.loader.exec_module(module)
    assert module.main(["--check"]) == 0, "run scripts/gen_diagnostics_doc.py"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (UnsupportedSourceError("x"), EXIT_UNSUPPORTED),
        (UnreadableInputError("x"), EXIT_UNREADABLE),
        (DrmProtectedError("x"), EXIT_DRM),
        (SourceNotFoundError("x"), EXIT_NOT_FOUND),
    ],
)
def test_each_error_kind_has_its_exit_code(error: Exception, code: int) -> None:
    assert exit_code(error) == code  # type: ignore[arg-type]


def test_cli_prints_actions_for_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "damaged.html"
    fixture = next((ROOT / "tests" / "corpus" / "wikipedia").glob("*/input.html"))
    path.write_bytes(fixture.read_bytes().replace(b"<p>", b"<p>\xff", 1))
    assert main(["convert", str(path), "-o", str(tmp_path / "out")]) == 0
    err = capsys.readouterr().err
    assert "warning text.invalid_unicode: 1" in err
    assert "-> The source file is damaged" in err
