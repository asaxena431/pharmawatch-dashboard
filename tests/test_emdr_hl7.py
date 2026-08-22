"""The CDRH eMDR HL7 v3 submission message written from a device report."""

import base64
import xml.etree.ElementTree as ET

from medwatch_ocr import emdr_hl7
from medwatch_ocr.models import (
    AdverseEvent,
    ManufacturerInfo,
    MedWatchReport,
    Patient,
    Reporter,
    SuspectDevice,
    UserFacility,
)
from medwatch_ocr.pipeline import FORMAT_EMDR_HL7, FORMATS, render_xml
from medwatch_ocr.xml_diff import flatten, message_format

NS = {"v3": "urn:hl7-org:v3"}


def _report() -> MedWatchReport:
    report = MedWatchReport(center="CDRH", stage="postmarket")
    report.patient = Patient(
        identifier="AH",
        age="37",
        age_unit="YR",
        date_of_birth="1989-01-02",
        sex="M",
        weight="250",
        weight_unit="lbs",
    )
    report.event = AdverseEvent(
        event_date="2026-07-19",
        report_date="2026-07-23",
        outcomes=["Death"],
        narrative="The device stopped during compressions.",
        location="Jail",
    )
    report.device = SuspectDevice(
        brand_name="LUCAS 3",
        common_name="LUCAS",
        model_number="LUCAS 3 v3.1",
        serial_number="3523 IO44",
        problem_code="4086",
    )
    report.device.manufacturer_name = "Jolife AB"
    report.manufacturer = ManufacturerInfo(name="Jolife AB", report_number="26-002332CLY")
    report.reporter = Reporter(
        given_name="Aaron",
        family_name="Artz",
        street="10 N Bemiston",
        city="Clayton",
        state="MO",
        postcode="63105",
        phone="314-290-8488",
        email="aartz@claytonmo.gov",
    )
    report.user_facility = UserFacility(
        report_number="26-002332CLY",
        name="Clayton Fire Department",
        date_sent_to_fda="2026-07-27",
    )
    return report


def _root(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_the_message_is_the_emdr_interaction():
    root = _root(emdr_hl7.to_xml_string(_report()))
    assert root.tag == "{urn:hl7-org:v3}PORR_IN040001UV01"
    assert root.get("ITSVersion") == "XML_1.0"
    assert root.find("v3:versionCode", NS).get("code") == "V3NORMED_2016"
    assert root.find(".//v3:message/v3:interactionId", NS).get("extension") == "PORR_IN04001"
    assert root.find(".//v3:controlActProcess", NS).find("v3:code", NS).get("code") == "PORR_TE040001UV01"


def test_the_batch_names_cdrh_as_receiver_and_the_ocr_pipeline_as_sender():
    root = _root(emdr_hl7.to_xml_string(_report()))
    receiver = root.find(".//v3:receiver//v3:name", NS)
    sender = root.find(".//v3:sender//v3:name", NS)
    assert receiver.text == "CDRH"
    assert sender.text == "CDRH-MW3500A-OCR"
    assert root.find(".//v3:sender//v3:softwareName", NS).text == "emdr_esub"


def test_the_message_carries_the_reported_values_against_their_nci_codes():
    values = flatten(_root(emdr_hl7.to_xml_string(_report())))
    coded = {
        path.rsplit("@", 1)[0]: value for path, value in values.items() if path.endswith("@code")
    }
    assert "C53982" in coded.values()  # device problem code
    assert "C25150" in coded.values()  # age
    assert any(value == "4086" for value in values.values())
    assert any(value == "3523 IO44" for value in values.values())


def test_a_date_of_birth_takes_the_first_of_its_month_as_cdrh_writes_it():
    root = _root(emdr_hl7.to_xml_string(_report()))
    assert root.find(".//v3:subjectAffectedPerson/v3:birthTime", NS).get("value") == "19890101"


def test_an_absent_date_is_written_as_the_placeholder_cdrh_uses():
    report = _report()
    report.patient.date_of_birth = None
    root = _root(emdr_hl7.to_xml_string(report))
    assert root.find(".//v3:subjectAffectedPerson/v3:birthTime", NS).get("value") == "19000101"


def test_the_source_pdf_is_embedded_byte_for_byte():
    content = b"%PDF-1.7\nform bytes\n%%EOF"
    xml = emdr_hl7.to_xml_string(_report(), documents=[("26-002332CL Y.pdf", content)])
    text = _root(xml).find(".//v3:attachment/v3:text", NS)
    assert text.get("representation") == "B64"
    assert base64.b64decode(text.text) == content
    assert text.find("v3:reference", NS).get("value") == "26-002332CL Y.pdf"


def test_documents_are_read_under_their_own_names(tmp_path):
    path = tmp_path / "case.pdf"
    path.write_bytes(b"%PDF-1.4 case")
    assert emdr_hl7.documents_from([str(path)]) == [("case.pdf", b"%PDF-1.4 case")]


def test_the_format_is_selectable_and_recognised():
    assert FORMAT_EMDR_HL7 in FORMATS
    xml = render_xml(_report(), FORMAT_EMDR_HL7)
    assert message_format(xml) == FORMAT_EMDR_HL7
