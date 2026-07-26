"""Serialise a :class:`MedWatchReport` as an ICH E2B(R2) ICSR message.

Used for drug / biologic 3500A reports (CDER).  Premarket reports (IND safety
reports) are emitted as study reports (``reporttype`` 2 with ``studytype`` 1),
postmarket reports as spontaneous reports (``reporttype`` 1).
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import STAGE_PREMARKET, MedWatchReport

E2B_DTD = "http://eudravigilance.ema.europa.eu/dtd/icsr21xml.dtd"

ROUTE_CODES: Dict[str, str] = {
    "oral": "048",
    "intravenous": "042",
    "intravenous drip": "041",
    "intramuscular": "030",
    "subcutaneous": "058",
    "topical": "061",
    "transdermal": "062",
    "inhalation": "055",
    "rectal": "054",
    "intrathecal": "037",
    "ophthalmic": "047",
    "unknown": "050",
}

QUALIFICATION_CODES: Dict[str, str] = {
    "physician": "1",
    "pharmacist": "2",
    "other health professional": "3",
    "lawyer": "4",
    "consumer": "5",
}

OUTCOME_TO_SERIOUSNESS = [
    (("death", "died", "fatal"), "seriousnessdeath"),
    (("life-threatening", "life threatening"), "seriousnesslifethreatening"),
    (("hospitalization", "hospitalisation", "hospitalized"), "seriousnesshospitalization"),
    (("disability", "permanent impairment", "disabling"), "seriousnessdisabling"),
    (("congenital anomaly", "birth defect"), "seriousnesscongenitalanomali"),
    (("required intervention", "other serious", "intervention"), "seriousnessother"),
]


def _digits(value: Optional[str]) -> Optional[str]:
    """Convert a date string to the E2B ``CCYYMMDD`` format where possible."""
    if not value:
        return None
    text = value.strip()
    match = re.search(r"(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})", text)
    if match:
        return f"{match.group(1)}{int(match.group(2)):02d}{int(match.group(3)):02d}"
    match = re.search(r"(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})", text)
    if match:
        return f"{match.group(3)}{int(match.group(1)):02d}{int(match.group(2)):02d}"
    for fmt in ("%d %b %Y", "%b %d %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(re.sub(r"[,]", "", text), fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return None


def _sub(parent: ET.Element, tag: str, text: Optional[str]) -> Optional[ET.Element]:
    if text is None or str(text).strip() == "":
        return None
    element = ET.SubElement(parent, tag)
    element.text = str(text).strip()
    return element


def _age_unit_code(unit: Optional[str]) -> str:
    return {
        "decade": "800",
        "year": "801",
        "month": "802",
        "week": "803",
        "day": "804",
        "hour": "805",
    }.get((unit or "year").lower(), "801")


def _sex_code(sex: Optional[str]) -> Optional[str]:
    if not sex:
        return None
    return {"male": "1", "female": "2"}.get(sex.strip().lower())


def _route_code(route: Optional[str]) -> Optional[str]:
    if not route:
        return None
    return ROUTE_CODES.get(route.strip().lower())


def _qualification_code(occupation: Optional[str]) -> str:
    if not occupation:
        return "3"
    text = occupation.strip().lower()
    for name, code in QUALIFICATION_CODES.items():
        if name in text:
            return code
    if any(word in text for word in ("physician", "doctor", "md", "cardiologist", "electrophysiologist", "surgeon")):
        return "1"
    if "nurse" in text or "pharmacist" in text:
        return "3"
    return "3"


def _seriousness(outcomes: List[str]) -> Dict[str, str]:
    flags: Dict[str, str] = {}
    joined = " ; ".join(outcomes).lower()
    for keywords, tag in OUTCOME_TO_SERIOUSNESS:
        if any(keyword in joined for keyword in keywords):
            flags[tag] = "1"
    return flags


def _reaction_outcome_code(outcomes: List[str]) -> str:
    joined = " ".join(outcomes).lower()
    if any(word in joined for word in ("death", "died", "fatal")):
        return "5"
    if "sequelae" in joined:
        return "4"
    if "recovered" in joined or "resolved" in joined:
        return "1"
    if "recovering" in joined or "resolving" in joined:
        return "2"
    return "6"


def build_e2b_r2(
    report: MedWatchReport,
    message_number: Optional[str] = None,
    sender_id: str = "PHARMAWATCH",
    receiver_id: str = "FDACDER",
) -> ET.Element:
    """Build the ``<ichicsr>`` element for ``report``."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d%H%M%S")
    today = now.strftime("%Y%m%d")
    mfr = report.manufacturer
    premarket = report.stage == STAGE_PREMARKET

    root = ET.Element("ichicsr", {"lang": "en"})

    header = ET.SubElement(root, "ichicsrmessageheader")
    _sub(header, "messagetype", "ichicsr")
    _sub(header, "messageformatversion", "2.1")
    _sub(header, "messageformatrelease", "2.0")
    _sub(header, "messagenumb", message_number or f"{sender_id}-{stamp}")
    _sub(header, "messagesenderidentifier", sender_id)
    _sub(header, "messagereceiveridentifier", receiver_id)
    _sub(header, "messagedateformat", "204")
    _sub(header, "messagedate", stamp)

    safety = ET.SubElement(root, "safetyreport")
    _sub(safety, "safetyreportversion", "1")
    _sub(safety, "safetyreportid", report.report_id or f"{sender_id}-{stamp}")
    _sub(safety, "primarysourcecountry", report.reporter.country or "US")
    _sub(safety, "occurcountry", report.reporter.country or "US")
    _sub(safety, "transmissiondateformat", "102")
    _sub(safety, "transmissiondate", today)
    _sub(safety, "reporttype", "2" if premarket else "1")
    _sub(safety, "serious", "1" if report.event.outcomes else "2")
    for tag, value in _seriousness(report.event.outcomes).items():
        _sub(safety, tag, value)
    _sub(safety, "receivedateformat", "102")
    _sub(safety, "receivedate", _digits(mfr.date_received_by_manufacturer) or _digits(report.event.report_date))
    _sub(safety, "receiptdateformat", "102")
    _sub(safety, "receiptdate", _digits(mfr.date_received_by_manufacturer) or _digits(report.event.report_date))
    _sub(safety, "additionaldocument", "2")
    _sub(safety, "fulfillexpeditecriteria", "1")
    _sub(safety, "companynumb", mfr.report_number)
    if premarket:
        _sub(safety, "studytype", "1")
        _sub(safety, "studyname", mfr.study_name)
        _sub(safety, "sponsorstudynumb", mfr.study_number)

    source = ET.SubElement(safety, "primarysource")
    _sub(source, "reportergivename", report.reporter.given_name)
    _sub(source, "reporterfamilyname", report.reporter.family_name)
    _sub(source, "reporterorganization", report.reporter.organization)
    _sub(source, "reportercountry", report.reporter.country or "US")
    _sub(source, "qualification", _qualification_code(report.reporter.occupation))
    _sub(source, "literaturereference", None)

    sender = ET.SubElement(safety, "sender")
    _sub(sender, "sendertype", "1")
    _sub(sender, "senderorganization", mfr.name or sender_id)
    _sub(sender, "senderdepartment", mfr.contact_office)
    _sub(sender, "sendertelephone", mfr.phone)

    receiver = ET.SubElement(safety, "receiver")
    _sub(receiver, "receivertype", "2")
    _sub(receiver, "receiverorganization", receiver_id)

    patient = ET.SubElement(safety, "patient")
    _sub(patient, "patientinitial", report.patient.initials or report.patient.identifier)
    _sub(patient, "patientbirthdateformat", "102" if _digits(report.patient.date_of_birth) else None)
    _sub(patient, "patientbirthdate", _digits(report.patient.date_of_birth))
    _sub(patient, "patientonsetage", report.patient.age)
    _sub(patient, "patientonsetageunit", _age_unit_code(report.patient.age_unit) if report.patient.age else None)
    _sub(patient, "patientweight", report.patient.weight_kg)
    _sub(patient, "patientsex", _sex_code(report.patient.sex))

    outcome_code = _reaction_outcome_code(report.event.outcomes)
    for term in report.event.reactions or ["Unspecified adverse event"]:
        reaction = ET.SubElement(patient, "reaction")
        _sub(reaction, "primarysourcereaction", term)
        _sub(reaction, "reactionmeddraversionpt", "27.0")
        _sub(reaction, "reactionmeddrapt", term)
        _sub(reaction, "reactionstartdateformat", "102" if _digits(report.event.event_date) else None)
        _sub(reaction, "reactionstartdate", _digits(report.event.event_date))
        _sub(reaction, "reactionoutcome", outcome_code)

    if report.event.relevant_tests:
        test = ET.SubElement(patient, "test")
        _sub(test, "testdateformat", "102" if _digits(report.event.event_date) else None)
        _sub(test, "testdate", _digits(report.event.event_date))
        _sub(test, "testname", "Relevant tests / laboratory data")
        _sub(test, "testresult", report.event.relevant_tests)

    for product in report.products:
        drug = ET.SubElement(patient, "drug")
        _sub(drug, "drugcharacterization", "1")
        _sub(drug, "medicinalproduct", product.name)
        _sub(drug, "obtaindrugcountry", report.reporter.country or "US")
        _sub(drug, "drugbatchnumb", product.lot)
        _sub(drug, "drugauthorizationnumb", product.ndc or mfr.nda_ind_number)
        _sub(drug, "drugstructuredosagenumb", product.dose_number)
        _sub(drug, "drugstructuredosageunit", "003" if (product.dose_unit or "").lower() == "mg" else None)
        _sub(drug, "drugdosagetext", product.dose)
        _sub(drug, "drugdosageform", None)
        _sub(drug, "drugadministrationroute", _route_code(product.route))
        _sub(drug, "drugindication", product.indication)
        _sub(drug, "drugstartdateformat", "102" if _digits(product.therapy_start) else None)
        _sub(drug, "drugstartdate", _digits(product.therapy_start))
        _sub(drug, "drugenddateformat", "102" if _digits(product.therapy_stop) else None)
        _sub(drug, "drugenddate", _digits(product.therapy_stop))
        if product.therapy_stop:
            _sub(drug, "actiondrug", "1")
        if product.event_abated_after_stop:
            abated = product.event_abated_after_stop.strip().lower().startswith("y")
            reappeared = (product.event_reappeared_after_reintroduction or "").strip().lower().startswith("y")
            _sub(drug, "drugrecurreadministration", "1" if reappeared else ("2" if abated else "3"))
        if product.active_substance:
            substance = ET.SubElement(drug, "activesubstance")
            _sub(substance, "activesubstancename", product.active_substance)

    narrative = ET.SubElement(safety, "summary")
    _sub(narrative, "narrativeincludeclinical", report.event.narrative or "Not provided.")
    if report.event.other_history:
        _sub(narrative, "reportercomment", report.event.other_history)

    return root


def to_xml_string(report: MedWatchReport, **kwargs) -> str:
    """Render ``report`` as a pretty-printed E2B(R2) XML document."""
    root = build_e2b_r2(report, **kwargs)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<!DOCTYPE ichicsr SYSTEM "{E2B_DTD}">\n'
        f"{body}\n"
    )
