"""Where each 3500A field sits relative to its printed caption.

One table serves every 3500A copy that has no widget template: the captions are
prescribed by the form, so a report printed from the 11/22 revision and one
rendered by a safety system as a "3500A facsimile" carry the same wording even
though the boxes are laid out differently.  Keys are the ones the official form
uses (``p{page}.{field}``) so mapping and XML serialisation are shared.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .form_extract import MAX_SUSPECTS, ExtractedForm, suspect_page
from .label_extract import Anchor, CheckAnchor, extract_by_captions

# In a continuation section each box is captioned with its block ("C2. Dose...").
CONTINUED_BOX = r"^(?:[a-h]\s*\d{1,2}\s*\.|block [a-h] )"
# A continuation section ends where the next block, or the page footer, begins.
CONTINUED_SECTION = r"^(?:[a-h]\s*\d{1,2}\s*\.|additional information|form fda|page \d+ of)"
# Free text that runs on captions where it goes; the carried part is joined back.
CARRY_MARKER = re.compile(r"\n?[^\n]*continued in additional info section\.*", re.IGNORECASE)
CARRIED_TEXT = (
    ("p0.advEvDesc", "EVENT DESCRIPTION (Continued)"),
    ("p2.testData", "RELEVANT TESTS (Continued)"),
    ("p2.otherHist", "OTHER RELEVANT HISTORY"),
)

DATE = r"^(?:\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4})$"
NUMBER = r"^\d+(?:[.,]\d+)?$"
# A row read out of an empty dose box holds only the therapy dates beside it.
DATES_ONLY = re.compile(
    r"^(?:(?:\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4}|to|-|ongoing|unk(?:nown)?)\s*)+$",
    re.IGNORECASE,
)
WORD = r"^[A-Za-z][A-Za-z./'-]*$"

ANCHORS: Tuple[Anchor, ...] = (
    Anchor("p0.mfr", r"mfr report #", where="right"),
    Anchor("p0.patID", r"1\.\s*patient identifier", where="below", x_slack=60, limit=1),
    Anchor("p0.patAge", r"2\.\s*age(?:\s*at time)?", keep=NUMBER, x_slack=40, limit=1),
    Anchor("p0.ageText", r"2\.\s*age(?:\s*at time)?", keep=r"^(?:years?|months?|weeks?|days?)$", x_slack=40, limit=1),
    Anchor("p0.sexText", r"3a?\.\s*sex", keep=r"^(?:male|female)$", x_slack=40, limit=1),
    Anchor("p0.weightText", r"4\.\s*weight", keep=r"^(?:\d+(?:[.,]\d+)?|kgs?|lbs?)$", x_slack=40, lines=3, limit=4, any_font=True),
    Anchor("p0.dateAdvEvent", r"3\.\s*date of event", keep=DATE, x_slack=40, limit=1),
    Anchor("p0.dateReport", r"4\.\s*date of (?:this|the) report", keep=DATE, x_slack=40, limit=1),
    Anchor("p0.deathDate", r"date of death", where="right", keep=DATE, limit=1),
    # Free-text boxes: everything typed between this caption and the next section.
    Anchor(
        "p0.advEvDesc",
        r"5\.\s*describe event or problem",
        lines=400,
        x_from=60,
        x_slack=520,
        stop=r"^(?:6\.\s*relevant test|form fda-3500a|page \d+ of)",
        drop=r"^(?:\[preferred|term\]|verbatim|\(related|symptoms|separated|commas\)|if|by)$",
        wide=True,
        by_line=True,
    ),
    Anchor(
        "p2.testData",
        r"6\.\s*relevant tests?/laboratory data",
        lines=400,
        x_from=60,
        x_slack=520,
        stop=r"^(?:7\.\s*other relevant history|form fda-3500a|page \d+ of)",
        wide=True,
        by_line=True,
    ),
    Anchor(
        "p2.otherHist",
        r"7\.\s*other relevant history",
        lines=400,
        x_from=60,
        x_slack=520,
        stop=r"^(?:8\.\s*|form fda-3500a|page \d+ of|e\.\s*initial reporter)",
        wide=True,
        by_line=True,
    ),
    # C. Suspect product(s): read as whole boxes, then split into rows (see _rows).
    Anchor(
        "rows.prodName",
        r"(?<![a-z])1\.\s*name(?:s)?[ ,(](?!and address|& address)",
        lines=8,
        repeat=MAX_SUSPECTS,
        markers=True,
        wide=True,
    ),
    Anchor(
        "rows.dose",
        r"(?<![a-z])2\.\s*dose,? (?:frequency|or amount)",
        lines=8,
        repeat=MAX_SUSPECTS,
        markers=True,
        wide=True,
    ),
    Anchor(
        "rows.therapy",
        r"(?<![a-z])(?:3\.\s*therapy dates|4\.\s*trea\s*t\s*ment dates)",
        lines=8,
        repeat=MAX_SUSPECTS,
        markers=True,
        wide=True,
    ),
    Anchor(
        "rows.diagnosis",
        r"(?<![a-z])(?:4|5)\.\s*diagnosis for use(?! \(continued)",
        lines=8,
        repeat=MAX_SUSPECTS,
        markers=True,
        wide=True,
    ),
    Anchor(
        "rows.concomitant",
        r"(?<![a-z])10\.\s*concomitant medical products(?! \(continued)",
        lines=30,
        x_from=20,
        markers=True,
        wide=True,
    ),
    Anchor("rows.concomitant2", r"2\.\s*list medical product and treatment given", lines=30, x_from=20, markers=True, wide=True),
    # Free text a vendor layout carries into its "additional information" pages.
    Anchor(
        "p0.advEvDescCont",
        r"b\d+\.\s*event description \(continued\)",
        lines=400,
        x_from=20,
        stop=CONTINUED_SECTION,
        wide=True,
        by_line=True,
        runs_on=True,
    ),
    Anchor(
        "p2.testDataCont",
        r"b\d+\.\s*relevant tests? \(continued\)",
        lines=400,
        x_from=20,
        stop=CONTINUED_SECTION,
        wide=True,
        by_line=True,
        runs_on=True,
    ),
    Anchor(
        "p2.otherHistCont",
        r"b\d+\.\s*other relevant history",
        lines=400,
        x_from=20,
        stop=CONTINUED_SECTION,
        wide=True,
        by_line=True,
        runs_on=True,
    ),
    # A revision that gives every suspect product a page of its own captions the
    # parts of the box separately; those captions are read as one row per copy.
    Anchor("rows.prodNameSub", r"^product name(?= strength|$)", repeat=MAX_SUSPECTS),
    Anchor("rows.doseSub", r"(?<![a-z])3\.\s*dose or amount", repeat=MAX_SUSPECTS),
    Anchor("rows.doseUnitSub", r"^unit\b", repeat=MAX_SUSPECTS, limit=1),
    Anchor("rows.routeSub", r"(?<!other )route$", repeat=MAX_SUSPECTS),
    # Rows a vendor layout carries over into its "(continued)" section.
    Anchor("rows.prodNameCont", r"c1\s*\.\s*name \(continued\)", lines=12, x_from=20, stop=CONTINUED_BOX, markers=True, wide=True),
    Anchor("rows.doseCont", r"c2\.\s*dose,? frequency", lines=12, x_from=20, stop=CONTINUED_BOX, markers=True, wide=True),
    Anchor("rows.therapyCont", r"c3\.\s*therapy dates", lines=12, x_from=20, stop=CONTINUED_BOX, markers=True, wide=True),
    Anchor("rows.diagnosisCont", r"c4\.\s*diagnosis for use", lines=12, x_from=20, stop=CONTINUED_BOX, markers=True, wide=True),
    Anchor(
        "rows.concomitantCont",
        r"c10\.\s*concomitant medical products",
        lines=40,
        x_from=20,
        stop=CONTINUED_BOX,
        markers=True,
        wide=True,
    ),
    # E. Initial reporter.  A revision that captions the parts of the name and
    # address separately is read part by part; one that prints a single "name and
    # address" box is read as a block and split afterwards (see _reporter).
    Anchor(
        "p6.nameBlock",
        r"1\.\s*name (?:and address|&)",
        lines=6,
        x_slack=220,
        stop=r"^(?:phone|2\.\s|submission of a report)",
        wide=True,
        by_line=True,
    ),
    Anchor("p6.reportLast", r"last name", x_slack=70, limit=2),
    Anchor("p6.reportFirst", r"first name", x_slack=70, limit=2),
    Anchor("p6.reportAddr", r"^address$", x_slack=220, wide=True),
    Anchor("p6.reportCity", r"^city\b", x_slack=70, limit=2),
    Anchor("p6.reportSt", r"state/province", x_slack=70, limit=2),
    Anchor("p6.reportZip", r"zip/postal", x_slack=70, limit=1),
    Anchor("p6.reportCountry", r"country$", x_slack=90, limit=3),
    Anchor("p6.reportPhone", r"phone\s*#", x_slack=70, limit=1),
    Anchor("p6.reportEmail", r"email(?: address)?", keep=r"^\S+@\S+$", x_slack=90, limit=1, any_font=True, section="e"),
    Anchor("p6.repOccupation", r"3\.\s*occupation", x_slack=90, keep=WORD, drop=r"^(?:fda|select|from|list\)?|n)$", limit=1),
    # G. All manufacturers
    Anchor(
        "p7.manuBlock",
        r"1\.\s*contact office(?:\s*\(and manufacturing site for devices\))?",
        lines=8,
        x_slack=200,
        stop=r"^(?:email|2\.\s|3\.\s|compounding)",
        any_font=True,
        by_line=True,
    ),
    Anchor("p7.manuAddr", r"^address$", occurrence=2, x_slack=260, lines=4, stop=r"^(?:compounding|2\.\s|3\.\s|email)", wide=True),
    Anchor("p7.manuPhone", r"phone number", keep=r"^[-()\d][-()\d ]{6,}$", x_slack=110, limit=1),
    Anchor("p7.manuEmail", r"email address", keep=r"^\S+@\S+$", x_slack=110, limit=1, any_font=True, section="g"),
    Anchor("p7.reportManuRecDate", r"(?:3|4)\.\s*date received by", keep=DATE, x_slack=160, lines=3, limit=1, wide=True),
    # A revision prints the IND number under its caption, a vendor beside it.
    Anchor("p7.numIND", r"ind\s*#", keep=r"^\d{4,}$", x_slack=60, limit=1),
    Anchor("p7.numINDRight", r"ind\s*#", where="right", keep=r"^\d{4,}$", limit=1, any_font=True),
    Anchor("p7.repFollowNum", r"follow-?up\s*#", where="right", keep=NUMBER, limit=1, any_font=True),
    Anchor(
        "p7.protNum",
        r"(?:if ind[^,]*, give protocol|give protocol\s*#)",
        keep=r"^[A-Za-z0-9]+[-/][A-Za-z0-9/-]+$",
        drop=r"^(?:bla|nda|anda|pma|distributor|other|checked?)$",
        x_from=30,
        x_slack=140,
        limit=1,
    ),
    Anchor("p7.advTerms", r"(?:7|8)\.\s*adverse event term", lines=6, x_slack=200, stop=r"privacy act|^\d{1,2}\.\s"),
    Anchor("p7.manuRepNum", r"(?:8|9)\.\s*manufacturer report number", x_slack=140, limit=1),
)

# The age-unit checkbox each typed unit stands for.
AGE_UNIT_BOXES = {"year": "p0.ageYrs", "month": "p0.ageMons", "week": "p0.ageWks", "day": "p0.ageDays"}

CHECKS: Tuple[CheckAnchor, ...] = (
    # A. Patient information
    CheckAnchor("p0.sexF", r"\bfemale\b"),
    CheckAnchor("p0.sexM", r"(?<!fe)\bmale\b"),
    CheckAnchor("p0.ageYrs", r"(?<![-\w])years?\b|(?<![-\w])year\(s\)"),
    CheckAnchor("p0.ageMons", r"(?<![-\w])months?\b|(?<![-\w])month\(s\)"),
    CheckAnchor("p0.ageWks", r"(?<![-\w])weeks?\b|(?<![-\w])week\(s\)"),
    CheckAnchor("p0.ageDays", r"(?<![-\w])days?\b|(?<![-\w])day\(s\)"),
    CheckAnchor("p0.weightLB", r"\blbs?\b"),
    CheckAnchor("p0.white", r"\bwhite\b"),
    CheckAnchor("p0.black", r"black or african american"),
    CheckAnchor("p0.asian", r"\basian\b"),
    CheckAnchor("p0.AmInAlNa", r"american indian"),
    CheckAnchor("p0.NaHIOtherPI", r"native hawaiian"),
    CheckAnchor("p0.hispanic", r"(?<!not )hispanic/latino|(?<!not )hispanic or latino"),
    # B. Adverse event or product problem
    CheckAnchor("p0.death", r"(?<!of )\bdeath\b"),
    CheckAnchor("p0.lifeThr", r"life-threatening"),
    CheckAnchor("p0.hospital", r"\bhospitali[sz]ation\b"),
    CheckAnchor("p0.disability", r"\bdisability\b"),
    CheckAnchor("p0.congenital", r"congenital anomaly"),
    CheckAnchor("p0.reqInterv", r"required intervention"),
    CheckAnchor("p0.otherOutcome", r"other serious"),
    CheckAnchor("p0.adverse", r"\badverse event\b(?! term)"),
    CheckAnchor("p0.prodProblem", r"\bproduct problem\b"),
    # G.2 Report source and G.6/G.7 Type of report
    CheckAnchor("p7.repsrcFor", r"\bforeign\b"),
    CheckAnchor("p7.rptsrcStu", r"\bstudy\b"),
    CheckAnchor("p7.repsrcLit", r"\bliterature\b"),
    CheckAnchor("p7.repsrcCons", r"\bconsumer\b"),
    CheckAnchor("p7.repsrcHP", r"health\s*professional(?!\?)"),
    CheckAnchor("p7.repsrcUF", r"use\s*r\s*facil\s*i\s*ty|user facility"),
    CheckAnchor("p7.repsrcCR", r"company representative|^company$"),
    CheckAnchor("p7.repsrcDI", r"distributor"),
    CheckAnchor("p7.rep5", r"(?<!\d)5-day"),
    CheckAnchor("p7.rep7", r"(?<!\d)7-day"),
    CheckAnchor("p7.rep10", r"(?<!\d)10-day"),
    CheckAnchor("p7.rep15", r"(?<!\d)15-day"),
    CheckAnchor("p7.rep30", r"(?<!\d)30-day"),
    CheckAnchor("p7.repPer", r"\bperiodic\b"),
    CheckAnchor("p7.repInit", r"\binitial\b(?!\s*(?:or prolonged|reporter))"),
    CheckAnchor("p7.repFollow", r"follow-?up\s*#"),
    CheckAnchor("p7.combo", r"combination\s*product"),
)

ROW = re.compile(r"(?:#\s*(\d+)\s*[.):]?(?=\s|$|[A-Za-z])|(?<![\d/])(\d+)\s*\))")

# What a copy prints where a row runs on into its continuation section.
CARRIED = re.compile(r"\(continued\)|continued in additional info section\.*", re.IGNORECASE)

# "588 milligram, single, Intravenous": the parts of a dose typed as one row.
DOSE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([A-Za-z%/]+(?:\s*/\s*[A-Za-z%]+)?)?\s*(?:,\s*(?P<rest>.*))?$")
ROUTES = (
    "intravenous",
    "oral",
    "subcutaneous",
    "intramuscular",
    "topical",
    "transdermal",
    "inhalation",
    "ophthalmic",
    "rectal",
    "intratumoral",
    "intraperitoneal",
    "unknown",
)
_DAY = r"\d{1,2}[-/][A-Za-z0-9]{2,3}[-/]\d{4}"
DATE_RANGE = re.compile(rf"({_DAY})\s*(?:to\s*)?({_DAY}|ongoing)?", re.IGNORECASE)


def extract_by_labels(pdf_path: str, dpi: int = 150) -> ExtractedForm:
    """Read a 3500A of unknown revision by anchoring on its printed captions."""
    form = extract_by_captions(pdf_path, ANCHORS, CHECKS, dpi=dpi)
    _sex_from_text(form)
    _weight_unit(form)
    _age_unit(form)
    _reporter(form)
    _manufacturer(form)
    _ind_number(form)
    _suspect_products(form)
    _concomitant_products(form)
    _carried_text(form)
    return form


def _carried_text(form: ExtractedForm) -> None:
    """Join a free-text box to the part of it printed in the continuation pages."""
    for key, heading in CARRIED_TEXT:
        carried = form.values.pop(f"{key}Cont", None)
        text = form.get(key)
        if not carried or not text:
            continue
        form.values[key] = f"{CARRY_MARKER.sub('', text).rstrip()}\n\n{heading}\n{carried}"


def _reporter(form: ExtractedForm) -> None:
    """Split a single "name and address" box into the reporter's name and address.

    A revision that captions the parts separately fills those keys directly; this
    only fills what such a box leaves empty, taking the first line as the name and
    the rest as the address.
    """
    block = form.values.pop("p6.nameBlock", "")
    if not block:
        return
    email = re.search(r"\S+@\S+", block)
    if email and not form.get("p6.reportEmail"):
        form.values["p6.reportEmail"] = email.group(0)
        block = (block[: email.start()] + block[email.end() :]).strip()
    parts = [part.strip() for part in block.splitlines() if part.strip()]
    if not parts:
        return
    name = parts[0].split()
    if name and not form.get("p6.reportLast"):
        form.values["p6.reportLast"] = name[-1]
        if len(name) > 1:
            form.values["p6.reportFirst"] = " ".join(name[:-1])
    if len(parts) > 1 and not form.get("p6.reportAddr"):
        form.values["p6.reportAddr"] = " ".join(parts[1:])


def _manufacturer(form: ExtractedForm) -> None:
    """Block G.1 prints the contact's name on its first line and the address below."""
    lines = [line.strip() for line in form.values.pop("p7.manuBlock", "").splitlines() if line.strip()]
    if not lines:
        return
    if not form.get("p7.manuName"):
        form.values["p7.manuName"] = lines[0]
    if len(lines) > 1 and not form.get("p7.manuAddr"):
        form.values["p7.manuAddr"] = " ".join(lines[1:])


