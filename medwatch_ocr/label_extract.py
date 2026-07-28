"""Caption-anchored extraction of 3500A copies whose layout we have no template for.

The template-guided reader (:mod:`medwatch_ocr.form_extract`) needs the widget
rectangles of the form revision it was built from.  Reports printed from an older
revision - or from a vendor safety system that renders its own "3500A facsimile"
- put the values in different places, so the rectangles no longer fit.

What every one of those copies does share is the *printed caption* next to each
box ("1. Patient Identifier", "3. Date of Event", ...) and the ruled box itself.
This module therefore locates each field by its caption and reads the value out
of the surrounding box:

* words are read from the PDF text layer together with their font, and the fonts
  are split into the ones printing the blank form's own words and the ones
  carrying typed values;
* words are grouped into lines, and each caption is matched against the line
  text so the caption's own rectangle is known;
* the form's ruled lines are read as page objects, and the smallest rule that
  encloses the caption is the field's box: the value is every typed word inside
  it, which keeps neighbouring columns and continuation lines apart;
* checkbox state is read from the small square rule next to the caption.

The result is an :class:`~medwatch_ocr.form_extract.ExtractedForm` keyed exactly
like the template reader's, so mapping and serialisation are unchanged.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
from collections import deque
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .form_extract import ExtractedForm

# Layout variants.  Only the current revision has a widget template of its own;
# the others are read by anchoring on their printed captions.
VARIANT_3500A_2025 = "3500A-09-2025"
VARIANT_3500A_2022 = "3500A-11-22"
VARIANT_FACSIMILE = "3500A-facsimile"
VARIANT_3500A_OTHER = "3500A-other"

# Layouts read by caption: a revision with no widget template, or a vendor's
# facsimile.  The current official revision has a template and is read from it.
CAPTION_ANCHORED_VARIANTS = frozenset({VARIANT_3500A_2022, VARIANT_FACSIMILE})

# The revision is printed in the form's footer, e.g. "FORM FDA 3500A (11/22)".
_REVISION = re.compile(r"FORMFDA-?3500A(?:MEDWATCH)?\((\d{2})/(\d{2,4})\)")

CAPTION_WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "fda_3500a_caption_words.json")

# A font is a value font once this share of its words is unknown to the blank form.
UNKNOWN_WORD_SHARE = 0.25


# A font "key" is the name plus the point size rounded to half a point.  The size
# has to be measured from the glyphs: a form printed by a safety system typesets
# captions and values in one named font, and the size pdfium reports for it is the
# text-matrix scale, identical for both.  Glyph *heights* differ per letter, so the
# size is derived from the height of a letter whose proportion of the em is known.
FontKey = Tuple[str, float]

EM_FRACTION = {
    "cap": 0.716,  # capitals and digits reach the cap height
    "ascender": 0.74,
    "x": 0.519,
}
CAPITALS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
ASCENDERS = set("bdfhklt")

# Ruled rectangles: a field box is at least this wide and tall.
MIN_BOX_WIDTH = 28.0
MIN_BOX_HEIGHT = 9.0

# Checkboxes, read from the rendered page: how far left of the caption to look (in
# line heights), which grey counts as ink, the square's side relative to the line
# height, and how much ink in the middle of the box means "ticked".
CHECKBOX_SEARCH = 2.6
CHECKBOX_DARK = 160
SQUARE_MIN = 0.6
SQUARE_MAX = 2.0
CHECKBOX_MARK_INK = 0.15
X_HEIGHT = set("acemnorsuvwxz")


# A printed line that ends the box a value was being read from.
NEXT_BOX = r"^(?:\d{1,2}[a-c]?\.\s+\S|[a-h]\.\s+[a-z][a-z ]{5,}|form fda-3500a|page \d+ of)"


@dataclass(frozen=True)
class Box:
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.top <= y <= self.bottom


@dataclass(frozen=True)
class Word:
    x0: float
    top: float
    x1: float
    bottom: float
    text: str
    font: FontKey

    @property
    def centre(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class Line:
    words: List[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def top(self) -> float:
        return min(word.top for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.bottom for word in self.words)

    def span(self, start: int, end: int) -> Tuple[float, float]:
        """Pixel x-range of the characters ``[start, end)`` of :attr:`text`."""
        offset = 0
        left, right = None, None
        for word in self.words:
            word_end = offset + len(word.text)
            if word_end > start and offset < end:
                left = word.x0 if left is None else min(left, word.x0)
                right = word.x1 if right is None else max(right, word.x1)
            offset = word_end + 1
        return (left or 0.0, right or 0.0)

    def words_after(self, end: int) -> List[Word]:
        """Words that start after character ``end`` of :attr:`text`."""
        offset = 0
        after: List[Word] = []
        for word in self.words:
            if offset >= end:
                after.append(word)
            offset += len(word.text) + 1
        return after


@dataclass(frozen=True)
class CheckAnchor:
    """A checkbox: its printed caption, with the box drawn just left of the text."""

    key: str
    caption: str
    occurrence: int = 0  # 0 = ticked if any copy of the caption carries a mark
    gap: float = 2.0  # points between the box and the caption text


@dataclass(frozen=True)
class Anchor:
    """One field: where its caption is printed and where its value sits."""

    key: str
    caption: str
    where: str = "box"  # "box" (after the caption and below it), "below" or "right"
    occurrence: int = 1
    lines: int = 1
    x_slack: float = 24.0
    x_from: float = 0.0  # widen the column to the left of the caption
    stop: Optional[str] = None
    drop: Optional[str] = None
    keep: Optional[str] = None  # only words matching this are part of the value
    limit: int = 0  # keep at most this many words (0 = the whole box)
    wide: bool = False  # a free-text box spanning the captions printed to its right
    repeat: int = 0  # read this many copies of the box, into "{key}@{n}" (0 = one)
    by_line: bool = False  # keep the box's line breaks ("\n") instead of one line


# Captions, item numbers and instructions printed inside the boxes: never values.
NOISE = re.compile(
    r"^(?:\(?e\.g\.?,?.*|\(?01-JAN-1900\)?:?|\(mm/dd/yyyy\)|lbs?|kgs?|"
    r"or|and|to|yes|no|unk|unknown|year\(s\)|month\(s\)|week\(s\)|day\(s\)|years?|"
    r"in|confidence|\(in|confidence\)|apply|apply\)|\(check|check|exp\.|id|&|"
    r"\d{1,2}[a-c]?\.|#|:|\(continued\.*\)?)$",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u2019", "'")).strip().lower()


def page_words(pdf_path: str) -> Tuple[List[List[Word]], int]:
    """Read every page's words, with their font, from the PDF text layer."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    pages: List[List[Word]] = []
    document = pdfium.PdfDocument(pdf_path)
    try:
        for index in range(len(document)):
            page = document[index]
            height = page.get_height()
            textpage = page.get_textpage()
            pages.append(_words_of_page(textpage, pdfium_c, height))
        return pages, len(document)
    finally:
        document.close()


