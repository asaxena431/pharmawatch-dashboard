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
    ConcomitantProduct,
    LabTest,
    ManufacturerInfo,
    MedWatchReport,
    Patient,
    Reporter,
    SuspectDevice,
    SuspectProduct,
    UserFacility,
)

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "fda_3500a_2025.json")

# Fraction of dark ink (inside the inset box) above which a checkbox counts as marked.
CHECKBOX_INK_THRESHOLD = 0.05
# A word is assigned to a field box when at least this much of it falls inside.
MIN_WORD_OVERLAP = 0.5

# A scan of the form is never exactly the published geometry: the page is a
# fraction of a percent smaller and shifted by a few points, and OCR reads a
# caption and the value beside it as one box.  The scanned path therefore
# registers each page against the blank form, assigns text a character at a
# time, and reads checkboxes from a smaller window with more ink required (the
# printed border of a box survives registration slop, its interior does not).
SCAN_PAD_PT = 2.0
SCAN_CHECKBOX_INSET = 0.3
SCAN_CHECKBOX_INK_THRESHOLD = 0.12
# A box a filler tinted rather than ticked: its interior sits a few grey levels
# below the paper touching the box, over most of the interior.
SHADED_BOX_MARGIN_PX = 4
SHADED_BOX_PAPER_GREY = 200
SHADED_BOX_TINT_GREY = 4
SHADED_BOX_COVERAGE = 0.5
# Registration: ink lying on a printed rule at least this many pixels long is
# what the two copies of the form are matched on.  A rule counts as found where
# the ink left on it reaches RULE_PEAK_FRACTION of the densest rule's, and as
# the same rule on both copies when the two sit RULE_TOLERANCE_PX apart.  The
# page may be shifted up to SHIFT_LIMIT pixels and scaled within SCALE_RANGE.
RULE_RUN_PX = 60
SHIFT_LIMIT = 90
SCALE_RANGE = 0.07
SCALE_STEP = 0.001
RULE_TOLERANCE_PX = 3.0
RULE_PEAK_FRACTION = 0.25
MIN_RULES = 2
MIN_RULE_MATCH = 0.5
# How far outside its own rectangle a printed caption may sit and still be a
# caption OCR can read into that field (pixels at the rendering resolution).
CAPTION_PAD_X = 90.0
CAPTION_PAD_Y = 30.0
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
        # A scan of the form is a fraction of a percent off its published size.
        if not _same_page_size(float(first.width), float(first.height), reference):
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


def _same_page_size(width: float, height: float, reference: dict, tolerance: float = 0.01) -> bool:
    """True when a page is the form's size, allowing a scan's slight shrink."""
    return (
        abs(width - reference["width"]) <= max(2.0, reference["width"] * tolerance)
        and abs(height - reference["height"]) <= max(2.0, reference["height"] * tolerance)
    )


def is_scanned_form(pdf_path: str) -> bool:
    """True when ``pdf_path`` is the official 3500A as an image, with no text of its own.

    Such a copy carries neither AcroForm values nor a text layer, so every value
    has to be read by OCR and placed by registering the page against the blank
    form rather than by the published geometry alone.
    """
    if not is_official_form(pdf_path):
        return False
    try:
        from pypdf import PdfReader

        if (PdfReader(pdf_path).get_fields() or {}):
            return False
    except Exception:
        return False
    try:
        from .ocr import extract_text_layer

        return not any(line.strip() for line in extract_text_layer(pdf_path))
    except Exception:
        return True


