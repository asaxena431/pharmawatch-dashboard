"""Template-guided extraction from the genuine FDA Form 3500A.

The published blank form has a fixed geometry, so every filled copy of the
``09/2025`` revision places each field in exactly the same box.  A committed
template (``templates/fda_3500a_2025.json``) records, for every AcroForm widget,
its page, field name, type and rectangle in PDF points.

Extraction is genuine OCR: each page is rasterised and run through PaddleOCR to
obtain word boxes, then every recognised word is assigned to the template
rectangle that contains it.  Checkbox state is read from the pixels of the box
(marked boxes have ink inside their border).  The result is mapped onto the same
:class:`MedWatchReport` used by the rest of the pipeline, so E2B(R2)/MDR
serialisation is shared with the flat-layout path.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
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

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "fda_3500a_2025.json")

# Fraction of dark ink (inside the inset box) above which a checkbox counts as marked.
CHECKBOX_INK_THRESHOLD = 0.05
# A word is assigned to a field box when at least this much of it falls inside.
MIN_WORD_OVERLAP = 0.5
_DATE_RE = re.compile(r"\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}")

# A few widget rectangles overlap their printed caption, which OCR then reads
# as part of the value.
LABEL_PREFIXES = {
    "mfr": "Mfr report #",
    "ufNum": "UF/Importer Report #",
    "varNum": "Exemption/Variance/ Alternative #",
}


@dataclass
class ExtractedForm:
    values: Dict[str, str] = field(default_factory=dict)  # "p{page}.{field}" -> text
    checks: List[str] = field(default_factory=list)  # marked checkbox keys "p{page}.{field}"
    pages: int = 0
    engine: str = "paddleocr"

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = self.values.get(key)
        return value if value not in (None, "") else default

    def checked(self, key: str) -> bool:
        return key in self.checks


def load_template(path: str = TEMPLATE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_official_form(pdf_path: str) -> bool:
    """True when ``pdf_path`` looks like the genuine FDA 3500A form.

    A fillable copy is recognised by its AcroForm field names.  A printed or
    scanned copy is recognised by the form's fixed shape: the template's page
    count and page size.  Field *values* are never read here - extraction itself
    is always done by OCR.
    """
    template = load_template()
    expected_pages = len(template["pages"])
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        fields = reader.get_fields() or {}
        if fields:
            present = {name.split(".")[-1].replace("[0]", "") for name in fields}
            if len({"patID", "advEvDesc", "dateAdvEvent"} & present) >= 2:
                return True
        if len(reader.pages) != expected_pages:
            return False
        first = reader.pages[0].mediabox
        reference = template["pages"]["0"]
        if abs(float(first.width) - reference["width"]) > 2 or abs(float(first.height) - reference["height"]) > 2:
            return False
    except Exception:
        return False
    try:
        from .ocr import extract_text_layer

        text = " ".join(extract_text_layer(pdf_path)).upper()
    except Exception:
        return True  # right shape, no readable text layer: treat as a scan of the form
    if not text.strip():
        return True
    return "3500A" in text and "MEDWATCH" in text


def _rect_to_pixels(rect: Sequence[float], page_height: float, scale: float) -> Tuple[float, float, float, float]:
    """Convert a PDF-point rectangle (origin bottom-left) to image pixels (origin top-left)."""
    x0, y0, x1, y1 = rect
    return (x0 * scale, (page_height - y1) * scale, x1 * scale, (page_height - y0) * scale)


def _word_center(word: Tuple[float, float, float, float, str]) -> Tuple[float, float]:
    return ((word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0)


def _overlap_fraction(
    word: Tuple[float, float, float, float, str],
    rect: Tuple[float, float, float, float],
) -> float:
    """Fraction of the word's area that lies inside ``rect``."""
    wx0, wy0, wx1, wy1 = word[0], word[1], word[2], word[3]
    rx0, ry0, rx1, ry1 = rect
    inter_w = min(wx1, rx1) - max(wx0, rx0)
    inter_h = min(wy1, ry1) - max(wy0, ry0)
    if inter_w <= 0 or inter_h <= 0:
        return 0.0
    area = (wx1 - wx0) * (wy1 - wy0)
    return (inter_w * inter_h) / area if area > 0 else 0.0


def _strip_label(key: str, text: str) -> str:
    """Remove a printed caption that shares the widget rectangle."""
    name = key.split(".", 1)[-1]
    prefix = LABEL_PREFIXES.get(name)
    if not prefix:
        return text
    pattern = r"^\s*" + r"\s*".join(re.escape(part) for part in prefix.split()) + r"\s*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


