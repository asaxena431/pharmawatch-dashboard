"""End-to-end pipeline: PDF -> OCR -> structured report -> XML.

Three official forms are supported: FDA 3500A drug reports (CDER) become
E2B(R2) ICSRs, FDA 3500A device reports (CDRH) become FDA MDR reports, and
FDA 1932 veterinary reports (CVM) become VICH GL42 AERs.  The same functions
back both the CLI (``python -m medwatch_ocr.cli``) and the Flask demo GUI
(``medwatch_ocr.web``).
"""

from dataclasses import dataclass
from typing import List, Optional, Union

from . import e2b_r2, gl42, mdr_xml
from .models import CENTER_CDER, CENTER_CDRH, CENTER_CVM, MedWatchReport, VeterinaryReport
from .ocr import OcrResult, ocr_pdf
from .parser import build_report

FORMAT_E2B = "e2b-r2"
FORMAT_MDR = "mdr"
FORMAT_GL42 = "gl42"
FORMATS = (FORMAT_E2B, FORMAT_MDR, FORMAT_GL42)

# Input layouts: the genuine boxed FDA form vs. the flat label/value facsimile.
LAYOUT_OFFICIAL = "official"
LAYOUT_FLAT = "flat"
LAYOUT_AUTO = "auto"
LAYOUT_1932 = "1932"
LAYOUTS = (LAYOUT_AUTO, LAYOUT_OFFICIAL, LAYOUT_FLAT, LAYOUT_1932)

# Engines, PDF text layer first: it is exact and instant on fillable PDFs.
ENGINE_TEXT_LAYER = "text-layer"
ENGINE_AUTO = "auto"
ENGINE_PADDLEOCR = "paddleocr"
ENGINES = (ENGINE_TEXT_LAYER, ENGINE_AUTO, ENGINE_PADDLEOCR)


@dataclass
class ConversionResult:
    report: Union[MedWatchReport, VeterinaryReport]
    ocr: OcrResult
    xml: str
    output_format: str
    layout: str = LAYOUT_FLAT

    @property
    def summary(self) -> dict:
        report = self.report
        if isinstance(report, VeterinaryReport):
            return {
                "center": report.center,
                "stage": "postmarket",
                "layout": self.layout,
                "output_format": self.output_format,
                "ocr_engine": self.ocr.engine,
                "pages": self.ocr.pages,
                "ocr_lines": len(self.ocr.lines),
                "report_id": report.report_id,
                "patient": report.animal.species,
                "event_terms": [sign.term for sign in report.event.signs if sign.term],
                "outcomes": _vet_outcomes(report),
                "products": [report.product.brand_name] if report.product.brand_name else [],
                "device": None,
                "unmapped_lines": 0,
            }
        return {
            "center": report.center,
            "stage": report.stage,
            "layout": self.layout,
            "output_format": self.output_format,
            "ocr_engine": self.ocr.engine,
            "pages": self.ocr.pages,
            "ocr_lines": len(self.ocr.lines),
            "report_id": report.report_id,
            "patient": report.patient.initials or report.patient.identifier,
            "event_terms": report.event.reactions,
            "outcomes": report.event.outcomes,
            "products": [p.name for p in report.products],
            "device": report.device.brand_name,
            "unmapped_lines": len(report.unmapped_lines),
        }


def _vet_outcomes(report: VeterinaryReport) -> List[str]:
    outcome = report.event.outcome
    labels = (
        ("Ongoing", outcome.ongoing),
        ("Recovered/normal", outcome.recovered_normal),
        ("Recovered with sequela", outcome.recovered_with_sequela),
        ("Died", outcome.died),
        ("Euthanised", outcome.euthanised),
        ("Unknown", outcome.unknown),
    )
    return [f"{label}: {count}" for label, count in labels if count]


def default_format(center: str) -> str:
    if center == CENTER_CVM:
        return FORMAT_GL42
    return FORMAT_MDR if center == CENTER_CDRH else FORMAT_E2B


def render_xml(report: Union[MedWatchReport, VeterinaryReport], output_format: Optional[str] = None) -> str:
    """Serialise ``report`` in ``output_format`` (defaults to the center's format)."""
    fmt = output_format or default_format(report.center)
    if isinstance(report, VeterinaryReport):
        if fmt != FORMAT_GL42:
            raise ValueError(f"veterinary reports are only serialised as {FORMAT_GL42}, not {fmt}")
        return gl42.to_xml_string(report)
    if fmt == FORMAT_GL42:
        raise ValueError("gl42 output requires a Form FDA 1932 veterinary report")
    if fmt == FORMAT_MDR:
        return mdr_xml.to_xml_string(report)
    if fmt == FORMAT_E2B:
        receiver = "FDACDRH" if report.center == CENTER_CDRH else "FDACDER"
        return e2b_r2.to_xml_string(report, receiver_id=receiver)
    raise ValueError(f"unknown output format: {fmt}")