def _font_name(textpage, pdfium_c, index: int) -> str:
    buffer = ctypes.create_string_buffer(96)
    flags = ctypes.c_int()
    pdfium_c.FPDFText_GetFontInfo(textpage.raw, index, buffer, 96, ctypes.byref(flags))
    return buffer.value.decode("utf-8", errors="replace")


def _point_size(char: str, height: float) -> Optional[float]:
    """Point size implied by one glyph's height, or ``None`` for an unusable letter."""
    if char in CAPITALS:
        return height / EM_FRACTION["cap"]
    if char in ASCENDERS:
        return height / EM_FRACTION["ascender"]
    if char in X_HEIGHT:
        return height / EM_FRACTION["x"]
    return None


def _words_of_page(textpage, pdfium_c, height: float) -> List[Word]:
    """Group the characters of one page into words, in PDF top-left coordinates."""
    words: List[Word] = []
    letters: List[str] = []
    box: Optional[List[float]] = None
    font: Optional[str] = None
    size: Optional[float] = None

    def flush() -> None:
        nonlocal letters, box, font, size
        text = "".join(letters).strip()
        if text and box is not None and font is not None:
            key = (font, round((size or 0.0) * 2.0) / 2.0)
            words.append(Word(box[0], height - box[3], box[2], height - box[1], text, key))
        letters, box, font, size = [], None, None, None

    for index in range(textpage.count_chars()):
        char = textpage.get_text_range(index, 1)
        if not char or char.isspace():
            flush()
            continue
        try:
            left, bottom, right, top = textpage.get_charbox(index)
        except Exception:  # pragma: no cover - unmappable glyph
            continue
        current = _font_name(textpage, pdfium_c, index)
        if box is None or current != font or left < box[0] - 1 or left > box[2] + 2.5:
            flush()
            box, font = [left, bottom, right, top], current
        else:
            box = [min(box[0], left), min(box[1], bottom), max(box[2], right), max(box[3], top)]
        implied = _point_size(char, top - bottom)
        if implied is not None:
            size = implied if size is None else max(size, implied)
        letters.append(char)
    flush()
    return words


