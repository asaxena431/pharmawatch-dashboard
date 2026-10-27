"""Tests for the genuine FORM FDA 1932 path (fill -> extract -> validated vich-hl7 message).

The blank static form is downloaded from fda.gov once and cached; when neither
the cache nor the network is available these tests skip.  Extraction uses the
AcroForm reader so the suite stays fast - the PaddleOCR geometry path over the
same PDF is exercised by ``scripts/verify_1932_roundtrip.py``.
"""

import os
import xml.etree.ElementTree as ET

import pytest

from cvm_aer.form_1932 import (
    VETERINARY_SAMPLES,
    blank_form_path,
    expected_checks,
    expected_values,
    fill_1932_form,
)
from cvm_aer.pipeline import FORMAT_VICH_HL7, LAYOUT_1932, convert_pdf, render_xml
from cvm_aer.vet_extract import (
    extract_1932_fields,
    is_1932_form,
    load_template,
    map_veterinary_report,
)

HL7 = {"h": "urn:hl7-org:v3"}


@pytest.fixture(scope="module")
def blank_form():
    try:
        return blank_form_path()
    except Exception as exc:  # pragma: no cover - offline environment
        pytest.skip(f"blank FORM FDA 1932 unavailable: {exc}")


@pytest.fixture(scope="module")
def veterinary_pdf(blank_form, tmp_path_factory):
    directory = tmp_path_factory.mktemp("vet")
    spec = VETERINARY_SAMPLES["cvm_veterinary"]
    return fill_1932_form(spec, str(directory / "FDA-1932_cvm_veterinary.pdf"), blank_path=blank_form)


def test_template_covers_every_page():
    template = load_template()
    assert len(template["pages"]) == 9
    assert len(template["fields"]) > 300
    assert len([f for f in template["fields"] if f["type"] == "Btn"]) > 100
    assert {f["type"] for f in template["fields"]} <= {"Tx", "Ch", "Btn"}


def test_sample_keeps_the_official_nine_page_form(veterinary_pdf):
    from pypdf import PdfReader

    reader = PdfReader(veterinary_pdf)
    assert len(reader.pages) == 9
    assert is_1932_form(veterinary_pdf)
    assert "1932" in (reader.pages[0].extract_text() or "")


def test_every_written_value_and_checkbox_reads_back(veterinary_pdf):
    form = extract_1932_fields(veterinary_pdf)
    for key, want in expected_values("cvm_veterinary").items():
        assert form.values.get(key) == want, key
    for key in expected_checks("cvm_veterinary"):
        assert form.checked(key), key


def test_veterinary_mapping(veterinary_pdf):
    report = map_veterinary_report(extract_1932_fields(veterinary_pdf))

    assert report.center == "CVM"
    assert report.report_id == "US-CAH-2026-000318"
    assert report.animal.species == "Dog"
    assert report.animal.breeds == ["Labrador Retriever"]
    assert report.animal.gender == "Female"
    assert report.animal.reproductive_status == "Neutered"
    assert report.animal.weight_min_kg == "28.4"
    assert report.animal.age_min_unit == "Year"
    assert report.product.brand_name == "DERMAQUELL CHEWABLE TABLETS"
    assert report.product.registration_id == "NADA 141-999"
    assert report.product.atc_vet_code == "QD11AH90"
    assert report.product.route == "Oral"
    assert report.product.active_ingredients[0].name == "veloxacitinib maleate"
    assert [sign.term for sign in report.event.signs][:2] == ["Vomiting", "Lethargy"]
    assert report.event.serious == "No"
    assert report.event.outcome.recovered_normal == "1"
    assert report.event.dechallenge == "Yes"
    assert report.event.attending_vet_assessment == "Probable"


def test_1932_form_converts_to_a_validated_vich_hl7_message(veterinary_pdf):
    result = convert_pdf(veterinary_pdf, engine="text-layer", layout=LAYOUT_1932)

    assert result.layout == LAYOUT_1932
    assert result.output_format == FORMAT_VICH_HL7
    assert result.validated is True
    assert result.summary["species"] == "Dog"
    assert result.summary["schema_validated"] is True

    root = ET.fromstring(result.xml)
    assert root.tag == "{urn:hl7-org:v3}MCCI_IN200100UV01"
    ids = {element.get("extension") for element in root.iterfind(".//h:id", HL7)}
    assert "US-CAH-2026-000318" in ids and "NADA 141-999" in ids
    assert root.find(".//h:code[@displayName='Dog']", HL7) is not None
    names = [element.text for element in root.iterfind(".//h:name", HL7)]
    assert "DERMAQUELL CHEWABLE TABLETS" in names and "veloxacitinib maleate" in names
    weight = root.find(".//h:low[@unit='kg']", HL7)
    assert weight.get("value") == "28.4"
    texts = [element.text for element in root.iterfind(".//h:originalText", HL7)]
    assert "Vomiting" in texts and "Probable" in texts
    # the form itself travels inside the message
    titles = [element.text for element in root.iterfind(".//h:title", HL7)]
    assert titles == [os.path.basename(veterinary_pdf)]


def test_1932_layout_is_auto_detected(veterinary_pdf):
    result = convert_pdf(veterinary_pdf, engine="text-layer")
    assert result.layout == LAYOUT_1932
    assert result.output_format == FORMAT_VICH_HL7


def test_only_vich_hl7_is_offered(veterinary_pdf):
    report = map_veterinary_report(extract_1932_fields(veterinary_pdf))
    with pytest.raises(ValueError):
        render_xml(report, "mdr")
