"""CDRH post-market MDRs in FDA's eMDR HL7 v3 submission format.

FDA's eMDR gateway takes a ``PORR_IN040001UV01`` message (Con170227.xsd): the
Form 3500A blocks become an HL7 v3 RIM graph - ``investigationEvent`` ->
``trigger/reaction`` for the event and the patient, ``pertinentInformation1``
for the user facility's and the manufacturer's notifications,
``pertainsTo/procedureEvent`` for the device - with every value carried in an
attribute against an NCI Thesaurus code, and the source PDF embedded base64 in
``message/attachment``.

Conventions of the eMDR messages produced by CDRH's own 3500A OCR pipeline are
reproduced here deliberately, including the ones a fresh implementation would do
differently: a date of birth is written with its day forced to the first of the
month, an absent date is written ``19000101`` rather than a null flavour, and a
structural element the form has no value for is written empty rather than
omitted.
"""

from __future__ import annotations

import base64
import os
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from .models import MedWatchReport

NS = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA = "urn:hl7-org:v3 ../../../../eMDRHL7/Impl_Files/Con170227.xsd"
VERSION_CODE = "V3NORMED_2016"
INTERACTION_ID = "PORR_IN04001"
TRIGGER_EVENT = "PORR_TE040001UV01"
SOFTWARE_NAME = "emdr_esub"
RECEIVER_ORGANISATION = "CDRH"
SENDER_ORGANISATION = "CDRH-MW3500A-OCR"

CS_NCI = "2.16.840.1.113883.3.26.1.1"  # NCI Thesaurus, as eMDR uses it
CS_FDA = "2.16.840.1.113883.3.24"  # FDA-assigned identifiers
CS_HL7_INTERACTION = "2.16.840.1.113883.1.6"

# The date an absent date is written as, and the length the message truncates a
# street address to.
ABSENT_DATE = "19000101"
STREET_LIMIT = 30

# NCI codes of the concepts the message states, by what they describe.
CODE_REPORT = ("C53054", "Adverse_Event_Or_Product_Problem_Report")
CODE_SEX = ("C20197", "Sex")
CODE_GENDER = ("C17357", "Gender")
CODE_AGE = ("C25150", "Age")
CODE_WEIGHT = ("C25208", "Weight")
CODE_TEST_RESULT = ("C36292", "Test_Result")
CODE_HISTORY = ("C53263", "Other_Personal_Medical_History")
CODE_RACE = ("C16352", "Race")
CODE_LOCATION = ("C17649", "Location")
CODE_OCCUPATION = ("C53289", "Occupation")
CODE_REPORT_TYPE = ("C53620", "Type_of_Report")
CODE_REPORT_RECEIVER = ("C17237", "Report_Receiver")
CODE_REPORTER_TYPE = ("C53567", "Type_of_Reporter")
CODE_OUTCOME = ("C49489", "Adverse_Event_Outcome")
CODE_MANUFACTURER_TYPE = ("C53616", "Type_of_Manufacturer")
CODE_REPROCESSOR_TYPE = ("C53614", "Type_of_Manufacturer")
CODE_DEVICE_TYPE = (None, "Type_of_Device")
CODE_EVALUATION_RESULT = ("C53985", "Evaluation_Result_Code")
CODE_EVALUATION_CONCLUSION = ("C53986", "Evaluation_Conclusion_Code")
CODE_EVALUATION_METHOD = ("C53984", "Evaluation_Method_Code")
CODE_DEVICE_AVAILABLE = ("C53449", "Device_available_for_evaluation")
CODE_DEVICE_AGE = ("C53451", "Approximate_Age_of_Device")
CODE_SINGLE_USE_LABEL = ("C53602", "Device_Labeled_for_single_use")
CODE_CORRECTIVE_ACTION = ("C53619", "Corrective_Action_Number")
CODE_DEVICE_EVALUATED = ("C53629", "Device_Evaluated_By_Manufacturer")
CODE_DEVICE_PROBLEM = ("C53982", "Device_Problem_Code")
CODE_RELATED_REPORTS = ("C103162", "Related_Reported_Numbers")
CODE_DEVICE_OPERATOR = ("C53287", "Device_Operator_Code")
CODE_REPROCESSED = ("C53563", "Single-Use_Device_Reprocessed_and_Reused_on_Patient")
CODE_THIRD_PARTY = ("C85488", "Device_Serviced_By_Third_Party")
CODE_EXEMPTION = ("F77776", "Exemption_No")