@lru_cache(maxsize=1)
def caption_vocabulary() -> Set[str]:
    """Every word printed on the blank FDA 3500A (built by scripts/build_caption_vocabulary.py)."""
    with open(CAPTION_WORDS_FILE, "r", encoding="utf-8") as handle:
        return set(json.load(handle))


def value_fonts(pages: Sequence[Sequence[Word]]) -> Set[FontKey]:
    """The fonts that carry *typed* values rather than the printed form.

    Both are usually the same typeface, and a vendor's rendering may even report
    the same size for the two, so the split is made on content instead: the blank
    form's own words are known, while identifiers, dates, names and narratives are
    not.  A font whose words are unknown often enough is holding typed values.
    """
    vocabulary = caption_vocabulary()
    by_font: Dict[FontKey, List[str]] = {}
    for words in pages:
        for word in words:
            by_font.setdefault(word.font, []).append(_normalise(word.text))

    fonts: Set[FontKey] = set()
    for font, tokens in by_font.items():
        unknown = sum(1 for token in tokens if token not in vocabulary)
        if unknown / len(tokens) >= UNKNOWN_WORD_SHARE:
            fonts.add(font)
    return fonts


def group_lines(words: Sequence[Word], tolerance: float = 0.6) -> List[Line]:
    """Cluster words into text lines by their vertical centre."""
    lines: List[Line] = []
    for word in sorted(words, key=lambda w: ((w.top + w.bottom) / 2.0, w.x0)):
        centre = (word.top + word.bottom) / 2.0
        if lines:
            last = lines[-1]
            limit = max(word.bottom - word.top, 4.0) * tolerance
            if abs(centre - (last.top + last.bottom) / 2.0) <= limit:
                last.words.append(word)
                continue
        lines.append(Line([word]))
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


# Date prompts are printed *inside* the box they describe, in the value's own font.
DATE_PROMPT = re.compile(r"\(?\s*(?:01\s*-\s*JAN\s*-\s*1900|mm/dd/yyyy|dd-mmm-yyyy)\s*\)?:?", re.IGNORECASE)


def _harvest(words: Sequence[Word], rules: "_Rules", limit: int = 0) -> str:
    kept = [
        word.text
        for word in words
        if not NOISE.match(word.text)
        and not (rules.drop and rules.drop.match(word.text))
        and (rules.keep is None or rules.keep.match(word.text))
    ]
    text = DATE_PROMPT.sub(" ", " ".join(kept))
    words_left = re.sub(r"\s+", " ", text).strip().split(" ") if text.strip() else []
    if limit:
        words_left = words_left[:limit]
    return " ".join(words_left)


