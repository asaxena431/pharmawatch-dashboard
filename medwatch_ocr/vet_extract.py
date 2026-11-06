"""Template-guided extraction from the genuine FDA Form 1932 (veterinary AER).

Mirrors :mod:`medwatch_ocr.form_extract` for the CVM form: the committed
template ``templates/fda_1932_2023.json`` holds the geometry of every AcroForm
widget of the published blank ``FORM FDA 1932 (8/23)``, so a filled copy can be
read either straight from its AcroForm (fillable PDF) or by PaddleOCR word boxes
assigned to the template rectangles (printed or scanned copy).  Keys are
``p{1-based page}.{field path}`` - the same keys the sample filler writes, which
makes the round-trip directly comparable.

The result is mapped onto :class:`~medwatch_ocr.models.VeterinaryReport`, whose
fields follow the VICH GL42 data elements printed on the form.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from .form_extract import (
    MIN_WORD_OVERLAP,
    ExtractedForm,
    _checkbox_marked,
    _overlap_fraction,
    _rect_to_pixels,
    _word_center,
)
from .form_1932 import field_key
from .models import (
    CENTER_CVM,
    ActiveIngredient,
    Animal,
    ClinicalSign,
    Organisation,
    Person,
    ProductDefect,
    VeterinaryEvent,
    VeterinaryOutcome,
    VeterinaryProduct,
    VeterinaryReport,
)

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "fda_1932_2023.json")

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Checkbox groups: report field -> (key, value) pairs, first marked box wins.
GENDER_BOXES = (
    ("p3.Lb15.fem.female", "Female"),
    ("p3.Lb15.mal.male", "Male"),
    ("p3.Lb15.mix.mixed", "Mixed"),
    ("p3.Lb15.unk.unknown", "Unknown"),
)
REPRODUCTIVE_BOXES = (
    ("p3.Lb16.int.intact", "Intact"),
    ("p3.Lb16.neu.neutered", "Neutered"),
    ("p3.Lb16.mix.mixedrep", "Mixed"),
    ("p3.Lb16.unk.unknownrep", "Unknown"),
)
WEIGHT_BASIS_BOXES = (
    ("p3.Lb181.meas.meas", "Measured"),
    ("p3.Lb181.est.estimated", "Estimated"),
    ("p3.Lb181.unk.unknownwei", "Unknown"),
)
AGE_BASIS_BOXES = (
    ("p3.Lb19.meas.measage", "Measured"),
    ("p3.Lb19.est.estimatedage", "Estimated"),
    ("p3.Lb19.unk.unknownage", "Unknown"),
)
MIN_AGE_UNIT_BOXES = (
    ("p3.Lb1921.sec.second", "Second"),
    ("p3.Lb1921.min.minute", "Minute"),
    ("p3.Lb1921.hr.hour", "Hour"),
    ("p3.Lb1921.day.day", "Day"),
    ("p3.Lb1921.mth.month", "Month"),
    ("p3.Lb1921.yr.year", "Year"),
)
MAX_AGE_UNIT_BOXES = (
    ("p3.Lb1931.sec.second2", "Second"),
    ("p3.Lb1931.min.minute2", "Minute"),
    ("p3.Lb1931.hr.hour2", "Hour"),
    ("p3.Lb1931.day.day2", "Day"),
    ("p3.Lb1931.mth.month2", "Month"),
    ("p3.Lb1931.yr.year2", "Year"),
)
INTERVAL_UNIT_BOXES = (
    ("p4.Lb2171311.sec.second2", "Second"),
    ("p4.Lb2171311.min.minute2", "Minute"),
    ("p4.Lb2171311.hr.hour2", "Hour"),
    ("p4.Lb2171311.day.day2", "Day"),
    ("p4.Lb2171311.mth.month2", "Month"),
    ("p4.Lb2171311.yr.year2", "Year"),
)
ADMINISTERED_BY_BOXES = (
    ("p5.Lb24.vet.veter", "Veterinarian"),
    ("p5.Lb24.own.owner", "Animal owner"),
    ("p5.Lb24.phys.phys", "Physician"),
    ("p5.Lb24.pat.patient", "Patient"),
    ("p5.Lb24.otherHC.otherpro", "Other health care professional"),
    ("p5.Lb24.mult.multiple", "Multiple"),
    ("p5.Lb24.oth.other", "Other"),
    ("p5.Lb24.unk.unknown", "Unknown"),
)
LABEL_USE_BOXES = (
    ("p5.Lb25.yes.yes1", "Yes"),
    ("p5.Lb25.no.no1", "No"),
    ("p5.Lb25.ni.noinfo1", "No information"),
)
DURATION_UNIT_BOXES = (
    ("p7.Lb3511.sec.second2", "Second"),
    ("p7.Lb3511.min.minute", "Minute"),
    ("p7.Lb3511.hr.hour2", "Hour"),
    ("p7.Lb3511.day.day2", "Day"),
    ("p7.Lb3511.mth.month2", "Month"),
    ("p7.Lb3511.yr.year2", "Year"),
)
SERIOUS_BOXES = (("p7.Lb36.yes.yesserious", "Yes"), ("p7.Lb36.no.noserious", "No"))
TREATED_BOXES = (
    ("p7.Lb37.yes.yesAE", "Yes"),
    ("p7.Lb37.no.noAE", "No"),
    ("p7.Lb37.unk.unknownAE", "Unknown"),
    ("p7.Lb37.ni.noinfoAE", "No information"),
)
PREVIOUS_EXPOSURE_BOXES = (
    ("p7.Lb39.yes.yes1", "Yes"),
    ("p7.Lb39.no.no1", "No"),
    ("p7.Lb39.unk.unknown1", "Unknown"),
    ("p7.Lb39.ni.nonapp1", "No information"),
)
PREVIOUS_REACTION_BOXES = (
    ("p7.Lb310.yes.yes3", "Yes"),
    ("p7.Lb310.no.no3", "No"),
    ("p7.Lb310.unk.unknown3", "Unknown"),
    ("p7.Lb310.ni.nonapp3", "No information"),
)
DECHALLENGE_BOXES = (
    ("p7.Lb41.yes.yes2", "Yes"),
    ("p7.Lb41.no.no2", "No"),
    ("p7.Lb41.unk.unknown2", "Unknown"),
    ("p7.Lb41.ni.noinfo2", "No information"),
    ("p7.Lb41.na.nonapp2", "Not applicable"),
)
RECHALLENGE_BOXES = (
    ("p7.Lb42.yes.yes4", "Yes"),
    ("p7.Lb42.no.no4", "No"),
    ("p7.Lb42.unk.unknown4", "Unknown"),
    ("p7.Lb42.ni.noinfo4", "No information"),
    ("p7.Lb42.na.nonapp4", "Not applicable"),
)
VET_ASSESSMENT_BOXES = (
    ("p7.Lb51.prob.prob", "Probable"),
    ("p7.Lb51.poss.poss", "Possible"),
    ("p7.Lb51.unl.unl", "Unlikely"),
    ("p7.Lb51.unk.unknownatt", "Unknown"),
    ("p7.Lb51.noassess.noassess", "No assessment"),
    ("p7.Lb51.novet.noattend", "No attending veterinarian"),
)
REPORT_CATEGORY_BOXES = (
    ("p8.Lb826.dom.domes", "Domestic"),
    ("p8.Lb826.forSa.foreignsame", "Foreign - same product"),
    ("p8.Lb826.forSi.reportsim", "Foreign - similar product"),
    ("p8.Lb826.other.otherrep", "Other"),
)
PROFILE_BOXES = (
    ("p8.Lb827.ae.adverse", "Adverse event"),
    ("p8.Lb827.aepp.adverseprod", "Adverse event and product problem"),
    ("p8.Lb827.pp.problem", "Product problem"),
)
REPORTER_CATEGORY_BOXES = (
    ("p1.Lrep.vet.veter", "Veterinarian"),
    ("p1.Lrep.own.owner", "Animal owner"),
    ("p1.Lrep.phys.phys", "Physician"),
    ("p1.Lrep.pat.patient", "Patient"),
    ("p1.Lrep.otherHC.prof", "Other health care professional"),
    ("p1.Lrep.other.other", "Other"),
    ("p1.Lrep.unk.unknown", "Unknown"),
)
SUBMISSION_TYPE_BOXES = (
    ("p2.LsubT.exp.exped", "Expedited"),
    ("p2.LsubT.per.peri", "Periodic"),
    ("p2.LsubT.fup.follow", "Follow-up"),
    ("p2.LsubT.nul.nullif", "Nullification"),
    ("p2.LsubT.fie.three", "Field alert / three-day"),
    ("p2.LsubT.oth.othertype", "Other"),
)
TIME_TO_ONSET_BOXES = (
    ("p7.Lb34.min.minute2", "<2 Minutes"),
    ("p7.Lb34.hr1.hour1", "<1 Hour"),
    ("p7.Lb34.hr12.hour12", "<12 Hours"),
    ("p7.Lb34.hr24.hour24", "<24 Hours"),
    ("p7.Lb34.hr48.hour48", "<48 Hours"),
    ("p7.Lb34.day3.day3", "<3 Days"),
    ("p7.Lb34.day7.day7", "<7 Days"),
    ("p7.Lb34.day14.day14", "<14 Days"),
    ("p7.Lb34.day30.day30", "<30 Days"),
    ("p7.Lb34.mth1t6.day30and", ">30 Days and <6 Months"),
    ("p7.Lb34.mth6t12.months6", ">6 Months and <12 Months"),
    ("p7.Lb34.mth12.months12", ">12 Months"),
    ("p7.Lb34.unk.timeunknow", "Unknown"),
)
# B.2.5.1.x off-label issues: label -> (yes, no, unknown, no-information) keys.
OFF_LABEL_ISSUES = {
    "Target species": ("p5.Lb2511.yes.yes2", "p5.Lb2511.no.no2"),
    "Route of exposure": ("p5.Lb2512.yes.yes3", "p5.Lb2512.no.no3"),
    "Overdose": ("p5.Lb2513.yes.yes4", "p5.Lb2513.no.no4"),
    "Underdose": ("p5.Lb2514.yes.yes5", "p5.Lb2514.no.no5"),
    "Treatment regime": ("p5.Lb2515.yes.yes6", "p5.Lb2515.no.no6"),
    "Indication": ("p5.Lb2516.yes.yes7", "p5.Lb2516.no.no7"),
    "Storage": ("p5.Lb2517.yes.yes8", "p5.Lb2517.no.no8"),
    "Expired product": ("p5.Lbi2518.yes.yes9", "p5.Lbi2518.no.no9"),
    "Other": ("p5.Lb2519.yes.yes10", "p5.Lb2519.no.no10"),
}


def load_template(path: str = TEMPLATE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_1932_form(pdf_path: str) -> bool:
    """True when ``pdf_path`` looks like the genuine FDA Form 1932."""
    template = load_template()
    try:
        from pypdf import PdfReader

        reader = PdfReader(pdf_path)
        fields = reader.get_fields() or {}
        if fields:
            present = {field_key(name).rsplit(".", 1)[-1] for name in fields}
            if len({"AERID", "ATCvet", "brandname", "RAname"} & present) >= 2:
                return True
        if len(reader.pages) != len(template["pages"]):
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
    return "FORM FDA 1932" in text or "VETERINARY ADVERSE DRUG REACTION" in text


def extract_1932_fields(pdf_path: str) -> ExtractedForm:
    """Read the official 1932 AcroForm values directly (no OCR)."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    result = ExtractedForm(pages=len(reader.pages), engine="acroform")
    for page_index, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            parts: List[str] = []
            node = widget
            while node is not None:
                title = node.get("/T")
                if title:
                    parts.append(str(title))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            if not parts:
                continue
            key = f"p{page_index + 1}.{field_key('.'.join(reversed(parts)))}"
            value = widget.get("/V")
            if value is None:
                parent = widget.get("/Parent")
                value = parent.get_object().get("/V") if parent is not None else None
            if value is None:
                continue
            text = str(value)
            if text.startswith("/"):
                if text != "/Off":
                    result.checks.append(key)
                continue
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                result.values[key] = text
    return result


