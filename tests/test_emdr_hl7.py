"""The CDRH eMDR HL7 v3 submission message written from a device report."""

import base64
import xml.etree.ElementTree as ET

from medwatch_ocr import emdr_hl7
from medwatch_ocr.models import (
    ConcomitantProduct,
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
    report.patient.deceased_date = None
    root = _root(emdr_hl7.to_xml_string(report))
    assert root.find(".//v3:subjectAffectedPerson/v3:deceasedTime", NS).get("value") == "19000101"


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


def test_every_box_ticked_in_a_block_is_stated_once():
    """B.1, A.5 and B.2 each repeat, one element per box the form ticks."""
    report = _report()
    report.event.report_types = ["Adverse Event", "Product Problem"]
    report.event.outcomes = ["Death", "Hospitalization", "Other serious"]
    report.patient.races = ["Asian", "White"]
    root = _root(emdr_hl7.to_xml_string(report))
    assert [
        code.get("code") for code in root.findall(".//v3:investigationEvent/v3:code", NS)
    ] == ["C53054", "C41331"]
    assert [
        code.get("code") for code in root.findall(".//v3:subjectAffectedPerson/v3:raceCode", NS)
    ] == ["C41260", "C41261"]
    assert [
        block.find("v3:value", NS).get("code")
        for block in root.findall(".//v3:pertinentInformation2/v3:caseSeriousness", NS)
    ] == ["C17649", "C25179", "C28554"]


def test_a_report_without_a_date_of_birth_states_the_date_as_unknown():
    report = _report()
    report.patient.date_of_birth = None
    report.patient.deceased_date = "2026-07-16"
    root = _root(emdr_hl7.to_xml_string(report))
    assert root.find(".//v3:subjectAffectedPerson/v3:birthTime", NS).get("nullFlavor") == "NI"
    assert root.find(".//v3:subjectAffectedPerson/v3:deceasedTime", NS).get("value") == "20260716"


def test_the_health_effect_is_stated_as_a_problem_and_an_impact_code():
    report = _report()
    report.event.patient_problem_code = "1762"
    report.event.patient_impact_code = "1762"
    values = flatten(_root(emdr_hl7.to_xml_string(report)))
    for code in ("C53983", "C122929"):
        prefix = next(path.rsplit("@", 1)[0] for path, value in values.items() if value == code)
        parent = prefix.rsplit("/", 1)[0]
        assert values[f"{parent}/value@code"] == "1762"


def test_each_concomitant_product_is_stated_with_its_therapy_start():
    report = _report()
    report.device.concomitants = [
        ConcomitantProduct(name="Naturalyte Bicarbonate", therapy_start="2024-01-22"),
        ConcomitantProduct(name="Combiset Bloodlines"),
    ]
    root = _root(emdr_hl7.to_xml_string(report))
    products = [
        observation
        for observation in root.findall(".//v3:procedureEvent/v3:pertinentInformation1/v3:observation", NS)
        if observation.find("v3:code", NS).get("code") == "C53630"
    ]
    assert [product.find("v3:value", NS).text for product in products] == [
        "Naturalyte Bicarbonate",
        "Combiset Bloodlines",
    ]
    assert products[0].find("v3:effectiveTime", NS).get("value") == "20240122"
    assert products[1].find("v3:effectiveTime", NS).get("nullFlavor") == "NI"


def test_a_place_the_form_names_is_coded_and_anything_else_keeps_its_text():
    report = _report()
    report.event.location = "Outpatient treatment facility"
    root = _root(emdr_hl7.to_xml_string(report))
    place = root.find(".//v3:locatedEntity/v3:location/v3:code", NS)
    assert place.get("code") == "C53549"
    assert place.find("v3:originalText", NS) is None
    # "Jail" is written against "other", so the message keeps the words too.
    other = _root(emdr_hl7.to_xml_string(_report())).find(".//v3:locatedEntity/v3:location/v3:code", NS)
    assert other.get("code") == "C17649"
    assert other.find("v3:originalText", NS).text == "Jail"


def test_the_reporter_occupation_is_stated_as_its_concept():
    report = _report()
    report.reporter.occupation = "Nurse"
    root = _root(emdr_hl7.to_xml_string(report))
    assert root.find(".//v3:primarySourceReport/v3:author/v3:assignedEntity/v3:code", NS).get("code") == "C20821"
    # An occupation the vocabulary does not list is "other health care professional".
    report.reporter.occupation = "Other Health"
    root = _root(emdr_hl7.to_xml_string(report))
    assert root.find(".//v3:primarySourceReport/v3:author/v3:assignedEntity/v3:code", NS).get("code") == "C53289"


def test_a_manufacturer_box_without_a_street_still_splits_into_town_and_state():
    address = emdr_hl7.parse_address("Fresenius Medical Care Waltham, MA 02451 USA")
    assert (address.name, address.city, address.state, address.postcode, address.country) == (
        "Fresenius Medical Care",
        "Waltham",
        "MA",
        "02451",
        "USA",
    )


def _facility_number(xml: str) -> str:
    holder = _root(xml).find(".//v3:pertinentInformation1/v3:secondaryCaseNotification/v3:id", NS)
    return holder.get("extension") or f"nullFlavor={holder.get('nullFlavor')}"


def _facility_email(xml: str) -> str:
    contact = _root(xml).find(
        ".//v3:pertinentInformation1/v3:secondaryCaseNotification/v3:author/v3:assignedEntity"
        "/v3:assignedOrganization/v3:contactParty/v3:contactPerson",
        NS,
    )
    return [each.get("value") for each in contact.findall("v3:telecom", NS) if each.get("value").startswith("mailto:")][0]


def test_a_report_number_already_written_fdas_way_is_sent_as_it_stands():
    facility = UserFacility(report_number="1234567890-2026-00001")
    assert emdr_hl7.uf_report_number(facility, "9999999") == "1234567890-2026-00001"


def test_the_report_number_is_rebuilt_from_the_facilitys_registration_number():
    facility = UserFacility(report_number="26-002332CLY", date_sent_to_fda="2026-07-27")
    assert emdr_hl7.uf_report_number(facility, "1825400000") == "1825400000-2026-02332"
    # A 7-digit CFN stands in for the FEI, and punctuation in it is ignored.
    assert emdr_hl7.uf_report_number(facility, "182-5400") == "1825400-2026-02332"


def test_a_report_number_without_its_own_year_takes_the_year_it_was_sent():
    facility = UserFacility(report_number="002332", date_sent_to_fda="2025-01-04")
    assert emdr_hl7.uf_report_number(facility, "1825400000") == "1825400000-2025-02332"


def test_the_number_on_the_form_is_kept_where_no_registration_number_is_configured(monkeypatch):
    monkeypatch.delenv(emdr_hl7.UF_FEI_ENV, raising=False)
    facility = UserFacility(report_number="26-002332CLY")
    # Neither configured nor of a registration number's length: the form's own
    # number is all there is to send.
    assert emdr_hl7.uf_report_number(facility) == "26-002332CLY"
    assert emdr_hl7.uf_report_number(facility, "12345") == "26-002332CLY"
    assert emdr_hl7.uf_report_number(UserFacility()) is None


def test_the_registration_number_reaches_the_message_from_the_environment(monkeypatch):
    monkeypatch.setenv(emdr_hl7.UF_FEI_ENV, "1825400000")
    assert _facility_number(emdr_hl7.to_xml_string(_report())) == "1825400000-2026-02332"
    # An argument, from the ini or the command line, is preferred to it.
    assert _facility_number(emdr_hl7.to_xml_string(_report(), uf_fei="1234567")) == "1234567-2026-02332"
    assert _facility_number(render_xml(_report(), FORMAT_EMDR_HL7, uf_fei="1234567")) == "1234567-2026-02332"


def test_the_facility_contact_is_reachable_by_e_mail():
    report = _report()
    # Block F has no e-mail box of its own, so the reporter's stands in for it.
    assert _facility_email(emdr_hl7.to_xml_string(report)) == "mailto:aartz@claytonmo.gov"
    report.user_facility.email = "safety@claytonmo.gov"
    assert _facility_email(emdr_hl7.to_xml_string(report)) == "mailto:safety@claytonmo.gov"
