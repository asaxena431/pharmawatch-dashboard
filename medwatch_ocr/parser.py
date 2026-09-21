"""Map OCR lines from a 3500A MedWatch form onto :class:`MedWatchReport`.

The parser is label driven and tolerant of OCR noise:

* leading item numbers (``A.2a``, ``G.1c`` ...) are used as a section hint,
* labels are normalised and matched against a registry with fuzzy matching so
  that ``Diagnosis For Use lndication`` still resolves to the indication field,
* long free-text blocks (narrative, lab data) absorb continuation lines.
"""

import difflib
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    CENTER_CDER,
    CENTER_CDRH,
    STAGE_POSTMARKET,
    STAGE_PREMARKET,
    AdverseEvent,
    ManufacturerInfo,
    MedWatchReport,
    Patient,
    Reporter,
    SuspectDevice,
    SuspectProduct,
)

# key -> (allowed section letters, label variants)
FIELD_REGISTRY: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "patient_identifier": (("A",), ("patient identifier", "patient id")),
    "patient_initials": (("A",), ("patient initials", "initials")),
    "patient_age": (("A",), ("age at time of event", "age")),
    "patient_dob": (("A",), ("date of birth",)),
    "patient_sex": (("A",), ("sex", "gender")),
    "patient_weight": (("A",), ("weight",)),
    "patient_ethnicity": (("A",), ("ethnicity", "race")),
    "event_type": (("B",), ("event type", "check all that apply")),
    "event_outcome": (("B",), ("outcome attributed to adverse event", "outcomes attributed to adverse event")),
    "event_date": (("B",), ("date of event",)),
    "report_date": (("B",), ("date of this report",)),
    "event_terms": (("B",), ("adverse event term", "adverse event terms", "event term")),
    "event_description": (
        ("B",),
        (
            "describe event problem or product use error",
            "describe event or problem",
            "description of event or problem",
        ),
    ),
    "relevant_tests": (("B",), ("relevant tests laboratory data", "relevant tests and laboratory data")),
    "other_history": (("B",), ("other relevant history", "other relevant patient history")),
    "product_name": (("C",), ("suspect product name", "name of suspect product", "product name")),
    "active_substance": (("C",), ("active substance", "generic name")),
    "dose": (("C",), ("dose",)),
    "frequency": (("C",), ("frequency",)),
    "route": (("C",), ("route used", "route of administration", "route")),
    "therapy_start": (("C",), ("therapy start date", "therapy dates start")),
    "therapy_stop": (("C",), ("therapy stop date", "therapy dates stop")),
    "indication": (("C",), ("diagnosis for use indication", "indication")),
    "dechallenge": (("C",), ("event abated after use stopped", "event abated after use stopped or dose reduced")),
    "rechallenge": (("C",), ("event reappeared after reintroduction",)),
    "ndc": (("C",), ("ndc number or unique id", "ndc number")),
    "device_brand": (("D",), ("brand name",)),
    "device_common_name": (("D",), ("common device name", "common name")),
    "product_code": (("D",), ("product code",)),
    "device_manufacturer": (("D",), ("manufacturer name",)),
    "device_manufacturer_address": (("D",), ("manufacturer address",)),
    "model": (("D",), ("model number",)),
    "catalog": (("D",), ("catalog number",)),
    "serial": (("D",), ("serial number",)),
    "udi": (("D",), ("unique device identifier", "udi")),
    "other_identifier": (("D",), ("other identifying number",)),
    "operator": (("D",), ("operator of device", "operator")),
    "implant_date": (("D",), ("implant date",)),
    "explant_date": (("D",), ("explant date",)),
    "reprocessed": (("D",), ("single use device reprocessed", "reprocessed and reused single use device")),
    "reprocessor": (("D",), ("reprocessor name",)),
    "device_available": (("D",), ("device available for evaluation",)),
    "device_returned_date": (("D",), ("date device returned to manufacturer",)),
    "lot_number": (("C", "D"), ("lot number",)),
    "expiration_date": (("C", "D"), ("expiration date",)),
    "concomitant_products": (("C", "D"), ("concomitant medical products",)),
    "reporter_name": (("E",), ("reporter name", "name and address")),
    "reporter_org": (("E",), ("reporter organization", "organization")),
    "reporter_address": (("E",), ("address",)),
    "reporter_phone": (("E",), ("phone number", "phone")),
    "reporter_email": (("E",), ("email", "e mail")),
    "health_professional": (("E",), ("health professional",)),
    "occupation": (("E",), ("occupation",)),
    "initial_reporter_fda": (("E",), ("initial reporter also sent report to fda", "also reported to fda")),
    "mfr_name": (("F", "G"), ("manufacturer name",)),
    "mfr_contact_office": (("F", "G"), ("contact office", "contact office name")),
    "mfr_address": (("F", "G"), ("address",)),
    "mfr_phone": (("F", "G"), ("phone number",)),
    "mfr_registration": (("F", "G"), ("manufacturer registration number", "registration number")),
    "report_source": (("F", "G"), ("report source", "report source code")),
    "date_received": (("F", "G"), ("date received by manufacturer",)),
    "mfr_report_number": (("F", "G"), ("manufacturer report number",)),
    "report_sequence": (("F", "G"), ("report type",)),
    "ae_type": (("F", "G"), ("adverse event type",)),
    "ind_nda": (("F", "G"), ("ind or nda number", "nda number", "ind number")),
    "pma_510k": (("F", "G"), ("pma or 510k number", "pma number", "510k number")),
    "study_name": (("F", "G"), ("study name",)),
    "study_number": (("F", "G"), ("sponsor study number", "study number")),
    "remedial_action": (("F", "G"), ("remedial action taken", "remedial action")),
    "evaluation_conclusion": (("G", "H"), ("device evaluation conclusion", "conclusion")),
}