def extract_1932_form(pdf_path: str, dpi: int = 200, lang: str = "en", template: Optional[dict] = None) -> ExtractedForm:
    """OCR the official 1932 form and read every field into an :class:`ExtractedForm`."""
    from .ocr import ocr_page_words

    template = template or load_template()
    per_page_words, images = ocr_page_words(pdf_path, dpi=dpi, lang=lang)
    scale = dpi / 72.0

    fields_by_page: Dict[int, List[dict]] = {}
    for spec in template["fields"]:
        fields_by_page.setdefault(spec["page"], []).append(spec)

    result = ExtractedForm(pages=len(images), engine="paddleocr")
    for page_index, image in enumerate(images):
        page_meta = template["pages"].get(str(page_index))
        if page_meta is None:
            continue
        page_height = float(page_meta["height"])
        words = per_page_words[page_index] if page_index < len(per_page_words) else []
        text_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] in ("Tx", "Ch")]
        box_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] == "Btn"]

        pixel_rects = [(_rect_to_pixels(s["rect"], page_height, scale), s) for s in text_specs]
        buckets: Dict[str, List[tuple]] = {}
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
            key = f"p{page_index + 1}.{best_spec['name']}"
            buckets.setdefault(key, []).append((_word_center(word)[1], word[0], word[4].strip()))

        for key, entries in buckets.items():
            entries.sort(key=lambda e: (round(e[0] / 6.0), e[1]))
            result.values[key] = re.sub(r"\s+", " ", " ".join(e[2] for e in entries)).strip()

        for spec in box_specs:
            if _checkbox_marked(image, _rect_to_pixels(spec["rect"], page_height, scale)):
                result.checks.append(f"p{page_index + 1}.{spec['name']}")

    return result


