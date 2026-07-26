"""Serialise a :class:`MedWatchReport` as an FDA MDR (Medical Device Report) XML.

Used for postmarket device 3500A reports (CDRH).  The document follows the
content model of FDA's electronic MDR (eMDR) 3500A submission: one
``<mdrReport>`` per form, with the blocks of the paper form kept as separate
elements (patient, adverse event, suspect device, initial reporter, device
manufacturer, evaluation) and coded values for event type, source and
reportable outcomes.  It is the report *content*; the ESG/AS2 transport
envelope is out of scope.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .e2b_r2 import _digits, _sub
from .models import MedWatchReport

MDR_NAMESPACE = "urn:fda:cdrh:emdr:3500A"

EVENT_TYPE_CODES = [
    (("death", "died", "fatal"), "D", "Death"),
    (("serious injury", "injury", "life threatening", "life-threatening", "hospitalization", "intervention"), "IN", "Serious Injury"),
    (("malfunction",), "M", "Malfunction"),
]

REPORT_SOURCE_CODES: Dict[str, str] = {
    "user facility": "1",
    "importer": "2",
    "distributor": "3",
    "manufacturer": "4",
    "company representative": "5",
    "literature": "6",
    "consumer": "7",
    "health professional": "8",
    "study": "9",
    "other": "10",
}

OUTCOME_CODES: Dict[str, str] = {
    "death": "D",
    "life-threatening": "L",
    "life threatening": "L",
    "hospitalization": "H",
    "disability": "S",
    "congenital anomaly": "C",
    "required intervention": "R",
    "other": "O",
}


def _iso_date(value: Optional[str]) -> Optional[str]:
    digits = _digits(value)
    if not digits:
        return None
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"


def _event_types(report: MedWatchReport) -> List[Dict[str, str]]:
    haystack = " ".join(
        filter(
            None,
            [
                report.manufacturer.adverse_event_type,
                " ".join(report.event.outcomes),
                " ".join(report.event.reactions),
                report.event.event_problem,
            ],
        )
    ).lower()
    found: List[Dict[str, str]] = []
    for keywords, code, label in EVENT_TYPE_CODES:
        if any(keyword in haystack for keyword in keywords):
            found.append({"code": code, "label": label})
    return found or [{"code": "O", "label": "Other"}]


def _report_source_code(source: Optional[str]) -> Optional[str]:
    if not source:
        return None
    text = source.strip().lower()
    for name, code in REPORT_SOURCE_CODES.items():
        if name in text:
            return code
    return REPORT_SOURCE_CODES["other"]


def _yes_no(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip().lower()
    if text.startswith("y") or "returned" in text:
        return "Y"
    if text.startswith("n"):
        return "N"
    return "U"


def build_mdr(
    report: MedWatchReport,
    submitter_id: str = "PHARMAWATCH",
    report_sequence: Optional[str] = None,
) -> ET.Element:
    """Build the ``<mdrReports>`` root element for ``report``."""
    now = datetime.now(timezone.utc)
    mfr = report.manufacturer
    device = report.device

    root = ET.Element(
        "mdrReports",
        {
            "xmlns": MDR_NAMESPACE,
            "submissionDate": now.strftime("%Y-%m-%d"),
            "submitterId": submitter_id,
        },
    )
    mdr = ET.SubElement(root, "mdrReport", {"reportType": "Manufacturer", "center": "CDRH"})

    header = ET.SubElement(mdr, "reportHeader")
    _sub(header, "manufacturerReportNumber", mfr.report_number)
    _sub(header, "reportSequenceNumber", report_sequence or mfr.report_sequence or "Initial")
    _sub(header, "dateOfThisReport", _iso_date(mfr.date_of_this_report or report.event.report_date))
    _sub(header, "dateManufacturerBecameAware", _iso_date(mfr.date_received_by_manufacturer))
    _sub(header, "reportSourceCode", _report_source_code(mfr.report_source))
    _sub(header, "reportSourceText", mfr.report_source)
    _sub(header, "pmaOr510kNumber", mfr.pma_510k_number)
    for event_type in _event_types(report):
        ET.SubElement(header, "adverseEventType", {"code": event_type["code"]}).text = event_type["label"]

    # Block A
    patient = ET.SubElement(mdr, "patientInformation")
    _sub(patient, "patientIdentifier", report.patient.identifier)
    _sub(patient, "patientInitials", report.patient.initials)
    if report.patient.age:
        ET.SubElement(patient, "age", {"unit": (report.patient.age_unit or "year").capitalize()}).text = report.patient.age
    _sub(patient, "dateOfBirth", _iso_date(report.patient.date_of_birth))
    _sub(patient, "sex", report.patient.sex)
    if report.patient.weight_kg:
        ET.SubElement(patient, "weight", {"unit": "kg"}).text = report.patient.weight_kg

    # Block B
    event = ET.SubElement(mdr, "adverseEvent")
    _sub(event, "eventProblemType", report.event.event_problem)
    _sub(event, "dateOfEvent", _iso_date(report.event.event_date))
    _sub(event, "dateOfReport", _iso_date(report.event.report_date))
    outcomes = ET.SubElement(event, "outcomes")
    for outcome in report.event.outcomes:
        code = OUTCOME_CODES.get(outcome.strip().lower())
        attributes = {"code": code} if code else {}
        ET.SubElement(outcomes, "outcome", attributes).text = outcome
    terms = ET.SubElement(event, "eventTerms")
    for term in report.event.reactions:
        ET.SubElement(terms, "eventTerm").text = term
    _sub(event, "eventDescription", report.event.narrative)
    _sub(event, "relevantTestsAndLabData", report.event.relevant_tests)
    _sub(event, "otherRelevantHistory", report.event.other_history)

    # Block D
    suspect = ET.SubElement(mdr, "suspectDevice")
    _sub(suspect, "brandName", device.brand_name)
    _sub(suspect, "commonDeviceName", device.common_name)
    _sub(suspect, "productCode", device.product_code)
    identifiers = ET.SubElement(suspect, "deviceIdentifiers")
    _sub(identifiers, "modelNumber", device.model_number)
    _sub(identifiers, "catalogNumber", device.catalog_number)
    _sub(identifiers, "serialNumber", device.serial_number)
    _sub(identifiers, "lotNumber", device.lot_number)
    _sub(identifiers, "uniqueDeviceIdentifier", device.udi)
    _sub(identifiers, "otherIdentifyingNumber", device.other_identifier)
    _sub(identifiers, "expirationDate", _iso_date(device.expiration))
    manufacturer_of_device = ET.SubElement(suspect, "deviceManufacturer")
    _sub(manufacturer_of_device, "name", device.manufacturer_name)
    _sub(manufacturer_of_device, "address", device.manufacturer_address)
    _sub(suspect, "operatorOfDevice", device.operator)
    _sub(suspect, "implantDate", _iso_date(device.implant_date))
    _sub(suspect, "explantDate", _iso_date(device.explant_date))
    reprocessed = ET.SubElement(suspect, "singleUseDeviceReprocessed")
    reprocessed.set("value", _yes_no(device.single_use_reprocessed) or "U")
    _sub(reprocessed, "reprocessorName", device.reprocessor_name)
    evaluation = ET.SubElement(suspect, "deviceEvaluation")
    _sub(evaluation, "deviceAvailableForEvaluation", device.device_available_for_evaluation)
    _sub(evaluation, "deviceReturnedToManufacturer", _yes_no(device.device_available_for_evaluation))
    _sub(evaluation, "dateDeviceReturned", _iso_date(device.device_returned_date))
    _sub(evaluation, "evaluationConclusion", mfr.evaluation_conclusion)
    _sub(suspect, "concomitantMedicalProducts", device.concomitant_products)

    # Block C (drugs are optional on a device report but preserved when present)
    if report.products:
        products = ET.SubElement(mdr, "suspectProducts")
        for product in report.products:
            node = ET.SubElement(products, "suspectProduct")
            _sub(node, "name", product.name)
            _sub(node, "dose", product.dose)
            _sub(node, "route", product.route)
            _sub(node, "therapyStartDate", _iso_date(product.therapy_start))
            _sub(node, "therapyStopDate", _iso_date(product.therapy_stop))
            _sub(node, "indication", product.indication)
            _sub(node, "lotNumber", product.lot)

    # Block E
    reporter = ET.SubElement(mdr, "initialReporter")
    _sub(reporter, "name", report.reporter.name)
    _sub(reporter, "organization", report.reporter.organization)
    _sub(reporter, "address", report.reporter.address)
    _sub(reporter, "phone", report.reporter.phone)
    _sub(reporter, "email", report.reporter.email)
    _sub(reporter, "occupation", report.reporter.occupation)
    _sub(reporter, "healthProfessional", _yes_no(report.reporter.health_professional))
    _sub(reporter, "alsoSentToFda", _yes_no(report.reporter.initial_reporter_to_fda))

    # Blocks G / H
    submitter = ET.SubElement(mdr, "deviceManufacturerSubmitter")
    _sub(submitter, "name", mfr.name)
    _sub(submitter, "contactOffice", mfr.contact_office)
    _sub(submitter, "address", mfr.address)
    _sub(submitter, "phone", mfr.phone)
    _sub(submitter, "registrationNumber", mfr.registration_number)
    _sub(submitter, "remedialActionTaken", mfr.remedial_action)

    provenance = ET.SubElement(mdr, "extractionProvenance")
    _sub(provenance, "sourceForm", "FDA 3500A (MedWatch)")
    _sub(provenance, "sourcePages", str(report.source_pages or ""))
    _sub(provenance, "ocrEngine", report.ocr_engine)
    _sub(provenance, "extractedAt", now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    return root


def to_xml_string(report: MedWatchReport, **kwargs) -> str:
    """Render ``report`` as a pretty-printed FDA MDR XML document."""
    root = build_mdr(report, **kwargs)
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
