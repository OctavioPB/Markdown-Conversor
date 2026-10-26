"""A PDF's title among its witnesses: metadata, cover, file name (cases from a real
library of downloaded books)."""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from tomd.ir import Heading, Paragraph
from tomd.pipeline.dispatch import prepare_document, read_source
from tomd.readers.pdf.ocr import OcrMode
from tomd.readers.pdf.title import choose_title, is_plausible, main_title, title_from_file_name


@pytest.mark.parametrize(
    ("stem", "title"),
    [
        ("[Wiley series in probability and statistics] Elisa T. Lee, John Wang - Statistical "
         "methods for survival data analysis (2013, Wiley) - libgen.li",
         "Statistical methods for survival data analysis"),
        ("Steven S. Skiena - Calculated Bets _ Computers, Gambling, and Mathematical Modeling "
         "to Win (2001, Cambridge University Press) - libgen.li",
         "Calculated Bets: Computers, Gambling, and Mathematical Modeling to Win"),
        ("oreilly-data-analysis-with-open-source-tools-nov-2010",
         "Data analysis with open source tools"),
        ("dokumen.pub_statistics-for-business-decision-making-and-analysis-1292023392-"
         "9781292023397", "Statistics for business decision making and analysis"),
        ("690967865-Python-Data-Analysis-Packt-2021-Espanol-1 (1)", "Python Data Analysis"),
        ("Mining of Massive Datasets [Team-IRA]", "Mining of Massive Datasets"),
        ("Mona Khalil - Effective Data Analysis (MEAP V07)_ Hard and soft skills",
         "Effective Data Analysis: Hard and soft skills"),
        ("Nitin Jaglal Untwal_ Utku Kose - Data Analytics for Finance Using Python-CRC Press",
         "Data Analytics for Finance Using Python"),
        ("MEHTA, SHASHANK K. - Computer algorithms.. correctness proofs",
         "Computer algorithms.. correctness proofs"),
        ("Data Structures and Algorithms in Python ( PDFDrive.com )",
         "Data Structures and Algorithms in Python"),
        ("Advanced Analytics with Spark - Patterns for Learning from Data at Scale, 2nd",
         "Advanced Analytics with Spark: Patterns for Learning from Data at Scale"),
    ],
)  # fmt: skip
def test_titles_in_downloaded_file_names(stem: str, title: str) -> None:
    assert title_from_file_name(stem) == title


def test_logos_watermarks_sections_and_junk_are_no_titles() -> None:
    for junk in ("PEARSON", "Team-Fly®", "Contributors", "ok (Glossy)", "da", "E",
                 "Book.ID_25782_979-8-89113-816-2_7x10_(Latex)", "Microsoft Word - ch1.docx",
                 "Advance Praise for Head First Data Analysis", "About the Author"):  # fmt: skip
        assert not is_plausible(junk), junk
    assert is_plausible("Dive into Deep Learning") and is_plausible("Head First")


def test_the_witnesses_most_agree_with_win() -> None:
    lib = "libgen.li"
    # A logo on the cover: the file name.
    assert choose_title(None, "PEARSON", f"A. Author - Statistics for Business - {lib}", "") == (
        "Statistics for Business"
    )
    # One line of a two-line cover title: the complete title it is part of.
    assert (
        choose_title(
            None, "with Open Source Tools", "oreilly-data-analysis-with-open-source-tools", ""
        )
        == "Data analysis with open source tools"
    )
    # The book's own title beats a different file name (a renamed download)...
    assert choose_title(
        "Open Data Structures (in pseudocode)", "Open Data Structures (in pseudocode)",
        "Data Structure in Python", "",
    ) == "Open Data Structures (in pseudocode)"  # fmt: skip
    # ...and the file name's version of the same title comes with the document's capitals.
    assert choose_title("Head First Data Analysis", "Head First", "head first data analysis",
                        "") == "Head First Data Analysis"  # fmt: skip
    # A one-word file name ("scan", "input") is no witness.
    assert choose_title(None, "Scanned report", "scan", "") == "Scanned report"
    # A cover read by OCR loses to a plausible metadata title, unless another agrees.
    assert choose_title("Publisher Title", "Report of 2019", "input", "",
                        cover_from_ocr=True) == "Publisher Title"  # fmt: skip


def test_folders_are_named_after_the_main_title() -> None:
    assert main_title("Naked Statistics: Stripping the Dread from the Data") == "Naked Statistics"
    assert main_title("naked statistics. Stripping the Dread") == "naked statistics"
    assert main_title("AI-Driven: Social Media Analytics") == "AI-Driven: Social Media Analytics"
    assert main_title("Dive into Deep Learning") == "Dive into Deep Learning"


def test_a_publisher_logo_on_the_cover_is_not_the_title(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / "Jane Doe - Statistics for Business Decisions (2020, Pearson) - libgen.li.pdf"
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    canvas.setFont("Helvetica-Bold", 30)
    canvas.drawString(72, 700, "PEARSON")  # the logo, larger than the title
    canvas.setFont("Helvetica-Bold", 20)
    canvas.drawString(72, 600, "Statistics for Business Decisions")
    canvas.setFont("Helvetica", 10)
    for i in range(8):
        canvas.drawString(72, 400 - 14 * i, "Body text of the first page of the book, long enough.")
    canvas.save()
    slug, doc = prepare_document(read_source(str(path), ocr=OcrMode.NEVER))
    assert doc.meta.title == "Statistics for Business Decisions"
    assert slug == "statistics-for-business-decisions"
    # The logo stays as text, not as a second H1 that would push the headings down.
    assert not [b for b in doc.blocks if isinstance(b, Heading) and b.level == 1]
    assert any(isinstance(b, Paragraph) and "PEARSON" in str(b) for b in doc.blocks)
