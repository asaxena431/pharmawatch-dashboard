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
from .models import Sender, VeterinaryReport
from .ocr import OcrResult
from .validate import SchemaError, validate_xml  # noqa: F401  (SchemaError re-exported)

# CVM's electronic submission message: VICH GL42 carried in HL7 v3 (MCCI_IN200100UV01),
# supporting documents embedded; validates against FDA's published schemas.
FORMAT_VICH_HL7 = "vich-hl7"
# Accepted only to exercise the error path (error folder + e-mail note); never produced.
FORMAT_GL42 = "gl42"
FORMATS = (FORMAT_VICH_HL7, FORMAT_GL42)

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


class UnreadableFormError(ValueError):
    """Nothing could be read off the form, so its message would be all empty fields."""


class UnsupportedFormatError(ValueError):
    """A format that is accepted on the command line / in the ini but never converted to."""


def check_format(output_format: Optional[str]) -> str:
    """The format to write; ``gl42`` raises ``UnsupportedFormatError`` so the case fails on purpose."""
    fmt = output_format or FORMAT_VICH_HL7
    if fmt == FORMAT_GL42:
        raise UnsupportedFormatError(
            "gl42 is accepted only to test the error path and is not converted; use vich-hl7"
        )
    if fmt != FORMAT_VICH_HL7:
        raise ValueError(f"unknown output format: {fmt} (choose from {', '.join(FORMATS)})")
    return fmt


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
    sender: Optional[Sender] = None,
) -> str:
    """The report as the vich-hl7 message, schema-validated unless ``validate`` is off."""
    check_format(output_format)
    xml = vich_hl7.to_xml_string(report, documents=documents, sender=sender)
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
    sender: Optional[Sender] = None,
) -> ConversionResult:
    """Read a 1932 / 1932a PDF and convert it to the vich-hl7 message.

    ``layout`` selects the reader: ``1932a`` the XFA dataset, ``1932`` the
    field-template reader, ``auto`` looks at the PDF and picks.

    ``engine`` applies to the static 1932 only.  ``text-layer`` reads the
    AcroForm directly, which is exact and instant on a fillable form; ``auto``
    runs PaddleOCR and falls back to the text layer; ``paddleocr`` forces OCR.
    A scanned or printed copy (no fields, no text) is OCRed whatever the engine:
    a scanned 1932a with :mod:`cvm_aer.scan_1932a`, a scanned 1932 as ``auto``.

    ``attachments`` are the case's supporting documents: named in the report
    and embedded with the form itself in the message.

    The message is validated against FDA's VICH schemas (``SchemaError`` when it
    fails) unless ``validate`` is off.  ``output_format`` ``gl42`` raises
    ``UnsupportedFormatError`` before the PDF is read (error-path testing).
    """
    from .xfa_1932a import is_1932a_form

    check_format(output_format)
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    if engine not in ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    if layout != LAYOUT_1932 and is_1932a_form(pdf_path):
        return _convert_1932a(pdf_path, output_format, attachments, validate, sender)
    if layout != LAYOUT_1932 and is_image_only(pdf_path):
        scanned = _read_scanned_1932a(pdf_path, attachments, dpi, lang)
        if scanned is not None:
            return _result_1932a(scanned, output_format, validate, sender)
        # Nothing for the text layer to read: a scanned static 1932 is OCRed.
        if engine == ENGINE_TEXT_LAYER:
            engine = ENGINE_AUTO
    if layout == LAYOUT_1932A:
        return _convert_1932a(pdf_path, output_format, attachments, validate, sender)
    return _convert_1932(pdf_path, output_format, engine, dpi, lang, attachments, validate, sender)


def is_image_only(pdf_path: str) -> bool:
    """True for a scanned or printed copy: no form fields and no text on its first page."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(pdf_path)
        if reader.get_fields():
            return False
        return not (reader.pages[0].extract_text() or "").strip() if reader.pages else False
    except Exception:
        return False


def _read_scanned_1932a(pdf_path: str, attachments: Sequence[str], dpi: int, lang: str):
    """A scanned 1932a read with OCR; None when the scan is not a 1932a."""
    from . import scan_1932a

    try:
        import paddleocr  # noqa: F401
    except ImportError:
        raise UnreadableFormError(
            f"{os.path.basename(pdf_path)} is a scanned or printed copy; reading it needs OCR: "
            "pip install -r requirements-ocr.txt"
        ) from None
    try:
        return scan_1932a.read_scanned_1932a(pdf_path, attachments, dpi=dpi, lang=lang)
    except scan_1932a.NotA1932aScan:
        return None


def _convert_1932a(
    pdf_path: str,
    output_format: Optional[str],
    attachments: Sequence[str],
    validate: bool,
    sender: Optional[Sender] = None,
) -> ConversionResult:
    from . import xfa_1932a

    return _result_1932a(xfa_1932a.read_1932a(pdf_path, attachments), output_format, validate, sender)


def _result_1932a(
    form, output_format: Optional[str], validate: bool, sender: Optional[Sender] = None
) -> ConversionResult:
    from . import xfa_1932a

    report = xfa_1932a.map_veterinary_report(form)
    lines = [f"{key} = {value}" for key, value in form.values.items() if value]
    ocr = OcrResult(lines=lines, pages=0, engine=form.engine)
    documents = [(document.name, document.data) for document in form.documents]
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, output_format, documents, validate, sender),
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
    sender: Optional[Sender] = None,
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

    if not form.values and not form.checks:
        raise UnreadableFormError(
            f"nothing could be read from {os.path.basename(pdf_path)}: it has no fillable fields or text, "
            "so it is a scanned or printed copy (e.g. off a copier); send the fillable 1932 / 1932a PDF instead"
        )
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
        xml=render_xml(report, output_format, documents, validate, sender),
        output_format=output_format or FORMAT_VICH_HL7,
        layout=LAYOUT_1932,
        validated=validate,
    )
