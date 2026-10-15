"""Evaluate the golden corpus and print a per-case report.

Phase 0 skeleton: discovers cases under tests/corpus/<source>/<case>/ and emits a report
without metrics. Metrics (text similarity, heading F1, reading order, table cells) and the
baseline comparison are added once readers exist (see docs/TESTING.md).
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus"
DRAFT_MARKER = "# DRAFT"


@dataclass(frozen=True)
class Case:
    source: str
    name: str
    path: Path

    @property
    def case_id(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def is_draft(self) -> bool:
        expected = self.path / "expected.md"
        if not expected.is_file():
            return True
        with expected.open(encoding="utf-8") as fh:
            return fh.readline().startswith(DRAFT_MARKER)


def discover_cases(root: Path) -> list[Case]:
    """Return every directory with a case.yaml, sorted for deterministic output."""
    if not root.is_dir():
        return []
    cases = [
        Case(source=case_yaml.parent.parent.name, name=case_yaml.parent.name, path=case_yaml.parent)
        for case_yaml in root.glob("*/*/case.yaml")
    ]
    return sorted(cases, key=lambda c: c.case_id)


def render_report(cases: Sequence[Case]) -> str:
    lines = ["# Informe del corpus dorado", ""]
    if not cases:
        lines.append("No hay casos en el corpus.")
        return "\n".join(lines) + "\n"
    lines += ["| Caso | Estado |", "|---|---|"]
    for case in cases:
        status = "DRAFT (excluido)" if case.is_draft else "pendiente de métricas"
        lines.append(f"| {case.case_id} | {status} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--case", help="evaluate a single case, e.g. pdf/two-column-arxiv-01")
    args = parser.parse_args(argv)

    cases = discover_cases(args.corpus)
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]
        if not cases:
            print(f"Caso no encontrado: {args.case}", file=sys.stderr)
            return 2
    sys.stdout.write(render_report(cases))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