# B.2: the outcome the report ticks, as CDRH codes it.  An outcome with no code
# here is stated with an empty code, the way the messages state an unknown one,
# rather than with a guessed concept id.
OUTCOME_CODES = {"Death": "C28554"}

AGE_UNITS = {"Year": "YR", "Month": "MO", "Week": "WK", "Day": "DY", "Hour": "HR"}

MONTHS = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04", "MAY": "05", "JUN": "06",
    "JUL": "07", "AUG": "08", "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

STATE_CODES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "PUERTO RICO": "PR", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI",
    "WYOMING": "WY",
}

# ISO 3166 alpha-3 for the countries the forms name, including the spellings
# they are written with.
COUNTRY_CODES = {
    "UNITED STATES": "USA", "UNITED STATES OF AMERICA": "USA", "US": "USA", "USA": "USA",
    "CANADA": "CAN", "MEXICO": "MEX", "UNITED KINGDOM": "GBR", "GREAT BRITAIN": "GBR",
    "IRELAND": "IRL", "FRANCE": "FRA", "GERMANY": "DEU", "SPAIN": "ESP", "ITALY": "ITA",
    "NETHERLANDS": "NLD", "BELGIUM": "BEL", "DENMARK": "DNK", "NORWAY": "NOR",
    "SWEDEN": "SWE", "SWEEDEN": "SWE", "FINLAND": "FIN", "SWITZERLAND": "CHE",
    "AUSTRIA": "AUT", "POLAND": "POL", "CZECH REPUBLIC": "CZE", "JAPAN": "JPN",
    "CHINA": "CHN", "INDIA": "IND", "AUSTRALIA": "AUS", "NEW ZEALAND": "NZL",
    "BRAZIL": "BRA", "ISRAEL": "ISR", "SOUTH KOREA": "KOR", "KOREA": "KOR",
    "SINGAPORE": "SGP", "MALAYSIA": "MYS", "TURKEY": "TUR",
}

_POSTAL_CITY_RE = re.compile(r"^(?P<zip>(?:[A-Z]{1,2}-\s?)?\d{3}\s?\d{2}|\d{5}(?:-\d{4})?)\s+(?P<city>.+)$")
_CITY_POSTAL_RE = re.compile(r"^(?P<city>[^,]+?)[, ]+(?P<state>[A-Za-z]{2})?\.?\s*(?P<zip>\d{5}(?:-\d{4})?)$")


@dataclass
class PostalAddress:
    """A party's name and address, as the form prints them in one box."""

    name: Optional[str] = None
    street: Optional[str] = None
    street_full: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None


def parse_address(value: Optional[str]) -> PostalAddress:
    """Split a one-box "name, street, postcode city, country" into its parts."""
    if not value:
        return PostalAddress()
    segments = [part.strip() for part in re.split(r"\s*,\s*", re.sub(r"\s+", " ", value.strip())) if part.strip()]
    if not segments:
        return PostalAddress()
    address = PostalAddress(name=segments[0])
    rest = segments[1:]
    if rest and rest[-1].upper() in COUNTRY_CODES:
        address.country = COUNTRY_CODES[rest.pop().upper()]
    for index, segment in enumerate(rest):
        postal = _POSTAL_CITY_RE.match(segment)
        if postal:
            address.postcode, address.city = postal.group("zip"), postal.group("city")
            rest = rest[:index]
            break
        town = _CITY_POSTAL_RE.match(segment)
        if town:
            address.city, address.postcode = town.group("city"), town.group("zip")
            address.state = (town.group("state") or "").upper() or None
            rest = rest[:index]
            break
    address.street_full = ", ".join(rest) or None
    address.street = rest[0] if rest else None
    return address


def _element(parent: Optional[ET.Element], tag: str, **attributes) -> ET.Element:
    """An element whose attributes keep the message's order, with ``xsi:`` expanded.

    An attribute given as ``None`` is left out; one given as ``""`` is kept, as
    the eMDR messages state an unknown code that way.
    """
    clean = {
        ("xsi:" + key[4:] if key.startswith("xsi_") else key): str(value)
        for key, value in attributes.items()
        if value is not None
    }
    if parent is None:
        return ET.Element(tag, clean)
    return ET.SubElement(parent, tag, clean)


def _text(parent: ET.Element, tag: str, value: Optional[str], **attributes) -> ET.Element:
    element = _element(parent, tag, **attributes)
    if value:
        element.text = value
    return element