@dataclass
class PageAlignment:
    """Where the published form's geometry sits on one scanned page.

    ``scale_x``/``scale_y`` take blank-form pixels to scanned-page pixels (the
    scan is a slightly different size), ``dx``/``dy`` are the scan's offset in
    blank-form pixels, as measured by :func:`align_pages`.
    """

    scale_x: float = 1.0
    scale_y: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def to_scan(self, rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x0, y0, x1, y1 = rect
        return (
            (x0 - self.dx) * self.scale_x,
            (y0 - self.dy) * self.scale_y,
            (x1 - self.dx) * self.scale_x,
            (y1 - self.dy) * self.scale_y,
        )

    def to_template(self, word: Tuple[float, float, float, float, str]) -> Tuple[float, float, float, float, str]:
        return (
            word[0] / self.scale_x + self.dx,
            word[1] / self.scale_y + self.dy,
            word[2] / self.scale_x + self.dx,
            word[3] / self.scale_y + self.dy,
            word[4],
        )


def align_pages(images: Sequence[object], dpi: int, blank_path: Optional[str] = None) -> Dict[int, PageAlignment]:
    """Register each scanned page against the blank form, page by page.

    The form's own printed rules are the only ink both copies are certain to
    share, so the page is placed from them alone: ink that lies on a run of at
    least :data:`RULE_RUN_PX` pixels is kept, the rest (captions, typed values,
    handwriting, scanner noise) is discarded, and the rules that remain are
    reduced to the position of each one along the axis.  A copy is often not
    only offset but a few percent smaller than the published page - a fax or a
    photocopy shrinks it - so a shift alone cannot place a checkbox at the far
    side of the page; the scale and the offset are measured together, as the
    pair that puts the most of the scan's rules on the blank form's.  A page
    whose rules cannot be matched falls back to the ratio of the page sizes.
    """
    from .official_form import blank_form_path

    try:
        import pypdfium2 as pdfium

        blank = pdfium.PdfDocument(blank_path or blank_form_path())
    except Exception:  # pragma: no cover - no blank form to register against
        return {}

    alignments: Dict[int, PageAlignment] = {}
    try:
        for index, image in enumerate(images):
            if index >= len(blank):
                break
            reference = blank[index].render(scale=dpi / 72.0).to_pil()
            blank_ink = _binary(reference)
            scan_ink = _binary(image)
            width, height = image.size
            scale_x, dx = _rule_fit(blank_ink, scan_ink, axis=1, fallback=width / reference.size[0])
            scale_y, dy = _rule_fit(blank_ink, scan_ink, axis=0, fallback=height / reference.size[1])
            alignments[index] = PageAlignment(scale_x=scale_x, scale_y=scale_y, dx=dx, dy=dy)
    finally:
        blank.close()
    return alignments


def _rule_fit(blank_ink, scan_ink, axis: int, fallback: float) -> Tuple[float, float]:
    """The scale and offset along ``axis`` that put the scan's rules on the blank form's.

    ``axis`` 0 measures the page down (from its horizontal rules), 1 across it
    (from its vertical rules).  Every candidate pair is scored by how many of
    the blank form's rules land within :data:`RULE_TOLERANCE_PX` of one of the
    scan's, since a rule either coincides or it does not; an overlap of the ink
    itself would instead be maximised by squeezing a dense page onto itself.
    Ties go to the pair that moves the page least, and a page with too few
    rules, or too few of them matched, keeps ``fallback`` unshifted.
    """
    import numpy as np

    reference = _rule_positions(blank_ink, axis)
    other = _rule_positions(scan_ink, axis)
    if len(reference) < MIN_RULES or len(other) < MIN_RULES:
        return fallback, 0.0

    shifts = np.arange(-SHIFT_LIMIT, SHIFT_LIMIT + 1, dtype=np.float64)
    best = (fallback, 0.0, 0.0)
    for scale in np.arange(1.0 - SCALE_RANGE, 1.0 + SCALE_RANGE + SCALE_STEP / 2, SCALE_STEP):
        mapped = (reference[None, :] - shifts[:, None]) * scale
        distance = np.abs(mapped[:, :, None] - other[None, None, :]).min(axis=2)
        hits = (distance <= RULE_TOLERANCE_PX).sum(axis=1).astype(np.float64)
        hits -= np.abs(shifts) * 1e-3 + abs(scale - 1.0)
        index = int(hits.argmax())
        if hits[index] > best[2]:
            best = (float(scale), float(shifts[index]), float(hits[index]))
    if best[2] < MIN_RULE_MATCH * len(reference):
        return fallback, 0.0
    return best[0], best[1]


def _rule_positions(ink, axis: int):
    """Where each printed rule crosses ``axis``, as a position in pixels."""
    import numpy as np

    profile = _long_runs(ink, 1 - axis).mean(axis=1 - axis)
    if not profile.size or not profile.max():
        return np.zeros(0)
    lit = profile >= profile.max() * RULE_PEAK_FRACTION
    positions, start = [], None
    for index, value in enumerate(lit):
        if value and start is None:
            start = index
        elif not value and start is not None:
            positions.append((start + index - 1) / 2.0)
            start = None
    if start is not None:
        positions.append((start + len(lit) - 1) / 2.0)
    return np.array(positions, dtype=np.float64)


def _long_runs(ink, axis: int, run: int = RULE_RUN_PX):
    """Only the ink that lies on a printed rule at least ``run`` pixels long along ``axis``."""
    import numpy as np

    eroded = ink
    step = 1
    while step < run:
        eroded = np.minimum(eroded, np.roll(eroded, step, axis=axis))
        step *= 2
    grown = eroded
    for shift in range(1, run):
        grown = np.maximum(grown, np.roll(eroded, -shift, axis=axis))
    return grown


def _binary(image):
    import numpy as np

    return (np.asarray(image.convert("L"), dtype=np.float32) < 160).astype(np.float32)


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


def _checkbox_marked(
    image,
    pixel_rect: Tuple[float, float, float, float],
    inset: float = 0.2,
    threshold: float = CHECKBOX_INK_THRESHOLD,
) -> bool:
    import numpy as np

    x0, y0, x1, y1 = (int(round(v)) for v in pixel_rect)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return False
    inset_x = max(1, int((x1 - x0) * inset))
    inset_y = max(1, int((y1 - y0) * inset))
    crop = image.convert("L").crop((x0 + inset_x, y0 + inset_y, x1 - inset_x, y1 - inset_y))
    array = np.asarray(crop)
    if array.size == 0:
        return False
    if float((array < 128).mean()) >= threshold:
        return True
    return _checkbox_shaded(image, pixel_rect, array)


def _checkbox_shaded(
    image,
    pixel_rect: Tuple[float, float, float, float],
    interior,
) -> bool:
    """True when the box is filled with a flat tint instead of a drawn mark.

    Some filled forms print a tick as a light grey fill, which survives scanning
    as an interior a few grey levels below the paper around the box.  Page
    shading darkens box and paper alike, so the fill is only read as a mark when
    it covers most of the interior and the paper immediately outside stays light.
    """
    import numpy as np

    x0, y0, x1, y1 = (int(round(v)) for v in pixel_rect)
    grey = np.asarray(image.convert("L"), dtype=float)
    pad = SHADED_BOX_MARGIN_PX
    border = np.concatenate(
        [
            grey[max(0, y0 - pad) : y0, x0:x1].ravel(),
            grey[y1 : y1 + pad, x0:x1].ravel(),
            grey[y0:y1, max(0, x0 - pad) : x0].ravel(),
            grey[y0:y1, x1 : x1 + pad].ravel(),
        ]
    )
    paper = border[border > SHADED_BOX_PAPER_GREY]
    if paper.size == 0:
        return False
    level = float(np.median(paper))
    tinted = np.asarray(interior, dtype=float) < level - SHADED_BOX_TINT_GREY
    return float(tinted.mean()) >= SHADED_BOX_COVERAGE


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


def extract_form_scan(
    pdf_path: str,
    dpi: int = 200,
    lang: str = "en",
    template: Optional[dict] = None,
) -> ExtractedForm:
    """Read an *image-only* official 3500A: OCR the pages onto the registered geometry.

    A scanned or faxed copy carries no text and its pages are neither exactly the
    published size nor exactly where the published geometry says, so each page is
    first registered against the blank form (:func:`align_pages`).  OCR reads a
    printed caption and the value typed beside it as one box, so text is assigned
    a character at a time rather than a word at a time.
    """
    from .ocr import ocr_page_words

    template = template or load_template()
    per_page_words, images = ocr_page_words(pdf_path, dpi=dpi, lang=lang)
    return _extract_by_geometry(
        template,
        per_page_words,
        images,
        dpi=dpi,
        engine="paddleocr",
        alignments=align_pages(images, dpi=dpi),
        by_character=True,
        captions=blank_form_captions(dpi=dpi, template=template),
        checkbox_inset=SCAN_CHECKBOX_INSET,
        checkbox_threshold=SCAN_CHECKBOX_INK_THRESHOLD,
    )


def blank_form_captions(
    dpi: int = 200,
    template: Optional[dict] = None,
    blank_path: Optional[str] = None,
) -> Dict[str, List[str]]:
    """The printed words each field's rectangle can pick up, read off the blank form.

    The blank form has a text layer, so what is printed around every widget is
    known exactly.  Those words are the only ones OCR can add to a value that
    the person filling the form did not write, which is what makes them safe to
    drop from the edges of a scanned value (see :func:`_strip_captions`).
    """
    from .ocr import text_layer_page_words
    from .official_form import blank_form_path

    template = template or load_template()
    try:
        per_page_words, _ = text_layer_page_words(blank_path or blank_form_path(), dpi=dpi)
    except Exception:  # pragma: no cover - no blank form to read the captions from
        return {}
    scale = dpi / 72.0
    captions: Dict[str, List[str]] = {}
    for spec in template["fields"]:
        page = spec["page"]
        if spec["type"] not in ("Tx", "Ch") or page >= len(per_page_words):
            continue
        x0, y0, x1, y1 = _rect_to_pixels(spec["rect"], float(template["pages"][str(page)]["height"]), scale)
        tokens: List[str] = []
        for word in per_page_words[page]:
            if word[2] < x0 - CAPTION_PAD_X or word[0] > x1 + CAPTION_PAD_X:
                continue
            if word[3] < y0 - CAPTION_PAD_Y or word[1] > y1 + CAPTION_PAD_Y:
                continue
            tokens.extend(part for part in word[4].split() if part)
        if tokens:
            captions[f"p{page}.{spec['name']}"] = tokens
    return captions


def _extract_by_geometry(
    template: dict,
    per_page_words: List[List[Tuple[float, float, float, float, str]]],
    images: Sequence[object],
    dpi: int,
    engine: str,
    alignments: Optional[Dict[int, PageAlignment]] = None,
    by_character: bool = False,
    captions: Optional[Dict[str, List[str]]] = None,
    checkbox_inset: float = 0.2,
    checkbox_threshold: float = CHECKBOX_INK_THRESHOLD,
) -> ExtractedForm:
    """Assign word boxes to the template's field rectangles and read checkboxes.

    With ``alignments`` the words are moved into the published geometry's own
    pixel space first, and every rectangle read from the pixels is moved the
    other way, so a page that is slightly shrunk or shifted still lines up.
    """
    scale = dpi / 72.0
    alignments = alignments or {}

    fields_by_page: Dict[int, List[dict]] = {}
    for spec in template["fields"]:
        fields_by_page.setdefault(spec["page"], []).append(spec)

    result = ExtractedForm(pages=len(images), engine=engine)

    for page_index, image in enumerate(images):
        page_meta = template["pages"].get(str(page_index))
        if page_meta is None:
            continue
        alignment = alignments.get(page_index, PageAlignment())
        page_height = float(page_meta["height"])
        words = [alignment.to_template(w) for w in (per_page_words[page_index] if page_index < len(per_page_words) else [])]
        text_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] in ("Tx", "Ch")]
        box_specs = [s for s in fields_by_page.get(page_index, []) if s["type"] == "Btn"]

        pixel_rects = [(_rect_to_pixels(s["rect"], page_height, scale), s) for s in text_specs]
        buckets: Dict[str, List[Tuple[float, float, float, str]]] = {}
        for word in words:
            if not word[4].strip():
                continue
            for key, entry in _assign_word(word, pixel_rects, page_index, by_character, SCAN_PAD_PT * scale):
                buckets.setdefault(key, []).append(entry)

        for key, entries in buckets.items():
            text = _strip_label(key, _reading_order(entries))
            if captions:
                text = _strip_captions(text, captions.get(key, ()))
            if text:
                result.values[key] = text

        for spec in box_specs:
            pixel_rect = alignment.to_scan(_rect_to_pixels(spec["rect"], page_height, scale))
            if _checkbox_marked(image, pixel_rect, inset=checkbox_inset, threshold=checkbox_threshold):
                result.checks.append(f"p{page_index}.{spec['name']}")

    return result