def _checkbox_marked(image, pixel_rect: Tuple[float, float, float, float]) -> bool:
    import numpy as np

    x0, y0, x1, y1 = (int(round(v)) for v in pixel_rect)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return False
    inset_x = max(1, int((x1 - x0) * 0.2))
    inset_y = max(1, int((y1 - y0) * 0.2))
    crop = image.convert("L").crop((x0 + inset_x, y0 + inset_y, x1 - inset_x, y1 - inset_y))
    array = np.asarray(crop)
    if array.size == 0:
        return False
    return float((array < 128).mean()) >= CHECKBOX_INK_THRESHOLD


def extract_form(
    pdf_path: str,
    dpi: int = 200,
    lang: str = "en",
    template: Optional[dict] = None,
) -> ExtractedForm:
    """OCR the official 3500A and read every field into an :class:`ExtractedForm`."""
    from .ocr import ocr_page_words

    template = template or load_template()
    per_page_words, images = ocr_page_words(pdf_path, dpi=dpi, lang=lang)
    return _extract_by_geometry(template, per_page_words, images, dpi=dpi, engine="paddleocr")


def extract_form_text_layer(
    pdf_path: str,
    dpi: int = 200,
    template: Optional[dict] = None,
) -> ExtractedForm:
    """Read a *flattened* official 3500A from its text layer using form geometry.

    A printed-to-PDF copy of the form has no AcroForm values left, but the typed
    values are still real text at their original coordinates, so they can be
    assigned to the template rectangles exactly - no OCR needed.  Checkbox state
    is still read from the rendered pixels.
    """
    from .ocr import text_layer_page_words

    template = template or load_template()
    per_page_words, images = text_layer_page_words(pdf_path, dpi=dpi)
    return _extract_by_geometry(template, per_page_words, images, dpi=dpi, engine="text-layer")


def _extract_by_geometry(
    template: dict,
    per_page_words: List[List[Tuple[float, float, float, float, str]]],
    images: Sequence[object],
    dpi: int,
    engine: str,
) -> ExtractedForm:
    """Assign word boxes to the template's field rectangles and read checkboxes."""
    scale = dpi / 72.0

    fields_by_page: Dict[int, List[dict]] = {}
    for spec in template["fields"]:
        fields_by_page.setdefault(spec["page"], []).append(spec)

    result = ExtractedForm(pages=len(images), engine=engine)

    for page_index, image in enumerate(images):
        page_meta = template["pages"].get(str(page_index))
        if page_meta is None:
            continue
        page_height = float(page_meta["height"])
        words = per_page_words[page_index] if page_index < len(per_page_words) else []
        text_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] in ("Tx", "Ch")]
        box_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] == "Btn"]

        pixel_rects = [(_rect_to_pixels(s["rect"], page_height, scale), s) for s in text_specs]
        buckets: Dict[str, List[Tuple[float, float, str]]] = {}
        for word in words:
            if not word[4].strip():
                continue
            best_spec = None
            best_overlap = 0.0
            for rect, spec in pixel_rects:
                overlap = _overlap_fraction(word, rect)
                if overlap > best_overlap:
                    best_overlap, best_spec = overlap, spec
            if best_spec is None or best_overlap < MIN_WORD_OVERLAP:
                continue
            key = f"p{page_index}.{best_spec['name']}"
            buckets.setdefault(key, []).append((word[1], word[3], word[0], word[4].strip()))

        for key, entries in buckets.items():
            text = _reading_order(entries)
            result.values[key] = _strip_label(key, text)

        for spec in box_specs:
            pixel_rect = _rect_to_pixels(spec["rect"], page_height, scale)
            if _checkbox_marked(image, pixel_rect):
                result.checks.append(f"p{page_index}.{spec['name']}")

    return result


