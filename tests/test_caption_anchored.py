"""The caption-anchored reader used for 3500A copies that have no widget template."""

import xml.etree.ElementTree as ET

from medwatch_ocr.anchors_3500a import (
    _carried_text,
    _concomitant_products,
    _manufacturer,
    _reporter,
    _rows,
    _suspect_products,
    _weight_unit,
)
from medwatch_ocr.e2b_r2_fda import _add, _postcode, _suspect_drug, _tel
from medwatch_ocr.form_extract import ExtractedForm
from medwatch_ocr.xml_diff import message_format
from medwatch_ocr.label_extract import (
    CAPTION_ANCHORED_VARIANTS,
    VARIANT_3500A_2022,
    VARIANT_3500A_2025,
    VARIANT_FACSIMILE,
    Word,
    _reflow,
)


def form(**values: str) -> ExtractedForm:
    extracted = ExtractedForm(pages=1, engine="test")
    extracted.values.update(values)
    return extracted


def test_the_current_official_revision_keeps_its_template():
    assert VARIANT_3500A_2025 not in CAPTION_ANCHORED_VARIANTS
    assert {VARIANT_3500A_2022, VARIANT_FACSIMILE} <= CAPTION_ANCHORED_VARIANTS


def test_rows_are_numbered_however_the_copy_marks_them():
    assert _rows("#1. Aspirin #2. Lidocaine") == {1: "Aspirin", 2: "Lidocaine"}
    assert _rows("1) Aspirin 2) Lidocaine") == {1: "Aspirin", 2: "Lidocaine"}
    assert _rows("#9. Aspirin #10. Lidocaine") == {9: "Aspirin", 10: "Lidocaine"}
    assert _rows("Aspirin") == {1: "Aspirin"}


def test_a_block_printed_once_per_product_numbers_the_products_in_order():
    extracted = form(
        **{
            "rows.prodName@1": "HEPAXOLIB",
            "rows.prodName@2": "LIDOCAINE",
            "rows.therapy@1": "05-NOV-2025 to 04-MAR-2026",
        }
    )
    _suspect_products(extracted)
    assert extracted.get("p3.prodName1") == "HEPAXOLIB"
    assert extracted.get("p4.prodName2") == "LIDOCAINE"
    assert (extracted.get("p3.start1Date"), extracted.get("p3.end1Date")) == ("05-NOV-2025", "04-MAR-2026")


def test_a_row_continued_in_a_later_section_replaces_the_truncated_one():
    extracted = form(
        **{
            "rows.prodName@1": "#1. Actemra #2. Biopsy",
            "rows.prodNameCont": "#2. Transbronchial lung biopsy (N/A)",
        }
    )
    _suspect_products(extracted)
    assert extracted.get("p3.prodName1") == "Actemra"
    assert extracted.get("p4.prodName2") == "Transbronchial lung biopsy (N/A)"


def test_concomitant_products_run_on_into_the_continuation_section():
    extracted = form(
        **{
            "rows.concomitant": "#1. PREDNISONE 17-SEP-2025 ongoing",
            "rows.concomitantCont": "#2. MOFETIL 17-SEP-2025 to 16-MAR-2026",
        }
    )
    _concomitant_products(extracted)
    assert extracted.get("p5.cProdName1") == "PREDNISONE"
    assert extracted.get("p5.cProdEnd1") is None
    assert extracted.get("p5.cProdName2") == "MOFETIL"
    assert extracted.get("p5.cProdEnd2") == "16-MAR-2026"


def test_a_weight_box_holding_both_readings_reports_kilograms():
    extracted = form(**{"p0.weightText": "80.3 kgs 177.0 lbs"})
    extracted.checks.append("p0.weightLB")
    _weight_unit(extracted)
    assert extracted.get("p0.patWeight") == "80.3"
    assert not extracted.checked("p0.weightLB")

    unlabelled = form(**{"p0.weightText": "177.0 80.3"})
    _weight_unit(unlabelled)
    assert unlabelled.get("p0.patWeight") == "80.3"


def test_a_concomitant_row_keeps_the_text_the_copy_prints_before_its_dates():
    extracted = form(**{"rows.concomitant": "#1. LIDOCAINE (LIDOCAINE) Patch, 4 percent, 16-SEP-2025 to Ongoing"})
    _concomitant_products(extracted)
    assert extracted.get("p5.cProdName1") == "LIDOCAINE (LIDOCAINE) Patch, 4 percent,"


def test_a_revision_captioning_the_parts_of_the_box_is_read_part_by_part():
    extracted = form(
        **{
            "rows.prodNameSub@1": "(Continued...)",
            "rows.doseSub@1": "250",
            "rows.doseUnitSub@1": "--",
            "rows.routeSub@1": "Intratumoral",
        }
    )
    _suspect_products(extracted)
    assert extracted.get("p3.prodName1") == "(Continued...)"
    assert (extracted.get("p3.dose1"), extracted.get("p3.doseUnit1")) == ("250", "--")
    assert extracted.get("p3.route1") == "Intratumoral"


