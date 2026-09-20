"""Geometry shared by the template-guided form readers.

A form template lists each field's rectangle in PDF points; the reader rasterises
the page, OCRs it, and assigns every word to the box it falls in.  These helpers
convert rectangles to pixels, measure how much of a word lies inside a box, and
decide whether a checkbox carries a mark.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# Fraction of dark ink (inside the inset box) above which a checkbox counts as marked.
CHECKBOX_INK_THRESHOLD = 0.05
# A word is assigned to a field box when at least this much of it falls inside.
MIN_WORD_OVERLAP = 0.5

# A box a filler tinted rather than ticked: its interior sits a few grey levels
# below the paper touching the box, over most of the interior.
SHADED_BOX_MARGIN_PX = 4
SHADED_BOX_PAPER_GREY = 200
SHADED_BOX_TINT_GREY = 4
SHADED_BOX_COVERAGE = 0.5
# Registration: ink lying on a printed rule at least this many pixels long is

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


