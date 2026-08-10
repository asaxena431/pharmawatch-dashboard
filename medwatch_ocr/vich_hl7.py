"""VICH GL42 adverse event reports in FDA CVM's HL7 v3 submission format.

CVM's electronic submission gateway takes the VICH AER 1.0.0 message
(``MCCI_IN200100UV01`` carrying a ``PORR_IN049006UV``), where every GL42 data
element is an HL7 v3 act with an NCI/VICH code: the species, gender, route,
dosage form and outcome are coded values, the animal's weight and age are
``IVL_PQ`` intervals, and each attachment is embedded base64 in a
``reference/document``.

The serialiser writes that message from a :class:`~medwatch_ocr.models.VeterinaryReport`.
Codes the source document does not carry (VeDDRA reaction codes, breed codes,
ATCvet, ingredient UNII, NDC) are written ``nullFlavor="NI"`` with the reported
wording in ``originalText`` rather than guessed.
"""

from __future__ import annotations

import base64
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from .models import VeterinaryReport

NS = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA = "urn:hl7-org:v3 http://www.accessdata.fda.gov/icsr/schema/cvm/schemas/vich/multicacheschemas/MCCI_IN200100UV01.xsd"
VERSION_CODE = "VICHAER1.0.0"
PROFILE_ID = "AES.FDA.SRPRGL42.M.V1.ACCOUNT.AE"

# Code systems the message uses, by the concept they code.
CS_VICH = "2.16.840.1.113883.13.206"  # VICH AER data elements
CS_SPECIES = "2.16.840.1.113883.4.341"
CS_BREED = "2.16.840.1.113883.4.342"
CS_GENDER = "2.16.840.1.113883.13.198"
CS_REPRODUCTIVE = "2.16.840.1.113883.13.199"
CS_METHOD = "2.16.840.1.113883.13.200"
CS_DOSAGE_FORM = "2.16.840.1.113883.13.205"
CS_ROUTE = "2.16.840.1.113883.13.202"
CS_ADMINISTERED_BY = "2.16.840.1.113883.13.208"
CS_REPORTER = "2.16.840.1.113883.13.194"
CS_SUBMISSION = "2.16.840.1.113883.13.195"
CS_INFORMATION = "2.16.840.1.113883.13.196"
CS_COUNT_BASIS = "2.16.840.1.113883.13.209"
CS_REGION = "2.16.840.1.113883.13.212"
CS_UNIT = "2.16.840.1.113883.6.8"

SPECIES_CODES = {"Dog": "DOG", "Cat": "CAT", "Horse": "HORSE", "Cattle": "CATTLE", "Other": "OTHER"}
GENDER_CODES = {"Female": "C16576", "Male": "C20197", "Mixed": "C67447", "Unknown": "C17998"}
REPRODUCTIVE_CODES = {"Intact": "C54012", "Neutered": "C54011", "Unknown": "C17998"}
METHOD_CODES = {"Measured": "C44473", "Estimated": "C81239", "Unknown": "C17998"}
ROUTE_CODES = {
    "Oral": "C38288",
    "Subcutaneous": "C38299",
    "Intravenous": "C38276",
    "Intramuscular": "C28161",
    "Topical": "C38304",
    "Ophthalmic": "C38287",
    "Otic": "C38192",
    "Respiratory": "C38216",
}
DOSAGE_FORM_CODES = {
    "Tablet": "C42998",
    "Capsule": "C25158",
    "Injection": "C42946",
    "Chewable": "C42887",
    "Liquid": "C42953",
    "Topical": "C29167",
}
ADMINISTERED_BY_CODES = {"Owner": "C82468", "Veterinarian": "C82470", "Other": "C17649", "Unknown": "C17998"}
REPORTER_CODES = {"Owner": "C82468", "Veterinarian": "C82470", "Other": "C17649"}
# B.3.8 outcome counts, in the order the message lists them.
OUTCOME_CODES = (
    ("ongoing", "C53279", "Ongoing"),
    ("recovered_normal", "C82467", "Recovered / Normal"),
    ("recovered_with_sequela", "C49495", "Recovered With Sequela"),
    ("died", "C28554", "Died"),
    ("euthanised", "C21115", "Euthanized"),
    ("unknown", "C17998", "Outcome Unknown"),
)
AGE_UNITS = {"Year": "a", "Month": "mo", "Week": "wk", "Day": "d", "Hour": "h", "Minute": "min", "Second": "s"}
POUNDS_TO_KG = 0.45359237


def _element(parent: Optional[ET.Element], tag: str, **attributes) -> ET.Element:
    """An element whose attribute order is the message's, with ``xsi:`` expanded."""
    clean = {("xsi:" + key[4:] if key.startswith("xsi_") else key): value for key, value in attributes.items()}
    if parent is None:
        return ET.Element(tag, clean)
    return ET.SubElement(parent, tag, clean)