def _assign_word(
    word: Tuple[float, float, float, float, str],
    pixel_rects: Sequence[Tuple[Tuple[float, float, float, float], dict]],
    page_index: int,
    by_character: bool,
    pad: float,
) -> List[Tuple[str, Tuple[float, float, float, str]]]:
    """The field(s) ``word`` belongs to, with the text each one takes from it."""
    if not by_character:
        best_spec = None
        best_overlap = 0.0
        for rect, spec in pixel_rects:
            overlap = _overlap_fraction(word, rect)
            if overlap > best_overlap:
                best_overlap, best_spec = overlap, spec
        if best_spec is None or best_overlap < MIN_WORD_OVERLAP:
            return []
        return [(f"p{page_index}.{best_spec['name']}", (word[1], word[3], word[0], word[4].strip()))]

    # OCR reads "2. Age" and the 37 typed beside it as one box, so each character
    # is placed on its own and only the run that falls in a field is kept.
    text = word[4]
    width = (word[2] - word[0]) / max(len(text), 1)
    centre = (word[1] + word[3]) / 2.0
    runs: Dict[str, List[Tuple[float, str]]] = {}
    for index, character in enumerate(text):
        x = word[0] + width * (index + 0.5)
        owner = _smallest_containing(x, centre, pixel_rects, pad)
        if owner is None:
            continue
        runs.setdefault(f"p{page_index}.{owner['name']}", []).append((x, character))
    assigned: List[Tuple[str, Tuple[float, float, float, str]]] = []
    for key, characters in runs.items():
        piece = "".join(character for _, character in characters).strip()
        if not any(character.isalnum() for character in piece):
            continue
        assigned.append((key, (word[1], word[3], characters[0][0], piece)))
    return assigned


