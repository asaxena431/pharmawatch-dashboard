"""Form FDA 1932a submitted as a dynamic XFA PDF, and the ``pvx1932a`` message.

FDA's fillable 1932a is a dynamic XFA form: its pages are drawn by Adobe Reader
and it holds no page content and no AcroForm widgets, so neither a text layer nor
OCR can read it.  The data is not lost, though - a submitted copy carries it in
the XFA ``datasets`` packet as a ``<pvx1932a>`` element, which is exactly the
message the CVM upload service expects, with the report's PDF and its attachments
appended as base64 ``DOCUMENTS``.

This module therefore reads the dataset straight out of the PDF, re-serialises it
as the ``pvx1932a`` message, and maps the same values onto
:class:`~medwatch_ocr.models.VeterinaryReport` so the report can equally be
written as VICH GL42.
"""

from __future__ import annotations

import base64
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from pypdf import PdfReader

from .models import (
    ActiveIngredient,
    Animal,
    ClinicalSign,
    Organisation,
    Person,
    VeterinaryEvent,
    VeterinaryOutcome,
    VeterinaryProduct,
    VeterinaryReport,
)

ROOT = "pvx1932a"
XFA_DATA_NS = "{http://www.xfa.org/schema/xfa-data/1.0/}"

# Coded values the 1932a form stores, in the words the GL42 message uses.
SPECIES = {"DOG": "Dog", "CAT": "Cat", "HOR": "Horse", "BOV": "Cattle", "OTH": "Other"}
SEXES = {"F": "Female", "M": "Male", "FS": "Female", "MC": "Male", "U": "Unknown"}
ROUTES = {
    "SCU": "Subcutaneous",
    "IVN": "Intravenous",
    "IMU": "Intramuscular",
    "ORA": "Oral",
    "TOP": "Topical",
    "OPH": "Ophthalmic",
    "OTI": "Otic",
    "RES": "Respiratory",
    "OTH": "Other",
}
FORMULATIONS = {
    "INJE": "Injection",
    "TABL": "Tablet",
    "CHEW": "Chewable",
    "CAPS": "Capsule",
    "LIQU": "Liquid",
    "TOPI": "Topical",
    "OTHE": "Other",
}
ADMINISTERED_BY = {"AVET": "Veterinarian", "OWNE": "Owner", "OTHE": "Other", "UNKN": "Unknown"}
REPORTER_CATEGORIES = {"OWNE": "Owner", "AVET": "Veterinarian", "OTHE": "Other", "UNKN": "Unknown"}
CHALLENGE = {"Y": "Yes", "N": "No", "NR": "Not relevant", "UNK": "Unknown"}
AGE_UNITS = {"DAY": "Day", "WEEK": "Week", "MTH": "Month", "MONTH": "Month", "YEAR": "Year", "HOUR": "Hour"}
HEALTH = {"GOOD": "Good", "FAIR": "Fair", "POOR": "Poor", "UNKN": "Unknown"}
# B.3.8 outcome to date, as the count field of the GL42 outcome block it feeds.
OUTCOMES = {
    "ONGO": "ongoing",
    "RECN": "recovered_normal",
    "RECS": "recovered_with_sequela",
    "DEAD": "died",
    "EUTH": "euthanised",
    "UNKN": "unknown",
}


@dataclass
class Document:
    """A file the message carries: the report PDF itself, or an attachment."""

    name: str
    description: str
    data: bytes

    @classmethod
    def read(cls, path: str) -> "Document":
        name = os.path.basename(path)
        with open(path, "rb") as handle:
            return cls(name=name, description=os.path.splitext(name)[0], data=handle.read())


@dataclass
class Xfa1932a:
    """The dataset of a submitted 1932a, in the order the form stores it."""

    values: Dict[str, str] = field(default_factory=dict)
    documents: List[Document] = field(default_factory=list)
    engine: str = "xfa-dataset"

    def get(self, key: str) -> Optional[str]:
        value = self.values.get(key, "").strip()
        return value or None


