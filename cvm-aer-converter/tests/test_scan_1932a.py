"""A scanned Form FDA 1932a, read off OCR word boxes and the page's checkbox ink."""

import os
import sys

import numpy as np
import pytest
from pypdf import PdfWriter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cvm_aer import scan_1932a  # noqa: E402
from cvm_aer.pipeline import LAYOUT_1932A, UnreadableFormError, convert_pdf  # noqa: E402
from cvm_aer.scan_1932a import ScannedForm, label_of, read_values  # noqa: E402

WIDTH, HEIGHT = 1700, 2200


def _box(ink, x, y, tick):
    """A 30-px checkbox ending just left of the option at ``x``, ticked with an X."""
    left, top = int(x - 40), int(y + 2)
    ink[top, left : left + 30] = ink[top + 29, left : left + 30] = True
    ink[top : top + 30, left] = ink[top : top + 30, left + 29] = True
    if tick:
        for step in range(4, 26):
            ink[top + step, left + step] = ink[top + step, left + 29 - step] = True
            ink[top + step, left + step + 1] = ink[top + step, left + 28 - step] = True


def _form():
    page1 = [
        (110, 460, "Individual Case Safety Report Number (FDA Assigned Number)"),
        (109, 550, "Report Type (Check all that apply)"),
        (538, 567, "Adverse Event"),
        (844, 567, "Product Problem"),
        (109, 617, "Date of this Report (mm/dd/yyyy)"),
        (271, 672, "Month"), (362, 672, "03"), (436, 672, "Day"), (507, 672, "01"), (569, 672, "Year"), (652, 672, "2025"),
        (723, 739, "Sender Information"),
        (106, 779, "First Name"), (862, 779, "Last Name"),
        (111, 824, "Ada"), (869, 824, "Lovelace"),
        (103, 954, "City"), (716, 954, "State or Province"),
        (109, 998, "Tallahassee"), (720, 998, "Florida"),
        (104, 1043, "Country"),
        (106, 1086, "United States"),
        (103, 1215, "Sender Category"),
        (400, 1244, "Veterinarian"), (660, 1244, "Animal Owner"),
        (96, 2114, "FORM FDA 1932a (9/23)"),
    ]
    page2 = [
        (623, 1506, "Species and Related Information"),
        (186, 1562, "Dog"), (446, 1562, "Cat"),
        (95, 1757, "Breed"), (956, 1757, "Gender"), (1129, 1776, "Male"), (1377, 1776, "Female"),
        (99, 1802, "Tabby"),
        (99, 1863, "Age: 4-5 years old"), (853, 1863, "Weight: 9-10 Ibs"),
        (94, 2112, "FORM FDA 1932a (9/23)"),
    ]
    page3 = [
        (671, 137, "Adverse Event Occurrence"),
        (109, 174, "Date of Onset of Adverse Event (mm/dd/yyyy)"),
        (686, 206, "Month"), (780, 206, "02"), (848, 206, "Day28"), (983, 206, "Year"), (1068, 206, "2025"),
        (410, 400, "Adverse Event/Product Problem/Event Use Error (Long Narrative)"),
        (109, 454, "Describe the Adverse Event/Product Problem/Event Use Error."),
        (113, 504, "The cat stopped eating"), (113, 546, "and died overnight."),
        (100, 2109, "FORM FDA 1932a (9/23)"),
    ]
    ticks = {(0, "Adverse Event"): True, (0, "Product Problem"): False, (0, "Veterinarian"): False,
             (0, "Animal Owner"): True, (1, "Dog"): False, (1, "Cat"): True, (1, "Male"): False, (1, "Female"): True}
    pages = []
    for number, rows in enumerate((page1, page2, page3)):
        ink = np.zeros((HEIGHT, WIDTH), dtype=bool)
        for x, y, text in rows:
            if (number, text) in ticks:
                _box(ink, x, y, ticks[(number, text)])
        pages.append(([(x, y, x + 12 * len(text), y + 34, text) for x, y, text in rows], ink))
    return pages


def test_labels_match_their_printed_text():
    assert label_of("Report Type (Check all that apply)") == "report type"
    assert label_of("Telephone Number (Other)") == "telephone number (other)"
    assert label_of("Age: 4-5 years old") == "age:"
    assert label_of("Tallahassee") is None and label_of("Citywide") is None


def test_a_scanned_1932a_reads_into_the_dataset_names():
    values = read_values(ScannedForm(_form()))

    assert values["todaysdate"] == "2025-03-01"
    assert values["reporttype_ae"] == "Y"
    assert (values["rep01firstname"], values["rep01lastname"]) == ("Ada", "Lovelace")
    assert (values["rep01city"], values["rep01statetext"], values["rep01countrycode"]) == ("Tallahassee", "Florida", "US")
    assert values["rep01categorisation"] == "OWNE"
    assert (values["species"], values["breed"], values["sex"]) == ("CAT", "Tabby", "F")
    assert (values["age"], values["ageto"], values["ageunitcode"]) == ("4", "5", "YEAR")
    assert (values["weight"], values["weightto"], values["weightunit"]) == ("9", "10", "LBS")
    assert values["reactionstartdate"] == "2025-02-28"
    assert values["narrativeadr"] == "The cat stopped eating and died overnight."
    assert "pvcaseno" not in values


def _scan_pdf(path):
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def test_a_scanned_1932a_converts_to_a_validated_message(tmp_path, monkeypatch):
    pytest.importorskip("pypdfium2")
    pages = _form()
    monkeypatch.setitem(sys.modules, "paddleocr", type(sys)("paddleocr"))
    calls = iter(range(10))

    def fake_read(pdf_path, attachments=(), dpi=200, lang="en"):
        next(calls)
        values = read_values(ScannedForm(pages))
        documents = [scan_1932a.Document.read(path) for path in (pdf_path, *attachments)]
        return scan_1932a.Xfa1932a(values=values, documents=documents, engine="paddleocr (scanned 1932a, 3 form pages)")

    monkeypatch.setattr(scan_1932a, "read_scanned_1932a", fake_read)
    result = convert_pdf(_scan_pdf(str(tmp_path / "copier-scan.pdf")))

    assert result.layout == LAYOUT_1932A and result.validated
    assert result.report.event.onset_date == "2025-02-28"
    assert result.report.animal.species == "Cat"
    assert "20250228" in result.xml


def test_a_scan_without_ocr_installed_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "paddleocr", None)

    with pytest.raises(UnreadableFormError, match="requirements-ocr.txt"):
        convert_pdf(_scan_pdf(str(tmp_path / "copier-scan.pdf")))
