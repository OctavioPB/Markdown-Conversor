"""Every rule in docs/MARKDOWN_SPEC.md has at least one test named after it."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_every_spec_rule_has_a_test() -> None:
    spec = (ROOT / "docs" / "MARKDOWN_SPEC.md").read_text(encoding="utf-8")
    rules = sorted(set(re.findall(r"\*\*(M\d{2})\*\*", spec)))
    assert rules, "no rules found in the spec"
    tests = "\n".join(
        f.read_text(encoding="utf-8")
        for f in (ROOT / "tests" / "unit" / "writers").glob("test_*.py")
    )
    missing = [r for r in rules if not re.search(rf"def test_{r}_", tests)]
    assert missing == []