def xfa_dataset(pdf_path: str) -> Optional[ET.Element]:
    """The ``<pvx1932a>`` element of a dynamic XFA form, if the PDF is one."""
    try:
        reader = PdfReader(pdf_path)
        acroform = reader.trailer["/Root"].get("/AcroForm")
        xfa = acroform.get("/XFA") if acroform else None
    except Exception:
        return None
    if not xfa:
        return None
    for index in range(0, len(xfa) - 1, 2):
        if xfa[index] != "datasets":
            continue
        packet = ET.fromstring(xfa[index + 1].get_object().get_data().decode("utf-8", errors="replace"))
        data = packet.find(f"{XFA_DATA_NS}data")
        if data is None or not len(data):
            return None
        return data[0] if data[0].tag == ROOT else None
    return None


def is_1932a_form(pdf_path: str) -> bool:
    """True for a Form FDA 1932a submitted as a dynamic XFA PDF."""
    return xfa_dataset(pdf_path) is not None


def read_1932a(pdf_path: str, attachments: Sequence[str] = ()) -> Xfa1932a:
    """Read a submitted 1932a: its dataset, plus the files the message carries."""
    dataset = xfa_dataset(pdf_path)
    if dataset is None:
        raise ValueError(f"not a dynamic XFA Form 1932a: {pdf_path}")
    values: Dict[str, str] = {}
    for element in dataset:
        if len(element):
            continue
        # The form stores a line break as a carriage return; the message uses \n.
        values[element.tag] = (element.text or "").replace("\r\n", "\n").replace("\r", "\n")
    documents = [Document.read(path) for path in (pdf_path, *attachments)]
    return Xfa1932a(values=values, documents=documents)


def to_xml_string(form: Xfa1932a, upload_id: str = "1", processed: Optional[datetime] = None) -> str:
    """Serialise the dataset as the ``pvx1932a`` message the CVM service takes."""
    root = ET.Element(ROOT)
    stamp = (processed or datetime.now(timezone.utc)).strftime("%Y-%m-%d %I:%M:%S %p")
    ET.SubElement(root, "firstprocessdate").text = stamp
    ET.SubElement(root, "uploadid").text = upload_id
    for tag, value in form.values.items():
        element = ET.SubElement(root, tag)
        if value:
            element.text = value
    if form.documents:
        documents = ET.SubElement(root, "DOCUMENTS")
        for document in form.documents:
            element = ET.SubElement(documents, "DOCUMENT")
            ET.SubElement(element, "FILE_NAME").text = document.name
            ET.SubElement(element, "FILE_DESCRIPTION").text = document.description
            ET.SubElement(element, "FILE_DATA").text = base64.b64encode(document.data).decode("ascii")
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _person(form: Xfa1932a, prefix: str) -> Person:
    return Person(
        given_name=form.get(f"{prefix}firstname"),
        family_name=form.get(f"{prefix}lastname"),
        category=REPORTER_CATEGORIES.get(form.get(f"{prefix}categorisation") or ""),
        phone=form.get(f"{prefix}telephone"),
        fax=form.get(f"{prefix}fax"),
        email=form.get(f"{prefix}email"),
        organisation=Organisation(
            name=form.get(f"{prefix}institute"),
            street=form.get(f"{prefix}streetaddress1"),
            city=form.get(f"{prefix}city"),
            state=form.get(f"{prefix}state") or form.get(f"{prefix}statetext"),
            postcode=form.get(f"{prefix}postcode"),
            country=form.get(f"{prefix}countrycode"),
        ),
    )


def _outcome(form: Xfa1932a) -> VeterinaryOutcome:
    outcome = VeterinaryOutcome()
    name = OUTCOMES.get(form.get("outcometodate") or "")
    if name:
        setattr(outcome, name, form.get("reactednumber") or "1")
    return outcome


def _product(form: Xfa1932a) -> VeterinaryProduct:
    return VeterinaryProduct(
        brand_name=form.get("p01brandname"),
        dosage_form=FORMULATIONS.get(form.get("p01formulation") or "", form.get("p01formulation")),
        lot_number=form.get("p01lotnumber"),
        expiration_date=form.get("p01expiringdate"),
        route=ROUTES.get(form.get("p01administrationroute") or "", form.get("p01administrationroute")),
        dose_value=form.get("p01dosagetext"),
        administration_interval=form.get("p01treatmentduration"),
        administration_interval_unit=AGE_UNITS.get(form.get("p01treatmentdurationunit") or ""),
        first_exposure=form.get("p01treatmentstartdate"),
        last_exposure=form.get("p01treatmentenddate"),
        administered_by=ADMINISTERED_BY.get(form.get("p01administrationvmp") or ""),
        active_ingredients=_ingredients(form),
    )


