"""Serialise a :class:`VeterinaryReport` as a VICH GL42 adverse event report.

VICH GL42 ("Pharmacovigilance of Veterinary Medicinal Products: Data Elements
for Submission of Adverse Event Reports") defines the data elements that FDA CVM
collects on Form FDA 1932 - A.1 regulatory authority, A.2 marketing
authorisation holder, A.3 reporters, A.4 AER identification, B.1 animal, B.2
veterinary medicinal product, B.3 adverse event, B.4 dechallenge/rechallenge,
B.5 assessment, C message identification.

Every element emitted here carries the GL42 data-element number it was taken
from (``gl42="B.2.1.2"``), which is also the number printed next to the field on
the form, so the XML can be checked against the source document element by
element.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from .models import Organisation, Person, VeterinaryReport

GL42_NAMESPACE = "urn:vich:gl42:aer"
GL42_VERSION = "GL42"

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _iso_date(value: Optional[str]) -> Optional[str]:
    """Normalise a form date (``dd-Mon-yyyy``/``dd/mm/yyyy``) to ``CCYY-MM-DD``."""
    if not value:
        return None
    text = value.strip()
    match = re.search(r"(\d{1,2})[-/ ]([A-Za-z]{3})[a-z]*[-/ ](\d{4})", text)
    if match:
        month = MONTHS.get(match.group(2).lower())
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(1)):02d}"
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if match:
        return f"{match.group(3)}-{int(match.group(2)):02d}-{int(match.group(1)):02d}"
    return None


def _sub(parent: ET.Element, tag: str, text: Optional[str], gl42: Optional[str] = None) -> Optional[ET.Element]:
    if text is None or str(text).strip() == "":
        return None
    element = ET.SubElement(parent, tag, {"gl42": gl42} if gl42 else {})
    element.text = str(text).strip()
    return element


def _quantity(parent: ET.Element, tag: str, value: Optional[str], unit: Optional[str], gl42: str) -> Optional[ET.Element]:
    if not value or not str(value).strip():
        return None
    attributes = {"gl42": gl42, "value": str(value).strip()}
    if unit:
        attributes["unit"] = str(unit).strip()
    return ET.SubElement(parent, tag, attributes)


def _organisation(parent: ET.Element, tag: str, org: Organisation, gl42: str) -> Optional[ET.Element]:
    if not any((org.name, org.street, org.city, org.country)):
        return None
    element = ET.SubElement(parent, tag, {"gl42": gl42})
    _sub(element, "name", org.name, f"{gl42}.1")
    _sub(element, "streetAddress", org.street, f"{gl42}.2")
    _sub(element, "city", org.city, f"{gl42}.3")
    _sub(element, "stateOrProvince", org.state, f"{gl42}.4")
    _sub(element, "postalCode", org.postcode, f"{gl42}.5")
    _sub(element, "country", org.country, f"{gl42}.6")
    return element


def _person(parent: ET.Element, tag: str, person: Person, gl42: str, with_organisation: bool = False) -> Optional[ET.Element]:
    if not any((person.name, person.email, person.phone, person.category)):
        return None
    element = ET.SubElement(parent, tag, {"gl42": gl42})
    _sub(element, "category", person.category, f"{gl42}.1")
    _sub(element, "title", person.title, f"{gl42}.2")
    _sub(element, "givenName", person.given_name, f"{gl42}.3")
    _sub(element, "familyName", person.family_name, f"{gl42}.4")
    _sub(element, "telephone", person.phone, f"{gl42}.5")
    _sub(element, "fax", person.fax, f"{gl42}.6")
    _sub(element, "email", person.email, f"{gl42}.7")
    if with_organisation:
        _organisation(element, "organisation", person.organisation, f"{gl42}.8")
    return element


def build_gl42(report: VeterinaryReport, sender_id: str = "PHARMAWATCH", receiver_id: str = "FDACVM") -> ET.Element:
    """Build the ``<vichAdverseEventReport>`` element for ``report``."""
    now = datetime.now(timezone.utc)
    root = ET.Element(
        "vichAdverseEventReport",
        {"xmlns": GL42_NAMESPACE, "standard": GL42_VERSION, "lang": "en"},
    )

    header = ET.SubElement(root, "messageHeader")
    _sub(header, "messageNumber", report.message_number or f"{sender_id}-{now.strftime('%Y%m%d%H%M%S')}", "C.1.1")
    _sub(header, "messageSenderIdentifier", sender_id, "C.1.2")
    _sub(header, "messageReceiverIdentifier", receiver_id, "C.1.3")
    _sub(header, "messageDate", _iso_date(report.message_date) or now.strftime("%Y-%m-%d"), "C.1.4")
    _person(header, "messageSender", report.message_sender, "C.1.5")

    aer = ET.SubElement(root, "adverseEventReport")

    administrative = ET.SubElement(aer, "administrativeInformation")
    _sub(administrative, "uniqueAerIdentifier", report.aer_id, "A.4.1")
    _sub(administrative, "reportIdentifier", report.report_identifier, "C.2.5")
    _sub(administrative, "reportCategory", report.report_category, "C.2.6")
    _sub(administrative, "profileIdentifier", report.profile_identifier, "C.2.7")
    _sub(administrative, "typeOfInformation", report.type_of_information, "A.4.5")
    _sub(administrative, "dateFirstReceived", _iso_date(report.first_received_date), "A.4.2")
    _sub(administrative, "dateOfSubmission", _iso_date(report.submission_date), "A.4.3")
    if report.submission_types:
        types = ET.SubElement(administrative, "submissionTypes", {"gl42": "A.4.4"})
        for value in report.submission_types:
            _sub(types, "submissionType", value)
    _organisation(administrative, "regulatoryAuthority", report.regulatory_authority, "A.1.1")
    _organisation(administrative, "marketingAuthorisationHolder", report.marketing_authorisation_holder, "A.2.1")
    _person(administrative, "mahContact", report.mah_contact, "A.2.2")
    _person(administrative, "primaryReporter", report.primary_reporter, "A.3.1", with_organisation=True)
    _person(administrative, "otherReporter", report.other_reporter, "A.3.2", with_organisation=True)

    animal = report.animal
    animal_element = ET.SubElement(aer, "animal", {"gl42": "B.1"})
    _sub(animal_element, "species", animal.species, "B.1.3")
    for breed in animal.breeds:
        _sub(animal_element, "breed", breed, "B.1.4.1.1")
    for breed in animal.crossbreeds:
        _sub(animal_element, "crossbreed", breed, "B.1.4.2.1")
    _sub(animal_element, "gender", animal.gender, "B.1.5")
    _sub(animal_element, "reproductiveStatus", animal.reproductive_status, "B.1.6")
    _sub(animal_element, "physiologicalStatus", animal.physiological_status, "B.1.7")
    _sub(animal_element, "numberOfAnimalsTreated", animal.number_treated, "B.1.1")
    _sub(animal_element, "numberOfAnimalsAffected", animal.number_affected, "B.1.2")
    weight = ET.SubElement(animal_element, "weight", {"gl42": "B.1.8"})
    _sub(weight, "basis", animal.weight_basis, "B.1.8.1")
    _quantity(weight, "minimum", animal.weight_min_kg, "kg", "B.1.8.2")
    _quantity(weight, "maximum", animal.weight_max_kg, "kg", "B.1.8.3")
    age = ET.SubElement(animal_element, "age", {"gl42": "B.1.9"})
    _sub(age, "basis", animal.age_basis, "B.1.9.1")
    _quantity(age, "minimum", animal.age_min, animal.age_min_unit, "B.1.9.2")
    _quantity(age, "maximum", animal.age_max, animal.age_max_unit, "B.1.9.3")
    _sub(animal_element, "healthStatusBeforeTreatment", animal.health_before_treatment, "B.1.10")

    product = report.product
    product_element = ET.SubElement(aer, "veterinaryMedicinalProduct", {"gl42": "B.2"})
    _sub(product_element, "brandName", product.brand_name, "B.2.1")
    _sub(product_element, "productCode", product.product_code, "B.2.1.1")
    _sub(product_element, "registrationIdentifier", product.registration_id, "B.2.1.2")
    _sub(product_element, "atcVetCode", product.atc_vet_code, "B.2.1.3")
    _sub(product_element, "companyOrMah", product.company, "B.2.1.4")
    _sub(product_element, "mahAssessment", report.event.mah_assessment, "B.2.1.5")
    if report.event.ra_assessment or report.event.ra_assessment_explanation:
        ra = ET.SubElement(product_element, "regulatoryAuthorityAssessment", {"gl42": "B.2.1.6"})
        _sub(ra, "term", report.event.ra_assessment, "B.2.1.6.1")
        _sub(ra, "explanation", report.event.ra_assessment_explanation, "B.2.1.6.1.1")
    _sub(product_element, "routeOfExposure", product.route, "B.2.1.7")
    dose = ET.SubElement(product_element, "dosePerAdministration", {"gl42": "B.2.1.7.1"})
    _quantity(dose, "numerator", product.dose_value, product.dose_unit, "B.2.1.7.1.1")
    _quantity(dose, "denominator", product.dose_denominator_value, product.dose_denominator_unit, "B.2.1.7.1.2")
    _quantity(
        product_element,
        "intervalOfAdministration",
        product.administration_interval,
        product.administration_interval_unit,
        "B.2.1.7.1.3",
    )
    _sub(product_element, "dateOfFirstExposure", _iso_date(product.first_exposure), "B.2.1.8")
    _sub(product_element, "dateOfLastExposure", _iso_date(product.last_exposure), "B.2.1.9")
    for ingredient in product.active_ingredients:
        element = ET.SubElement(product_element, "activeIngredient", {"gl42": "B.2.2"})
        _sub(element, "name", ingredient.name, "B.2.2.1")
        _sub(element, "code", ingredient.code, "B.2.2.2")
        _quantity(element, "strengthNumerator", ingredient.strength_value, ingredient.strength_unit, "B.2.2.3")
        _quantity(
            element,
            "strengthDenominator",
            ingredient.strength_denominator_value,
            ingredient.strength_denominator_unit,
            "B.2.2.4",
        )
    _sub(product_element, "dosageForm", product.dosage_form, "B.2.2.5")
    _sub(product_element, "lotNumber", product.lot_number, "B.2.3.1")
    _sub(product_element, "expirationDate", _iso_date(product.expiration_date), "B.2.3.2")
    _sub(product_element, "administeredBy", product.administered_by, "B.2.4")
    _sub(product_element, "usedAccordingToLabel", product.used_according_to_label, "B.2.5")
    if product.off_label_use:
        off_label = ET.SubElement(product_element, "offLabelUse", {"gl42": "B.2.5.1"})
        for issue, answer in product.off_label_use.items():
            element = ET.SubElement(off_label, "issue", {"type": issue})
            element.text = answer

    defect = report.defect
    if any((defect.manufacturing_site, defect.defective_items, defect.returned_items, defect.ora_district)):
        defect_element = ET.SubElement(aer, "productProblem", {"gl42": "B.2.6"})
        _sub(defect_element, "manufacturingSite", defect.manufacturing_site, "B.2.6.1")
        _sub(defect_element, "manufacturingDate", _iso_date(defect.manufacturing_date), "B.2.6.2")
        _quantity(defect_element, "defectiveItems", defect.defective_items, defect.defective_item_units, "B.2.6.3")
        _quantity(defect_element, "returnedItems", defect.returned_items, defect.returned_item_units, "B.2.6.4")
        _sub(defect_element, "oraDistrict", defect.ora_district, "B.2.6.5")

    event = report.event
    event_element = ET.SubElement(aer, "adverseEvent", {"gl42": "B.3"})
    _sub(event_element, "narrative", event.narrative, "B.3.1")
    for sign in event.signs:
        element = ET.SubElement(event_element, "clinicalManifestation", {"gl42": "B.3.2"})
        _sub(element, "term", sign.term, "B.3.2.1")
        _sub(element, "numberOfAnimalsAffected", sign.animals_affected, "B.3.2.2")
        _sub(element, "countBasis", sign.count_basis, "B.3.2.2.1")
    _sub(event_element, "dateOfOnset", _iso_date(event.onset_date), "B.3.3")
    _sub(event_element, "timeToOnset", event.time_to_onset, "B.3.4")
    _quantity(event_element, "duration", event.duration, event.duration_unit, "B.3.5")
    _sub(event_element, "serious", event.serious, "B.3.6")
    _sub(event_element, "treatmentOfEvent", event.treated, "B.3.7")
    outcome = ET.SubElement(event_element, "outcome", {"gl42": "B.3.8"})
    _sub(outcome, "ongoing", event.outcome.ongoing, "B.3.8.1")
    _sub(outcome, "recoveredNormal", event.outcome.recovered_normal, "B.3.8.2")
    _sub(outcome, "recoveredWithSequela", event.outcome.recovered_with_sequela, "B.3.8.3")
    _sub(outcome, "died", event.outcome.died, "B.3.8.4")
    _sub(outcome, "euthanised", event.outcome.euthanised, "B.3.8.5")
    _sub(outcome, "unknown", event.outcome.unknown, "B.3.8.6")
    _sub(event_element, "previousExposure", event.previous_exposure, "B.3.9")
    _sub(event_element, "previousAdverseEvent", event.previous_reaction, "B.3.10")

    challenge = ET.SubElement(aer, "dechallengeRechallenge", {"gl42": "B.4"})
    _sub(challenge, "eventAbatedAfterStopping", event.dechallenge, "B.4.1")
    _sub(challenge, "eventReappearedAfterReintroduction", event.rechallenge, "B.4.2")

    assessment = ET.SubElement(aer, "assessment", {"gl42": "B.5"})
    _sub(assessment, "attendingVeterinarian", event.attending_vet_assessment, "B.5.1")

    if report.linked_reports:
        linked = ET.SubElement(aer, "linkedReports", {"gl42": "B.6"})
        _sub(linked, "uniqueAerIdentifier", report.linked_reports, "B.6.1")
        _sub(linked, "explanationForLinkage", report.linked_report_type, "B.6.1.1")
    if report.attachments:
        documents = ET.SubElement(aer, "supplementalDocuments", {"gl42": "B.7"})
        for name in report.attachments:
            _sub(documents, "attachedDocumentName", name, "B.7.1")

    _prune_empty(root)
    return root


def _prune_empty(element: ET.Element) -> None:
    """Drop container elements that ended up without any content."""
    for child in list(element):
        _prune_empty(child)
        if len(child) == 0 and not (child.text or "").strip() and set(child.keys()) <= {"gl42"}:
            element.remove(child)


def to_xml_string(report: VeterinaryReport, **kwargs) -> str:
    """Render ``report`` as a pretty-printed VICH GL42 AER XML document."""
    root = build_gl42(report, **kwargs)
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