LONG_TEXT_KEYS = {
    "event_description",
    "relevant_tests",
    "other_history",
    "evaluation_conclusion",
    "remedial_action",
    "reporter_address",
    "mfr_address",
    "device_manufacturer_address",
    "concomitant_products",
}

SECTION_HEADER_RE = re.compile(r"^([A-H])[\.\s]\s*[A-Z][A-Z \-/,&]{4,}$")
ITEM_PREFIX_RE = re.compile(r"^([A-H])[\.\,]?\s?(\d{1,2}[a-z]?)[\.\)]?\s+")
NOISE_RE = re.compile(
    r"^(page\s*\d+|form\s*approved|omb\s|see\s*omb|department of health"
    r"|for voluntary reporting|submission of a report does not)",
    re.I,
)

# Page headers / footers repeated on every page. OCR mangles them, so they are
# matched fuzzily rather than exactly.
BOILERPLATE = (
    "fda form 3500a medwatch mandatory reporting",
    "synthetic sample data for software testing not a real patient report",
    "drug biologic cder premarket ind safety report",
    "medical device cdrh postmarket medical device report",
)


def _is_boilerplate(line: str) -> bool:
    if NOISE_RE.match(line):
        return True
    normalized = re.sub(r"[^a-z0-9]+", " ", line.lower()).strip()
    if len(normalized) < 12:
        return False
    return any(
        difflib.SequenceMatcher(None, normalized, phrase).ratio() >= 0.7
        for phrase in BOILERPLATE
    )