def _strip_captions(text: str, captions: Sequence[str]) -> str:
    """Drop the field's printed caption from the start and end of an OCR'd value.

    OCR reads a caption and the value beside it as one box ("2. Age 62"), and
    registration slop lets the caption's own letters into the box as well
    ("...ge 62"), so words at either end that are - or end in - a word printed
    around that field on the blank form are the caption, not the value.
    """
    tokens = text.split()
    start, end = 0, len(tokens)
    while start < end and _caption_word(tokens[start], captions):
        start += 1
    while end > start and _caption_word(tokens[end - 1], captions):
        end -= 1
    return " ".join(tokens[start:end]).strip()


def _caption_word(token: str, captions: Sequence[str]) -> bool:
    _PUNCTUATION = ",.;:#()/-"
    stripped = token.strip(_PUNCTUATION).lower()
    if not stripped:
        return True
    for caption in captions:
        printed = caption.strip(_PUNCTUATION).lower()
        if not printed:
            continue
        # A caption OCR clipped keeps either end of the printed word.
        if stripped == printed or (len(stripped) >= 2 and (printed.startswith(stripped) or printed.endswith(stripped))):
            return True
    return False


def _smallest_containing(
    x: float,
    y: float,
    pixel_rects: Sequence[Tuple[Tuple[float, float, float, float], dict]],
    pad: float,
) -> Optional[dict]:
    """The tightest field rectangle holding point ``(x, y)``, within ``pad`` pixels."""
    best: Optional[dict] = None
    best_area = float("inf")
    for (rx0, ry0, rx1, ry1), spec in pixel_rects:
        if not (rx0 - pad <= x <= rx1 + pad and ry0 - pad <= y <= ry1 + pad):
            continue
        area = (rx1 - rx0) * (ry1 - ry0)
        if area < best_area:
            best, best_area = spec, area
    return best


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
        weight=form.get("p0.patWeight"),
        weight_unit="lbs" if form.checked("p0.weightLB") else ("kg" if form.get("p0.patWeight") else None),
        races=_races(form),
        deceased_date=_clean_date(form.get("p0.deathDate")),
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
    test_results = _test_results(form)
    tests = "; ".join(
        f"{test.result} ({test.date})" if test.date else (test.result or "") for test in test_results
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
        additional_comments=form.get("p2.addComm"),
        location=_event_location(form),
        report_types=event_types,
        test_results=test_results,
        patient_problem_code=form.get("p7.healthClinCode"),
        patient_impact_code=form.get("p7.healthImpCode"),
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
        concomitants=_concomitant_products(form),
        problem_code=form.get("p7.devProbCode"),
        age=_first(form, "p7.ageYears", "p7.ageMonths"),
        age_unit=(
            "Year"
            if form.checked("p7.deviceAgeYears") or form.get("p7.ageYears")
            else ("Month" if form.get("p7.ageMonths") else None)
        ),
        labeled_single_use=_yes_no(form, "p8.singleUseY2", "p8.singleUseN2"),
        evaluated_by_manufacturer=_yes_no(form, "p8.evalManuY", "p8.evalManuN"),
        serviced_by_third_party=_yes_no(form, "p6.thirdParY", "p6.thirdParN"),
    )

    # E. Initial reporter
    report.reporter = Reporter(
        given_name=form.get("p6.reportFirst"),
        family_name=form.get("p6.reportLast"),
        name=_full_name(form.get("p6.reportFirst"), form.get("p6.reportLast")),
        address=_reporter_address(form),
        street=form.get("p6.reportAddr"),
        city=form.get("p6.reportCity"),
        state=form.get("p6.reportSt"),
        postcode=form.get("p6.reportZip"),
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
        exemption_number=form.get("p0.varNum"),
        report_sent_to_manufacturer=_yes_no(form, "p7.reptManuY", "p7.reptManuN"),
        notified_name_address=form.get("p7.manuNameAddr2"),
        corrective_action_number=form.get("p8.correction"),
        related_report_numbers=form.get("p8.relRepNum"),
    )

    report.user_facility = _user_facility(form)

    return report


