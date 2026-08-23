"""Reading the fields of a scanned 3500A into the report model.

Values here are the shapes OCR returns from a scan - a box read short at its
border, a caption bleeding into the text, rows continuing onto the next page -
rather than the clean strings a fillable form gives back.
"""

from medwatch_ocr.form_extract import ExtractedForm, map_report
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
