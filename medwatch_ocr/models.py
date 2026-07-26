"""Structured representation of an FDA Form 3500A (MedWatch) report.

The model deliberately mirrors the blocks of the paper form (A-H) so that OCR
output can be mapped field-by-field and then serialised either as an
ICH E2B(R2) ICSR (drug reports, CDER) or as an FDA MDR report (device
reports, CDRH).
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional

# Center / lifecycle stage of the report.
CENTER_CDER = "CDER"
CENTER_CDRH = "CDRH"

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
    ethnicity: Optional[str] = None


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


@dataclass
class Reporter:
    """Block E - initial reporter."""

    name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    organization: Optional[str] = None
    address: Optional[str] = None
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
    source_pages: int = 0
    ocr_engine: Optional[str] = None
    unmapped_lines: List[str] = field(default_factory=list)

    @property
    def is_device_report(self) -> bool:
        return self.center == CENTER_CDRH

    def to_dict(self) -> dict:
        return asdict(self)
