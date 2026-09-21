"""End-to-end pipeline: Form FDA 1932 / 1932a PDF -> report model -> VICH HL7 message.

Two revisions of the form are read.  The dynamic XFA 1932a carries its data as
an XML dataset inside the PDF, which is read directly.  The static 1932 is read
from its AcroForm when it is fillable, or rasterised and OCRed with PaddleOCR
against the committed field template when it is a printed or scanned copy.

Whatever the reader, the report is written as CVM's HL7 v3 submission message
(``vich-hl7``) and validated against FDA's published VICH schemas before it is
returned: a message the schemas reject raises ``SchemaError``.
"""

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import vich_hl7
from .models import VeterinaryReport
from .ocr import OcrResult
from .validate import SchemaError, validate_xml  # noqa: F401  (SchemaError re-exported)

# CVM's electronic submission message: VICH GL42 carried in HL7 v3 (MCCI_IN200100UV01),
# supporting documents embedded; validates against FDA's published schemas.
FORMAT_VICH_HL7 = "vich-hl7"
FORMATS = (FORMAT_VICH_HL7,)

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
    validated: bool

    @property
    def summary(self) -> dict:
        report = self.report
        return {
            "layout": self.layout,
            "output_format": self.output_format,
            "schema_validated": self.validated,
            "ocr_engine": self.ocr.engine,
            "pages": self.ocr.pages,
            "aer_identifier": report.report_id,
            "species": report.animal.species,
            "product": report.product.brand_name,
            "clinical_signs": len(report.event.signs),
            "attachments": list(report.attachments),
        }


def render_xml(
    report: VeterinaryReport,
    output_format: Optional[str] = None,
    documents: Sequence[Tuple[str, bytes]] = (),
    validate: bool = True,
) -> str:
    """The report as the vich-hl7 message, schema-validated unless ``validate`` is off."""
    fmt = output_format or FORMAT_VICH_HL7
    if fmt != FORMAT_VICH_HL7:
        raise ValueError(f"unknown output format: {fmt} (choose from {', '.join(FORMATS)})")
    xml = vich_hl7.to_xml_string(report, documents=documents)
    if validate:
        validate_xml(xml)
    return xml


def convert_pdf(
    pdf_path: str,
    output_format: Optional[str] = None,
    engine: str = ENGINE_TEXT_LAYER,
    dpi: int = 200,
    lang: str = "en",
    layout: str = LAYOUT_AUTO,
    attachments: Sequence[str] = (),
    validate: bool = True,
) -> ConversionResult:
    """Read a 1932 / 1932a PDF and convert it to the vich-hl7 message.

    ``layout`` selects the reader: ``1932a`` the XFA dataset, ``1932`` the
    field-template reader, ``auto`` looks at the PDF and picks.

    ``engine`` applies to the static 1932 only.  ``text-layer`` reads the
    AcroForm directly, which is exact and instant on a fillable form; ``auto``
    runs PaddleOCR and falls back to the text layer; ``paddleocr`` forces OCR.
    Printed or scanned copies have no text layer and need one of the latter.

    ``attachments`` are the case's supporting documents: named in the report
    and embedded with the form itself in the message.

    The message is validated against FDA's VICH schemas (``SchemaError`` when it
    fails) unless ``validate`` is off.
    """
    from .xfa_1932a import is_1932a_form

    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    if engine not in ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    if layout == LAYOUT_1932A or (layout == LAYOUT_AUTO and is_1932a_form(pdf_path)):
        return _convert_1932a(pdf_path, output_format, attachments, validate)
    return _convert_1932(pdf_path, output_format, engine, dpi, lang, attachments, validate)


def _convert_1932a(
    pdf_path: str, output_format: Optional[str], attachments: Sequence[str], validate: bool
) -> ConversionResult:
    from . import xfa_1932a

    form = xfa_1932a.read_1932a(pdf_path, attachments)
    report = xfa_1932a.map_veterinary_report(form)
    lines = [f"{key} = {value}" for key, value in form.values.items() if value]
    ocr = OcrResult(lines=lines, pages=0, engine=form.engine)
    documents = [(document.name, document.data) for document in form.documents]
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, output_format, documents, validate),
        output_format=output_format or FORMAT_VICH_HL7,
        layout=LAYOUT_1932A,
        validated=validate,
    )


def _convert_1932(
    pdf_path: str,
    output_format: Optional[str],
    engine: str,
    dpi: int,
    lang: str,
    attachments: Sequence[str] = (),
    validate: bool = True,
) -> ConversionResult:
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
    if attachments:
        report.attachments = [os.path.basename(path) for path in attachments]
    documents = vich_hl7.documents_from([pdf_path, *attachments])
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, output_format, documents, validate),
        output_format=output_format or FORMAT_VICH_HL7,
        layout=LAYOUT_1932,
        validated=validate,
    )
