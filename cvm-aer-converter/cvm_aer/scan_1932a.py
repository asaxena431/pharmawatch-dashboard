"""Form FDA 1932a printed and scanned (e.g. off a copier), read with OCR.

A scan has no XFA dataset, so the values are read off the page images instead:
PaddleOCR finds each printed label ("First Name", "Breed", ...), the value is the
text in the box under it, and a checkbox counts as ticked when there is ink inside
the square at the left of its option.  The values are stored under the dataset's
own names, so a scan maps onto the report exactly as the fillable form does.

The form pages are the leading pages whose footer reads ``FORM FDA 1932a``; the
pages after them (medical records, invoices) are only embedded, never OCRed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .xfa_1932a import Document, Xfa1932a

FOOTER = "form fda 1932a"
# Ink inside a checkbox: an empty box is ~0, a ticked one ~0.2.
TICKED = 0.12
DARK = 140

# Section titles, in the order the form prints them.
SECTIONS = (
    ("sender", "sender information"),
    ("hcp", "health care professional information"),
    ("owner", "owner information"),
    ("product", "suspected product information"),
    ("use", "product use information"),
    ("manufacturer", "manufacturer or compounding"),
    ("event", "adverse event information"),
    ("species", "species and related information"),
    ("health", "overall health status"),
    ("occurrence", "adverse event occurrence"),
    ("documents", "document information"),
    ("problems", "concurrent clinical problem"),
    ("concurrent", "concurrent product information"),
    ("narrative", "adverse event/product problem/event use error"),
)

# The printed labels: a value box ends at the next one.  A label matches the start
# of the printed text ("Report Type (Check all that apply)"); "x:" labels carry
# their value on the same line.
LABELS = (
    "individual case safety report number", "submission type", "report type", "date of this report",
    "date of initial report", "first name", "last name", "street address", "city", "state or province",
    "postal/zip code", "country", "telephone number", "telephone number (other)", "fax number",
    "email address", "sender category", "sender previously reported", "if yes, provide the manufacturer",
    "no identity disclosure", "preferred method of contact", "name of suspected product",
    "is this a compounded product", "diagnosis and/or reason", "dosage form", "strength of active ingredient",
    "item", "ingredient", "strength", "date of first exposure", "date of last exposure",
    "duration of product use", "dose administered", "interval of administration", "route of administration",
    "product administered by", "lot number", "expiration date", "name of manufacturer",
    "veterinarian's level of suspicion", "treatment of adverse event", "did adverse event abate",
    "did adverse event reappear", "outcome", "breed", "gender", "age:", "weight:",
    "number of animals treated:", "number of animals affected:", "date of onset of adverse event",
    "length of time between first exposure", "length of time between last administration",
    "when the adverse event occurred", "attached document name", "attached document description",
    "were there concurrent clinical problems", "list concurrent clinical problem",
    "were concurrent products given", "list names of concurrent products", "describe the adverse event",
)
# Second lines of labels and instructions: never a value, never the end of one.
NOT_VALUES = (
    "suspected product(s) and onset", "please provide name(s)", "that the patient was taking",
    "copies of this form", "if you do not want your identity",
)

SENDER_CATEGORIES = {
    "veterinarian": "AVET", "animal owner": "OWNE", "physician": "OTHE", "patient": "OTHE",
    "other health care professional": "OTHE", "other": "OTHE", "unknown": "UNKN",
}
SPECIES = {"cat": "CAT", "dog": "DOG", "horse": "HOR", "cattle": "BOV"}
SPECIES_OPTIONS = (
    "budgerigar", "cat", "cattle", "cockatiel", "cockatoo", "dog", "ferret", "fish", "goat", "guinea pig",
    "horse", "human", "parrot", "pig", "rabbit", "sheep", "other (specify):",
)
GENDERS = {"male": "M", "female": "F", "male neutered": "MC", "female neutered": "FS"}
HEALTH = {"excellent": "GOOD", "good": "GOOD", "fair": "FAIR", "poor": "POOR", "critical": "POOR"}
OUTCOMES = {"recovered": "RECN", "died": "DEAD"}
CHALLENGE = {"yes": "Y", "no": "N", "not applicable": "NR"}
ADMINISTERED_BY = {"veterinarian/veterinary staff": "AVET", "owner": "OWNE", "other": "OTHE"}
SUSPICION = {"high": "High", "medium": "Medium", "low": "Low", "unknown": "Unknown"}
COUNTRIES = {"united states": "US", "usa": "US", "us": "US", "canada": "CA"}
PERSONS = {"sender": "rep01", "hcp": "rep02", "owner": "rep03"}
UNITS = {"y": "YEAR", "m": "MTH", "w": "WEEK", "d": "DAY"}

Box = Tuple[float, float, float, float, str]


class NotA1932aScan(ValueError):
    """The scan's first page is not a Form FDA 1932a page."""