def page_rules(pdf_path: str) -> List[List[Box]]:
    """Per page, the ruled field boxes of the form.

    Only rules large enough to be a field box are kept.  Small squares are not
    collected: a form printed by a safety system draws its checkboxes inside form
    XObjects, whose path coordinates are the object's own, not the page's, so they
    cannot be located this way - checkbox state is read from the pixels instead.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    per_page: List[List[Box]] = []
    document = pdfium.PdfDocument(pdf_path)
    try:
        for index in range(len(document)):
            page = document[index]
            height = page.get_height()
            cells: List[Box] = []
            for obj in page.get_objects(max_depth=3):
                if obj.type != pdfium_c.FPDF_PAGEOBJ_PATH:
                    continue
                try:
                    x0, y0, x1, y1 = obj.get_bounds()
                except Exception:  # pragma: no cover - degenerate path
                    continue
                box = Box(x0, height - y1, x1, height - y0)
                if box.width >= MIN_BOX_WIDTH and box.height >= MIN_BOX_HEIGHT:
                    cells.append(box)
            per_page.append(cells)
        return per_page
    finally:
        document.close()


@dataclass
class Page:
    index: int
    lines: List[Line]
    cells: List[Box]


def read_pages(pdf_path: str) -> Tuple[List[Page], Set[FontKey]]:
    """Words, lines, ruled boxes and the value fonts of every page of ``pdf_path``."""
    words_by_page, _ = page_words(pdf_path)
    rules = page_rules(pdf_path)
    fonts = value_fonts(words_by_page)
    pages = [
        Page(index, group_lines(words), rules[index] if index < len(rules) else [])
        for index, words in enumerate(words_by_page)
    ]
    return pages, fonts


def detect_variant(pdf_path: str) -> Optional[str]:
    """Which 3500A layout ``pdf_path`` is, read from the revision printed on it.

    ``None`` when the document is not a 3500A at all.  Only the current revision
    has a committed widget template; every other revision, and a vendor
    facsimile, is read by :func:`extract_by_captions`.
    """
    from .ocr import extract_text_layer

    try:
        text = re.sub(r"\s+", "", " ".join(extract_text_layer(pdf_path)).upper())
    except Exception:
        return None
    if "3500AFACSIMILE" in text:
        return VARIANT_FACSIMILE
    if "3500A" not in text:
        return None
    revision = _REVISION.search(text)
    if not revision:
        return VARIANT_3500A_OTHER
    month, year = revision.group(1), revision.group(2)[-2:]
    if (month, year) == ("09", "25"):
        return VARIANT_3500A_2025
    if (month, year) == ("11", "22"):
        return VARIANT_3500A_2022
    return VARIANT_3500A_OTHER


def extract_by_captions(
    pdf_path: str,
    anchors: Sequence[Anchor],
    checks: Sequence[CheckAnchor] = (),
    dpi: int = 150,
) -> ExtractedForm:
    """Read ``anchors`` and ``checks`` out of ``pdf_path`` by their printed captions."""
    pages, fonts = read_pages(pdf_path)
    form = ExtractedForm(pages=len(pages), engine="text-layer (caption-anchored)")
    for anchor in anchors:
        rules = _Rules(
            re.compile(anchor.caption, re.IGNORECASE),
            re.compile(anchor.stop or NEXT_BOX, re.IGNORECASE),
            re.compile(anchor.drop, re.IGNORECASE) if anchor.drop else None,
            re.compile(anchor.keep, re.IGNORECASE) if anchor.keep else None,
        )
        for copy in range(1, anchor.repeat + 1) if anchor.repeat else (anchor.occurrence,):
            probe = replace(anchor, occurrence=copy) if anchor.repeat else anchor
            key = f"{anchor.key}@{copy}" if anchor.repeat else anchor.key
            seen = 0
            for page in pages:
                value, hits = _value_for(page, probe, rules, fonts, seen)
                seen += hits
                if value:
                    form.values[key] = value
                    break
    if checks:
        form.checks.extend(_marked_checkboxes(pdf_path, pages, checks, fonts, dpi=dpi))
    return form


@dataclass(frozen=True)
class _Rules:
    caption: re.Pattern
    stop: re.Pattern
    drop: Optional[re.Pattern]
    keep: Optional[re.Pattern]


def _enclosing_cell(cells: Sequence[Box], line: Line, left: float, right: float) -> Optional[Box]:
    """The smallest ruled box holding both the caption and room for a value below it.

    Rules drawn *around the caption alone* are ignored: several revisions underline
    the caption inside the field's box, and that rule is not the box.
    """
    middle = (line.top + line.bottom) / 2.0
    inside = [
        box
        for box in cells
        if box.contains(left + 1.0, middle) and box.x1 >= right + 4.0 and box.bottom >= line.bottom + 3.0
    ]
    return min(inside, key=lambda box: box.width * box.height) if inside else None


def _value_for(
    page: Page,
    anchor: Anchor,
    rules: _Rules,
    fonts: Set[FontKey],
    seen: int,
) -> Tuple[Optional[str], int]:
    """The value of ``anchor`` on ``page``, and how often its caption appeared."""
    hits = 0
    for index, line in enumerate(page.lines):
        match = rules.caption.search(_normalise(line.text))
        if not match:
            continue
        hits += 1
        if seen + hits != anchor.occurrence:
            continue
        left, right = line.span(match.start(), match.end())
        cell = _enclosing_cell(page.cells, line, left, right)
        # The caption of the next field on the same line marks the end of this one's column.
        neighbour = None
        if not anchor.wide:
            neighbour = next((word.x0 - 2.0 for word in line.words_after(match.end()) if word.font not in fonts), None)
        slack = None if anchor.where == "right" or anchor.wide else right + anchor.x_slack
        edges = [value for value in (cell.x1 if cell else None, neighbour, slack) if value is not None]
        edge = min(edges) if edges else float("inf")
        band = (max(cell.x0, left - anchor.x_from) if cell else left - anchor.x_from, edge)

        first = [word for word in line.words_after(match.end()) if word.font in fonts and word.centre <= band[1]]
        if anchor.where == "right":
            return _harvest(first, rules, anchor.limit) or None, hits
        rows: List[List[Word]] = [] if anchor.where == "below" or not first else [first]

        blanks = 0
        for following in page.lines[index + 1 :]:
            if cell and following.top >= cell.bottom:
                break
            in_band = [word for word in following.words if band[0] - 2 <= word.centre <= band[1]]
            printed = [word for word in in_band if word.font not in fonts]
            # Item numbers are often set in the value font, so the whole line is tested.
            if printed and rules.stop.search(_normalise(" ".join(word.text for word in in_band))):
                break
            typed = [word for word in in_band if word.font in fonts]
            if not typed:
                blanks += 1
                if blanks > 2 and not cell:
                    break
                continue
            blanks = 0
            rows.append(typed)
            if len(rows) >= anchor.lines:
                break
        if anchor.by_line:
            harvested = [_harvest(row, rules, anchor.limit) for row in rows]
            return "\n".join(row for row in harvested if row) or None, hits
        return _harvest([word for row in rows for word in row], rules, anchor.limit) or None, hits
    return None, hits


def _marked_checkboxes(
    pdf_path: str,
    pages: Sequence[Page],
    checks: Sequence[CheckAnchor],
    fonts: Set[FontKey],
    dpi: int,
) -> List[str]:
    """Read the state of the checkbox printed left of each caption, from the pixels."""
    import numpy as np
    import pypdfium2 as pdfium

    scale = dpi / 72.0
    marked: List[str] = []
    document = pdfium.PdfDocument(pdf_path)
    try:
        images: Dict[int, "np.ndarray"] = {}
        for check in checks:
            pattern = re.compile(check.caption, re.IGNORECASE)
            seen = 0
            done = False
            for page in pages:
                for line in page.lines:
                    match = pattern.search(_normalise(line.text))
                    if not match:
                        continue
                    if not _printed_caption(line, match, fonts):
                        continue  # the words matched are typed text, not a caption
                    seen += 1
                    if check.occurrence and seen != check.occurrence:
                        continue
                    if page.index not in images:
                        images[page.index] = np.asarray(document[page.index].render(scale=scale).to_pil().convert("L"))
                    left = line.span(match.start(), match.end())[0]
                    if _checkbox_marked_left_of(images[page.index], line, left, scale, check.gap):
                        marked.append(check.key)
                        done = True
                    elif check.occurrence:
                        done = True
                    if done:
                        break
                if done:
                    break
    finally:
        document.close()
    return marked


def _printed_caption(line: Line, match: re.Match, fonts: Set[FontKey]) -> bool:
    """True when the matched words are printed on the form rather than typed into it."""
    left, right = line.span(match.start(), match.end())
    return any(word.font not in fonts and word.x0 >= left - 1.0 and word.x1 <= right + 1.0 for word in line.words)


def _checkbox_marked_left_of(image, line: Line, left: float, scale: float, gap: float) -> bool:
    """True when the checkbox drawn left of ``left`` on ``line`` carries a mark.

    The box is found as the square-shaped run of dark pixels nearest the caption,
    then the middle of it is examined: an empty box is ink only on its border, a
    ticked one is crossed or filled through the centre.
    """
    height = max(line.bottom - line.top, 7.0)
    x0 = max(left - CHECKBOX_SEARCH * height, 0.0)
    x1 = left - gap / 4.0
    strip = image[
        int(max(line.top - 2.5, 0.0) * scale) : int((line.bottom + 2.5) * scale),
        int(x0 * scale) : int(x1 * scale),
    ]
    if strip.size == 0:
        return False
    dark = strip < CHECKBOX_DARK
    box = _square_component(dark, height * scale)
    if box is None:
        return False
    bx0, by0, bx1, by1 = box
    inset_x = max(1, int((bx1 - bx0 + 1) * 0.25))
    inset_y = max(1, int((by1 - by0 + 1) * 0.25))
    inner = dark[by0 + inset_y : by1 - inset_y + 1, bx0 + inset_x : bx1 - inset_x + 1]
    return bool(inner.size) and float(inner.mean()) >= CHECKBOX_MARK_INK


def _square_component(dark, side: float) -> Optional[Tuple[int, int, int, int]]:
    """The bounding box of the square-ish run of dark pixels closest to the caption."""
    height, width = dark.shape
    seen = [[False] * width for _ in range(height)]
    best: Optional[Tuple[int, int, int, int]] = None
    for start_y in range(height):
        for start_x in range(width):
            if not dark[start_y, start_x] or seen[start_y][start_x]:
                continue
            queue = deque([(start_y, start_x)])
            seen[start_y][start_x] = True
            top = bottom = start_y
            box_left = box_right = start_x
            while queue:
                y, x = queue.popleft()
                top, bottom = min(top, y), max(bottom, y)
                box_left, box_right = min(box_left, x), max(box_right, x)
                for next_y in (y - 1, y, y + 1):
                    for next_x in (x - 1, x, x + 1):
                        if 0 <= next_y < height and 0 <= next_x < width and dark[next_y, next_x] and not seen[next_y][next_x]:
                            seen[next_y][next_x] = True
                            queue.append((next_y, next_x))
            box_width, box_height = box_right - box_left + 1, bottom - top + 1
            if not (SQUARE_MIN * side <= box_width <= SQUARE_MAX * side and SQUARE_MIN * side <= box_height <= SQUARE_MAX * side):
                continue
            if not 0.7 <= box_width / box_height <= 1.45:
                continue
            if best is None or box_right > best[2]:
                best = (box_left, top, box_right, bottom)
    return best