def convert_pdf(
    pdf_path: str,
    center: Optional[str] = None,
    stage: Optional[str] = None,
    output_format: Optional[str] = None,
    engine: str = ENGINE_TEXT_LAYER,
    dpi: int = 200,
    lang: str = "en",
    layout: str = LAYOUT_AUTO,
) -> ConversionResult:
    """Read a form PDF and convert it to E2B(R2), MDR or GL42 XML.

    ``layout`` selects the input geometry: ``official`` uses the committed
    FDA-3500A field template (boxed form, checkboxes), ``1932`` the FDA-1932
    veterinary template, ``flat`` the label/value line parser, and ``auto``
    detects which official form was supplied.

    ``engine`` defaults to ``text-layer``: the PDF text layer / AcroForm is read
    directly, which is exact and instant on a fillable form.  ``auto`` runs
    PaddleOCR and falls back to the text layer, ``paddleocr`` forces OCR - both
    are needed for printed or scanned copies, which have no text layer.
    """
    from .form_extract import is_official_form
    from .vet_extract import is_1932_form

    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout: {layout}")
    if layout == LAYOUT_1932 or (layout == LAYOUT_AUTO and center == CENTER_CVM) or (
        layout == LAYOUT_AUTO and is_1932_form(pdf_path)
    ):
        return _convert_1932(pdf_path, output_format, engine, dpi, lang)
    if layout == LAYOUT_OFFICIAL or (layout == LAYOUT_AUTO and is_official_form(pdf_path)):
        return _convert_official(pdf_path, center, stage, output_format, engine, dpi, lang)

    ocr = ocr_pdf(pdf_path, dpi=dpi, lang=lang, engine=engine)
    report = build_report(
        ocr.lines,
        center=center,
        stage=stage,
        ocr_engine=ocr.engine,
        pages=ocr.pages,
    )
    fmt = output_format or default_format(report.center)
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, fmt),
        output_format=fmt,
        layout=LAYOUT_FLAT,
    )


def _convert_official(
    pdf_path: str,
    center: Optional[str],
    stage: Optional[str],
    output_format: Optional[str],
    engine: str,
    dpi: int,
    lang: str,
) -> ConversionResult:
    """Template-guided conversion of the genuine FDA 3500A form."""
    from .form_extract import extract_form, extract_form_fields, map_report

    if engine == "text-layer":
        form = extract_form_fields(pdf_path)
    else:
        try:
            form = extract_form(pdf_path, dpi=dpi, lang=lang)
        except Exception as exc:
            if engine == "paddleocr":
                raise
            form = extract_form_fields(pdf_path)
            form.engine = f"acroform (PaddleOCR unavailable: {exc})"

    report = map_report(form, center=center, stage=stage, ocr_engine=form.engine)
    lines = [f"{key} = {value}" for key, value in sorted(form.values.items())]
    lines += [f"[x] {key}" for key in sorted(form.checks)]
    ocr = OcrResult(lines=lines, pages=form.pages, engine=form.engine)
    fmt = output_format or default_format(report.center)
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, fmt),
        output_format=fmt,
        layout=LAYOUT_OFFICIAL,
    )


def _convert_1932(
    pdf_path: str,
    output_format: Optional[str],
    engine: str,
    dpi: int,
    lang: str,
) -> ConversionResult:
    """Template-guided conversion of the genuine FDA 1932 veterinary form."""
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
    lines = [f"{key} = {value}" for key, value in sorted(form.values.items())]
    lines += [f"[x] {key}" for key in sorted(form.checks)]
    ocr = OcrResult(lines=lines, pages=form.pages, engine=form.engine)
    fmt = output_format or FORMAT_GL42
    return ConversionResult(
        report=report,
        ocr=ocr,
        xml=render_xml(report, fmt),
        output_format=fmt,
        layout=LAYOUT_1932,
    )


def convert_lines(
    lines: List[str],
    center: Optional[str] = None,
    stage: Optional[str] = None,
    output_format: Optional[str] = None,
    ocr_engine: str = "provided-text",
) -> ConversionResult:
    """Convert already-extracted OCR lines (useful for tests and re-runs)."""
    ocr = OcrResult(lines=list(lines), pages=0, engine=ocr_engine)
    report = build_report(lines, center=center, stage=stage, ocr_engine=ocr_engine)
    fmt = output_format or default_format(report.center)
    return ConversionResult(report=report, ocr=ocr, xml=render_xml(report, fmt), output_format=fmt)


CENTERS = (CENTER_CDER, CENTER_CDRH, CENTER_CVM)