def _normalize_label(raw: str) -> str:
    text = raw.lower()
    text = re.sub(r"^[a-h][\.\,]?\s?\d{1,2}[a-z]?[\.\)]?\s*", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_EXACT_WITH_SECTION: Dict[Tuple[str, str], str] = {}
_EXACT_ANY: Dict[str, str] = {}
for _key, (_sections, _variants) in FIELD_REGISTRY.items():
    for _variant in _variants:
        for _section in _sections:
            _EXACT_WITH_SECTION.setdefault((_section, _variant), _key)
        _EXACT_ANY.setdefault(_variant, _key)


def _resolve_label(label: str, section: Optional[str]) -> Optional[str]:
    normalized = _normalize_label(label)
    if not normalized:
        return None
    if section:
        key = _EXACT_WITH_SECTION.get((section, normalized))
        if key:
            return key
        candidates = [v for (s, v) in _EXACT_WITH_SECTION if s == section]
        close = difflib.get_close_matches(normalized, candidates, n=1, cutoff=0.84)
        if close:
            return _EXACT_WITH_SECTION[(section, close[0])]
    key = _EXACT_ANY.get(normalized)
    if key:
        return key
    close = difflib.get_close_matches(normalized, list(_EXACT_ANY), n=1, cutoff=0.88)
    return _EXACT_ANY[close[0]] if close else None


def _split_label_value(line: str) -> Optional[Tuple[str, str]]:
    # OCR frequently confuses ':' with ';' or '.' right after a label.
    match = re.search(r"[:;](?!\d)", line)
    if not match or match.start() > 80:
        return None
    return line[: match.start()].strip(), line[match.start() + 1 :].strip()


def parse_fields(lines: Sequence[str]) -> Tuple[Dict[str, str], List[str], Optional[str], Optional[str]]:
    """Return (fields, unmapped_lines, detected_center, detected_stage)."""
    fields: Dict[str, str] = {}
    unmapped: List[str] = []
    section: Optional[str] = None
    last_key: Optional[str] = None
    center: Optional[str] = None
    stage: Optional[str] = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        upper = line.upper()
        if "CDRH" in upper or "MEDICAL DEVICE REPORT" in upper:
            center = center or CENTER_CDRH
        elif "CDER" in upper or "DRUG" in upper and "BIOLOGIC" in upper:
            center = center or CENTER_CDER
        if "PREMARKET" in upper or "IND SAFETY" in upper:
            stage = stage or STAGE_PREMARKET
        elif "POSTMARKET" in upper or "POST-MARKET" in upper:
            stage = stage or STAGE_POSTMARKET

        header = SECTION_HEADER_RE.match(line)
        if header:
            section = header.group(1)
            last_key = None
            continue
        if _is_boilerplate(line):
            continue

        item = ITEM_PREFIX_RE.match(line)
        if item:
            section = item.group(1)

        split = _split_label_value(line)
        if split:
            label, value = split
            key = _resolve_label(label, section)
            if key:
                if key in fields and value:
                    fields[key] = f"{fields[key]}; {value}"
                else:
                    fields[key] = value
                last_key = key
                continue

        if last_key and last_key in LONG_TEXT_KEYS:
            fields[last_key] = f"{fields[last_key]} {line}".strip()
            continue
        unmapped.append(line)

    return fields, unmapped, center, stage


def _split_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[;,]|\band\b", value)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def _parse_age(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(year|yr|month|week|day|hour|decade)?", value, re.I)
    if not match:
        return None, None
    unit = (match.group(2) or "year").lower()
    unit = {"yr": "year"}.get(unit, unit)
    return match.group(1), unit


def _parse_weight(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|kilograms?|lb|lbs|pounds?)?", value, re.I)
    if not match:
        return None
    weight = float(match.group(1))
    unit = (match.group(2) or "kg").lower()
    if unit.startswith("lb") or unit.startswith("pound"):
        weight = round(weight * 0.45359237, 1)
    return f"{weight:g}"


def _parse_dose(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|iu|units?|%)", value, re.I)
    if not match:
        return None, None
    return match.group(1), match.group(2).lower()