def _races(form: ExtractedForm) -> List[str]:
    """Block A.5, in the order the form prints the boxes."""
    races = []
    for key, label in (
        ("p0.AmInAlNa", "American Indian or Alaska Native"),
        ("p0.asian", "Asian"),
        ("p0.black", "Black or African American"),
        ("p0.hispanic", "Hispanic or Latino"),
        ("p0.middleEastern", "Middle Eastern or North African"),
        ("p0.NaHIOtherPI", "Native Hawaiian or Pacific Islander"),
        ("p0.white", "White"),
    ):
        if form.checked(key):
            races.append(label)
    return races


def _event_location(form: ExtractedForm) -> Optional[str]:
    for key, label in (
        ("p7.locHosp", "Hospital"),
        ("p7.locHome", "Home"),
        ("p7.locNurs", "Nursing home"),
        ("p7.locAmb", "Ambulatory surgical facility"),
        ("p7.locDiag", "Diagnostic facility"),
        ("p7.locTreat", "Outpatient treatment facility"),
    ):
        if form.checked(key):
            return label
    if form.checked("p7.locOther"):
        return form.get("p7.locOtherSpec") or "Other"
    return None


def _user_facility(form: ExtractedForm) -> UserFacility:
    """Block F, from the facility's name-and-address block and its contact fields."""
    name, street, city, state, postcode = _split_facility_address(form.get("p7.facilityNAddr"))
    contact = form.get("p7.facPOC")
    given, family = _split_person_name(contact)
    return UserFacility(
        report_number=_facility_report_number(form),
        name=name,
        street=street,
        city=city,
        state=state,
        postcode=postcode,
        contact_name=contact,
        contact_given_name=given,
        contact_family_name=family,
        phone=form.get("p7.facPhone"),
        date_aware=_clean_date(form.get("p7.userAwareDate")),
        date_sent_to_fda=_clean_date(form.get("p7.reportSentDate")),
        report_type="Initial" if form.checked("p7.reptInitial") else ("Follow-up" if form.checked("p7.reptFollowUp") else None),
    )