def test_a_dose_whose_unit_e2b_does_not_code_is_serialised_as_free_text():
    coded = form(**{"p3.prodName1": "HEPAXOLIB", "p3.dose1": "588", "p3.doseUnit1": "milligram"})
    values = _suspect_drug(coded, 1, 3)
    assert values is not None
    assert (values["drugstructuredosagenumb"], values["drugstructuredosageunit"]) == ("588", "003")
    assert "drugdosagetext" not in values

    free = form(**{"p3.prodName1": "HEPAXOLIB", "p3.dose1": "250", "p3.doseUnit1": "--"})
    values = _suspect_drug(free, 1, 3)
    assert values is not None
    assert values["drugdosagetext"] == "250 --"
    assert "drugstructuredosagenumb" not in values


def test_a_suspect_box_read_without_a_product_name_is_not_a_drug():
    assert _suspect_drug(form(**{"p3.dose1": "250"}), 1, 3) is None


def test_free_text_is_joined_to_the_part_carried_into_the_continuation_pages():
    extracted = form(
        **{
            "p2.testData": "16Mar2026: bronchoscopy\ncontinued in additional info section...",
            "p2.testDataCont": "through the mucosa.",
        }
    )
    _carried_text(extracted)
    assert extracted.get("p2.testData") == "16Mar2026: bronchoscopy\n\nRELEVANT TESTS (Continued)\nthrough the mucosa."
    assert extracted.get("p2.testDataCont") is None


def test_a_dose_box_read_empty_beside_its_therapy_dates_holds_no_dose():
    extracted = form(**{"rows.prodName@1": "Actemra", "rows.dose@1": "03/16/2026 03/16/2026"})
    _suspect_products(extracted)
    assert extracted.get("p3.dose1") is None


def test_a_continuation_page_is_read_one_record_per_line():
    words = [
        [Word(40, 10, 500, 20, "through the mucosa, with air tracking from the bronchus into a", ("typed", 8.0))],
        [Word(40, 22, 300, 32, "contained track in the surrounding fibrosis.", ("typed", 8.0))],
        [Word(40, 34, 120, 44, "10May2026:", ("typed", 8.0))],
        [Word(40, 46, 400, 56, "-Follow-up CT scan of chest without contrast.", ("typed", 8.0))],
    ]
    harvested = [row[0].text for row in words]
    assert _reflow(words, harvested).split("\n") == [
        "through the mucosa, with air tracking from the bronchus into a contained track in the surrounding fibrosis.",
        "10May2026: -Follow-up CT scan of chest without contrast.",
    ]


def test_a_value_longer_than_the_profile_allows_is_cut_to_length():
    long_name = "COSOPT (DORZOLAMIDE HYDROCHLORIDE, TIMOLOL MALEATE) Eye drops, 22.3-6.8 milligram per millilitre,"
    values = _suspect_drug(form(**{"p3.prodName1": long_name}), 1, 3)
    assert values is not None
    drug = ET.Element("drug")
    _add(drug, "medicinalproduct", values["medicinalproduct"])
    assert drug[0].text == long_name[:70]


def test_a_redacted_postcode_or_telephone_is_not_a_value():
    assert _postcode("Withheld") is None
    assert _postcode("02114-2696") == "02114-2696"
    assert _tel("Withheld") is None
    assert _tel("(713) 745-4462") == "7137454462"


def test_a_contact_office_block_names_the_contact_on_its_first_line():
    extracted = form(**{"p7.manuBlock": "Julia Goldstein, MD\nOffice of Regulatory Affairs\nBethesda, MD 20892"})
    _manufacturer(extracted)
    assert extracted.get("p7.manuName") == "Julia Goldstein, MD"
    assert extracted.get("p7.manuAddr") == "Office of Regulatory Affairs Bethesda, MD 20892"


def test_a_single_name_and_address_box_is_split_into_name_address_and_email():
    extracted = form(**{"p6.nameBlock": "Dr. Brian Keller bckeller@mgh.harvard.edu\n55 Fruit Street\nBoston, MA"})
    _reporter(extracted)
    assert extracted.get("p6.reportFirst") == "Dr. Brian"
    assert extracted.get("p6.reportLast") == "Keller"
    assert extracted.get("p6.reportEmail") == "bckeller@mgh.harvard.edu"
    assert extracted.get("p6.reportAddr") == "55 Fruit Street Boston, MA"


def test_the_profile_of_the_expected_message_is_recognised():
    plain = "<ichicsr><safetyreport><safetyreportid>1</safetyreportid></safetyreport></ichicsr>"
    assert message_format(plain) == "e2b-r2"
    assert message_format(plain.replace("<safetyreportid>1</safetyreportid>", "<formtype>3500A</formtype>")) == "e2b-r2-fda"
    assert message_format("<mdrReports/>") == "mdr"
    assert message_format("not xml") is None