@dataclass
class Word:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    section: str = ""

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("☑", "").replace("☐", "").replace("|", " ")).strip().lower()


def label_of(text: str) -> Optional[str]:
    """The printed label ``text`` is, if it is one (the longest that matches)."""
    norm = _norm(text)
    found = None
    for label in LABELS:
        if not norm.startswith(label):
            continue
        if label.endswith(":") or len(norm) == len(label) or not norm[len(label)].isalnum():
            if found is None or len(label) > len(found):
                found = label
    return found


def _is_value(word: Word) -> bool:
    norm = _norm(word.text)
    return bool(norm) and label_of(word.text) is None and not any(norm.startswith(text) for text in NOT_VALUES)


def is_footer_page(words: Sequence[Box]) -> bool:
    return any(FOOTER in _norm(text) for *_, text in words)


class ScannedForm:
    """The OCRed form pages: word boxes plus each page's ink mask, for the checkboxes."""

    def __init__(self, pages: Sequence[Tuple[Sequence[Box], "object"]]):
        self.ink = [ink for _, ink in pages]
        self.widths = [ink.shape[1] for ink in self.ink]
        self.words: List[Word] = []
        section = ""
        for number, (boxes, _) in enumerate(pages):
            ordered = sorted(boxes, key=lambda box: (box[1], box[0]))
            footer = min((box[1] for box in ordered if FOOTER in _norm(box[4])), default=float("inf"))
            for x0, y0, x1, y1, text in ordered:
                if y0 >= footer - 5:
                    continue
                norm = _norm(text)
                for name, title in SECTIONS:
                    if norm.startswith(title):
                        section = name
                        break
                self.words.append(Word(number, x0, y0, x1, y1, text, section))

    def labels(self, section: str, label: str) -> List[Word]:
        return [word for word in self.words if word.section == section and label_of(word.text) == label]

    def cell(self, label: Word) -> List[Word]:
        """The words in the box under (and on the line of) ``label``, up to the next label."""
        page_words = [word for word in self.words if word.page == label.page]
        right = min(
            (
                word.x0
                for word in page_words
                if word is not label and label_of(word.text) and abs(word.middle - label.middle) < label.height * 0.7
                and word.x0 > label.x1
            ),
            default=self.widths[label.page],
        )
        left = label.x0 - label.height
        below = [
            word.y0
            for word in page_words
            # The next row of labels, in this column or starting left of it.
            if (label_of(word.text) or self._is_title(word)) and word.y0 > label.y1 - 2
            and word.x0 < right and word is not label
        ]
        bottom = min(below, default=float("inf"))
        return [
            word
            for word in page_words
            if word is not label and _is_value(word) and not self._is_title(word) and word.section == label.section
            and left <= (word.x0 + word.x1) / 2 < right and label.y0 - label.height * 0.7 < word.middle < bottom
        ]

    @staticmethod
    def _is_title(word: Word) -> bool:
        return any(_norm(word.text).startswith(title) for _, title in SECTIONS)

    def text(self, section: str, label: str, occurrence: int = 0) -> Optional[str]:
        found = self.labels(section, label)
        if len(found) <= occurrence:
            return None
        head = found[occurrence]
        words = [word for word in self.cell(head) if word.middle > head.y1 - head.height * 0.3]
        return _join(words)

    def inline(self, section: str, label: str) -> Optional[str]:
        """A value printed after its label on the same line ("Age: 4-5 years")."""
        for word in self.labels(section, label):
            rest = word.text.split(":", 1)[1].strip() if ":" in word.text else ""
            if not rest:
                rest = _join([other for other in self.cell(word) if abs(other.middle - word.middle) < word.height * 0.6]) or ""
            return rest or None
        return None

    def date(self, section: str, label: str, occurrence: int = 0) -> Optional[str]:
        """A Month / Day / Year date, as the dataset's YYYY-MM-DD."""
        found = self.labels(section, label)
        if len(found) <= occurrence:
            return None
        words = sorted(self.cell(found[occurrence]), key=lambda word: word.x0)
        flat = " ".join(word.text for word in words)
        match = re.search(r"month\D*(\d{1,2})\D*day\D*(\d{1,2})\D*year\D*(\d{4})", flat, re.I)
        if not match:
            return None
        month, day, year = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None

    def ticked(self, section: str, label: str, options: Sequence[str], occurrence: int = 0) -> List[str]:
        """The options of question ``label`` whose box is ticked."""
        found = self.labels(section, label)
        if len(found) <= occurrence:
            return []
        return self.ticked_in(self.cell(found[occurrence]), options)

    def ticked_in(self, words: Sequence[Word], options: Sequence[str]) -> List[str]:
        chosen = []
        for word in words:
            option = _norm(word.text)
            if option in options and self._ink(word) >= TICKED:
                chosen.append(option)
        return chosen

    def section_words(self, section: str) -> List[Word]:
        return [word for word in self.words if word.section == section and _is_value(word) and not self._is_title(word)]

    def _ink(self, word: Word) -> float:
        """The ink inside the checkbox at the left of ``word`` (0 when there is no box)."""
        ink = self.ink[word.page]
        h = word.height
        top, bottom = int(max(0, word.y0 - 0.3 * h)), int(word.y1 + 0.3 * h)
        left, right = int(max(0, word.x0 - 2.2 * h)), int(word.x0 + 1.4 * h)
        window = ink[top:bottom, left:right]
        if not window.size:
            return 0.0
        edges = [index for index, share in enumerate(window.mean(0)) if share > 0.45]
        runs: List[List[int]] = []
        for index in edges:
            if runs and index - runs[-1][-1] <= 2:
                runs[-1].append(index)
            else:
                runs.append([index])
        for first, second in zip(runs, runs[1:]):
            if not 0.5 * h <= second[0] - first[-1] <= 1.4 * h:
                continue
            inside = window[:, first[-1] + 1 : second[0]]
            lines = [index for index, share in enumerate(inside.mean(1)) if share > 0.6]
            if len(lines) < 2:
                continue
            core = inside[lines[0] + 3 : lines[-1] - 2, 3:-3]
            if core.size:
                return float(core.mean())
        return 0.0


