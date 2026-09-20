"""End-to-end pipeline: Form FDA 1932 / 1932a PDF -> report model -> VICH GL42 XML.

Two revisions of the form are read.  The dynamic XFA 1932a carries its data as
an XML dataset inside the PDF, which is read directly.  The static 1932 is read
from its AcroForm when it is fillable, or rasterised and OCRed with PaddleOCR
against the committed field template when it is a printed or scanned copy.
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import gl42
from .models import VeterinaryReport
from .ocr import OcrResult

FORMAT_GL42 = "gl42"
FORMATS = (FORMAT_GL42,)

LAYOUT_AUTO = "auto"
LAYOUT_1932 = "1932"
# Form FDA 1932a submitted as a dynamic XFA PDF: the data is in its dataset.
LAYOUT_1932A = "1932a"
LAYOUTS = (LAYOUT_AUTO, LAYOUT_1932, LAYOUT_1932A)

# Engines, PDF text layer first: it is exact and instant on fillable PDFs.
ENGINE_TEXT_LAYER = "text-layer"
ENGINE_AUTO = "auto"
ENGINE_PADDLEOCR = "paddleocr"
ENGINES = (ENGINE_TEXT_LAYER, ENGINE_AUTO, ENGINE_PADDLEOCR)


@dataclass
class ConversionResult:
    report: VeterinaryReport
    ocr: OcrResult
    xml: str
    output_format: str
    layout: str

    @property
    def summary(self) -> dict:
        report = self.report
        return {
            "layout": self.layout,
            "output_format": self.output_format,
            "ocr_engine": self.ocr.engine,
            "pages": self.ocr.pages,
            "aer_identifier": report.report_id,
            "species": report.animal.species,
            "product": report.product.brand_name,
            "clinical_signs": len(report.event.signs),
            "attachments": list(report.attachments),
        }


def render_xml(report: VeterinaryReport, output_format: Optional[str] = None) -> str:
    fmt = output_format or FORMAT_GL42
    if fmt != FORMAT_GL42:
        raise ValueError(f"unknown output format: {fmt} (choose from {', '.join(FORMATS)})")
    return gl42.to_xml_string(report)


def convert_pdf(
    pdf_path: str,
    output_format: Optional[str] = None,
    engine: str = ENGINE_TEXT_LAYER,
    dpi: int = 200,
    lang: str = "en",
    layout: str = LAYOUT_AUTO,
    attachments: Sequence[str] = (),
) -> ConversionResult:
    """Read a 1932 / 1932a PDF and convert it to GL42 XML.

    ``layout`` selects the reader: ``1932a`` the XFA dataset, ``1932`` the
    field-template reader, ``auto`` looks at the PDF and picks.

    ``engine`` applies to the static 1932 only.  ``text-layer`` reads the
    AcroForm directly, which is exact and instant on a fillable form; ``auto``
    runs PaddleOCR and falls back to the text layer; ``paddleocr`` forces OCR.
    Printed or scanned copies have no text layer and need one of the latter.

    ``attachments`` are the case's supporting documents.  GL42 itself carries no
    documents, so they are recorded on the result only through the caller.
    """
    from .xfa_1932a import is_1932a_form

    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    if engine not in ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    if layout == LAYOUT_1932A or (layout == LAYOUT_AUTO and is_1932a_form(pdf_path)):
        return _convert_1932a(pdf_path, output_format, attachments)
    return _convert_1932(pdf_path, output_format, engine, dpi, lang)


def _convert_1932a(pdf_path: str, output_format: Optional[str], attachments: Sequence[str]) -> ConversionResult:
    from . import xfa_1932a

    form = xfa_1932a.read_1932a(pdf_path, attachments)
    report = xfa_1932a.map_veterinary_report(form)
    lines = [f"{key} = {value}" for key, value in form.values.items() if value]
    ocr = OcrResult(lines=lines, pages=0, engine=form.engine)
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, output_format),
        output_format=output_format or FORMAT_GL42,
        layout=LAYOUT_1932A,
    )


def _convert_1932(pdf_path: str, output_format: Optional[str], engine: str, dpi: int, lang: str) -> ConversionResult:
    from .vet_extract import extract_1932_fields, extract_1932_form, map_veterinary_report

    if engine == ENGINE_TEXT_LAYER:
        form = extract_1932_fields(pdf_path)
    else:
        try:
            form = extract_1932_form(pdf_path, dpi=dpi, lang=lang)
        except Exception as exc:
            if engine == ENGINE_PADDLEOCR:
                raise
            form = extract_1932_fields(pdf_path)
            form.engine = f"acroform (PaddleOCR unavailable: {exc})"

    report = map_veterinary_report(form, ocr_engine=form.engine)
    lines: List[str] = [f"{key} = {value}" for key, value in sorted(form.values.items())]
    lines += [f"[x] {key}" for key in sorted(form.checks)]
    ocr = OcrResult(lines=lines, pages=form.pages, engine=form.engine)
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, output_format),
        output_format=output_format or FORMAT_GL42,
        layout=LAYOUT_1932,
    )
