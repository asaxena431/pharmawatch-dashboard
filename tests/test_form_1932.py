"""Tests for the genuine FORM FDA 1932 path (fill -> extract -> VICH GL42 XML).

The blank static form is downloaded from fda.gov once and cached; when neither
the cache nor the network is available these tests skip.  Extraction uses the
AcroForm reader so the suite stays fast - the PaddleOCR geometry path over the
same PDF is exercised by ``scripts/verify_1932_roundtrip.py``.
"""

import xml.etree.ElementTree as ET

import pytest

from medwatch_ocr.form_1932 import (
    VETERINARY_SAMPLES,
    blank_form_path,
    expected_checks,
    expected_values,
    fill_1932_form,
)
from medwatch_ocr.models import CENTER_CVM
from medwatch_ocr.pipeline import FORMAT_GL42, LAYOUT_1932, convert_pdf, render_xml
from medwatch_ocr.vet_extract import (
    extract_1932_fields,
    is_1932_form,
    load_template,
    map_veterinary_report,
)

NAMESPACE = {"g": "urn:vich:gl42:aer"}


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

    assert report.center == CENTER_CVM
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


def test_1932_form_converts_to_gl42(veterinary_pdf):
    result = convert_pdf(veterinary_pdf, engine="text-layer", layout=LAYOUT_1932)

    assert result.layout == LAYOUT_1932
    assert result.output_format == FORMAT_GL42
    assert result.summary["center"] == CENTER_CVM

    root = ET.fromstring(result.xml[result.xml.index("<vichAdverseEventReport") :])
    assert root.get("standard") == "GL42"
    assert root.findtext("./g:messageHeader/g:messageReceiverIdentifier", namespaces=NAMESPACE) == "FDACVM"
    report = root.find("./g:adverseEventReport", NAMESPACE)
    assert report.findtext("./g:administrativeInformation/g:uniqueAerIdentifier", namespaces=NAMESPACE) == (
        "US-CAH-2026-000318"
    )
    assert report.findtext("./g:administrativeInformation/g:dateFirstReceived", namespaces=NAMESPACE) == "2026-02-03"
    assert report.findtext("./g:animal/g:species", namespaces=NAMESPACE) == "Dog"
    assert report.findtext("./g:animal/g:weight/g:basis", namespaces=NAMESPACE) == "Measured"
    weight = report.find("./g:animal/g:weight/g:minimum", NAMESPACE)
    assert (weight.get("value"), weight.get("unit")) == ("28.4", "kg")
    age = report.find("./g:animal/g:age/g:minimum", NAMESPACE)
    assert (age.get("value"), age.get("unit")) == ("4", "Year")
    product = report.find("./g:veterinaryMedicinalProduct", NAMESPACE)
    assert product.findtext("./g:brandName", namespaces=NAMESPACE) == "DERMAQUELL CHEWABLE TABLETS"
    assert product.findtext("./g:registrationIdentifier", namespaces=NAMESPACE) == "NADA 141-999"
    assert product.findtext("./g:activeIngredient/g:name", namespaces=NAMESPACE) == "veloxacitinib maleate"
    signs = [e.findtext("./g:term", namespaces=NAMESPACE) for e in report.findall("./g:adverseEvent/g:clinicalManifestation", NAMESPACE)]
    assert "Vomiting" in signs
    assert report.findtext("./g:adverseEvent/g:outcome/g:recoveredNormal", namespaces=NAMESPACE) == "1"
    assert report.findtext("./g:dechallengeRechallenge/g:eventAbatedAfterStopping", namespaces=NAMESPACE) == "Yes"
    assert report.findtext("./g:assessment/g:attendingVeterinarian", namespaces=NAMESPACE) == "Probable"
    # every element carries its GL42 data-element number
    assert product.get("gl42") == "B.2"


def test_1932_layout_is_auto_detected(veterinary_pdf):
    result = convert_pdf(veterinary_pdf, engine="text-layer")
    assert result.layout == LAYOUT_1932
    assert result.output_format == FORMAT_GL42


def test_gl42_output_requires_a_veterinary_report(veterinary_pdf):
    report = map_veterinary_report(extract_1932_fields(veterinary_pdf))
    with pytest.raises(ValueError):
        render_xml(report, "mdr")