def _ingredients(form: Xfa1932a) -> List[ActiveIngredient]:
    """The product's ingredients; the form lists up to three, each with a strength."""
    ingredients = []
    for index in (1, 2, 3):
        name = form.get(f"p01ingredient{index}")
        strength = form.get(f"p01ingredientstrength{index}")
        if not name and not strength:
            continue
        # A strength reads "15mg/ml": amount and unit over a unit of the product.
        match = re.match(r"\s*(\d+(?:\.\d+)?)\s*([^/\s]+)?\s*(?:/\s*(\d+(?:\.\d+)?)?\s*(\S+))?", strength or "")
        ingredients.append(
            ActiveIngredient(
                name=name,
                strength_value=match.group(1) if match else None,
                strength_unit=match.group(2) if match else None,
                strength_denominator_value=(match.group(3) or "1") if match and match.group(4) else None,
                strength_denominator_unit=match.group(4) if match else None,
            )
        )
    return ingredients


def map_veterinary_report(form: Xfa1932a) -> VeterinaryReport:
    """Map a 1932a dataset onto the report the GL42 serialiser writes."""
    signs = [ClinicalSign(term=term.strip(), animals_affected=form.get("reactednumber")) for term in _signs(form)]
    return VeterinaryReport(
        aer_id=form.get("pvcaseno"),
        report_identifier=form.get("manufacturerref"),
        type_of_information="Adverse event" if form.get("reporttype_ae") == "Y" else "Product defect",
        first_received_date=form.get("receiveddate"),
        submission_date=form.get("todaysdate"),
        primary_reporter=_person(form, "rep01"),
        other_reporter=_person(form, "rep02"),
        message_sender=_person(form, "rep04"),
        regulatory_authority=_person(form, "rep04").organisation,
        animal=Animal(
            species=SPECIES.get(form.get("species") or "", form.get("speciesother")),
            breeds=[breed for breed in (form.get("breed"), form.get("breedother")) if breed],
            gender=SEXES.get(form.get("sex") or ""),
            number_treated=form.get("exposednumber"),
            number_affected=form.get("reactednumber"),
            weight_min_kg=_kilograms(form.get("weight"), form.get("weightunit")),
            weight_max_kg=_kilograms(form.get("weightto"), form.get("weightunit")),
            weight_basis="Estimated" if form.get("weightapx") == "Y" else "Measured",
            age_min=form.get("age"),
            age_min_unit=AGE_UNITS.get(form.get("ageunitcode") or "YEAR", "Year"),
            age_max=form.get("ageto"),
            age_basis="Estimated" if form.get("ageapx") == "Y" else "Measured",
            health_before_treatment=HEALTH.get(form.get("priorcond") or "", form.get("priorcond")),
        ),
        product=_product(form),
        event=VeterinaryEvent(
            onset_date=form.get("reactionstartdate"),
            time_to_onset=form.get("p01timetoonset"),
            duration=form.get("p01onsetlast"),
            duration_unit=AGE_UNITS.get(form.get("p01onsetlastunit") or ""),
            treated=form.get("treateddetails"),
            narrative=form.get("narrativeadr"),
            signs=signs,
            outcome=_outcome(form),
            dechallenge=CHALLENGE.get(form.get("p01dechallenge") or ""),
            rechallenge=CHALLENGE.get(form.get("p01rechallenge") or ""),
            attending_vet_assessment=form.get("p01vetassessment"),
        ),
        attachments=[document.name for document in form.documents[1:]],
        ocr_engine=form.engine,
    )


def _kilograms(value: Optional[str], unit: Optional[str]) -> Optional[str]:
    """A weight in kilograms; the form records pounds as readily as kilograms."""
    if not value:
        return None
    try:
        weight = float(value)
    except ValueError:
        return value
    if (unit or "").upper().startswith("LB"):
        weight *= 0.45359237
    return f"{weight:.4g}"


def _signs(form: Xfa1932a) -> List[str]:
    """The clinical signs the form lists, one per line of its problem text."""
    text = form.get("concurrentproblemstext") or ""
    return [line for line in (part.strip() for part in text.split("\n")) if line]