def _facility_report_number(form: ExtractedForm) -> Optional[str]:
    """The facility's report number, from block F.1 or the header that repeats it.

    The header box (``UF/Importer Report #``) and F.1 hold the same number, so
    the fuller reading wins where one is a truncation of the other - a box whose
    text runs to its border is read short.  Whatever OCR keeps of the caption is
    dropped with it: the caption up to its ``#``, or the stray letter or two a
    caption leaves at the start of a box.
    """
    header = _report_number(form.get("p0.ufNum"))
    block = _report_number(form.get("p7.reportNum"))
    if header and block and (header in block or block in header):
        return max(header, block, key=len)
    return block or header or None


_CAPTION_RESIDUE_RE = re.compile(r"^(?:.*#\s*|[A-Za-z]{1,2}\.?\s+)")


def _report_number(value: Optional[str]) -> str:
    return _CAPTION_RESIDUE_RE.sub("", (value or "").strip()).strip()


_FACILITY_TAIL_RE = re.compile(r",\s*(?P<state>[A-Za-z]{2})\.?\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$")
_STREET_RE = re.compile(r"\b\d+[A-Za-z]?\s+\S")


def _split_facility_address(
    value: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Split "Clayton Fire Department 10 N. Bemiston Clayton, MO 63105" into its parts.

    The form prints the facility's name and address in one box and OCR reads it
    as one run of text, so the state and ZIP are matched at the end, the street
    at its number, and the town is the word between them.  A town written as
    more than one word therefore keeps only its last word.
    """
    if not value:
        return None, None, None, None, None
    text = re.sub(r"\s+", " ", value).strip()
    state = postcode = city = None
    tail = _FACILITY_TAIL_RE.search(text)
    if tail:
        state, postcode = tail.group("state").upper(), tail.group("zip")
        text = text[: tail.start()].strip()
    street_at = _STREET_RE.search(text)
    name, rest = (text[: street_at.start()].strip(), text[street_at.start():].strip()) if street_at else (text, "")
    words = rest.split()
    if tail and words:
        city, rest = words[-1], " ".join(words[:-1])
    elif tail and not street_at:
        words = name.split()
        city, name = words[-1], " ".join(words[:-1])
    return name or None, rest or None, city, state, postcode


def _split_person_name(value: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    parts = value.split()
    if len(parts) == 1:
        return None, parts[0]
    return " ".join(parts[:-1]), parts[-1]


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
    present = [product.name for product in _concomitant_products(form) if product.name]
    return "; ".join(present) or None


def _concomitant_products(form: ExtractedForm) -> List[ConcomitantProduct]:
    """Block D.9, whose rows the form continues onto the following page."""
    products = []
    for page in (5, 6):
        for index in range(1, 11):
            name = form.get(f"p{page}.cProdName{index}")
            if name:
                products.append(
                    ConcomitantProduct(
                        name=name,
                        therapy_start=_clean_date(form.get(f"p{page}.cProdStart{index}")),
                    )
                )
    return products


def _test_results(form: ExtractedForm) -> List[LabTest]:
    """Block B.3, one row per test the form lists."""
    tests = []
    for index in range(1, 5):
        result = form.get(f"p2.testData{index}")
        if result:
            tests.append(LabTest(result=result, date=_clean_date(form.get(f"p2.testDDate{index}"))))
    return tests


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
