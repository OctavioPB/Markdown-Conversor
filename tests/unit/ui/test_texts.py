"""Every diagnostic has words for the person reviewing."""

from tomd.ir.diagnostics import CATALOG
from tomd.ui.texts import AUTOMATIC, REVIEW, failure_text, review_hint, review_title


def test_every_catalog_code_has_a_text_by_severity() -> None:
    for code, info in CATALOG.items():
        if info.severity == "info":
            assert code in AUTOMATIC, code
        else:
            assert code in REVIEW and REVIEW[code].hint, code
    assert set(REVIEW) | set(AUTOMATIC) == set(CATALOG)


def test_titles_carry_the_page_when_known() -> None:
    assert review_title("pdf.reading_order_uncertain", 4) == "Orden de las columnas · pág. 4"
    assert review_title("pdf.reading_order_uncertain", None) == "Orden de las columnas"
    assert (
        review_title("unknown.code", None) == "unknown.code" and review_hint("unknown.code") == ""
    )


def test_failures_in_plain_words() -> None:
    assert "DRM" in failure_text("DrmProtectedError: protected")
    assert "dañado" in failure_text("UnreadableInputError: truncated")
    assert "Tardó" in failure_text("No result after 600 s.")
    assert "memoria" in failure_text("Memory limit exceeded.")
    assert "fallo interno" in failure_text(None)
