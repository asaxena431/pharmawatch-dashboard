"""Tests for the genuine FDA Form 3500A path (fill -> extract -> XML).

The blank fillable form is downloaded from fda.gov once and cached; when neither
the cache nor the network is available these tests skip.  Extraction here uses
the AcroForm reader so the suite stays fast - the PaddleOCR geometry path over
the same PDFs is verified by ``scripts/verify_official_roundtrip.py``.
"""

import xml.etree.ElementTree as ET

import pytest

from medwatch_ocr.form_extract import (
    extract_form_fields,
    is_official_form,
    load_template,
    map_report,
)
from medwatch_ocr.models import CENTER_CDER, CENTER_CDRH
from medwatch_ocr.official_form import (
    OFFICIAL_SAMPLES,
    blank_form_path,
    expected_checks,
    expected_values,
    fill_official_form,
)
from medwatch_ocr.pipeline import FORMAT_E2B, FORMAT_MDR, LAYOUT_OFFICIAL, convert_pdf


@pytest.fixture(scope="module")
def blank_form():
    try:
        return blank_form_path()
    except Exception as exc:  # pragma: no cover - offline environment
        pytest.skip(f"blank FDA 3500A form unavailable: {exc}")


@pytest.fixture(scope="module")
def official_pdfs(blank_form, tmp_path_factory):
    directory = tmp_path_factory.mktemp("official")
    return {
        name: fill_official_form(spec, str(directory / f"FDA-3500A_{name}.pdf"), blank_path=blank_form)
        for name, spec in OFFICIAL_SAMPLES.items()
    }


def _shift(key: str) -> str:
    """official_form uses 1-based page keys; extraction uses 0-based."""
    page, _, name = key.partition(".")
    return f"p{int(page.lstrip('p')) - 1}.{name}"


def test_template_covers_every_page():
    template = load_template()
    assert len(template["pages"]) == 9
    assert len(template["fields"]) > 300
    assert {f["type"] for f in template["fields"]} <= {"Tx", "Ch", "Btn"}
    # Pushbuttons (Reset Form) must not be mistaken for checkboxes.
    assert not [f for f in template["fields"] if f["name"] in ("resetButton", "T100")]


def test_samples_keep_the_official_nine_page_form(official_pdfs):
    from pypdf import PdfReader

    for path in official_pdfs.values():
        reader = PdfReader(path)
        assert len(reader.pages) == 9
        assert is_official_form(path)
        page_one = reader.pages[0].extract_text() or ""
        assert "MEDWATCH" in page_one.upper()


@pytest.mark.parametrize("name", sorted(OFFICIAL_SAMPLES))
def test_every_written_value_and_checkbox_reads_back(official_pdfs, name):
    form = extract_form_fields(official_pdfs[name])
    for key, want in expected_values(name).items():
        assert form.values.get(_shift(key)) == want, key
    for key in expected_checks(name):
        assert form.checked(_shift(key)), key


def test_cder_official_form_to_e2b_r2(official_pdfs):
    result = convert_pdf(official_pdfs["cder_premarket"], engine="text-layer", layout=LAYOUT_OFFICIAL)

    assert result.layout == LAYOUT_OFFICIAL
    assert result.report.center == CENTER_CDER
    assert result.report.stage == "premarket"
    assert result.output_format == FORMAT_E2B

    report = result.report
    assert report.patient.identifier == "SUBJ-2210-0148"
    assert report.patient.age == "63"
    assert report.patient.age_unit == "Year"
    assert report.patient.sex == "Male"
    assert report.patient.weight_kg == "78.5 kg"
    assert "Hospitalization" in report.event.outcomes
    assert "Life-threatening" in report.event.outcomes
    assert report.event.reactions == ["Acute hepatic failure", "Jaundice", "Elevated ALT"]
    assert report.products[0].name.startswith("HEPAXOLIB")
    assert report.products[0].route == "Oral"
    assert report.products[0].event_abated_after_stop == "Yes"

    root = ET.fromstring(result.xml[result.xml.index("<ichicsr") :])
    assert root.findtext("./safetyreport/safetyreportid") == "US-VTX-2025-004871"
    assert root.findtext("./safetyreport/seriousnesshospitalization") == "1"
    assert root.findtext("./safetyreport/sponsorstudynumb") == "VTX-HEPA-301"


def test_cdrh_official_form_to_mdr(official_pdfs):
    result = convert_pdf(official_pdfs["cdrh_postmarket"], engine="text-layer", layout=LAYOUT_OFFICIAL)

    assert result.layout == LAYOUT_OFFICIAL
    assert result.report.center == CENTER_CDRH
    assert result.output_format == FORMAT_MDR

    device = result.report.device
    assert device.brand_name == "CARDIOSTEP XR2 Implantable Pulse Generator"
    assert device.model_number == "XR2-3120"
    assert device.serial_number == "SN-4471902"
    assert device.lot_number == "LOT-2023-0914"
    assert device.udi.startswith("(01)")
    assert device.operator == "Health Professional"
    assert "Malfunction" in (result.report.manufacturer.adverse_event_type or "")
    assert "Replace" in (result.report.manufacturer.remedial_action or "")

    namespace = {"m": "urn:fda:cdrh:emdr:3500A"}
    root = ET.fromstring(result.xml)
    header = root.find("m:mdrReport/m:reportHeader", namespace)
    assert header.findtext("m:manufacturerReportNumber", namespaces=namespace) == "2183456-2026-00037"
    assert header.findtext("m:pmaOr510kNumber", namespaces=namespace) == "P980021/S045"
    identifiers = root.find("m:mdrReport/m:suspectDevice/m:deviceIdentifiers", namespace)
    assert identifiers.findtext("m:serialNumber", namespaces=namespace) == "SN-4471902"


def test_center_inferred_from_the_device_block(official_pdfs):
    form = extract_form_fields(official_pdfs["cdrh_postmarket"])
    assert map_report(form).center == CENTER_CDRH
    form = extract_form_fields(official_pdfs["cder_premarket"])
    assert map_report(form).center == CENTER_CDER


def test_official_layout_is_auto_detected(official_pdfs):
    result = convert_pdf(official_pdfs["cdrh_postmarket"], engine="text-layer")
    assert result.layout == LAYOUT_OFFICIAL
