"""Structured representation of FDA adverse-event report forms.

:class:`MedWatchReport` mirrors the blocks of the paper Form 3500A (A-H) so that
OCR output can be mapped field-by-field and then serialised either as an
ICH E2B(R2) ICSR (drug reports, CDER) or as an FDA MDR report (device
reports, CDRH).

:class:`VeterinaryReport` does the same for Form FDA 1932, whose sections follow
the VICH GL42 data elements, and is serialised as a VICH GL42 AER (CVM).
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# Center / lifecycle stage of the report.
CENTER_CDER = "CDER"
CENTER_CDRH = "CDRH"
CENTER_CVM = "CVM"

STAGE_PREMARKET = "premarket"
STAGE_POSTMARKET = "postmarket"


@dataclass
class Patient:
    identifier: Optional[str] = None
    initials: Optional[str] = None
    age: Optional[str] = None
    age_unit: Optional[str] = None
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None
    weight_kg: Optional[str] = None
    weight: Optional[str] = None  # A.4 as written on the form
    weight_unit: Optional[str] = None  # "lbs" / "kg", as ticked on the form
    ethnicity: Optional[str] = None
    races: List[str] = field(default_factory=list)  # A.5, all that apply
    deceased_date: Optional[str] = None  # B.2 date of death


@dataclass
class LabTest:
    """Block B.3 - one relevant test with the date it was taken."""

    result: Optional[str] = None
    date: Optional[str] = None


@dataclass
class ConcomitantProduct:
    """Block D.9 - one concomitant medical product and its therapy start."""

    name: Optional[str] = None
    therapy_start: Optional[str] = None


@dataclass
class AdverseEvent:
    event_date: Optional[str] = None
    report_date: Optional[str] = None
    outcomes: List[str] = field(default_factory=list)
    reactions: List[str] = field(default_factory=list)
    narrative: Optional[str] = None
    relevant_tests: Optional[str] = None
    other_history: Optional[str] = None
    event_problem: Optional[str] = None  # B.1 checkbox text (adverse event / product problem)
    additional_comments: Optional[str] = None
    location: Optional[str] = None  # F.5 where the event happened
    report_types: List[str] = field(default_factory=list)  # B.1, all that apply
    test_results: List[LabTest] = field(default_factory=list)  # B.3 row by row
    patient_problem_code: Optional[str] = None  # F.6 health effect - clinical code
    patient_impact_code: Optional[str] = None  # F.6 health effect - impact code


@dataclass
class SuspectProduct:
    """Block C - suspect medication (drug/biologic)."""

    name: Optional[str] = None
    active_substance: Optional[str] = None
    dose: Optional[str] = None
    dose_number: Optional[str] = None
    dose_unit: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    therapy_start: Optional[str] = None
    therapy_stop: Optional[str] = None
    indication: Optional[str] = None
    ndc: Optional[str] = None
    lot: Optional[str] = None
    expiration: Optional[str] = None
    event_abated_after_stop: Optional[str] = None  # dechallenge
    event_reappeared_after_reintroduction: Optional[str] = None  # rechallenge


@dataclass
class SuspectDevice:
    """Block D - suspect medical device."""

    brand_name: Optional[str] = None
    common_name: Optional[str] = None
    product_code: Optional[str] = None
    manufacturer_name: Optional[str] = None
    manufacturer_address: Optional[str] = None
    model_number: Optional[str] = None
    catalog_number: Optional[str] = None
    serial_number: Optional[str] = None
    lot_number: Optional[str] = None
    udi: Optional[str] = None
    expiration: Optional[str] = None
    other_identifier: Optional[str] = None
    operator: Optional[str] = None
    implant_date: Optional[str] = None
    explant_date: Optional[str] = None
    single_use_reprocessed: Optional[str] = None
    reprocessor_name: Optional[str] = None
    device_available_for_evaluation: Optional[str] = None
    device_returned_date: Optional[str] = None
    concomitant_products: Optional[str] = None
    concomitants: List[ConcomitantProduct] = field(default_factory=list)  # D.9 row by row
    problem_code: Optional[str] = None  # F.10 medical device problem code
    age: Optional[str] = None  # F.8 approximate age of device
    age_unit: Optional[str] = None  # "Year" / "Month"
    labeled_single_use: Optional[str] = None
    evaluated_by_manufacturer: Optional[str] = None
    serviced_by_third_party: Optional[str] = None


@dataclass
class Reporter:
    """Block E - initial reporter."""

    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    organization: Optional[str] = None
    address: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    occupation: Optional[str] = None
    country: Optional[str] = None
    health_professional: Optional[str] = None
    initial_reporter_to_fda: Optional[str] = None


@dataclass
class ManufacturerInfo:
    """Blocks F/G/H - manufacturer / submitter data."""

    name: Optional[str] = None
    contact_office: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    report_number: Optional[str] = None
    registration_number: Optional[str] = None
    date_received_by_manufacturer: Optional[str] = None
    date_of_this_report: Optional[str] = None
    report_sequence: Optional[str] = None  # initial / follow-up N
    report_source: Optional[str] = None
    adverse_event_type: Optional[str] = None  # death / serious injury / malfunction
    nda_ind_number: Optional[str] = None
    pma_510k_number: Optional[str] = None
    study_name: Optional[str] = None
    study_number: Optional[str] = None
    remedial_action: Optional[str] = None
    evaluation_conclusion: Optional[str] = None
    exemption_number: Optional[str] = None
    report_sent_to_manufacturer: Optional[str] = None
    notified_name_address: Optional[str] = None  # F.13 manufacturer the facility told
    corrective_action_number: Optional[str] = None
    related_report_numbers: Optional[str] = None


@dataclass
class UserFacility:
    """Block F - the user facility or importer that reported the event to FDA."""

    report_number: Optional[str] = None
    name: Optional[str] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None
    contact_name: Optional[str] = None
    contact_given_name: Optional[str] = None
    contact_family_name: Optional[str] = None
    phone: Optional[str] = None
    date_aware: Optional[str] = None  # F.4 date user facility became aware
    date_sent_to_fda: Optional[str] = None  # F.7 date report sent to FDA
    report_type: Optional[str] = None  # initial / follow-up


@dataclass
class MedWatchReport:
    center: str = CENTER_CDER
    stage: str = STAGE_POSTMARKET
    report_id: Optional[str] = None
    patient: Patient = field(default_factory=Patient)
    event: AdverseEvent = field(default_factory=AdverseEvent)
    products: List[SuspectProduct] = field(default_factory=list)
    device: SuspectDevice = field(default_factory=SuspectDevice)
    reporter: Reporter = field(default_factory=Reporter)
    manufacturer: ManufacturerInfo = field(default_factory=ManufacturerInfo)
    user_facility: UserFacility = field(default_factory=UserFacility)
    source_pages: int = 0
    ocr_engine: Optional[str] = None
    unmapped_lines: List[str] = field(default_factory=list)

    @property
    def is_device_report(self) -> bool:
        return self.center == CENTER_CDRH

    def to_dict(self) -> dict:
        return asdict(self)


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