def _join(words: Sequence[Word]) -> Optional[str]:
    """Words as text: a line per row, wrapped rows run on, a blank gap starts a new paragraph."""
    if not words:
        return None
    rows: List[List[Word]] = []
    for word in sorted(words, key=lambda word: (word.page, word.middle)):
        if rows and rows[-1][0].page == word.page and abs(rows[-1][0].middle - word.middle) < word.height * 0.5:
            rows[-1].append(word)
        else:
            rows.append([word])
    text, previous = "", None
    for row in rows:
        line = " ".join(word.text.replace("☑", "").strip() for word in sorted(row, key=lambda word: word.x0)).strip()
        if previous is not None:
            gap = row[0].y0 - previous.y1 if row[0].page == previous.page else 0
            text += "\n" if gap > previous.height * 1.2 else " "
        text += line
        previous = row[0]
    return text.strip() or None


def _range(text: Optional[str]) -> Tuple[Optional[str], Optional[str], str]:
    """"4-5 years old" -> ("4", "5", "y"); "9-10 lbs" -> ("9", "10", "l")."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|to)?\s*(\d+(?:\.\d+)?)?\s*([a-z]*)", (text or "").lower())
    if not match:
        return None, None, ""
    return match.group(1), match.group(2), match.group(3)


def read_values(form: ScannedForm) -> Dict[str, str]:
    """The scan's values under the 1932a dataset's names."""
    values: Dict[str, Optional[str]] = {}

    values["pvcaseno"] = form.text("", "individual case safety report number")
    values["todaysdate"] = form.date("", "date of this report")
    report_types = form.ticked("", "report type", ("adverse event", "product problem", "product use error"))
    values["reporttype_ae"] = "Y" if "adverse event" in report_types or not report_types else "N"

    for section, prefix in PERSONS.items():
        values[f"{prefix}firstname"] = form.text(section, "first name")
        values[f"{prefix}lastname"] = form.text(section, "last name")
        values[f"{prefix}streetaddress1"] = form.text(section, "street address")
        values[f"{prefix}city"] = form.text(section, "city")
        values[f"{prefix}statetext"] = form.text(section, "state or province")
        values[f"{prefix}postcode"] = form.text(section, "postal/zip code")
        country = form.text(section, "country")
        values[f"{prefix}countrycode"] = COUNTRIES.get((country or "").lower(), country)
        values[f"{prefix}telephone"] = form.text(section, "telephone number")
        values[f"{prefix}fax"] = form.text(section, "fax number")
        values[f"{prefix}email"] = (form.text(section, "email address") or "").replace(" ", "") or None
    category = form.ticked("sender", "sender category", tuple(SENDER_CATEGORIES))
    values["rep01categorisation"] = SENDER_CATEGORIES[category[0]] if category else None

    values["p01brandname"] = form.text("product", "name of suspected product")
    values["p01indication"] = form.text("product", "diagnosis and/or reason")
    values["p01formulation"] = form.text("product", "dosage form")
    values.update(_ingredients(form))
    values["p01treatmentstartdate"] = form.date("product", "date of first exposure")
    values["p01treatmentenddate"] = form.date("product", "date of last exposure")
    values["p01dosagetext"] = form.text("use", "dose administered")
    values["p01administrationroute"] = form.text("use", "route of administration")
    given_by = form.ticked("use", "product administered by", tuple(ADMINISTERED_BY))
    values["p01administrationvmp"] = ADMINISTERED_BY[given_by[0]] if given_by else None
    values["p01lotnumber"] = form.text("use", "lot number")
    values["p01expiringdate"] = form.date("use", "expiration date")

    suspicion = form.ticked("event", "veterinarian's level of suspicion", tuple(SUSPICION))
    values["p01vetassessment"] = SUSPICION[suspicion[0]] if suspicion else None
    values["treateddetails"] = form.text("event", "treatment of adverse event")
    abate = form.ticked("event", "did adverse event abate", tuple(CHALLENGE))
    values["p01dechallenge"] = CHALLENGE[abate[0]] if abate else None
    reappear = form.ticked("event", "did adverse event reappear", tuple(CHALLENGE))
    values["p01rechallenge"] = CHALLENGE[reappear[0]] if reappear else None
    outcome = form.ticked("event", "outcome", ("recovered", "died", "other"))
    values["outcometodate"] = OUTCOMES.get(outcome[0], "UNKN") if outcome else None

    species = form.ticked_in(form.section_words("species"), SPECIES_OPTIONS)
    if species:
        values["species"] = SPECIES.get(species[0], "OTH")
        if values["species"] == "OTH":
            values["speciesother"] = species[0].replace(" (specify):", "").title()
    values["breed"] = form.text("species", "breed")
    gender = form.ticked("species", "gender", tuple(GENDERS))
    values["sex"] = GENDERS[gender[0]] if gender else None
    low, high, unit = _range(form.inline("species", "age:"))
    values["age"], values["ageto"] = low, high
    values["ageunitcode"] = UNITS.get(unit[:1], "YEAR") if low else None
    low, high, unit = _range(form.inline("species", "weight:"))
    values["weight"], values["weightto"] = low, high
    # OCR reads "lbs" as "Ibs" as readily as "lbs".
    values["weightunit"] = ("LBS" if unit[:1] in ("l", "i", "p") else "KG") if low else None

    health = form.ticked_in(form.section_words("health"), tuple(HEALTH))
    values["priorcond"] = HEALTH[health[0]] if health else None
    values["exposednumber"] = _range(form.inline("health", "number of animals treated:"))[0]
    values["reactednumber"] = _range(form.inline("health", "number of animals affected:"))[0]

    values["reactionstartdate"] = form.date("occurrence", "date of onset of adverse event")
    values["p01timetoonset"] = form.text("occurrence", "length of time between first exposure")
    values["concurrentproblemstext"] = form.text("problems", "list concurrent clinical problem")
    values["concurrentproducts"] = form.text("concurrent", "list names of concurrent products")
    values["narrativeadr"] = _narrative(form)
    return {key: value for key, value in values.items() if value}


def _ingredients(form: ScannedForm) -> Dict[str, Optional[str]]:
    """The Item / Ingredient / Strength table: up to three rows."""
    found: Dict[str, Optional[str]] = {}
    heads = {name: form.labels("product", name) for name in ("item", "ingredient", "strength")}
    if not all(heads.values()):
        return found
    item, ingredient, strength = (heads[name][0] for name in ("item", "ingredient", "strength"))
    words = [word for word in form.section_words("product") if word.page == item.page and word.y0 > item.y1 - 2]
    for number in (1, 2, 3):
        marker = next(
            (word for word in words if word.text.strip() == str(number) and abs(word.x0 - item.x0) < item.height * 2),
            None,
        )
        if marker is None:
            continue
        row = [word for word in words if abs(word.middle - marker.middle) < marker.height * 0.7 and word is not marker]
        found[f"p01ingredient{number}"] = _join([word for word in row if ingredient.x0 - 20 <= word.x0 < strength.x0 - 20])
        found[f"p01ingredientstrength{number}"] = _join([word for word in row if word.x0 >= strength.x0 - 20])
    return found


def _narrative(form: ScannedForm) -> Optional[str]:
    """The long narrative; the signature under it (handwriting, much taller) is left out."""
    words = form.section_words("narrative")
    if not words:
        return None
    heights = sorted(word.height for word in words)
    usual = heights[len(heights) // 2]
    return _join([word for word in words if word.height < usual * 1.8])


def _ocr(image, lang: str) -> List[Box]:
    from .ocr import _get_paddle_engine, _words_from_paddle

    return _words_from_paddle(_get_paddle_engine(lang=lang), image)


def _footer_says_1932a(image, lang: str) -> bool:
    """Only the bottom of the page is OCRed to tell a form page from an attachment."""
    width, height = image.size
    return is_footer_page(_ocr(image.crop((0, int(height * 0.9), width, height)), lang))


def read_scanned_1932a(
    pdf_path: str, attachments: Sequence[str] = (), dpi: int = 200, lang: str = "en"
) -> Xfa1932a:
    """OCR a scanned 1932a's form pages; ``NotA1932aScan`` when page 1 is not one."""
    import numpy as np
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    pages = []
    try:
        for index in range(len(document)):
            image = document[index].render(scale=dpi / 72.0).to_pil()
            if not _footer_says_1932a(image, lang):
                break
            pages.append((_ocr(image, lang), np.array(image.convert("L")) < DARK))
    finally:
        document.close()
    if not pages:
        raise NotA1932aScan(f"page 1 of {pdf_path} is not a Form FDA 1932a page")
    values = read_values(ScannedForm(pages))
    documents = [Document.read(path) for path in (pdf_path, *attachments)]
    return Xfa1932a(values=values, documents=documents, engine=f"paddleocr (scanned 1932a, {len(pages)} form pages)")