def _ind_number(form: ExtractedForm) -> None:
    beside = form.values.pop("p7.numINDRight", None)
    if beside and not form.get("p7.numIND"):
        form.values["p7.numIND"] = beside


def _sex_from_text(form: ExtractedForm) -> None:
    """Some copies print the patient's sex as text where the form has checkboxes."""
    sex = (form.values.pop("p0.sexText", "") or "").lower()
    if sex.startswith("f") and "p0.sexF" not in form.checks:
        form.checks.append("p0.sexF")
    elif sex.startswith("m") and "p0.sexM" not in form.checks:
        form.checks.append("p0.sexM")


def _weight_unit(form: ExtractedForm) -> None:
    """A weight box may hold both readings ("80.3 kgs 177.0 lbs"); prefer kilograms."""
    text = form.values.pop("p0.weightText", "")
    if not text:
        return
    kilograms = re.search(r"(\d+(?:\.\d+)?)\s*kgs?", text, re.IGNORECASE)
    pounds = re.search(r"(\d+(?:\.\d+)?)\s*lbs?", text, re.IGNORECASE)
    if kilograms:
        form.values["p0.patWeight"] = kilograms.group(1)
        if "p0.weightLB" in form.checks:
            form.checks.remove("p0.weightLB")
        return
    if pounds:
        form.values["p0.patWeight"] = pounds.group(1)
        if "p0.weightLB" not in form.checks:
            form.checks.append("p0.weightLB")
        return
    numbers = [float(number) for number in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return
    if len(numbers) > 1:
        # Both readings typed without their units: kilograms is the smaller one.
        form.values["p0.patWeight"] = f"{min(numbers):g}"
        if "p0.weightLB" in form.checks:
            form.checks.remove("p0.weightLB")
    else:
        # One reading only: the unit is whichever box the copy has ticked.
        form.values["p0.patWeight"] = f"{numbers[0]:g}"


def _age_unit(form: ExtractedForm) -> None:
    """Some copies type the age unit ("69 years") where the form has checkboxes."""
    unit = (form.values.pop("p0.ageText", "") or "").lower()
    key = next((box for word, box in AGE_UNIT_BOXES.items() if unit.startswith(word)), None)
    if key:
        for other in AGE_UNIT_BOXES.values():
            if other in form.checks:
                form.checks.remove(other)
        form.checks.append(key)


def _rows(text: Optional[str]) -> Dict[int, str]:
    """Split a box listing several products ("#1. ... #2. ...") into its rows."""
    if not text:
        return {}
    marks = list(ROW.finditer(text))
    if not marks:
        return {1: text.strip()}
    rows: Dict[int, str] = {}
    for position, mark in enumerate(marks):
        end = marks[position + 1].start() if position + 1 < len(marks) else len(text)
        number = int(mark.group(1) or mark.group(2))
        value = CARRIED.sub(" ", text[mark.end() : end]).strip(" ,;")
        if value and number not in rows:
            rows[number] = value
    return rows


def _suspect_products(form: ExtractedForm) -> None:
    """Fill the serialiser's per-product keys from the boxes read row by row."""
    names = _product_rows(form, "rows.prodNameSub", "rows.prodName")
    doses = _product_rows(form, "rows.doseSub", "rows.dose")
    units = _product_rows(form, "rows.doseUnitSub")
    routes = _product_rows(form, "rows.routeSub")
    therapy = _product_rows(form, "rows.therapy")
    diagnosis = _product_rows(form, "rows.diagnosis")
    for index in range(1, MAX_SUSPECTS + 1):
        prefix = f"p{suspect_page(index)}."
        if not names.get(index):
            continue  # a box read without a product name is not a product
        form.values[f"{prefix}prodName{index}"] = names[index]
        _dose_parts(form, prefix, index, doses.get(index))
        for key, rows in (("doseUnit", units), ("route", routes)):
            if rows.get(index) and not form.get(f"{prefix}{key}{index}"):
                form.values[f"{prefix}{key}{index}"] = rows[index]
        if diagnosis.get(index):
            form.values[f"{prefix}diagnosis{index}"] = diagnosis[index]
        dates = DATE_RANGE.search(therapy.get(index, ""))
        if dates:
            form.values[f"{prefix}start{index}Date"] = dates.group(1)
            if dates.group(2) and dates.group(2).lower() != "ongoing":
                form.values[f"{prefix}end{index}Date"] = dates.group(2)


def _dose_parts(form: ExtractedForm, prefix: str, index: int, row: Optional[str]) -> None:
    """Split a dose row ("588 milligram, single, Intravenous") into its own fields."""
    if not row or row.strip().lower() in {"unk", "unknown", "n/a"}:
        return
    if DATES_ONLY.match(row.strip()):
        return  # the therapy dates of a row whose dose box is empty
    match = DOSE.match(row.strip())
    if not match:
        form.values[f"{prefix}dose{index}"] = row
        return
    form.values[f"{prefix}dose{index}"] = match.group(1)
    if match.group(2):
        form.values[f"{prefix}doseUnit{index}"] = match.group(2)
    parts = [part.strip() for part in (match.group("rest") or "").split(",") if part.strip()]
    route = next((part for part in parts if part.lower() in ROUTES), None)
    if route:
        form.values[f"{prefix}route{index}"] = route
        parts.remove(route)
    if parts:
        form.values[f"{prefix}freq{index}"] = ", ".join(parts)


def _product_rows(form: ExtractedForm, key: str, also: Optional[str] = None) -> Dict[int, str]:
    """The rows of a suspect-product box, however the copy repeats the product.

    One revision prints a whole block per product, so the n-th copy of the box is
    product n; another lists the products inside a single box, numbered "#1., #2.";
    a vendor layout does both and carries long rows into a "(continued)" section.
    """
    rows: Dict[int, str] = {}
    blocks = 0
    keys = [key] if also is None else [key, also]
    for copy in range(1, MAX_SUSPECTS + 1):
        read = [form.values.pop(f"{name}@{copy}", None) for name in keys]
        text = next((value for value in read if value), None)
        if not text:
            continue
        if ROW.search(text):
            for index, value in _rows(text).items():
                rows.setdefault(index, value)
        else:
            blocks += 1
            rows.setdefault(blocks, text.strip())
    for name in keys:
        for index, value in _rows(form.values.pop(f"{name}Cont", None)).items():
            if len(value) > len(rows.get(index, "")):
                rows[index] = value
    return rows


def _concomitant_products(form: ExtractedForm) -> None:
    """Block C.10 lists concomitant products with their therapy dates on one line each."""
    listed = form.values.pop("rows.concomitant", None) or form.values.pop("rows.concomitant2", None)
    form.values.pop("rows.concomitant2", None)
    carried = form.values.pop("rows.concomitantCont", None)
    if carried:  # the list runs on in the continuation section, mid-row
        listed = f"{listed} {carried}" if listed else carried
        listed = CARRIED.sub(" ", listed)
    for index, row in _rows(listed).items():
        dates = DATE_RANGE.search(row)
        name = (row[: dates.start()] if dates else row).strip()
        if not name:
            continue
        form.values[f"p5.cProdName{index}"] = name
        if dates:
            form.values[f"p5.cProdStart{index}"] = dates.group(1)
            if dates.group(2) and dates.group(2).lower() != "ongoing":
                form.values[f"p5.cProdEnd{index}"] = dates.group(2)


def anchor_keys() -> List[str]:
    return [anchor.key for anchor in ANCHORS]