def _coded(parent: ET.Element, tag: str, code: Optional[str], system: str, display: Optional[str], **extra) -> ET.Element:
    """A coded element, or ``nullFlavor="NI"`` when the source has no code for it."""
    if not code:
        return _element(parent, tag, nullFlavor="NI", **extra)
    attributes = {"code": code, "codeSystem": system}
    if display:
        attributes["displayName"] = display
    attributes.update(extra)
    return _element(parent, tag, **attributes)


def _value(parent: ET.Element, kind: str, **attributes) -> ET.Element:
    return _element(parent, "value", xsi_type=kind, **attributes)


def _string(parent: ET.Element, tag: str, value: Optional[str], **attributes) -> ET.Element:
    """A character element; a string datatype takes text or a null flavour, not ""."""
    if not value:
        return _element(parent, tag, nullFlavor="NI", **attributes)
    element = _element(parent, tag, **attributes)
    element.text = value
    return element


def _observation(parent: ET.Element, code: str, display: str, wrapper: str = "subjectOf2") -> ET.Element:
    """One VICH data element: ``<subjectOf2><observation><code .../>``."""
    if wrapper == "outboundRelationship2":
        holder = _element(parent, wrapper, typeCode="PERT", contextConductionInd="true")
    else:
        holder = _element(parent, wrapper, typeCode="SBJ")
    observation = _element(holder, "observation", classCode="OBS", moodCode="EVN")
    _element(observation, "code", code=code, codeSystem=CS_VICH, displayName=display)
    return observation


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d%H%M%S%z") or moment.strftime("%Y%m%d%H%M%S")


def _date(value: Optional[str]) -> Optional[str]:
    """A GL42 date (``YYYY-MM-DD`` or ``YYYYMMDD``) as the message's ``YYYYMMDD``."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)[:8]
    return digits if len(digits) == 8 else None


def _number(value: Optional[str]) -> Optional[str]:
    """A quantity without the trailing zeros the forms pad it with."""
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        return None
    number = float(match.group())
    return str(int(number)) if number == int(number) else f"{number:g}"


def _telecoms(parent: ET.Element, phone: Optional[str], email: Optional[str], fax: Optional[str] = None) -> None:
    """The three telecoms every party carries, in the message's fixed order."""
    for prefix, value in (("TEL:", phone), ("MAILTO:", email), ("FAX:", fax)):
        if value:
            _element(parent, "telecom", value=prefix + re.sub(r"\s", "", value))
        else:
            _element(parent, "telecom", nullFlavor="NI")


def _address(parent: ET.Element, organisation) -> None:
    address = _element(parent, "addr", use="WP")
    for tag, value in (
        ("streetAddressLine", organisation.street),
        ("city", organisation.city),
        ("state", organisation.state),
        ("postalCode", organisation.postcode),
        ("country", organisation.country),
    ):
        element = _element(address, tag) if value else _element(address, tag, nullFlavor="NI")
        if value:
            element.text = value


def _name(parent: ET.Element, given: Optional[str], family: Optional[str], prefix: Optional[str] = None) -> None:
    name = _element(parent, "name")
    if prefix:
        _element(name, "prefix").text = prefix
    for tag, value in (("given", given), ("family", family)):
        element = _element(name, tag) if value else _element(name, tag, nullFlavor="NI")
        if value:
            element.text = value