def _parse_sex(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = value.strip().lower()
    if text.startswith("m"):
        return "Male"
    if text.startswith("f"):
        return "Female"
    return value.strip()


def build_report(
    lines: Sequence[str],
    center: Optional[str] = None,
    stage: Optional[str] = None,
    ocr_engine: Optional[str] = None,
    pages: int = 0,
) -> MedWatchReport:
    """Turn OCR lines into a :class:`MedWatchReport`."""
    fields, unmapped, detected_center, detected_stage = parse_fields(lines)

    if center is None:
        if detected_center:
            center = detected_center
        elif any(k in fields for k in ("device_brand", "model", "serial", "udi")):
            center = CENTER_CDRH
        else:
            center = CENTER_CDER
    if stage is None:
        stage = detected_stage or (STAGE_PREMARKET if fields.get("study_number") else STAGE_POSTMARKET)

    age, age_unit = _parse_age(fields.get("patient_age"))
    dose_number, dose_unit = _parse_dose(fields.get("dose"))

    report = MedWatchReport(
        center=center,
        stage=stage,
        report_id=fields.get("mfr_report_number") or fields.get("patient_identifier"),
        patient=Patient(
            identifier=fields.get("patient_identifier"),
            initials=fields.get("patient_initials"),
            age=age,
            age_unit=age_unit,
            date_of_birth=fields.get("patient_dob"),
            sex=_parse_sex(fields.get("patient_sex")),
            weight_kg=_parse_weight(fields.get("patient_weight")),
            ethnicity=fields.get("patient_ethnicity"),
        ),
        event=AdverseEvent(
            event_date=fields.get("event_date"),
            report_date=fields.get("report_date"),
            outcomes=_split_list(fields.get("event_outcome")),
            reactions=_split_list(fields.get("event_terms")),
            narrative=fields.get("event_description"),
            relevant_tests=fields.get("relevant_tests"),
            other_history=fields.get("other_history"),
            event_problem=fields.get("event_type"),
        ),
        reporter=Reporter(
            name=fields.get("reporter_name"),
            organization=fields.get("reporter_org"),
            address=fields.get("reporter_address"),
            phone=fields.get("reporter_phone"),
            email=fields.get("reporter_email"),
            occupation=fields.get("occupation"),
            health_professional=fields.get("health_professional"),
            initial_reporter_to_fda=fields.get("initial_reporter_fda"),
        ),
        manufacturer=ManufacturerInfo(
            name=fields.get("mfr_name") or fields.get("device_manufacturer"),
            contact_office=fields.get("mfr_contact_office"),
            address=fields.get("mfr_address"),
            phone=fields.get("mfr_phone"),
            report_number=fields.get("mfr_report_number"),
            registration_number=fields.get("mfr_registration"),
            date_received_by_manufacturer=fields.get("date_received"),
            date_of_this_report=fields.get("report_date"),
            report_sequence=fields.get("report_sequence"),
            report_source=fields.get("report_source"),
            adverse_event_type=fields.get("ae_type"),
            nda_ind_number=fields.get("ind_nda"),
            pma_510k_number=fields.get("pma_510k"),
            study_name=fields.get("study_name"),
            study_number=fields.get("study_number"),
            remedial_action=fields.get("remedial_action"),
            evaluation_conclusion=fields.get("evaluation_conclusion"),
        ),
        source_pages=pages,
        ocr_engine=ocr_engine,
        unmapped_lines=unmapped,
    )

    if fields.get("reporter_name"):
        parts = fields["reporter_name"].split()
        report.reporter.given_name = parts[0] if parts else None
        report.reporter.family_name = " ".join(parts[1:]) if len(parts) > 1 else None
    if report.reporter.address:
        country = report.reporter.address.strip().rstrip(".").split(",")[-1].strip()
        report.reporter.country = country if len(country) <= 3 else None

    if fields.get("product_name"):
        report.products.append(
            SuspectProduct(
                name=fields.get("product_name"),
                active_substance=fields.get("active_substance"),
                dose=fields.get("dose"),
                dose_number=dose_number,
                dose_unit=dose_unit,
                frequency=fields.get("frequency"),
                route=fields.get("route"),
                therapy_start=fields.get("therapy_start"),
                therapy_stop=fields.get("therapy_stop"),
                indication=fields.get("indication"),
                ndc=fields.get("ndc"),
                lot=fields.get("lot_number"),
                expiration=fields.get("expiration_date"),
                event_abated_after_stop=fields.get("dechallenge"),
                event_reappeared_after_reintroduction=fields.get("rechallenge"),
            )
        )

    if any(fields.get(k) for k in ("device_brand", "model", "serial", "udi", "product_code")):
        report.device = SuspectDevice(
            brand_name=fields.get("device_brand"),
            common_name=fields.get("device_common_name"),
            product_code=fields.get("product_code"),
            manufacturer_name=fields.get("device_manufacturer") or fields.get("mfr_name"),
            manufacturer_address=fields.get("device_manufacturer_address") or fields.get("mfr_address"),
            model_number=fields.get("model"),
            catalog_number=fields.get("catalog"),
            serial_number=fields.get("serial"),
            lot_number=fields.get("lot_number"),
            udi=fields.get("udi"),
            expiration=fields.get("expiration_date"),
            other_identifier=fields.get("other_identifier"),
            operator=fields.get("operator"),
            implant_date=fields.get("implant_date"),
            explant_date=fields.get("explant_date"),
            single_use_reprocessed=fields.get("reprocessed"),
            reprocessor_name=fields.get("reprocessor"),
            device_available_for_evaluation=fields.get("device_available"),
            device_returned_date=fields.get("device_returned_date"),
            concomitant_products=fields.get("concomitant_products"),
        )

    return report
