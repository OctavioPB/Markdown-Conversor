"""Evaluate the golden corpus and print a per-case report (docs/TESTING.md, ADR-007).

    uv run python scripts/eval_corpus.py                       # all cases vs. baseline
    uv run python scripts/eval_corpus.py --phenomenon infobox
    uv run python scripts/eval_corpus.py --case wikipedia/x --diff
    uv run python scripts/eval_corpus.py --update-baseline     # only with user approval

Cases whose expected.md starts with "# DRAFT" are listed but excluded from metrics.
Exit status is 1 when a non-draft case falls below its thresholds.
"""

import argparse
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from tomd.pipeline.dispatch import read_source, render_document

CORPUS_ROOT = Path(__file__).resolve().parent.parent / "tests" / "corpus"
BASELINE_NAME = ".baseline.json"
DRAFT_MARKER = "# DRAFT"
# A metric that drops by more than this against the baseline is a regression (skill
# golden-corpus).
REGRESSION_TOLERANCE = 0.01

# Phase thresholds per source (PLAN.md); case.yaml `thresholds` overrides them.
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "epub": {"text_similarity": 0.98, "heading_f1": 0.95},
    "wikipedia": {"text_similarity": 0.97, "math_recall": 1.0},
    "pdf": {"text_similarity": 0.95, "heading_f1": 0.85},
    # PLAN phase 6: similarity >= 0.90 on clean scans.
    "pdf-ocr": {"text_similarity": 0.90, "heading_f1": 0.85},
}

_ATX = re.compile(r"^(#{1,6}) +(.*?)(?: +#+)? *$")
_FENCE = re.compile(r"^ *(`{3,}|~{3,})")


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

    @property
    def config(self) -> dict[str, Any]:
        data = yaml.safe_load((self.path / "case.yaml").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    @property
    def phenomena(self) -> list[str]:
        return [str(p) for p in self.config.get("phenomena") or []]

    @property
    def input_path(self) -> Path:
        inputs = sorted(self.path.glob("input.*"))
        if not inputs:
            raise FileNotFoundError(f"{self.case_id}: no input.* file")
        return inputs[0]

    def thresholds(self) -> dict[str, float]:
        merged = dict(DEFAULT_THRESHOLDS.get(self.source, {}))
        merged.update({k: float(v) for k, v in (self.config.get("thresholds") or {}).items()})
        return merged


@dataclass
class CaseResult:
    case: Case
    metrics: dict[str, float] = field(default_factory=dict)
    diagnostics: Counter[str] = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)
    output: str = ""
    expected: str = ""


def discover_cases(root: Path) -> list[Case]:
    """Every directory with a case.yaml, sorted for deterministic output."""
    if not root.is_dir():
        return []
    cases = [
        Case(source=case_yaml.parent.parent.name, name=case_yaml.parent.name, path=case_yaml.parent)
        for case_yaml in root.glob("*/*/case.yaml")
    ]
    return sorted(cases, key=lambda c: c.case_id)


# --- Normalization and metrics --------------------------------------------------------


def strip_front_matter(markdown: str) -> str:
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end != -1:
            return markdown[end + 5 :]
    return markdown


