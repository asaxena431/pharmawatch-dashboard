"""Structured representation of Form FDA 1932 / 1932a veterinary adverse event reports.

:class:`VeterinaryReport` mirrors the sections of the form, which follow the
VICH GL42 data elements, so a form read by OCR or from its XFA dataset can be
mapped field-by-field and then serialised as a VICH GL42 AER for FDA CVM.
"""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

CENTER_CVM = "CVM"
STAGE_POSTMARKET = "postmarket"

@dataclass
class Organisation:
    """A party on Form FDA 1932 part A (regulatory authority, MAH, reporter site)."""

    name: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None

    @property
    def address(self) -> Optional[str]:
        parts = [p for p in (self.street, self.city, self.state, self.postcode, self.country) if p]
        return ", ".join(parts) or None


@dataclass
class Person:
    """A named contact on Form FDA 1932 (MAH contact, primary/other reporter, sender)."""

    title: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    category: Optional[str] = None  # A.3.1.1 reporter category
    phone: Optional[str] = None
    fax: Optional[str] = None
    email: Optional[str] = None
    organisation: Organisation = field(default_factory=Organisation)

    @property
    def name(self) -> Optional[str]:
        parts = [p for p in (self.given_name, self.family_name) if p]
        return " ".join(parts) or None


@dataclass
class Animal:
    """Form FDA 1932 part B.1 - the treated animal(s)."""

    species: Optional[str] = None
    breeds: List[str] = field(default_factory=list)
    crossbreeds: List[str] = field(default_factory=list)
    gender: Optional[str] = None
    reproductive_status: Optional[str] = None
    physiological_status: Optional[str] = None
    number_treated: Optional[str] = None
    number_affected: Optional[str] = None
    weight_min_kg: Optional[str] = None
    weight_max_kg: Optional[str] = None
    weight_basis: Optional[str] = None  # measured / estimated / unknown
    age_min: Optional[str] = None
    age_min_unit: Optional[str] = None
    age_max: Optional[str] = None
    age_max_unit: Optional[str] = None
    age_basis: Optional[str] = None
    health_before_treatment: Optional[str] = None


@dataclass
class ActiveIngredient:
    """Form FDA 1932 part B.2.2 - one active ingredient of the VMP."""

    name: Optional[str] = None
    code: Optional[str] = None
    strength_value: Optional[str] = None
    strength_unit: Optional[str] = None
    strength_denominator_value: Optional[str] = None
    strength_denominator_unit: Optional[str] = None


@dataclass
class VeterinaryProduct:
    """Form FDA 1932 part B.2 - the veterinary medicinal product (VMP)."""

    brand_name: Optional[str] = None
    product_code: Optional[str] = None
    registration_id: Optional[str] = None
    atc_vet_code: Optional[str] = None
    company: Optional[str] = None
    dosage_form: Optional[str] = None
    lot_number: Optional[str] = None
    expiration_date: Optional[str] = None
    route: Optional[str] = None
    dose_value: Optional[str] = None
    dose_unit: Optional[str] = None
    dose_denominator_value: Optional[str] = None
    dose_denominator_unit: Optional[str] = None
    administration_interval: Optional[str] = None
    administration_interval_unit: Optional[str] = None
    first_exposure: Optional[str] = None
    last_exposure: Optional[str] = None
    administered_by: Optional[str] = None
    used_according_to_label: Optional[str] = None
    off_label_use: Dict[str, str] = field(default_factory=dict)  # issue -> Yes/No/No information
    active_ingredients: List[ActiveIngredient] = field(default_factory=list)


@dataclass
class ClinicalSign:
    """Form FDA 1932 part B.3.2 - one observed clinical manifestation."""

    term: Optional[str] = None
    animals_affected: Optional[str] = None
    count_basis: Optional[str] = None  # actual / estimated


@dataclass
class VeterinaryOutcome:
    """Form FDA 1932 part B.3.8 - number of animals per outcome."""

    ongoing: Optional[str] = None
    recovered_normal: Optional[str] = None
    recovered_with_sequela: Optional[str] = None
    died: Optional[str] = None
    euthanised: Optional[str] = None
    unknown: Optional[str] = None


@dataclass
class VeterinaryEvent:
    """Form FDA 1932 parts B.3-B.5 - the adverse event and its assessment."""

    onset_date: Optional[str] = None
    time_to_onset: Optional[str] = None
    duration: Optional[str] = None
    duration_unit: Optional[str] = None
    serious: Optional[str] = None
    treated: Optional[str] = None
    narrative: Optional[str] = None
    signs: List[ClinicalSign] = field(default_factory=list)
    outcome: VeterinaryOutcome = field(default_factory=VeterinaryOutcome)
    previous_exposure: Optional[str] = None
    previous_reaction: Optional[str] = None
    dechallenge: Optional[str] = None
    rechallenge: Optional[str] = None
    attending_vet_assessment: Optional[str] = None
    mah_assessment: Optional[str] = None
    ra_assessment: Optional[str] = None
    ra_assessment_explanation: Optional[str] = None


@dataclass
class ProductDefect:
    """Form FDA 1932 part B.2.6 - product/manufacturing defect details."""

    manufacturing_site: Optional[str] = None
    site_identifier_type: Optional[str] = None
    manufacturing_date: Optional[str] = None
    defective_items: Optional[str] = None
    defective_item_units: Optional[str] = None
    returned_items: Optional[str] = None
    returned_item_units: Optional[str] = None
    ora_district: Optional[str] = None


@dataclass
class VeterinaryReport:
    """A Form FDA 1932 veterinary adverse experience report (CVM -> VICH GL42)."""

    center: str = CENTER_CVM
    aer_id: Optional[str] = None
    report_identifier: Optional[str] = None
    report_category: Optional[str] = None  # domestic / foreign
    profile_identifier: Optional[str] = None  # adverse event / product problem
    submission_types: List[str] = field(default_factory=list)
    submission_reason: Optional[str] = None
    type_of_information: Optional[str] = None
    first_received_date: Optional[str] = None
    submission_date: Optional[str] = None
    regulatory_authority: Organisation = field(default_factory=Organisation)
    marketing_authorisation_holder: Organisation = field(default_factory=Organisation)
    mah_contact: Person = field(default_factory=Person)
    primary_reporter: Person = field(default_factory=Person)
    other_reporter: Person = field(default_factory=Person)
    message_sender: Person = field(default_factory=Person)
    message_number: Optional[str] = None
    message_date: Optional[str] = None
    animal: Animal = field(default_factory=Animal)
    product: VeterinaryProduct = field(default_factory=VeterinaryProduct)
    defect: ProductDefect = field(default_factory=ProductDefect)
    event: VeterinaryEvent = field(default_factory=VeterinaryEvent)
    linked_reports: Optional[str] = None
    linked_report_type: Optional[str] = None
    attachments: List[str] = field(default_factory=list)
    source_pages: int = 0
    ocr_engine: Optional[str] = None

    @property
    def report_id(self) -> Optional[str]:
        return self.aer_id or self.report_identifier

    def to_dict(self) -> dict:
        return asdict(self)
