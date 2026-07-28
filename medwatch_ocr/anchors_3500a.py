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

DATE = r"^(?:\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4})$"
NUMBER = r"^\d+(?:[.,]\d+)?$"
WORD = r"^[A-Za-z][A-Za-z./'-]*$"

ANCHORS: Tuple[Anchor, ...] = (
    Anchor("p0.mfr", r"mfr report #", where="right"),
    Anchor("p0.patID", r"1\.\s*patient identifier", where="below", x_slack=60, limit=1),
    Anchor("p0.patAge", r"2\.\s*age(?:\s*at time)?", keep=NUMBER, x_slack=40, limit=1),
    Anchor("p0.ageText", r"2\.\s*age(?:\s*at time)?", keep=r"^(?:years?|months?|weeks?|days?)$", x_slack=40, limit=1),
    Anchor("p0.sexText", r"3a?\.\s*sex", keep=r"^(?:male|female)$", x_slack=40, limit=1),
    Anchor("p0.weightText", r"4\.\s*weight", keep=r"^(?:\d+(?:[.,]\d+)?|kgs?|lbs?)$", x_slack=40, limit=4),
    Anchor("p0.dateAdvEvent", r"3\.\s*date of event", keep=DATE, x_slack=40, limit=1),
    Anchor("p0.dateReport", r"4\.\s*date of (?:this|the) report", keep=DATE, x_slack=40, limit=1),
    Anchor("p0.deathDate", r"date of death", where="right", keep=DATE, limit=1),
    # Free-text boxes: everything typed between this caption and the next section.
    Anchor(
        "p0.advEvDesc",
        r"5\.\s*describe event or problem",
        lines=400,
        x_from=40,
        x_slack=520,
        stop=r"^(?:6\.\s*relevant test|form fda-3500a|page \d+ of)",
        drop=r"^(?:\[preferred|term\]|verbatim|\(related|symptoms|separated|commas\)|if|by)$",
        wide=True,
    ),
    Anchor(
        "p2.testData",
        r"6\.\s*relevant tests?/laboratory data",
        lines=400,
        x_from=40,
        x_slack=520,
        stop=r"^(?:7\.\s*other relevant history|form fda-3500a|page \d+ of)",
        wide=True,
    ),
    Anchor(
        "p2.otherHist",
        r"7\.\s*other relevant history",
        lines=400,
        x_from=40,
        x_slack=520,
        stop=r"^(?:8\.\s*|form fda-3500a|page \d+ of|e\.\s*initial reporter)",
        wide=True,
    ),
    # C. Suspect product(s): read as whole boxes, then split into rows (see _rows).
    Anchor("rows.prodName", r"1\.\s*name(?:s)?[ ,(]", lines=8, repeat=MAX_SUSPECTS, wide=True),
    Anchor("rows.dose", r"2\.\s*dose,? (?:frequency|or amount)", lines=8, repeat=MAX_SUSPECTS, wide=True),
    Anchor("rows.therapy", r"(?:3\.\s*therapy dates|4\.\s*trea\s*t\s*ment dates)", lines=8, repeat=MAX_SUSPECTS, wide=True),
    Anchor("rows.diagnosis", r"(?:4|5)\.\s*diagnosis for use(?! \(continued)", lines=8, repeat=MAX_SUSPECTS, wide=True),
    Anchor("rows.concomitant", r"10\.\s*concomitant medical products(?! \(continued)", lines=30, x_from=20, wide=True),
    Anchor("rows.concomitant2", r"2\.\s*list medical product and treatment given", lines=30, x_from=20, wide=True),
    # Rows a vendor layout carries over into its "(continued)" section.
    Anchor("rows.prodNameCont", r"c1\s*\.\s*name \(continued\)", lines=12, x_from=20, stop=CONTINUED_BOX, wide=True),
    Anchor("rows.doseCont", r"c2\.\s*dose,? frequency", lines=12, x_from=20, stop=CONTINUED_BOX, wide=True),
    Anchor("rows.therapyCont", r"c3\.\s*therapy dates", lines=12, x_from=20, stop=CONTINUED_BOX, wide=True),
    Anchor("rows.diagnosisCont", r"c4\.\s*diagnosis for use", lines=12, x_from=20, stop=CONTINUED_BOX, wide=True),
    Anchor("rows.concomitantCont", r"c10\.\s*concomitant medical products", lines=40, x_from=20, stop=CONTINUED_BOX, wide=True),
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
    Anchor("p6.reportEmail", r"email", keep=r"@", x_slack=90, limit=1),
    Anchor("p6.repOccupation", r"3\.\s*occupation", x_slack=90, keep=WORD, drop=r"^(?:fda|select|from|list\)?|n)$", limit=1),
    # G. All manufacturers
    Anchor("p7.manuName", r"1\.\s*contact office", x_slack=200, keep=r"^(?!\(?\d)\S+$", wide=True),
    Anchor("p7.manuAddr", r"^address$", occurrence=2, x_slack=260, lines=4, stop=r"^(?:compounding|2\.\s|3\.\s|email)", wide=True),
    Anchor("p7.manuPhone", r"phone number", keep=r"^[-()\d][-()\d ]{6,}$", x_slack=110, limit=1),
    Anchor("p7.manuEmail", r"email address", keep=r"@", x_slack=110, limit=1),
    Anchor("p7.reportManuRecDate", r"(?:3|4)\.\s*date received by", keep=DATE, x_slack=160, lines=3, limit=1, wide=True),
    Anchor("p7.numIND", r"ind\s*#", keep=r"^\d{4,}$", x_slack=60, limit=1),
    Anchor("p7.repFollowNum", r"follow-?up\s*#", where="right", keep=NUMBER, limit=1),
    Anchor(
        "p7.protNum",
        r"(?:give protocol\s*#|if ind, give protocol)",
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
    CheckAnchor("p7.rep15", r"(?<!\d)15-day"),
    CheckAnchor("p7.rep30", r"(?<!\d)30-day"),
    CheckAnchor("p7.repPer", r"\bperiodic\b"),
    CheckAnchor("p7.repInit", r"\binitial\b(?!\s*(?:or prolonged|reporter))"),
    CheckAnchor("p7.repFollow", r"follow-?up\s*#"),
    CheckAnchor("p7.combo", r"combination\s*product"),
)

ROW = re.compile(r"(?:#\s*(\d+)\s*[.):]?(?=\s|$)|(?<![\d/])(\d+)\s*\))")
_DAY = r"\d{1,2}[-/][A-Za-z0-9]{2,3}[-/]\d{4}"
DATE_RANGE = re.compile(rf"({_DAY})\s*(?:to\s*)?({_DAY}|ongoing)?", re.IGNORECASE)


def extract_by_labels(pdf_path: str, dpi: int = 150) -> ExtractedForm:
    """Read a 3500A of unknown revision by anchoring on its printed captions."""
    form = extract_by_captions(pdf_path, ANCHORS, CHECKS, dpi=dpi)
    _sex_from_text(form)
    _weight_unit(form)
    _age_unit(form)
    _reporter(form)
    _suspect_products(form)
    _concomitant_products(form)
    return form


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
        value = text[mark.end() : end].strip(" ,;")
        if value and number not in rows:
            rows[number] = value
    return rows


def _suspect_products(form: ExtractedForm) -> None:
    """Fill the serialiser's per-product keys from the boxes read row by row."""
    names = _product_rows(form, "rows.prodName")
    doses = _product_rows(form, "rows.dose")
    therapy = _product_rows(form, "rows.therapy")
    diagnosis = _product_rows(form, "rows.diagnosis")
    for index in range(1, MAX_SUSPECTS + 1):
        prefix = f"p{suspect_page(index)}."
        if names.get(index):
            form.values[f"{prefix}prodName{index}"] = names[index]
        if doses.get(index):
            form.values[f"{prefix}dose{index}"] = doses[index]
        if diagnosis.get(index):
            form.values[f"{prefix}diagnosis{index}"] = diagnosis[index]
        dates = DATE_RANGE.search(therapy.get(index, ""))
        if dates:
            form.values[f"{prefix}start{index}Date"] = dates.group(1)
            if dates.group(2) and dates.group(2).lower() != "ongoing":
                form.values[f"{prefix}end{index}Date"] = dates.group(2)


def _product_rows(form: ExtractedForm, key: str) -> Dict[int, str]:
    """The rows of a suspect-product box, however the copy repeats the product.

    One revision prints a whole block per product, so the n-th copy of the box is
    product n; another lists the products inside a single box, numbered "#1., #2.";
    a vendor layout does both and carries long rows into a "(continued)" section.
    """
    rows: Dict[int, str] = {}
    blocks = 0
    for copy in range(1, MAX_SUSPECTS + 1):
        text = form.values.pop(f"{key}@{copy}", None)
        if not text:
            continue
        if ROW.search(text):
            for index, value in _rows(text).items():
                rows.setdefault(index, value)
        else:
            blocks += 1
            rows.setdefault(blocks, text.strip())
    for index, value in _rows(form.values.pop(f"{key}Cont", None)).items():
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
    for index, row in _rows(listed).items():
        dates = DATE_RANGE.search(row)
        name = (row[: dates.start()] if dates else row).strip(" ,;")
        if not name:
            continue
        form.values[f"p5.cProdName{index}"] = name
        if dates:
            form.values[f"p5.cProdStart{index}"] = dates.group(1)
            if dates.group(2) and dates.group(2).lower() != "ongoing":
                form.values[f"p5.cProdEnd{index}"] = dates.group(2)


def anchor_keys() -> List[str]:
    return [anchor.key for anchor in ANCHORS]
