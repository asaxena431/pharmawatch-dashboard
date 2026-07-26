"""Round-trip tests for the MedWatch 3500A pipeline.

The samples are rendered as PDFs and read back with the ``text-layer`` engine
so the suite runs without the (large) PaddleOCR dependency. The PaddleOCR path
produces the same lines and is exercised via
``python -m medwatch_ocr.cli demo --engine paddleocr``.
"""

import xml.etree.ElementTree as ET

import pytest

from medwatch_ocr import CENTER_CDER, CENTER_CDRH, FORMAT_E2B, FORMAT_MDR, convert_pdf
from medwatch_ocr.samples import SAMPLES, render_sample


@pytest.fixture(scope="module")
def sample_pdfs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("samples")
    return {
        name: render_sample(spec, str(directory / f"3500A_{name}.pdf"))
        for name, spec in SAMPLES.items()
    }


def test_cder_premarket_maps_to_e2b_r2(sample_pdfs):
    result = convert_pdf(sample_pdfs["cder_premarket"], engine="text-layer")

    assert result.report.center == CENTER_CDER
    assert result.report.stage == "premarket"
    assert result.output_format == FORMAT_E2B

    root = ET.fromstring(result.xml[result.xml.index("<ichicsr") :])  # skip the DOCTYPE
    assert root.tag == "ichicsr"
    assert root.findtext("./safetyreport/safetyreportid") == "US-VANTERA-2025-004871"
    assert root.findtext("./safetyreport/reporttype") == "2"  # report from study
    assert root.findtext("./safetyreport/studytype") == "1"
    assert root.findtext("./safetyreport/sponsorstudynumb") == "VTX-HEPA-301"
    assert root.findtext("./safetyreport/seriousnesshospitalization") == "1"
    assert root.findtext("./safetyreport/seriousnesslifethreatening") == "1"

    patient = root.find("./safetyreport/patient")
    assert patient.findtext("patientinitial") == "R.K.M."
    assert patient.findtext("patientonsetage") == "63"
    assert patient.findtext("patientonsetageunit") == "801"
    assert patient.findtext("patientsex") == "1"
    assert patient.findtext("patientweight") == "78.5"

    reactions = [r.findtext("reactionmeddrapt") for r in patient.findall("reaction")]
    assert reactions == ["Acute hepatic failure", "Jaundice", "Elevated ALT"]

    drug = patient.find("drug")
    assert drug.findtext("medicinalproduct") == "HEPAXOLIB (investigational)"
    assert drug.findtext("drugstructuredosagenumb") == "200"
    assert drug.findtext("drugadministrationroute") == "048"  # oral
    assert drug.findtext("drugstartdate") == "20251010"
    assert drug.findtext("drugenddate") == "20251104"
    assert drug.findtext("actiondrug") == "1"  # withdrawn
    assert drug.findtext("./activesubstance/activesubstancename") == "hepaxolib mesylate"

    narrative = root.findtext("./safetyreport/summary/narrativeincludeclinical")
    assert "progressive jaundice" in narrative
    assert not result.report.unmapped_lines


def test_cdrh_postmarket_maps_to_mdr(sample_pdfs):
    result = convert_pdf(sample_pdfs["cdrh_postmarket"], engine="text-layer")

    assert result.report.center == CENTER_CDRH
    assert result.report.stage == "postmarket"
    assert result.output_format == FORMAT_MDR

    root = ET.fromstring(result.xml)
    namespace = {"m": "urn:fda:cdrh:emdr:3500A"}
    report = root.find("m:mdrReport", namespace)
    assert report.get("center") == "CDRH"

    header = report.find("m:reportHeader", namespace)
    assert header.findtext("m:manufacturerReportNumber", namespaces=namespace) == "2183456-2026-00037"
    assert header.findtext("m:reportSourceCode", namespaces=namespace) == "1"  # user facility
    assert header.findtext("m:pmaOr510kNumber", namespaces=namespace) == "P980021/S045"
    event_types = {e.get("code") for e in header.findall("m:adverseEventType", namespace)}
    assert event_types == {"IN", "M"}

    device = report.find("m:suspectDevice", namespace)
    assert device.findtext("m:brandName", namespaces=namespace).startswith("CARDIOSTEP XR2")
    identifiers = device.find("m:deviceIdentifiers", namespace)
    assert identifiers.findtext("m:serialNumber", namespaces=namespace) == "SN-4471902"
    assert identifiers.findtext("m:lotNumber", namespaces=namespace) == "LOT-2023-0914"
    assert identifiers.findtext("m:expirationDate", namespaces=namespace) == "2028-09-30"
    assert device.findtext("m:explantDate", namespaces=namespace) == "2026-01-21"
    evaluation = device.find("m:deviceEvaluation", namespace)
    assert evaluation.findtext("m:deviceReturnedToManufacturer", namespaces=namespace) == "Y"
    assert evaluation.findtext("m:dateDeviceReturned", namespaces=namespace) == "2026-01-26"

    event = report.find("m:adverseEvent", namespace)
    outcome_codes = {o.get("code") for o in event.findall("./m:outcomes/m:outcome", namespace)}
    assert outcome_codes == {"R", "H"}
    assert "battery voltage drop" in event.findtext("m:eventDescription", namespaces=namespace)
    assert not result.report.unmapped_lines


def test_device_report_can_also_be_rendered_as_e2b(sample_pdfs):
    result = convert_pdf(sample_pdfs["cdrh_postmarket"], engine="text-layer", output_format=FORMAT_E2B)
    assert "<ichicsr" in result.xml
    assert "FDACDRH" in result.xml


def test_center_and_stage_are_inferred_when_not_given(sample_pdfs):
    drug = convert_pdf(sample_pdfs["cder_premarket"], engine="text-layer")
    device = convert_pdf(sample_pdfs["cdrh_postmarket"], engine="text-layer")
    assert (drug.report.center, drug.report.stage) == (CENTER_CDER, "premarket")
    assert (device.report.center, device.report.stage) == (CENTER_CDRH, "postmarket")


def test_parser_tolerates_ocr_noise():
    from medwatch_ocr.pipeline import convert_lines

    lines = [
        "FDA Form 35OOA - MedWatch Mandatory Reporting Page 1",
        "A. PATIENT INFORMATION",
        "A.1 Patient ldentifier: PT-9",
        "A.2 Age At Tirne Of Event: 45 Years",
        "A.3 Sex; Fernale",
        "A.4 Weight: 143 lb",
        "B. ADVERSE EVENT, PRODUCT PROBLEM OR ERROR",
        "B.2 Outcome Attributed To Adverse Event: Hospitalization",
        "B.5 Adverse Event Term: Anaphylaxis",
        "C. SUSPECT PRODUCTS",
        "C.1 Suspect Product Narne: AMOXICILLIN",
        "C.2b Route Used: Oral",
    ]
    result = convert_lines(lines, ocr_engine="synthetic")

    assert result.report.patient.identifier == "PT-9"
    assert result.report.patient.age == "45"
    assert result.report.patient.sex == "Female"
    assert result.report.patient.weight_kg == "64.9"  # converted from pounds
    assert result.report.event.reactions == ["Anaphylaxis"]
    assert result.report.products[0].name == "AMOXICILLIN"
    assert "<seriousnesshospitalization>1</seriousnesshospitalization>" in result.xml