def _choice(form: ExtractedForm, boxes) -> Optional[str]:
    for key, label in boxes:
        if form.checked(key):
            return label
    return None


def _date(form: ExtractedForm, day_key: str, month_key: str, year_key: str) -> Optional[str]:
    day, month, year = form.get(day_key), form.get(month_key), form.get(year_key)
    if not (day and month and year):
        return None
    try:
        name = MONTHS[int(month) - 1]
    except (ValueError, IndexError):
        return f"{day}-{month}-{year}"
    return f"{int(day):02d}-{name}-{year}"


def _list(form: ExtractedForm, keys: List[str]) -> List[str]:
    return [value for value in (form.get(key) for key in keys) if value]


def map_veterinary_report(form: ExtractedForm, ocr_engine: Optional[str] = None) -> VeterinaryReport:
    """Map an :class:`ExtractedForm` of Form FDA 1932 onto a :class:`VeterinaryReport`."""
    report = VeterinaryReport(center=CENTER_CVM)
    report.source_pages = form.pages
    report.ocr_engine = ocr_engine or form.engine

    # Part A - administrative information
    report.aer_id = form.get("p2.AERID")
    report.report_identifier = form.get("p8.repident") or report.aer_id
    report.report_category = _choice(form, REPORT_CATEGORY_BOXES)
    report.profile_identifier = _choice(form, PROFILE_BOXES)
    report.submission_types = [label for key, label in SUBMISSION_TYPE_BOXES if form.checked(key)]

    report.type_of_information = form.get("p2.typeinfo")
    report.first_received_date = _date(form, "p2.dayA42", "p2.monthA42", "p2.yearA42")
    report.submission_date = _date(form, "p2.dayA43", "p2.monthA43", "p2.yearA43")

    report.regulatory_authority = Organisation(
        name=form.get("p1.RAname"),
        street=form.get("p1.RAadd"),
        city=form.get("p1.RAcity"),
        state=form.get("p1.RAstate"),
        postcode=form.get("p1.RAzip"),
        country=form.get("p1.RAcontry"),
    )
    report.marketing_authorisation_holder = Organisation(
        name=form.get("p1.MAHname"),
        street=form.get("p1.MAHadd"),
        city=form.get("p1.MAHcity"),
        state=form.get("p1.MAHstate"),
        postcode=form.get("p1.MAHzip"),
        country=form.get("p1.MAHcountry"),
    )
    report.mah_contact = Person(
        title=form.get("p1.title1"),
        given_name=form.get("p1.firstname1"),
        family_name=form.get("p1.lastname1"),
        phone=form.get("p1.Phone1"),
        fax=form.get("p1.faxNo1"),
        email=form.get("p1.email1"),
        organisation=report.marketing_authorisation_holder,
    )
    report.primary_reporter = Person(
        given_name=form.get("p1.firstname2"),
        family_name=form.get("p1.lastname2"),
        category=_choice(form, REPORTER_CATEGORY_BOXES),
        phone=form.get("p1.Phone2"),
        fax=form.get("p1.faxNo2"),
        email=form.get("p1.email2"),
        organisation=Organisation(
            name=form.get("p1.PRname"),
            street=form.get("p1.PRadd"),
            city=form.get("p1.PRcity"),
            state=form.get("p1.PRstate"),
            postcode=form.get("p1.PRzip"),
            country=form.get("p1.PRcountry"),
        ),
    )
    report.message_sender = Person(
        title=form.get("p8.messagesendtitle"),
        given_name=form.get("p8.messagesendfirst"),
        family_name=form.get("p8.messagesendlast"),
        phone=form.get("p8.messagesendtele"),
        fax=form.get("p8.messagesendfax"),
        email=form.get("p8.messagesendemail"),
    )
    report.message_number = form.get("p8.messagenumext") or form.get("p8.messagenumroot")
    report.message_date = _date(form, "p8.messcreateday", "p8.messcreatemonth", "p8.messcreateyear")

    # Part B.1 - animal data
    report.animal = Animal(
        species=form.get("p2.species"),
        breeds=_list(form, [f"p2.purebreed{i}" for i in (1, 2, 3)]),
        crossbreeds=_list(form, [f"p3.crossbreed{i}" for i in (1, 2, 3)]),
        gender=_choice(form, GENDER_BOXES),
        reproductive_status=_choice(form, REPRODUCTIVE_BOXES),
        physiological_status=_physiological_status(form),
        number_treated=form.get("p2.animtreat"),
        number_affected=form.get("p2.animaffect"),
        weight_min_kg=form.get("p3.minikilos"),
        weight_max_kg=form.get("p3.maxikilos"),
        weight_basis=_choice(form, WEIGHT_BASIS_BOXES),
        age_min=form.get("p3.miniage"),
        age_min_unit=_choice(form, MIN_AGE_UNIT_BOXES),
        age_max=form.get("p3.maxiage1_9_3"),
        age_max_unit=_choice(form, MAX_AGE_UNIT_BOXES),
        age_basis=_choice(form, AGE_BASIS_BOXES),
        health_before_treatment=form.get("p2.assesment"),
    )

    # Part B.2 - veterinary medicinal product
    ingredients = []
    for index in (1, 2, 3):
        name = form.get(f"p4.activeingred{index}")
        if not name:
            continue
        ingredients.append(
            ActiveIngredient(
                name=name,
                code=form.get(f"p4.activeingredcode{index}"),
                strength_value=form.get(f"p4.strengthnumvalue{index}"),
                strength_unit=form.get(f"p4.strengthnumunits{index}"),
                strength_denominator_value=form.get(f"p4.strengthdenomvalue{index}"),
                strength_denominator_unit=form.get(f"p4.strengthdenomunits{index}"),
            )
        )
    report.product = VeterinaryProduct(
        brand_name=form.get("p3.brandname"),
        product_code=form.get("p3.prodcode"),
        registration_id=form.get("p3.regID"),
        atc_vet_code=form.get("p3.ATCvet"),
        company=form.get("p3.compMAH"),
        dosage_form=form.get("p4.dosageform"),
        lot_number=form.get("p4.lotnumb"),
        expiration_date=_date(form, "p4.day", "p4.month", "p4.year"),
        route=form.get("p3.route"),
        dose_value=form.get("p3.dosenumvalue"),
        dose_unit=form.get("p3.dosenumunits"),
        dose_denominator_value=form.get("p3.dosedenomvalue"),
        dose_denominator_unit=form.get("p3.dosedenomunits"),
        administration_interval=form.get("p4.intervalofadmin"),
        administration_interval_unit=_choice(form, INTERVAL_UNIT_BOXES),
        first_exposure=_date(form, "p4.dayfirstexp", "p4.monthfirstexp", "p4.yearfirstexp"),
        last_exposure=_date(form, "p4.daylastexp", "p4.monthlastexp", "p4.yearlastexp"),
        administered_by=_choice(form, ADMINISTERED_BY_BOXES),
        used_according_to_label=_choice(form, LABEL_USE_BOXES),
        off_label_use=_off_label(form),
        active_ingredients=ingredients,
    )

    report.defect = ProductDefect(
        manufacturing_site=form.get("p5.manusite"),
        manufacturing_date=_date(form, "p5.day", "p5.month", "p5.year"),
        defective_items=form.get("p5.defitems"),
        defective_item_units=form.get("p5.defitemunits"),
        returned_items=form.get("p5.retitems"),
        returned_item_units=form.get("p5.retitemunits"),
        ora_district=form.get("p5.ORAdist"),
    )

    # Part B.3-B.5 - the event
    signs = []
    for index in range(1, 7):
        term = form.get(f"p6.Table1.Row{index}.adverse{index}")
        if not term:
            continue
        actual = form.checked(f"p6.Table1.Row{index}.acc{index}.act.actual{index}")
        estimated = form.checked(f"p6.Table1.Row{index}.acc{index}.est.estimated{index}")
        signs.append(
            ClinicalSign(
                term=term,
                animals_affected=form.get(f"p6.Table1.Row{index}.number{index}"),
                count_basis="Actual" if actual else ("Estimated" if estimated else None),
            )
        )
    narrative = " ".join(part for part in (form.get("p5.narrative"), form.get("p6.narrativecont")) if part)
    report.event = VeterinaryEvent(
        onset_date=_date(form, "p7.day", "p7.month", "p7.year"),
        time_to_onset=_choice(form, TIME_TO_ONSET_BOXES),
        duration=form.get("p7.duration1"),
        duration_unit=_choice(form, DURATION_UNIT_BOXES),
        serious=_choice(form, SERIOUS_BOXES),
        treated=_choice(form, TREATED_BOXES),
        narrative=narrative or None,
        signs=signs,
        outcome=VeterinaryOutcome(
            ongoing=form.get("p7.ongoing"),
            recovered_normal=form.get("p7.recovnormal"),
            recovered_with_sequela=form.get("p7.recovwseq"),
            died=form.get("p7.died"),
            euthanised=form.get("p7.euth"),
            unknown=form.get("p7.unknown"),
        ),
        previous_exposure=_choice(form, PREVIOUS_EXPOSURE_BOXES),
        previous_reaction=_choice(form, PREVIOUS_REACTION_BOXES),
        dechallenge=_choice(form, DECHALLENGE_BOXES),
        rechallenge=_choice(form, RECHALLENGE_BOXES),
        attending_vet_assessment=_choice(form, VET_ASSESSMENT_BOXES),
        mah_assessment=form.get("p3.MAHassessment"),
        ra_assessment=form.get("p3.RAassessment"),
        ra_assessment_explanation=form.get("p3.Explanation"),
    )

    report.linked_reports = form.get("p7.unique")
    report.attachments = _list(form, [f"p7.Table2.Row{i}.attached{i}" for i in (1, 2, 3)])
    return report


def _physiological_status(form: ExtractedForm) -> Optional[str]:
    for key, label in (
        ("p3.Lb17.lact.lact", "Nonpregnant lactating"),
        ("p3.Lb17.npl.nonlact", "Nonpregnant nonlactating"),
        ("p3.Lb17.pl.preglact", "Pregnant lactating"),
        ("p3.Lb17.pnl.pregnonlact", "Pregnant nonlactating"),
        ("p3.Lb17.mix.mixedphy", "Mixed"),
        ("p3.Lb17.na.nonapp", "Not applicable"),
        ("p3.Lb17.unk.unknownphy", "Unknown"),
    ):
        if form.checked(key):
            return label
    return None


def _off_label(form: ExtractedForm) -> Dict[str, str]:
    issues: Dict[str, str] = {}
    for label, (yes_key, no_key) in OFF_LABEL_ISSUES.items():
        if form.checked(yes_key):
            issues[label] = "Yes"
        elif form.checked(no_key):
            issues[label] = "No"
    return issues