def _reading_order(entries: List[Tuple[float, float, float, str]]) -> str:
    """Join words of one field in reading order.

    ``entries`` are ``(top, bottom, left, text)``.  Multi-line fields (narratives)
    are recovered by clustering words into text lines first: a word joins the
    current line while its vertical centre stays within a fraction of the line's
    height, which keeps wrapped sentences in order.
    """
    heights = [e[1] - e[0] for e in entries if e[1] > e[0]]
    tolerance = (sum(heights) / len(heights)) * 0.6 if heights else 6.0

    lines: List[List[Tuple[float, float, float, str]]] = []
    for entry in sorted(entries, key=lambda e: ((e[0] + e[1]) / 2.0, e[2])):
        centre = (entry[0] + entry[1]) / 2.0
        if lines and abs(centre - (lines[-1][0][0] + lines[-1][0][1]) / 2.0) <= tolerance:
            lines[-1].append(entry)
        else:
            lines.append([entry])

    parts: List[str] = []
    for line in lines:
        line.sort(key=lambda e: e[2])
        parts.extend(e[3] for e in line)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def extract_form_fields(pdf_path: str) -> ExtractedForm:
    """Read the official form's AcroForm values directly (no OCR).

    Used when the uploaded 3500A is still a fillable digital PDF, where the field
    values are authoritative and OCR would only add noise.
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    result = ExtractedForm(pages=len(reader.pages), engine="acroform")
    for page_index, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            parent = widget.get("/Parent")
            name = widget.get("/T") or (parent.get("/T") if parent else None)
            if name is None:
                continue
            value = widget.get("/V")
            if value is None and parent is not None:
                value = parent.get("/V")
            if value is None:
                continue
            key = f"p{page_index}.{str(name).replace('[0]', '')}"
            text = str(value)
            if text.startswith("/"):
                if text != "/Off":
                    result.checks.append(key)
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                result.values[key] = _strip_label(key, text)
    return result


def _first(form: ExtractedForm, *keys: str) -> Optional[str]:
    for key in keys:
        value = form.get(key)
        if value:
            return value
    return None


def _clean_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    match = _DATE_RE.search(value)
    return match.group(0) if match else value


def map_report(
    form: ExtractedForm,
    center: Optional[str] = None,
    stage: Optional[str] = None,
    ocr_engine: Optional[str] = None,
) -> MedWatchReport:
    """Map an :class:`ExtractedForm` onto a :class:`MedWatchReport`."""
    device_present = bool(_first(form, "p5.brandName", "p5.modelNum", "p5.serNum"))
    resolved_center = center or (CENTER_CDRH if device_present else CENTER_CDER)
    if stage:
        resolved_stage = stage
    else:
        resolved_stage = STAGE_POSTMARKET if resolved_center == CENTER_CDRH else STAGE_PREMARKET

    report = MedWatchReport(center=resolved_center, stage=resolved_stage)
    report.source_pages = form.pages
    report.ocr_engine = ocr_engine or form.engine
    report.report_id = _first(form, "p7.manuRepNum", "p0.mfr")

    # A. Patient
    age_unit = None
    if form.checked("p0.ageYrs"):
        age_unit = "Year"
    elif form.checked("p0.ageMons"):
        age_unit = "Month"
    elif form.checked("p0.ageWks"):
        age_unit = "Week"
    elif form.checked("p0.ageDays"):
        age_unit = "Day"
    sex = "Male" if form.checked("p0.sexM") else ("Female" if form.checked("p0.sexF") else None)
    weight = weight_in_kg(form.get("p0.patWeight"), pounds=form.checked("p0.weightLB"))
    report.patient = Patient(
        identifier=form.get("p0.patID"),
        age=form.get("p0.patAge"),
        age_unit=age_unit,
        date_of_birth=_clean_date(form.get("p0.patDOB")),
        sex=sex,
        weight_kg=weight,
    )

    # B. Adverse event
    outcomes: List[str] = []
    for key, label in (
        ("p0.death", "Death"),
        ("p0.lifeThr", "Life-threatening"),
        ("p0.hospital", "Hospitalization"),
        ("p0.disability", "Disability"),
        ("p0.congenital", "Congenital anomaly"),
        ("p0.reqInterv", "Required intervention"),
        ("p0.otherOutcome", "Other serious"),
    ):
        if form.checked(key):
            outcomes.append(label)
    event_types = []
    if form.checked("p0.adverse"):
        event_types.append("Adverse Event")
    if form.checked("p0.prodProblem"):
        event_types.append("Product Problem")
    narrative = _first(form, "p1.advEvDescribe", "p0.advEvDesc")
    tests = "; ".join(
        v for v in (
            _join_test(form, 1), _join_test(form, 2), _join_test(form, 3), _join_test(form, 4)
        ) if v
    )
    report.event = AdverseEvent(
        event_date=_clean_date(form.get("p0.dateAdvEvent")),
        report_date=_clean_date(form.get("p0.dateReport")),
        outcomes=outcomes,
        reactions=_split_terms(form.get("p7.advTerms")),
        narrative=narrative,
        relevant_tests=tests or None,
        other_history=form.get("p2.otherHist"),
        event_problem=" and ".join(event_types) or None,
    )

    # C. Suspect product (drug/biologic)
    product_name = form.get("p3.prodName1")
    if product_name:
        report.products = [
            SuspectProduct(
                name=product_name,
                dose_number=form.get("p3.dose1") or form.get("p3.prodStr1"),
                dose_unit=_choice_code(form.get("p3.doseUnit1")),
                dose=_join_dose(form.get("p3.dose1"), form.get("p3.doseUnit1")),
                frequency=form.get("p3.freq1"),
                route=form.get("p3.route1"),
                therapy_start=_clean_date(form.get("p3.start1Date")),
                therapy_stop=_clean_date(form.get("p3.end1Date")),
                indication=form.get("p3.diagnosis1"),
                ndc=form.get("p3.ndc1"),
                lot=form.get("p3.lot1"),
                expiration=_clean_date(form.get("p3.expDate1")),
                event_abated_after_stop=_yes_no(form, "p3.abate1Yes", "p3.abate1No", "p3.abate1NA"),
                event_reappeared_after_reintroduction=_yes_no(
                    form, "p3.reappear1Yes", "p3.reappear1No", "p3.reappear1NA"
                ),
            )
        ]

    # D. Suspect device
    report.device = SuspectDevice(
        brand_name=form.get("p5.brandName"),
        common_name=form.get("p5.commonName"),
        product_code=form.get("p5.proCode"),
        manufacturer_name=form.get("p5.manuNameAddr"),
        manufacturer_address=form.get("p5.manuNameAddr"),
        model_number=form.get("p5.modelNum"),
        catalog_number=form.get("p5.catNum"),
        serial_number=form.get("p5.serNum"),
        lot_number=form.get("p5.lotNum"),
        udi=form.get("p5.udi"),
        expiration=_clean_date(form.get("p5.expDate")),
        operator=_device_operator(form),
        implant_date=_clean_date(form.get("p6.implantDate")),
        explant_date=_clean_date(form.get("p6.explantDate")),
        single_use_reprocessed=_yes_no(form, "p6.singleUseY", "p6.singleUseN"),
        device_available_for_evaluation=_device_evaluation(form),
        device_returned_date=_clean_date(form.get("p6.returnDate")),
        concomitant_products=_concomitant(form),
    )

    # E. Initial reporter
    report.reporter = Reporter(
        given_name=form.get("p6.reportFirst"),
        family_name=form.get("p6.reportLast"),
        name=_full_name(form.get("p6.reportFirst"), form.get("p6.reportLast")),
        address=_reporter_address(form),
        phone=form.get("p6.reportPhone"),
        email=form.get("p6.reportEmail"),
        occupation=form.get("p6.repOccupation"),
        country=form.get("p6.reportCountry"),
        health_professional=_yes_no(form, "p6.repHPY", "p6.repHPN"),
        initial_reporter_to_fda=_yes_no(form, "p6.reportFDAY", "p6.reportFDAN", "p6.reportFDAU"),
    )

    # F/G/H. Manufacturer / submitter
    report.manufacturer = ManufacturerInfo(
        name=form.get("p7.manuName"),
        address=form.get("p7.manuAddr"),
        phone=form.get("p7.manuPhone"),
        report_number=_first(form, "p7.manuRepNum", "p7.reportNum", "p0.mfr"),
        date_received_by_manufacturer=_clean_date(form.get("p7.reportManuRecDate")),
        report_sequence="Initial" if form.checked("p7.reptInitial") else None,
        report_source=_report_source(form),
        adverse_event_type=_event_type(form),
        nda_ind_number=_first(form, "p7.numIND", "p7.numNDA", "p7.numANDA", "p7.numBLA"),
        pma_510k_number=form.get("p7.numPMA"),
        study_number=form.get("p7.protNum"),
        remedial_action=_remedial(form),
        evaluation_conclusion=_join_parts(form.get("p8.invFindings"), form.get("p8.invConc"), form.get("p8.addNarr")),
    )

    return report


# A copy may list more suspect products than the official form's two boxes; the
# extra ones use the same key convention on the pages that follow.
MAX_SUSPECTS = 6


def suspect_page(index: int) -> int:
    """The key prefix (``p{page}.``) suspect product ``index`` is stored under."""
    return index + 2


def weight_in_kg(value: Optional[str], pounds: bool) -> Optional[str]:
    """Normalise block A.4 to kilograms; both XML formats state the unit themselves."""
    if not value:
        return None
    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return None
    weight = float(match.group(0))
    if pounds:
        weight = round(weight * 0.45359237, 1)
    return f"{weight:g}"


def _join_parts(*values: Optional[str]) -> Optional[str]:
    present = [v.strip().rstrip(".") for v in values if v and v.strip()]
    return ". ".join(present) + "." if present else None


def _join_test(form: ExtractedForm, index: int) -> Optional[str]:
    data = form.get(f"p2.testData{index}")
    if not data:
        return None
    date = _clean_date(form.get(f"p2.testDDate{index}"))
    return f"{data} ({date})" if date else data


def _split_terms(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in re.split(r"[;,]", value) if part.strip()]


def _choice_code(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if " - " in value:
        return value.rsplit(" - ", 1)[-1].strip()
    return value


def _join_dose(amount: Optional[str], unit: Optional[str]) -> Optional[str]:
    code = _choice_code(unit)
    if amount and code:
        return f"{amount} {code}"
    return amount or code


def _yes_no(form: ExtractedForm, yes_key: str, no_key: str, na_key: Optional[str] = None) -> Optional[str]:
    if form.checked(yes_key):
        return "Yes"
    if form.checked(no_key):
        return "No"
    if na_key and form.checked(na_key):
        return "Not applicable"
    return None


def _device_operator(form: ExtractedForm) -> Optional[str]:
    if form.checked("p6.devOpHP"):
        return "Health Professional"
    if form.checked("p6.devOpPat"):
        return "Patient/Consumer"
    if form.checked("p6.devOpOther"):
        return form.get("p6.devOpOtherSpec") or "Other"
    return None


def _device_evaluation(form: ExtractedForm) -> Optional[str]:
    if form.checked("p6.evalRet"):
        return "Returned to manufacturer"
    if form.checked("p6.evalY"):
        return "Yes"
    if form.checked("p6.evalN"):
        return "No"
    return None


def _concomitant(form: ExtractedForm) -> Optional[str]:
    names = [form.get(f"p5.cProdName{i}") for i in range(1, 11)]
    names += [form.get(f"p6.cProdName{i}") for i in range(1, 11)]
    present = [n for n in names if n]
    return "; ".join(present) or None


def _full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    parts = [p for p in (first, last) if p]
    return " ".join(parts) or None


def _reporter_address(form: ExtractedForm) -> Optional[str]:
    parts = [
        form.get("p6.reportAddr"),
        form.get("p6.reportCity"),
        form.get("p6.reportSt"),
        form.get("p6.reportZip"),
        form.get("p6.reportCountry"),
    ]
    present = [p for p in parts if p]
    return ", ".join(present) or None


def _report_source(form: ExtractedForm) -> Optional[str]:
    sources = []
    for key, label in (
        ("p7.repsrcFor", "Foreign"),
        ("p7.rptsrcStu", "Study"),
        ("p7.repsrcLit", "Literature"),
        ("p7.repsrcCons", "Consumer"),
        ("p7.repsrcHP", "Health Professional"),
        ("p7.repsrcUF", "User Facility"),
        ("p7.repsrcCR", "Company Representative"),
        ("p7.repsrcDI", "Distributor/Importer"),
    ):
        if form.checked(key):
            sources.append(label)
    return "; ".join(sources) or None


def _event_type(form: ExtractedForm) -> Optional[str]:
    types = []
    if form.checked("p8.eventDeath"):
        types.append("Death")
    if form.checked("p8.eventSInj"):
        types.append("Serious Injury")
    if form.checked("p8.eventMal"):
        types.append("Malfunction")
    return " and ".join(types) or None


def _remedial(form: ExtractedForm) -> Optional[str]:
    actions = []
    for key, label in (
        ("p8.remRecall", "Recall"),
        ("p8.remRepair", "Repair"),
        ("p8.remReplace", "Replace"),
        ("p8.remRel", "Relabeling"),
        ("p8.remNot", "Notification"),
        ("p8.remInsp", "Inspection"),
        ("p8.remMonitor", "Patient Monitoring"),
        ("p8.remMod", "Modification/Adjustment"),
    ):
        if form.checked(key):
            actions.append(label)
    if form.checked("p8.remOther"):
        actions.append(form.get("p8.remOtherSpec") or "Other")
    return "; ".join(actions) or None
