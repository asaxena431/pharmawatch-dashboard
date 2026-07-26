"""PDF -> text extraction for MedWatch 3500A forms.

PaddleOCR is the primary engine: pages are rasterised with pypdfium2 and each
page image is run through PP-OCR detection + recognition.  Recognised text
boxes are re-assembled into reading-order lines using their bounding boxes so
that ``Label: value`` pairs survive OCR.

If the PDF carries a text layer and PaddleOCR is unavailable (or fails to
load its models, e.g. on an offline machine), the embedded text layer is used
as a fallback so the rest of the pipeline stays testable.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

DEFAULT_DPI = 200


@dataclass
class OcrResult:
    lines: List[str]
    pages: int
    engine: str

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class OcrError(RuntimeError):
    pass


def rasterize_pdf(pdf_path: str, dpi: int = DEFAULT_DPI) -> List["object"]:
    """Render each PDF page to a PIL image."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise OcrError("pypdfium2 is required to rasterize PDFs: pip install pypdfium2") from exc

    scale = dpi / 72.0
    document = pdfium.PdfDocument(pdf_path)
    try:
        return [document[i].render(scale=scale).to_pil() for i in range(len(document))]
    finally:
        document.close()


_PADDLE_ENGINE = None


def _get_paddle_engine(lang: str = "en"):
    """Instantiate (and cache) a PaddleOCR engine, tolerating API differences."""
    global _PADDLE_ENGINE
    if _PADDLE_ENGINE is not None:
        return _PADDLE_ENGINE

    from paddleocr import PaddleOCR

    last_error: Optional[Exception] = None
    # PaddleOCR 3.x uses use_textline_orientation; 2.x used use_angle_cls.
    # oneDNN kernels crash on some CPU builds of paddlepaddle 3.x, so the
    # first (preferred) configuration disables them.
    # Document unwarping/orientation preprocessing is skipped: form scans are
    # already upright and the unwarping step can crop text near the page edge.
    for kwargs in (
        {
            "lang": lang,
            "use_textline_orientation": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "enable_mkldnn": False,
        },
        {"lang": lang, "use_textline_orientation": False, "enable_mkldnn": False},
        {"lang": lang, "use_textline_orientation": False},
        {"lang": lang, "use_angle_cls": False, "show_log": False},
        {"lang": lang},
    ):
        try:
            _PADDLE_ENGINE = PaddleOCR(**kwargs)
            return _PADDLE_ENGINE
        except (TypeError, ValueError) as exc:
            last_error = exc
    raise OcrError(f"could not initialise PaddleOCR: {last_error}")


def _boxes_from_paddle(engine, image) -> List[Tuple[float, float, float, str]]:
    """Return (top, bottom, left, text) tuples for one page image."""
    import numpy as np

    array = np.array(image.convert("RGB"))
    raw = None
    if hasattr(engine, "predict"):
        raw = engine.predict(array)
    else:  # pragma: no cover - very old releases
        raw = engine.ocr(array)

    boxes: List[Tuple[float, float, float, str]] = []
    for page in raw or []:
        # PaddleOCR >= 3.0 returns dict-like results.
        if isinstance(page, dict) or hasattr(page, "get"):
            texts = page.get("rec_texts") or []
            polys = page.get("rec_polys")
            if polys is None:
                polys = page.get("dt_polys") or page.get("rec_boxes") or []
            for text, poly in zip(texts, polys):
                ys = [float(p[1]) for p in poly]
                xs = [float(p[0]) for p in poly]
                boxes.append((min(ys), max(ys), min(xs), str(text)))
            continue
        # PaddleOCR 2.x: [[poly, (text, score)], ...]
        for entry in page or []:
            poly, rec = entry[0], entry[1]
            text = rec[0] if isinstance(rec, (list, tuple)) else str(rec)
            ys = [float(p[1]) for p in poly]
            xs = [float(p[0]) for p in poly]
            boxes.append((min(ys), max(ys), min(xs), str(text)))
    return boxes


def group_boxes_into_lines(boxes: Sequence[Tuple[float, float, float, str]]) -> List[str]:
    """Merge OCR boxes that share a text line, left-to-right."""
    if not boxes:
        return []
    heights = [b[1] - b[0] for b in boxes if b[1] > b[0]]
    tolerance = (sum(heights) / len(heights) * 0.6) if heights else 6.0

    lines: List[List[Tuple[float, float, float, str]]] = []
    for box in sorted(boxes, key=lambda b: (b[0], b[2])):
        centre = (box[0] + box[1]) / 2
        placed = False
        for line in lines:
            line_centre = sum((b[0] + b[1]) / 2 for b in line) / len(line)
            if abs(centre - line_centre) <= tolerance:
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])

    out: List[str] = []
    for line in lines:
        line.sort(key=lambda b: b[2])
        text = " ".join(b[3].strip() for b in line if b[3].strip())
        if text:
            out.append(text)
    return out


def extract_text_layer(pdf_path: str) -> List[str]:
    """Read the embedded text layer of a PDF, one entry per visual line."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    lines: List[str] = []
    try:
        for index in range(len(document)):
            page_text = document[index].get_textpage().get_text_range()
            for raw_line in page_text.splitlines():
                stripped = raw_line.strip()
                if stripped:
                    lines.append(stripped)
    finally:
        document.close()
    return lines


def ocr_pdf(
    pdf_path: str,
    dpi: int = DEFAULT_DPI,
    lang: str = "en",
    engine: str = "paddleocr",
) -> OcrResult:
    """Extract lines of text from a 3500A PDF.

    ``engine`` may be ``paddleocr`` (rasterise + PP-OCR), ``text-layer``
    (embedded text only) or ``auto`` (PaddleOCR with text-layer fallback).
    """
    if engine == "text-layer":
        lines = extract_text_layer(pdf_path)
        return OcrResult(lines=lines, pages=_page_count(pdf_path), engine="text-layer")

    try:
        paddle = _get_paddle_engine(lang=lang)
        images = rasterize_pdf(pdf_path, dpi=dpi)
        lines: List[str] = []
        for image in images:
            lines.extend(group_boxes_into_lines(_boxes_from_paddle(paddle, image)))
        if not lines:
            raise OcrError("PaddleOCR returned no text")
        return OcrResult(lines=lines, pages=len(images), engine="paddleocr")
    except Exception as exc:
        if engine == "paddleocr":
            raise OcrError(f"PaddleOCR extraction failed: {exc}") from exc
        fallback = extract_text_layer(pdf_path)
        if not fallback:
            raise OcrError(f"PaddleOCR failed ({exc}) and the PDF has no text layer") from exc
        return OcrResult(lines=fallback, pages=_page_count(pdf_path), engine="text-layer (PaddleOCR unavailable)")


def _page_count(pdf_path: str) -> int:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        return len(document)
    finally:
        document.close()
