"""Reading the fields of a scanned 3500A into the report model.

Values here are the shapes OCR returns from a scan - a box read short at its
border, a caption bleeding into the text, rows continuing onto the next page -
rather than the clean strings a fillable form gives back.
"""

from medwatch_ocr.form_extract import ExtractedForm, _checkbox_marked, map_report
from medwatch_ocr.models import CENTER_CDRH


def _form(values: dict, checks: list) -> ExtractedForm:
    return ExtractedForm(values=values, checks=checks, pages=9)


def test_a_report_number_read_short_at_its_border_takes_the_fuller_reading():
    """The header box and F.1 hold the same number; the caption goes with it."""
    report = map_report(
        _form(
            {
                "p0.ufNum": "UF/Importer Report # 1825400000-2026-0001",
                "p7.reportNum": "r 1825400000",
                "p5.commonName": "Dialysis machine",
            },
            [],
        )
    )
    assert report.user_facility.report_number == "1825400000-2026-0001"


def test_every_race_ticked_is_kept_in_the_order_the_form_prints_them():
    report = map_report(
        _form({"p5.commonName": "Dialysis machine"}, ["p0.white", "p0.asian"]),
    )
    assert report.patient.races == ["Asian", "White"]


def test_both_kinds_of_report_are_kept_when_the_form_ticks_both():
    report = map_report(
        _form(
            {"p5.commonName": "Dialysis machine", "p5.brandName": "2008T"},
            ["p0.adverse", "p0.prodProblem"],
        )
    )
    assert set(report.event.report_types) == {"Adverse Event", "Product Problem"}
    assert report.center == CENTER_CDRH


def test_concomitant_rows_continue_onto_the_following_page():
    report = map_report(
        _form(
            {
                "p5.commonName": "Dialysis machine",
                "p5.cProdName1": "Naturalyte Bicarbonate",
                "p5.cProdStart1": "22-Jan-2024",
                "p6.cProdName1": "Combiset Bloodlines",
            },
            [],
        )
    )
    assert [product.name for product in report.device.concomitants] == [
        "Naturalyte Bicarbonate",
        "Combiset Bloodlines",
    ]
    assert report.device.concomitants[0].therapy_start == "22-Jan-2024"


def _box(paper: int, interior: int, shade: int = 0):
    """A 40x40 page holding one 20x20 box, on paper of a given grey."""
    from PIL import Image, ImageDraw

    image = Image.new("L", (40, 40), max(0, paper - shade))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 29, 29), outline=0)
    draw.rectangle((11, 11, 28, 28), fill=max(0, interior - shade))
    return image


def test_a_box_tinted_instead_of_ticked_is_read_as_marked():
    assert _checkbox_marked(_box(paper=254, interior=245), (10, 10, 30, 30))


def test_paper_shading_alone_does_not_mark_a_box():
    """A grey band across the scan darkens box and paper together."""
    assert not _checkbox_marked(_box(paper=254, interior=254, shade=10), (10, 10, 30, 30))


def test_an_untouched_box_stays_unmarked():
    assert not _checkbox_marked(_box(paper=255, interior=255), (10, 10, 30, 30))