def normalize_text(markdown: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", strip_front_matter(markdown)).split())


def text_similarity(output: str, expected: str) -> float:
    return float(
        Levenshtein.normalized_similarity(normalize_text(output), normalize_text(expected))
    )


def headings(markdown: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for line in strip_front_matter(markdown).splitlines():
        m = _FENCE.match(line)
        if m:
            marker = m.group(1)[0] * 3
            fence = None if fence == marker else (fence or marker)
            continue
        if fence:
            continue
        h = _ATX.match(line)
        if h:
            text = " ".join(unicodedata.normalize("NFKC", h.group(2)).split()).casefold()
            out.append((len(h.group(1)), text))
    return out


def multiset_f1(found: Sequence[object], expected: Sequence[object]) -> float:
    if not found and not expected:
        return 1.0
    common = sum((Counter(found) & Counter(expected)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(found), common / len(expected)
    return 2 * precision * recall / (precision + recall)


def _math_parser() -> MarkdownIt:
    return (
        MarkdownIt("commonmark")
        .enable("table")
        .use(front_matter_plugin)
        .use(dollarmath_plugin, allow_space=False)
    )


def formulas(markdown: str) -> list[str]:
    found: list[str] = []
    for token in _math_parser().parse(markdown):
        if token.type == "math_block":
            # mdit-py-plugins' dollarmath keeps the "> " prefixes of math blocks inside
            # block quotes in the token content; they are not part of the TeX.
            content = re.sub(r"(?m)^\s*>[ ]?", "", token.content)
            found.append(" ".join(content.split()))
        for child in token.children or []:
            if child.type == "math_inline":
                found.append(" ".join(child.content.split()))
    return found


# Paragraphs shorter than this are too ambiguous to anchor an order comparison.
MIN_ORDER_PARAGRAPH = 20
# Paragraphs match when their similarity (rapidfuzz ratio, 0-100) reaches this
# (docs/TESTING.md: "emparejados por similitud >= 0.9").
ORDER_MATCH_RATIO = 90.0
# Candidates this close to the best score are indistinguishable by content (near-duplicate
# paragraphs, "Paragraph 3.2" vs "3.3" with an OCR slip): the one nearest by relative
# position is taken, since their text carries no information about order.
ORDER_TIE = 1.0


def paragraphs(markdown: str) -> list[str]:
    blocks = (" ".join(b.split()) for b in strip_front_matter(markdown).split("\n\n"))
    return [b for b in blocks if len(b) >= MIN_ORDER_PARAGRAPH]


def reading_order(output: str, expected: str) -> float | None:
    """Kendall's tau between the order of matched paragraphs in output and expected."""
    out_paras = paragraphs(output)
    exp_paras = paragraphs(expected)
    used: set[int] = set()
    positions: list[int] = []
    for i, para in enumerate(exp_paras):
        scores = {
            j: fuzz.ratio(para, candidate) for j, candidate in enumerate(out_paras) if j not in used
        }
        top = max(scores.values(), default=0.0)
        if top < ORDER_MATCH_RATIO:
            continue
        where = i / max(len(exp_paras) - 1, 1)
        tied = [j for j, score in scores.items() if score >= top - ORDER_TIE]
        # Nearest relative position, then document order (repeated boilerplate).
        best = min(tied, key=lambda j: (abs(j / max(len(out_paras) - 1, 1) - where), j))
        used.add(best)
        positions.append(best)
    n = len(positions)
    if n < 2:
        return None
    concordant = discordant = 0
    for i in range(n):
        for k in range(i + 1, n):
            if positions[i] < positions[k]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (n * (n - 1) / 2)


def math_recall(output: str, expected: str) -> float | None:
    wanted = formulas(expected)
    if not wanted:
        return None
    got = Counter(formulas(output))
    return sum((Counter(wanted) & got).values()) / len(wanted)


# --- Evaluation ------------------------------------------------------------------------


def evaluate(case: Case) -> CaseResult:
    result = CaseResult(case)
    _, rendered, _ = render_document(read_source(str(case.input_path)))
    result.output = rendered.text
    expected = (case.path / "expected.md").read_text(encoding="utf-8")
    if expected.startswith(DRAFT_MARKER):  # only evaluated with --include-drafts
        expected = expected.partition("\n")[2]
    result.expected = expected
    result.diagnostics = Counter(d.code for d in rendered.diagnostics)

    result.metrics["text_similarity"] = text_similarity(result.output, result.expected)
    result.metrics["heading_f1"] = multiset_f1(headings(result.output), headings(result.expected))
    order = reading_order(result.output, result.expected)
    if order is not None:
        result.metrics["reading_order"] = order
    recall = math_recall(result.output, result.expected)
    if recall is not None:
        result.metrics["math_recall"] = recall

    for name, minimum in sorted(case.thresholds().items()):
        value = result.metrics.get(name)
        if value is not None and value < minimum:
            result.failures.append(f"{name} {value:.4f} < {minimum}")
    errors = sum(1 for d in rendered.diagnostics if d.severity == "error")
    if errors:
        result.failures.append(f"{errors} error diagnostic(s)")
    return result


def load_baseline(root: Path) -> dict[str, dict[str, float]]:
    path = root / BASELINE_NAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_baseline(root: Path, results: Sequence[CaseResult]) -> None:
    data = {r.case.case_id: r.metrics for r in sorted(results, key=lambda r: r.case.case_id)}
    (root / BASELINE_NAME).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# --- Report ----------------------------------------------------------------------------

METRICS = ("text_similarity", "heading_f1", "reading_order", "math_recall")


def _fmt(value: float | None, base: float | None) -> str:
    if value is None:
        return "—"
    if base is None:
        return f"{value:.4f}"
    delta = value - base
    flag = " ⚠" if delta < -REGRESSION_TOLERANCE else ""
    return f"{value:.4f} ({delta:+.4f}){flag}"


def render_report(
    cases: Sequence[Case],
    results: Sequence[CaseResult] = (),
    baseline: dict[str, dict[str, float]] | None = None,
) -> str:
    lines = ["# Informe del corpus dorado", ""]
    if not cases:
        lines.append("No hay casos en el corpus.")
        return "\n".join(lines) + "\n"
    baseline = baseline or {}
    by_id = {r.case.case_id: r for r in results}

    lines += [
        "| Caso | Estado | " + " | ".join(METRICS) + " |",
        "|---|---|" + "---|" * len(METRICS),
    ]
    for case in cases:
        r = by_id.get(case.case_id)
        if r is None:
            status = "DRAFT (excluido)" if case.is_draft else "sin evaluar"
            lines.append(f"| {case.case_id} | {status} |" + " |" * len(METRICS))
            continue
        base = baseline.get(case.case_id, {})
        status = "FALLA: " + "; ".join(r.failures) if r.failures else "ok"
        cells = [_fmt(r.metrics.get(m), base.get(m)) for m in METRICS]
        lines.append(f"| {case.case_id} | {status} | " + " | ".join(cells) + " |")

    regressions = [
        f"- {r.case.case_id}: {m} {base_v:.4f} → {r.metrics[m]:.4f}"
        for r in results
        for m, base_v in sorted(baseline.get(r.case.case_id, {}).items())
        if m in r.metrics and r.metrics[m] < base_v - REGRESSION_TOLERANCE
    ]
    lines += ["", "## Regresiones", ""] + (regressions or ["Ninguna."])

    if results:
        lines += ["", "## Por fenómeno", ""]
        phenomena: dict[str, list[CaseResult]] = {}
        for r in results:
            for p in r.case.phenomena:
                phenomena.setdefault(p, []).append(r)
        for p in sorted(phenomena):
            group = phenomena[p]
            sim = sum(r.metrics["text_similarity"] for r in group) / len(group)
            lines.append(f"- {p}: {len(group)} caso(s), similitud media {sim:.4f}")

        lines += ["", "## Promedio global", ""]
        for m in METRICS:
            values = [r.metrics[m] for r in results if m in r.metrics]
            if values:
                lines.append(f"- {m}: {sum(values) / len(values):.4f}")

        lines += ["", "## Diagnósticos", ""]
        total: Counter[str] = Counter()
        for r in results:
            total.update(r.diagnostics)
        lines += [f"- {code}: {n}" for code, n in sorted(total.items())] or ["Ninguno."]
    return "\n".join(lines) + "\n"


def render_diff(result: CaseResult) -> str:
    expected = strip_front_matter(result.expected).splitlines()
    output = strip_front_matter(result.output).splitlines()
    diff = difflib.unified_diff(expected, output, "expected.md", "output.md", lineterm="")
    return "\n".join(diff) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS_ROOT)
    parser.add_argument("--case", help="evaluate a single case, e.g. wikipedia/teorema-de-bayes")
    parser.add_argument("--phenomenon", help="only cases listing this phenomenon")
    parser.add_argument("--diff", action="store_true", help="print expected vs. output diff")
    parser.add_argument(
        "--update-baseline", action="store_true", help="store current metrics as baseline"
    )
    parser.add_argument(
        "--include-drafts",
        action="store_true",
        help="also evaluate DRAFT cases (development only; never for the baseline)",
    )
    args = parser.parse_args(argv)
    # Reports carry accents and typographic spaces; Windows consoles default to cp1252.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    cases = discover_cases(args.corpus)
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]
        if not cases:
            print(f"Caso no encontrado: {args.case}", file=sys.stderr)
            return 2
    if args.phenomenon:
        cases = [c for c in cases if args.phenomenon in c.phenomena]

    if args.include_drafts and args.update_baseline:
        parser.error("--include-drafts cannot be combined with --update-baseline")
    has_expected = [c for c in cases if (c.path / "expected.md").is_file()]
    results = [evaluate(c) for c in has_expected if args.include_drafts or not c.is_draft]
    sys.stdout.write(render_report(cases, results, load_baseline(args.corpus)))
    if args.diff:
        for r in results:
            sys.stdout.write(f"\n## Diff {r.case.case_id}\n\n" + render_diff(r))
    if args.update_baseline:
        save_baseline(args.corpus, results)
        print(f"Baseline actualizado: {len(results)} caso(s).", file=sys.stderr)
    return 1 if any(r.failures for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