def _weight_kg(report: VeterinaryReport, unit: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The animal's weight range in kilograms, the unit the message requires."""
    factor = POUNDS_TO_KG if (unit or "").upper().startswith("LB") else 1.0
    values = []
    for raw in (report.animal.weight_min_kg, report.animal.weight_max_kg):
        number = _number(raw)
        values.append(f"{float(number) * factor:.4g}" if number else None)
    return values[0], values[1]


def _receiver(parent: ET.Element) -> None:
    """FDA, the only receiver of a CVM submission."""
    holder = _element(parent, "receiver", typeCode="RCV")
    device = _element(holder, "device", classCode="DEV", determinerCode="INSTANCE")
    _element(device, "id")
    agent = _element(device, "asAgent", classCode="AGNT")
    organisation = _element(agent, "representedOrganization", determinerCode="INSTANCE", classCode="ORG")
    _element(organisation, "id", root="USFDA")


def _device_sender(parent: ET.Element, report: VeterinaryReport, organisation_id: str, trailer: bool = False) -> None:
    """The organisation the message comes from, as batch and message sender.

    The batch trailer carries the telecoms on the notification party itself,
    the message header on the contact person.
    """
    device = _element(parent, "device", determinerCode="INSTANCE", classCode="DEV")
    _element(device, "id")
    agent = _element(device, "asAgent", classCode="AGNT")
    organisation = _element(agent, "representedOrganization", determinerCode="INSTANCE", classCode="ORG")
    holder = report.marketing_authorisation_holder
    if holder.name:
        _element(organisation, "id", root=organisation_id, extension=holder.name)
    else:
        _element(organisation, "id", root=organisation_id)
    party = _element(organisation, "notificationParty", classCode="CON")
    _element(party, "id")
    contact = report.mah_contact
    if trailer:
        _telecoms(party, contact.phone, contact.email, contact.fax)
    person = _element(party, "contactPerson")
    _element(person, "id")
    _name(person, contact.given_name, contact.family_name, contact.title)
    if not trailer:
        _telecoms(person, contact.phone, contact.email, contact.fax)


def _documents(parent: ET.Element, documents: Sequence[Tuple[str, bytes]]) -> None:
    """B.7 attached documents, embedded base64 as the gateway expects."""
    for index, (name, data) in enumerate(documents or [("", b"")], start=1):
        reference = _element(parent, "reference", typeCode="REFR")
        document = _element(reference, "document", classCode="DOC", moodCode="EVN")
        _element(document, "id", extension=str(index))
        _element(document, "code", nullFlavor="NI")
        if not name:
            _element(document, "title", nullFlavor="NI")
            _element(document, "text", mediaType="text/plain", representation="B64", nullFlavor="NI")
            continue
        _element(document, "title").text = name
        text = _element(document, "text", mediaType="application/pdf", representation="B64")
        text.text = base64.b64encode(data).decode("ascii")


def _animal(parent: ET.Element, report: VeterinaryReport, weight_unit: Optional[str]) -> None:
    animal = report.animal
    player = _element(parent, "player2", determinerCode="INSTANCE", classCode="ANM")
    _coded(player, "code", SPECIES_CODES.get(animal.species or ""), CS_SPECIES, animal.species, codeSystemVersion="2")
    treated = _number(animal.number_treated)
    if treated:
        _element(player, "quantity", xsi_type="PQ", value=treated)
    else:
        _element(player, "quantity", xsi_type="PQ", nullFlavor="NI")
    _coded(player, "administrativeGenderCode", GENDER_CODES.get(animal.gender or ""), CS_GENDER, animal.gender)
    _coded(
        player,
        "genderStatusCode",
        REPRODUCTIVE_CODES.get(animal.reproductive_status or ""),
        CS_REPRODUCTIVE,
        animal.reproductive_status,
    )

    # B.1.4 breed: the reported wording, since the form's breed code is not the message's.
    for crossbred, breeds in ((False, animal.breeds), (True, animal.crossbreeds)):
        observation = _observation(parent, "T95007", "Are Animals Crossbred")
        _value(observation, "BL", value="true" if crossbred else "false") if breeds else _value(observation, "BL", nullFlavor="NI")
        for breed in breeds or [None]:
            relationship = _element(observation, "inboundRelationship", typeCode="COMP")
            inner = _element(relationship, "observation", classCode="OBS", moodCode="EVN")
            _element(inner, "code", code="T95008", codeSystem=CS_VICH, displayName="Breed Components")
            value = _value(inner, "CE", nullFlavor="NI")
            if breed:
                _element(value, "originalText").text = breed

    observation = _observation(parent, "T95005", "Number Of Animals Affected")
    affected = _number(animal.number_affected)
    _value(observation, "INT", value=affected) if affected else _value(observation, "INT", nullFlavor="NI")

    observation = _observation(parent, "T95006", "Assessment of Health Status Prior to the Exposure to Product")
    value = _value(observation, "CE", nullFlavor="NI")
    if animal.health_before_treatment:
        _element(value, "originalText").text = animal.health_before_treatment
    author = _element(observation, "author", typeCode="AUT")
    entity = _element(author, "assignedEntity", classCode="ASSIGNED")
    _coded(entity, "code", REPORTER_CODES.get(report.primary_reporter.category or ""), CS_REPORTER, report.primary_reporter.category)

    observation = _observation(parent, "T95010", "Female Physiological Status")
    _value(observation, "CE", nullFlavor="NI")

    low, high = _weight_kg(report, weight_unit)
    _interval(_observation(parent, "T95011", "Weight"), low, high, "kg", animal.weight_basis)
    unit = AGE_UNITS.get(animal.age_min_unit or "", "a")
    _interval(_observation(parent, "T95012", "Age"), _number(animal.age_min), _number(animal.age_max), unit, animal.age_basis)


def _interval(observation: ET.Element, low: Optional[str], high: Optional[str], unit: str, basis: Optional[str]) -> None:
    """A range; a form that repeats one value in both boxes states a single value."""
    value = _value(observation, "IVL_PQ")
    _element(value, "low", value=low, unit=unit) if low else _element(value, "low", nullFlavor="NI")
    if high and high != low:
        _element(value, "high", value=high, unit=unit)
    else:
        _element(value, "high", nullFlavor="NI")
    _coded(observation, "methodCode", METHOD_CODES.get(basis or ""), CS_METHOD, basis)


def _reactions(parent: ET.Element, report: VeterinaryReport) -> None:
    """B.3.2 clinical manifestations: the reported terms, VeDDRA coding left to CVM."""
    for sign in report.event.signs:
        observation = _observation(parent, "T95020", "Reaction")
        value = _value(observation, "CE", nullFlavor="NI")
        if sign.term:
            _element(value, "originalText").text = sign.term
        reference = _element(observation, "referenceRange")
        range_element = _element(reference, "observationRange", classCode="OBS", moodCode="EVN.CRT")
        count = _number(sign.animals_affected)
        _value(range_element, "INT", value=count) if count else _value(range_element, "INT", nullFlavor="NI")
        _element(
            range_element,
            "interpretationCode",
            code="C25274" if (sign.count_basis or "Actual") == "Actual" else "C81239",
            codeSystem=CS_COUNT_BASIS,
            displayName=sign.count_basis or "Actual",
        )

    event = report.event
    observation = _observation(parent, "T95020", "Reaction")
    time = _element(observation, "effectiveTime", xsi_type="IVL_TS")
    onset = _date(event.onset_date)
    _element(time, "low", value=onset) if onset else _element(time, "low", nullFlavor="NI")
    duration = _number(event.duration)
    if duration:
        _element(time, "width", value=duration, unit=AGE_UNITS.get(event.duration_unit or "Day", "d"))
    else:
        _element(time, "width", nullFlavor="NI")

    observation = _observation(parent, "T95021", "Length Of Time Between Exposure To VMP And Onset Of AE")
    onset_delay = _number(event.time_to_onset)
    if onset_delay:
        _value(observation, "PQ", value=onset_delay, unit=AGE_UNITS.get(event.duration_unit or "Day", "d"))
    else:
        _value(observation, "CE", nullFlavor="NI")

    observation = _observation(parent, "T95023", "Treatment Of AE")
    _value(observation, "BL", value="true") if event.treated else _value(observation, "BL", nullFlavor="NI")

    for attribute, code, display in OUTCOME_CODES:
        observation = _observation(parent, code, display)
        count = _number(getattr(event.outcome, attribute))
        _value(observation, "INT", value=count) if count else _value(observation, "INT", nullFlavor="NI")


def _product(parent: ET.Element, report: VeterinaryReport, product_id: str) -> None:
    product = report.product
    event = report.event
    holder = _element(parent, "subjectOf2", typeCode="SBJ")
    administration = _element(holder, "substanceAdministration", classCode="SBADM", moodCode="EVN")
    _element(administration, "id", root="1.2.3.4", extension=product_id)

    time = _element(administration, "effectiveTime", xsi_type="SXPR_TS")
    span = _element(time, "comp", xsi_type="IVL_TS")
    first, last = _date(product.first_exposure), _date(product.last_exposure)
    _element(span, "low", value=first) if first else _element(span, "low", nullFlavor="NI")
    _element(span, "high", value=last) if last else _element(span, "high", nullFlavor="NI")
    interval = _number(product.administration_interval)
    if interval:
        period = _element(time, "comp", xsi_type="PIVL_TS", operator="A")
        _element(period, "period", value=interval, unit=AGE_UNITS.get(product.administration_interval_unit or "Day", "d"))

    _coded(administration, "routeCode", ROUTE_CODES.get(product.route or ""), CS_ROUTE, product.route)
    dose = _element(administration, "doseCheckQuantity")
    value = _number(product.dose_value)
    if value and product.dose_unit:
        numerator = _element(dose, "numerator", xsi_type="PQ", value=value)
        _element(numerator, "translation", code=product.dose_unit, codeSystem=CS_UNIT, displayName=product.dose_unit)
    elif value:
        # A dose without a coded unit ("1 Vial 15mg/ml"): the amount, unit unknown.
        numerator = _element(dose, "numerator", xsi_type="PQ", value=value)
        _element(numerator, "translation", nullFlavor="NI")
    else:
        _element(dose, "numerator", xsi_type="PQ", nullFlavor="NI")
    denominator = _element(dose, "denominator", xsi_type="PQ", value=_number(product.dose_denominator_value) or "1")
    _element(denominator, "translation", code=product.dose_denominator_unit or "1", codeSystem=CS_UNIT)

    consumable = _element(administration, "consumable", typeCode="CSM")
    instance = _element(consumable, "instanceOfKind", classCode="INST")
    physical = _element(instance, "productInstanceInstance", classCode="MMAT", determinerCode="INSTANCE")
    _element(physical, "id", extension="1")
    existence = _element(physical, "existenceTime", xsi_type="IVL_TS")
    _element(existence, "low", nullFlavor="NI")
    if product.lot_number:
        _element(physical, "lotNumberText", mediaType="text/plain").text = product.lot_number
    else:
        _element(physical, "lotNumberText", mediaType="text/plain", nullFlavor="NI")
    expiry = _date(product.expiration_date)
    _element(physical, "expirationTime", value=expiry) if expiry else _element(physical, "expirationTime", nullFlavor="NI")

    kind = _element(consumable.find("instanceOfKind"), "kindOfProduct", classCode="MMAT", determinerCode="KIND")
    _element(kind, "code", code=product.product_code, codeSystem="2.16.840.1.113883.6.69") if product.product_code else _element(
        kind, "code", nullFlavor="NI"
    )
    _string(kind, "name", product.brand_name, xsi_type="TN")
    _coded(kind, "formCode", DOSAGE_FORM_CODES.get(product.dosage_form or ""), CS_DOSAGE_FORM, product.dosage_form)
    manufactured = _element(kind, "asManufacturedProduct", classCode="MANU")
    organisation = _element(manufactured, "manufacturerOrganization", classCode="ORG", determinerCode="INSTANCE")
    if product.company:
        _element(organisation, "name").text = product.company
    else:
        _element(organisation, "name", nullFlavor="NI")
    subject = _element(manufactured, "subjectOf", typeCode="SBJ")
    approval = _element(subject, "approval", classCode="CNTRCT", moodCode="EVN")
    if product.registration_id:
        _element(approval, "id", extension=product.registration_id)
    else:
        _element(approval, "id", nullFlavor="NI")

    for ingredient in product.active_ingredients:
        block = _element(kind, "ingredient", classCode="INGR")
        quantity = _element(block, "quantity")
        strength = _number(ingredient.strength_value)
        if strength:
            numerator = _element(quantity, "numerator", xsi_type="PQ", value=strength)
            _element(numerator, "translation", code=ingredient.strength_unit or "1", codeSystem=CS_UNIT)
        else:
            _element(quantity, "numerator", xsi_type="PQ", nullFlavor="NI")
        denominator_unit = ingredient.strength_denominator_unit or product.dosage_form or ""
        strength_denominator = _element(quantity, "denominator", xsi_type="PQ", value=_number(ingredient.strength_denominator_value) or "1")
        if denominator_unit:
            _element(strength_denominator, "translation", code=denominator_unit, codeSystem=CS_UNIT, displayName=denominator_unit)
        substance = _element(block, "ingredientSubstance", determinerCode="KIND", classCode="MMAT")
        if ingredient.code:
            _element(substance, "code", code=ingredient.code, codeSystem="2.16.840.1.113883.4.9")
        else:
            _element(substance, "code", nullFlavor="NI")
        _string(substance, "name", ingredient.name, xsi_type="TN")

    # B.2.6 the physical item: manufacturing site, defective and returned counts.
    physical_kind = _element(kind, "instanceOfKind", classCode="INST")
    _element(physical_kind, "productInstanceInstance")
    subject = _element(physical_kind, "subjectOf", typeCode="SBJ")
    product_event = _element(subject, "productEvent", classCode="ACT", moodCode="EVN")
    performer = _element(product_event, "performer", typeCode="PRF")
    entity = _element(performer, "assignedEntity", classCode="ASSIGNED")
    site = _element(entity, "representedOrganization", classCode="ORG", determinerCode="INSTANCE")
    if report.defect.manufacturing_site:
        _element(site, "id", extension=report.defect.manufacturing_site)
    else:
        _element(site, "id", nullFlavor="NI")
    for code, display, count, unit in (
        ("T95017", "Number of Defective Items", report.defect.defective_items, report.defect.defective_item_units),
        ("T95018", "Number Of Items Returned", report.defect.returned_items, report.defect.returned_item_units),
    ):
        subject = _element(physical_kind, "subjectOf", typeCode="SBJ")
        event_element = _element(subject, "observationEvent", classCode="OBS", moodCode="EVN")
        _element(event_element, "code", code=code, codeSystem=CS_VICH, displayName=display)
        number = _number(count)
        value_element = _value(event_element, "PQ", value=number) if number else _value(event_element, "PQ", nullFlavor="NI")
        if unit:
            _element(value_element, "translation", code=unit, codeSystem=CS_UNIT, displayName=unit)
        else:
            _element(value_element, "translation", nullFlavor="NI")

    # B.2.1.3 ATCvet code
    specialised = _element(kind, "asSpecializedKind", classCode="GEN")
    generalised = _element(specialised, "generalizedMaterialKind", classCode="MMAT", determinerCode="KIND")
    _element(generalised, "code", code="T95013", codeSystem=CS_VICH, displayName="ATCvet Code")
    if product.atc_vet_code:
        _element(generalised, "name", xsi_type="TN").text = product.atc_vet_code
    else:
        _element(generalised, "name", xsi_type="TN", nullFlavor="NI")

    # B.2.6.5 ORA district field office
    subject = _element(instance, "subjectOf", typeCode="SBJ")
    event_element = _element(subject, "observationEvent", classCode="OBS", moodCode="EVN")
    _element(event_element, "code", code="T95019", codeSystem=CS_VICH, displayName="ORA District Field Office")
    value_element = _value(event_element, "CE", nullFlavor="NI")
    if report.defect.ora_district:
        _element(value_element, "originalText").text = report.defect.ora_district

    performer = _element(administration, "performer", typeCode="PRF")
    entity = _element(performer, "assignedEntity", classCode="ASSIGNED")
    _coded(entity, "code", ADMINISTERED_BY_CODES.get(product.administered_by or ""), CS_ADMINISTERED_BY, product.administered_by)

    for code, display, value in (
        ("T95024", "Previous Exposure To The VMP", event.previous_exposure),
        ("T95025", "Previous AE To The VMP", event.previous_reaction),
        ("T95026", "Did AE Abate After Stopping the VMP", event.dechallenge),
        ("T95027", "Did AE Reappear After Re-Introduction of the VMP", event.rechallenge),
    ):
        observation = _observation(administration, code, display, wrapper="outboundRelationship2")
        _boolean(observation, value)

    _off_label(administration, product)


# B.2.5.1 the ways a treatment can depart from the label, in the message's order.
OFF_LABEL_ISSUES = (
    ("T95029", "Dose Off-Label", "dose"),
    ("T95030", "Species Off-Label", "species"),
    ("T95031", "Route Off-Label", "route"),
    ("T95032", "Treatment regimen Off-Label", "regimen"),
    ("T95033", "Indication Off-Label", "indication"),
    ("T95034", "Storage condition Off-Label", "storage"),
    ("T95035", "Product expired", "expired"),
    ("T95036", "Other off label issue", "other"),
)


def _off_label(administration: ET.Element, product) -> None:
    """B.2.5 label conformity, each off-label issue hanging off the answer."""
    observation = _observation(administration, "T95028", "Used According To Label", wrapper="outboundRelationship2")
    _boolean(observation, product.used_according_to_label)
    for code, display, key in OFF_LABEL_ISSUES:
        issue = _observation(observation, code, display, wrapper="outboundRelationship2")
        _boolean(issue, product.off_label_use.get(key))


def _boolean(observation: ET.Element, value: Optional[str]) -> None:
    """A GL42 yes/no answer; "not relevant" and unknown carry their own null flavours."""
    answer = (value or "").strip().lower()
    if answer in ("yes", "y", "true"):
        _value(observation, "BL", value="true")
    elif answer in ("no", "n", "false"):
        _value(observation, "BL", value="false")
    elif answer.startswith("not relevant"):
        _value(observation, "BL", nullFlavor="NA")
    else:
        _value(observation, "BL", nullFlavor="NI")


def _assessments(parent: ET.Element, report: VeterinaryReport, product_id: str) -> None:
    event = report.event
    for code_system, code, display, assessment, explanation in (
        (CS_REPORTER, "C82470", "Veterinarian", event.attending_vet_assessment, None),
        (CS_VICH, "T95001", "MAH", event.mah_assessment, None),
        (CS_VICH, "T95009", "RA", event.ra_assessment, event.ra_assessment_explanation),
    ):
        component = _element(parent, "component", typeCode="COMP")
        causality = _element(component, "causalityAssessment", classCode="INVSTG", moodCode="EVN")
        if display == "RA":
            text = _element(causality, "text") if explanation else _element(causality, "text", nullFlavor="NI")
            if explanation:
                text.text = explanation
        value = _value(causality, "CE", nullFlavor="NI")
        if assessment:
            _element(value, "originalText").text = assessment
        author = _element(causality, "author", typeCode="AUT")
        entity = _element(author, "assignedEntity", classCode="ASSIGNED")
        _element(entity, "code", code=code, codeSystem=code_system, displayName=display)
        if display in ("MAH", "RA"):
            subject = _element(causality, "subject2", typeCode="SUBJ")
            reference = _element(subject, "productUseReference", classCode="INFO", moodCode="EVN")
            _element(reference, "id", extension=product_id)


def _source_report(parent: ET.Element, person, priority: int, received: Optional[str]) -> None:
    holder = _element(parent, "outboundRelationship", typeCode="SPRT")
    _element(holder, "priorityNumber", value=str(priority))
    investigation = _element(holder, "relatedInvestigation", classCode="INVSTG", moodCode="EVN")
    _element(investigation, "code", code="T95002", codeSystem=CS_VICH, displayName="Sourcereport")
    if received:
        _element(investigation, "effectiveTime", value=received)
    participation = _element(investigation, "participation", typeCode="AUT")
    entity = _element(participation, "assignedEntity", classCode="ASSIGNED")
    _coded(entity, "code", REPORTER_CODES.get(person.category or ""), CS_REPORTER, person.category)
    _address(entity, person.organisation)
    _telecoms(entity, person.phone, person.email, person.fax)
    assigned = _element(entity, "assignedPerson", determinerCode="INSTANCE", classCode="PSN")
    _name(assigned, person.given_name, person.family_name)
    identified = _element(assigned, "asIdentifiedEntity", classCode="IDENT")
    organisation = _element(identified, "assigningOrganization", determinerCode="INSTANCE", classCode="ORG")
    if person.organisation.name:
        _element(organisation, "name").text = person.organisation.name
    else:
        _element(organisation, "name", nullFlavor="NI")


def _parties(parent: ET.Element, report: VeterinaryReport) -> None:
    holder = _element(parent, "subjectOf1", typeCode="SUBJ")
    control = _element(holder, "controlActEvent", classCode="CACT", moodCode="EVN")
    author = _element(control, "author", typeCode="AUT")
    entity = _element(author, "assignedEntity", classCode="ASSIGNED")
    _element(entity, "code", code="T95001", codeSystem=CS_VICH, displayName="MAH")
    _address(entity, report.marketing_authorisation_holder)
    organisation = _element(entity, "representedOrganization", determinerCode="INSTANCE", classCode="ORG")
    if report.marketing_authorisation_holder.name:
        _element(organisation, "name").text = report.marketing_authorisation_holder.name
    else:
        _element(organisation, "name", nullFlavor="NI")
    contact = report.mah_contact
    party = _element(organisation, "contactParty", classCode="CON")
    _telecoms(party, contact.phone, contact.email, contact.fax)
    person = _element(party, "contactPerson", determinerCode="INSTANCE", classCode="PSN")
    _name(person, contact.given_name, contact.family_name, contact.title)

    recipient = _element(control, "primaryInformationRecipient", typeCode="PRCP")
    entity = _element(recipient, "assignedEntity", classCode="ASSIGNED")
    _element(entity, "code", code="T95009", codeSystem=CS_VICH, displayName="RA")
    _address(entity, report.regulatory_authority)
    organisation = _element(entity, "representedOrganization", determinerCode="INSTANCE", classCode="ORG")
    if report.regulatory_authority.name:
        _element(organisation, "name").text = report.regulatory_authority.name
    else:
        _element(organisation, "name", nullFlavor="NI")


def _characteristics(parent: ET.Element, report: VeterinaryReport) -> None:
    for code, display, kind, attributes, original in (
        ("T95003", "Type Of Submission", "CE", _submission(report), True),
        ("T95004", "Type Of Information In Report", "CE", _information(report), False),
    ):
        holder = _element(parent, "subjectOf2", typeCode="SUBJ")
        characteristic = _element(holder, "investigationCharacteristic", classCode="CASE", moodCode="EVN")
        _element(characteristic, "code", code=code, codeSystem=CS_VICH, displayName=display)
        value = _value(characteristic, kind, **attributes)
        if original:
            _element(value, "originalText", nullFlavor="NI")

    holder = _element(parent, "subjectOf2", typeCode="SUBJ")
    characteristic = _element(holder, "investigationCharacteristic", classCode="CASE", moodCode="EVN")
    _element(characteristic, "code", code="T95022", codeSystem=CS_VICH, displayName="Serious AE")
    _boolean(characteristic, report.event.serious)

    holder = _element(parent, "subjectOf2", typeCode="SUBJ")
    characteristic = _element(holder, "investigationCharacteristic", moodCode="EVN", classCode="CASE")
    _element(characteristic, "code", code="T95037", codeSystem=CS_VICH, displayName="Report number(s) of linked report(s)")
    value = _value(characteristic, "CD", nullFlavor="NI")
    text = _element(value, "originalText")
    if report.linked_reports:
        text.text = report.linked_reports
    else:
        text.set("nullFlavor", "NI")


def _submission(report: VeterinaryReport) -> dict:
    kinds = {"initial": ("C48660", "Initial"), "follow-up": ("C68609", "Follow-up"), "periodic": ("C53578", "Periodic")}
    for submission in report.submission_types:
        entry = kinds.get(submission.strip().lower())
        if entry:
            return {"code": entry[0], "codeSystem": CS_SUBMISSION, "displayName": entry[1]}
    return {"nullFlavor": "NI"}


def _information(report: VeterinaryReport) -> dict:
    if (report.type_of_information or "").lower().startswith("adverse"):
        return {"code": "C82461", "codeSystem": CS_INFORMATION, "displayName": "Safety Issue "}
    if (report.type_of_information or "").lower().startswith("product"):
        return {"code": "C82462", "codeSystem": CS_INFORMATION, "displayName": "Product Defect"}
    return {"nullFlavor": "NI"}


def to_xml_string(
    report: VeterinaryReport,
    documents: Sequence[Tuple[str, bytes]] = (),
    weight_unit: Optional[str] = None,
    batch_id: Optional[str] = None,
    sender_root: str = "ID-PHARMAWATCH",
    created: Optional[datetime] = None,
) -> str:
    """Serialise ``report`` as CVM's VICH AER 1.0.0 HL7 v3 submission message."""
    moment = created or datetime.now(timezone.utc).astimezone()
    identifier = report.report_identifier or report.aer_id
    batch = batch_id or identifier or moment.strftime("%Y%m%d%H%M%S")
    # The assessments refer to the product by this id, so it must be stable per report.
    product_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{batch}/{report.product.brand_name or ''}"))

    root = _element(
        None,
        "MCCI_IN200100UV01",
        ITSVersion="XML_1.0",
    )
    root.set("xsi:schemaLocation", SCHEMA)
    root.set("xmlns", NS)
    root.set("xmlns:xs", "http://www.w3.org/2001/XMLSchema")
    root.set("xmlns:xsi", XSI)
    _element(root, "id", root=sender_root, extension=batch)
    _element(root, "creationTime", value=_stamp(moment))
    _element(root, "responseModeCode")
    _element(root, "versionCode", code=VERSION_CODE)
    _element(root, "interactionId")

    message = _element(root, "PORR_IN049006UV", xsi_type="PORR_IN049006UV.MCCI_MT000100UV01.Message")
    _element(message, "id", root=sender_root, extension=batch)
    _element(message, "creationTime", value=_stamp(moment))
    _element(message, "interactionId")
    _element(message, "profileId", root="2.16.840.1.113883.13.207", extension=PROFILE_ID)
    for tag in ("processingCode", "processingModeCode", "acceptAckCode"):
        _element(message, tag)

    _receiver(message)
    _device_sender(_element(message, "sender", typeCode="SND"), report, sender_root)

    line = _element(message, "attentionLine")
    _element(line, "keyWordText").text = "Report Identifier"
    _string(line, "value", identifier, xsi_type="ST")
    line = _element(message, "attentionLine")
    _element(line, "keyWordText").text = "Domestic vs Foreign Report Category"
    domestic = (report.report_category or "domestic").lower().startswith("domestic")
    _element(
        line,
        "value",
        xsi_type="SC",
        code="C62264" if domestic else "C16276",
        codeSystem=CS_REGION,
        displayName="Domestic" if domestic else "Foreign",
    )

    control = _element(message, "controlActProcess", classCode="CACT", moodCode="EVN")
    subject = _element(control, "subject", typeCode="SUBJ")
    investigation = _element(subject, "investigationEvent", classCode="INVSTG", moodCode="EVN")
    _element(investigation, "id", root="1.2.3.4", extension=identifier or batch)
    _element(investigation, "code")
    _string(investigation, "text", report.event.narrative, mediaType="text/plain")
    _element(investigation, "statusCode")
    submitted = _date(report.submission_date) or moment.strftime("%Y%m%d")
    _element(investigation, "availabilityTime", value=submitted)
    _documents(investigation, documents)

    component = _element(investigation, "component", typeCode="COMP")
    assessment = _element(component, "adverseEventAssessment", classCode="INVSTG", moodCode="EVN")
    subject1 = _element(assessment, "subject1", typeCode="SBJ")
    primary = _element(subject1, "primaryRole", classCode="INVSBJ")
    _animal(primary, report, weight_unit)
    _reactions(primary, report)
    _product(primary, report, product_id)
    _assessments(assessment, report, product_id)

    _source_report(investigation, report.primary_reporter, 1, _date(report.first_received_date))
    _source_report(investigation, report.other_reporter, 2, None)
    _parties(investigation, report)
    _characteristics(investigation, report)

    # The batch's own receiver and sender close it, after the message it carries.
    _receiver(root)
    _device_sender(_element(root, "sender"), report, sender_root, trailer=True)

    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def documents_from(paths: Iterable[str], data: Optional[Mapping[str, bytes]] = None) -> Sequence[Tuple[str, bytes]]:
    """Read ``paths`` into the (filename, bytes) pairs the message embeds."""
    import os

    pairs = []
    for path in paths:
        name = os.path.basename(path)
        content = data[name] if data and name in data else open(path, "rb").read()
        pairs.append((name, content))
    return pairs