def _coded(parent: ET.Element, tag: str, code: Tuple[Optional[str], str], system: str = CS_NCI, **extra) -> ET.Element:
    return _element(parent, tag, code=code[0], codeSystem=system, codeSystemName=code[1], **extra)


def _date(value: Optional[str], default: Optional[str] = None) -> Optional[str]:
    """A form date (``19-Jul-2026``, ``2026-07-19``) as the message's ``YYYYMMDD``."""
    if not value:
        return default
    text = value.strip()
    match = re.match(r"(\d{1,2})[-/\s]([A-Za-z]{3})[A-Za-z]*[-/\s](\d{4})", text)
    if match:
        month = MONTHS.get(match.group(2).upper())
        if month:
            return f"{match.group(3)}{month}{int(match.group(1)):02d}"
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return digits
    return default


def _birth_date(value: Optional[str]) -> Optional[str]:
    """A date of birth, whose day the eMDR pipeline writes as the first of the month."""
    stamp = _date(value)
    return stamp[:6] + "01" if stamp else None


def _number(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    return match.group() if match else None


def _phone(value: Optional[str]) -> str:
    """``314-290-8488`` as the message's ``tel:+1(314)290-8488``."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"tel:+1({digits[:3]}){digits[3:6]}-{digits[6:]}"
    return f"tel:{value.strip()}" if value else "tel:"


def _telecoms(parent: ET.Element, phone: Optional[str] = None, email: Optional[str] = None) -> None:
    """The three telecoms every eMDR party carries, in their fixed order."""
    _element(parent, "telecom", value=_phone(phone) if phone else "tel:")
    _element(parent, "telecom", value="fax:")
    _element(parent, "telecom", value=f"mailto:{email}" if email else "mailto:")


def _person_name(parent: ET.Element, given: Optional[str], family: Optional[str]) -> None:
    name = _element(parent, "name")
    _element(name, "prefix")
    _text(name, "given", given)
    _element(name, "given")
    _text(name, "family", family)


def _state(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip()
    return STATE_CODES.get(text.upper(), text.upper() if len(text) == 2 else None)


def _country(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return COUNTRY_CODES.get(re.sub(r"\s+", " ", value.strip()).upper())


def _observation(parent: ET.Element, code: Tuple[Optional[str], str], system: str = CS_NCI) -> ET.Element:
    holder = _element(parent, "subjectOf")
    observation = _element(holder, "observation", moodCode="EVN")
    _coded(observation, "code", code, system)
    return observation


def _device_observation(parent: ET.Element, code: Tuple[Optional[str], str], name_first: bool = True) -> ET.Element:
    holder = _element(parent, "subjectOf")
    observation = _element(holder, "deviceObservation")
    if name_first:
        _element(observation, "code", code=code[0], codeSystemName=code[1], codeSystem=CS_NCI)
    else:
        _coded(observation, "code", code)
    return observation


def _boolean(parent: ET.Element, value: Optional[str]) -> None:
    """A ``BL`` value from a Yes/No box, ``nullFlavor="NI"`` when neither is ticked."""
    if not value:
        _element(parent, "value", xsi_type="BL", nullFlavor="NI")
        return
    _element(parent, "value", xsi_type="BL", value="true" if value.strip().lower().startswith(("y", "true", "return")) else "false")


def _patient(parent: ET.Element, report: MedWatchReport) -> None:
    person = _element(parent, "subjectAffectedPerson")
    _text(person, "name", report.patient.identifier or report.patient.initials)
    _coded(person, "administrativeGenderCode", CODE_SEX)
    _element(person, "birthTime", value=_birth_date(report.patient.date_of_birth) or ABSENT_DATE)
    _element(person, "deceasedTime", value=ABSENT_DATE)
    _coded(person, "raceCode", CODE_RACE)


def _patient_observations(parent: ET.Element, report: MedWatchReport) -> None:
    _observation(parent, CODE_GENDER)
    _element(_observation(parent, CODE_GENDER), "value", xsi_type="ED")

    age = _observation(parent, CODE_AGE)
    unit = AGE_UNITS.get(report.patient.age_unit or "Year", "YR")
    _element(age, "value", xsi_type="PQ", unit=unit, value=_number(report.patient.age))

    weight = _observation(parent, CODE_WEIGHT)
    _element(
        weight,
        "value",
        xsi_type="PQ",
        unit=report.patient.weight_unit or "lbs",
        value=_number(report.patient.weight) or _number(report.patient.weight_kg),
    )

    tests = _observation(parent, CODE_TEST_RESULT, system="NCI")
    _text(tests, "value", report.event.relevant_tests, mediaType="text/plain", xsi_type="ED")

    history = _observation(parent, CODE_HISTORY)
    _text(history, "value", report.event.other_history, mediaType="text/plain", xsi_type="ED")


def _primary_source(parent: ET.Element, report: MedWatchReport) -> None:
    """E: the person who reported the event, under the reaction's source report."""
    source = _element(_element(parent, "pertinentInformation"), "primarySourceReport")
    _element(source, "id", nullFlavor="ASKU")
    _element(source, "code", nullFlavor="ASKU")
    receiver = _element(source, "receiver", negationInd="false")
    _element(receiver, "time", nullFlavor="NI")
    _text(_element(_element(receiver, "assignedEntity"), "assignedOrganization"), "name", "FDA")

    entity = _element(_element(source, "author"), "assignedEntity")
    _coded(entity, "code", CODE_OCCUPATION)
    person = _element(entity, "assignedPerson")
    _person_name(person, report.reporter.given_name, report.reporter.family_name)
    _telecoms(person, report.reporter.phone, report.reporter.email)
    reporter = report.reporter
    parsed = parse_address(reporter.address) if not reporter.street else PostalAddress()
    addr = _element(person, "addr")
    _text(addr, "streetAddressLine", reporter.street or parsed.street)
    _element(addr, "streetAddressLine")
    _text(addr, "city", reporter.city or parsed.city)
    _text(addr, "state", _state(reporter.state) or parsed.state)
    _text(addr, "postalCode", reporter.postcode or parsed.postcode)
    _text(addr, "country", _country(reporter.country) or parsed.country)
    _element(_element(entity, "representedOrganization"), "name")


def _facility_notification(parent: ET.Element, report: MedWatchReport) -> None:
    """F: the user facility's report of the event to FDA."""
    facility = report.user_facility
    holder = _element(parent, "pertinentInformation1")
    _element(holder, "sequenceNumber", nullFlavor="NI")
    notification = _element(holder, "secondaryCaseNotification")
    if facility.report_number:
        _element(notification, "id", assigningAuthorityName="FDA", extension=facility.report_number, root=CS_FDA)
    else:
        _element(notification, "id", nullFlavor="NA")
    _coded(notification, "code", CODE_REPORT_TYPE)
    _element(notification, "effectiveTime", value=_date(facility.date_aware))

    receiver = _element(notification, "receiver", negationInd="false")
    time = _element(receiver, "time")
    sent = _date(facility.date_sent_to_fda)
    if sent:
        _element(time, "low", value=sent)
    else:
        _element(time, "low", nullFlavor="NI")
    _element(time, "high", nullFlavor="NI")
    _coded(_element(_element(receiver, "assignedEntity"), "assignedOrganization"), "code", CODE_REPORT_RECEIVER)

    entity = _element(_element(notification, "author"), "assignedEntity")
    _coded(entity, "code", CODE_REPORTER_TYPE)
    organisation = _element(entity, "assignedOrganization")
    _element(organisation, "name", nullFlavor="NA")
    addr = _element(organisation, "addr")
    _text(addr, "additionalLocator", facility.name)
    _text(addr, "streetAddressLine", facility.street)
    _element(addr, "streetAddressLine")
    _text(addr, "city", facility.city)
    _text(addr, "state", _state(facility.state))
    _text(addr, "country", _country(facility.country))
    _text(addr, "postalCode", facility.postcode)
    contact = _element(_element(organisation, "contactParty"), "contactPerson")
    _person_name(contact, facility.contact_given_name, facility.contact_family_name)
    _telecoms(contact, facility.phone)


def _manufacturer_notification(parent: ET.Element, report: MedWatchReport, address: PostalAddress) -> None:
    """F.6: whether the facility also told the manufacturer, and who that is."""
    notification = _element(_element(parent, "pertinentInformation1"), "secondaryCaseNotification")
    _element(notification, "id", nullFlavor="NA")
    _element(notification, "code", nullFlavor="NI")
    told = (report.manufacturer.report_sent_to_manufacturer or "").strip().lower().startswith("y")
    receiver = _element(notification, "receiver", negationInd="false" if told else "true")
    time = _element(receiver, "time")
    _element(time, "low", nullFlavor="NI")
    _element(time, "high")
    organisation = _element(_element(receiver, "assignedEntity"), "assignedOrganization")
    _text(organisation, "name", address.name)
    _telecoms(organisation)
    addr = _element(organisation, "addr")
    _element(addr, "additionalLocator")
    _text(addr, "streetAddressLine", (address.street_full or "")[:STREET_LIMIT] or None)
    _element(addr, "streetAddressLine")
    _text(addr, "city", address.city)
    _text(addr, "state", address.state)
    _text(addr, "country", address.country)
    _text(addr, "postalCode", address.postcode)


def _seriousness(parent: ET.Element, report: MedWatchReport) -> None:
    seriousness = _element(_element(parent, "pertinentInformation2"), "caseSeriousness")
    _coded(seriousness, "code", CODE_OUTCOME)
    outcome = next((OUTCOME_CODES.get(name, "") for name in report.event.outcomes), "")
    _element(seriousness, "value", xsi_type="CE", code=outcome, codeSystem=CS_NCI)


def _manufacturer(parent: ET.Element, report: MedWatchReport, address: PostalAddress) -> None:
    """D.6/H: the device's manufacturer and the evaluation it reports."""
    product = _element(parent, "asManufacturedProduct")
    _element(product, "id", nullFlavor="NI")
    _element(product, "code", code=re.sub(r"\s+", "", report.device.udi or ""))
    manufacturer = _element(product, "manufacturerOrReprocessor")
    _coded(manufacturer, "code", CODE_MANUFACTURER_TYPE)
    _text(manufacturer, "name", address.name)
    _telecoms(manufacturer, report.manufacturer.phone)
    addr = _element(manufacturer, "addr")
    _text(addr, "streetAddressLine", address.street)
    _element(addr, "streetAddressLine")
    _text(addr, "city", address.city)
    _text(addr, "state", address.state)
    _text(addr, "postalCode", address.postcode)
    _text(addr, "country", address.country)

    investigation = _element(
        _element(_element(manufacturer, "asRole"), "performance"), "investigationEvent"
    )
    for wrapper, tag, code in (
        ("component1", "evaluationResult", CODE_EVALUATION_RESULT),
        ("component2", "evaluationConclusion", CODE_EVALUATION_CONCLUSION),
        ("component3", "evaluationMethod", CODE_EVALUATION_METHOD),
    ):
        evaluation = _element(_element(investigation, wrapper), tag)
        _coded(evaluation, "code", code)
        _element(evaluation, "value", xsi_type="CE", code="", codeSystem=CS_NCI)

    for last in (False, True):
        party = _element(manufacturer, "contactParty")
        addr = _element(party, "addr")
        _element(addr, "additionalLocator")
        _element(addr, "streetAddressLine")
        _element(addr, "streetAddressLine")
        _element(addr, "city")
        _element(addr, "state")
        _element(addr, "postalCode")
        _element(addr, "country")
        _telecoms(party)
        contact = _element(party, "contactManufacturerContact")
        if not last:
            _person_name(contact, None, None)

    # H.1: the reprocessor block, which a report that names none states empty.
    reprocessed = _element(parent, "asManufacturedProduct")
    _element(reprocessed, "id", nullFlavor="NI")
    _element(reprocessed, "code", code="")
    reprocessor = _element(reprocessed, "manufacturerOrReprocessor")
    _coded(reprocessor, "code", CODE_REPROCESSOR_TYPE)
    _text(reprocessor, "name", report.device.reprocessor_name)
    _telecoms(reprocessor)
    addr = _element(reprocessor, "addr")
    for tag in ("streetAddressLine", "streetAddressLine", "city", "state", "postalCode", "country"):
        _element(addr, tag)


def _device(parent: ET.Element, report: MedWatchReport, address: PostalAddress) -> None:
    """D/F: the suspect device, its model and what is known about it."""
    procedure = _element(parent, "procedureEvent")
    _element(procedure, "code", nullFlavor="ASKU")
    outer = _element(_element(procedure, "device"), "identifiedDevice")
    _element(outer, "id", nullFlavor="NI")
    device = _element(outer, "identifiedDevice")
    if report.device.serial_number:
        _element(device, "id", extension=report.device.serial_number)
    else:
        _element(device, "id", nullFlavor="NI")
    _element(device, "existenceTime", nullFlavor="NI")
    _text(device, "lotNumberText", report.device.lot_number, mediaType="text/plain")
    expiry = _date(report.device.expiration)
    if expiry:
        _element(device, "expirationTime", value=expiry)
    else:
        _element(device, "expirationTime", nullFlavor="NI")
    _manufacturer(device, report, address)

    model = _element(_element(device, "inventoryItem"), "manufacturedDeviceModel")
    if report.device.model_number:
        _element(model, "id", extension=report.device.model_number)
    else:
        _element(model, "id", nullFlavor="NI")
    code = _coded(model, "code", CODE_DEVICE_TYPE)
    _text(code, "originalText", report.device.common_name)
    _text(model, "manufacturerModelName", report.device.brand_name)
    for identifier in (report.device.product_code, report.manufacturer.pma_510k_number):
        regulated = _element(model, "asRegulatedProduct")
        if identifier:
            _element(regulated, "id", extension=identifier)
        else:
            _element(regulated, "id", nullFlavor="NI")

    available = _device_observation(outer, CODE_DEVICE_AVAILABLE, name_first=False)
    _element(available, "effectiveTime", value=_date(report.device.device_returned_date, ABSENT_DATE))
    _boolean(available, report.device.device_available_for_evaluation)

    age = _device_observation(outer, CODE_DEVICE_AGE)
    years = _number(report.device.age)
    _element(
        age,
        "value",
        xsi_type="PQ",
        value=f"{float(years):.1f}" if years else None,
        unit=AGE_UNITS.get(report.device.age_unit or "Year", "YR"),
    )

    _boolean(_device_observation(outer, CODE_SINGLE_USE_LABEL), report.device.labeled_single_use)
    _text(
        _device_observation(outer, CODE_CORRECTIVE_ACTION),
        "value",
        report.manufacturer.corrective_action_number,
        xsi_type="ED",
        mediaType="text/plain",
    )
    _boolean(_device_observation(outer, CODE_DEVICE_EVALUATED), report.device.evaluated_by_manufacturer)

    problem = _device_observation(outer, CODE_DEVICE_PROBLEM)
    _element(problem, "value", xsi_type="CE", code=report.device.problem_code or "")

    related = _device_observation(outer, CODE_RELATED_REPORTS)
    _text(related, "value", report.manufacturer.related_report_numbers, xsi_type="ED", mediaType="text/plain")

    _coded(_element(_element(procedure, "authorOrPerformer", typeCode="AUT"), "assignedEntity"), "code", CODE_DEVICE_OPERATOR)

    for code, value in (
        (CODE_REPROCESSED, report.device.single_use_reprocessed),
        (CODE_THIRD_PARTY, report.device.serviced_by_third_party),
    ):
        observation = _element(_element(procedure, "pertinentInformation1"), "observation", moodCode="EVN")
        _coded(observation, "code", code)
        _boolean(observation, value)

    for wrapper, tag, date in (
        ("component1", "implantation", report.device.implant_date),
        ("component2", "explantation", report.device.explant_date),
    ):
        event = _element(_element(procedure, wrapper), tag)
        stamp = _date(date)
        if stamp:
            _element(event, "effectiveTime", value=stamp)
        else:
            _element(event, "effectiveTime", nullFlavor="NI")


def _attachment(parent: ET.Element, documents: Sequence[Tuple[str, bytes]]) -> None:
    """The source form, embedded base64 in 76-character lines, as eMDR sends it."""
    for name, content in documents:
        attachment = _element(parent, "attachment")
        _element(attachment, "id", nullFlavor="NA")
        text = _element(attachment, "text", representation="B64", mediaType="text/plain")
        encoded = base64.b64encode(content).decode("ascii")
        text.text = "\r\n".join(encoded[index:index + 76] for index in range(0, len(encoded), 76)) + "\r\n"
        _element(text, "reference", value=name)


def to_xml_string(
    report: MedWatchReport,
    documents: Sequence[Tuple[str, bytes]] = (),
    created: Optional[datetime] = None,
    message_id: Optional[str] = None,
) -> str:
    """Serialise ``report`` as FDA's eMDR ``PORR_IN040001UV01`` submission message."""
    moment = created or datetime.now()
    today = moment.strftime("%Y%m%d")

    root = _element(None, "PORR_IN040001UV01", ITSVersion="XML_1.0")
    root.set("xmlns", NS)
    root.set("xmlns:xsi", XSI)
    root.set("xsi:schemaLocation", SCHEMA)
    _element(
        root,
        "id",
        assigningAuthorityName="MessageSender",
        extension=message_id or uuid.uuid4().hex,
        root="1.1",
    )
    _element(root, "creationTime", value=today)
    _element(root, "responseModeCode")
    _element(root, "versionCode", code=VERSION_CODE)
    _element(root, "interactionId")
    _element(root, "batchTotalNumber", value="1")

    for tag, organisation, software in (
        ("receiver", RECEIVER_ORGANISATION, None),
        ("sender", SENDER_ORGANISATION, SOFTWARE_NAME),
    ):
        party = _element(root, tag)
        _element(party, "telecom")
        device = _element(party, "device")
        _element(device, "id", nullFlavor="NA")
        if software:
            _text(device, "softwareName", software)
        represented = _element(_element(device, "asAgent"), "representedOrganization")
        _element(represented, "id", nullFlavor="NA")
        _text(represented, "name", organisation)

    message = _element(root, "message")
    _element(message, "id", extension="1")
    _element(message, "creationTime", nullFlavor="NA", value=today)
    _element(
        message,
        "interactionId",
        root=CS_HL7_INTERACTION,
        extension=INTERACTION_ID,
        assigningAuthorityName="HL7",
    )
    for tag in ("processingCode", "processingModeCode", "acceptAckCode"):
        _element(message, tag, nullFlavor="NA")
    for tag in ("receiver", "sender"):
        party = _element(message, tag)
        _element(party, "telecom")
        _element(_element(party, "device"), "id", nullFlavor="NA")
    _attachment(message, documents)

    control = _element(message, "controlActProcess", moodCode="EVN")
    _element(control, "code", code=TRIGGER_EVENT, codeSystem="HL7", codeSystemName="HL7 Trigger Event Id")
    _element(control, "effectiveTime", value=_date(report.event.report_date, today))

    investigation = _element(_element(control, "subject"), "investigationEvent")
    _element(investigation, "id", assigningAuthorityName="FDA", root=CS_FDA, nullFlavor="NI")
    _coded(investigation, "code", CODE_REPORT)
    # The event's own text stays empty: the eMDR messages carry the narrative on
    # the reaction and state block H's additional comments nowhere.
    _element(investigation, "text", mediaType="text/plain")
    _element(investigation, "statusCode")
    _element(investigation, "activityTime")
    _element(investigation, "availabilityTime")
    _element(_element(investigation, "authorOrPerformer", typeCode="AUT"), "assignedEntity")

    reaction = _element(_element(investigation, "trigger"), "reaction")
    _text(reaction, "text", report.event.narrative, mediaType="text/plain")
    _text(reaction, "term", "; ".join(report.event.reactions) or None, mediaType="text/plain")
    _element(reaction, "effectiveTime", value=_date(report.event.event_date))

    subject = _element(_element(reaction, "subject"), "investigativeSubject")
    _patient(subject, report)
    _patient_observations(subject, report)

    location = _element(_element(_element(reaction, "location"), "locatedEntity"), "location")
    code = _coded(location, "code", CODE_LOCATION)
    _text(code, "originalText", report.event.location)
    _primary_source(reaction, report)

    address = parse_address(report.device.manufacturer_address or report.device.manufacturer_name)
    _facility_notification(investigation, report)
    _manufacturer_notification(investigation, report, address)
    _seriousness(investigation, report)
    _device(_element(investigation, "pertainsTo"), report, address)
    _element(
        _element(_element(_element(investigation, "component"), "adverseEventAssessment"), "subject1"),
        "primaryRole",
    )

    issue = _element(_element(control, "reasonOf"), "detectedIssueEvent")
    _coded(issue, "code", CODE_EXEMPTION, system=CS_FDA)
    _element(issue, "value", xsi_type="CE", code=report.manufacturer.exemption_number or "", codeSystem=CS_FDA)

    ET.indent(root, space="   ")
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="unicode") + "\n"


def documents_from(paths: Iterable[str], data: Optional[Mapping[str, bytes]] = None) -> Sequence[Tuple[str, bytes]]:
    """Read ``paths`` into the (filename, bytes) pairs the message embeds."""
    pairs = []
    for path in paths:
        name = os.path.basename(path)
        content = data[name] if data and name in data else open(path, "rb").read()
        pairs.append((name, content))
    return pairs
