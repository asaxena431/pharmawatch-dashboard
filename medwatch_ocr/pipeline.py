"""End-to-end pipeline: 3500A PDF -> OCR -> structured report -> XML.

The same functions back both the CLI (``python -m medwatch_ocr.cli``) and the
Flask demo GUI (``medwatch_ocr.web``).
"""

from dataclasses import dataclass
from typing import List, Optional

from . import e2b_r2, mdr_xml
from .models import CENTER_CDER, CENTER_CDRH, MedWatchReport
from .ocr import OcrResult, ocr_pdf
from .parser import build_report

FORMAT_E2B = "e2b-r2"
FORMAT_MDR = "mdr"


@dataclass
class ConversionResult:
    report: MedWatchReport
    ocr: OcrResult
    xml: str
    output_format: str

    @property
    def summary(self) -> dict:
        report = self.report
        return {
            "center": report.center,
            "stage": report.stage,
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


def default_format(center: str) -> str:
    return FORMAT_MDR if center == CENTER_CDRH else FORMAT_E2B


def render_xml(report: MedWatchReport, output_format: Optional[str] = None) -> str:
    """Serialise ``report`` in ``output_format`` (defaults to the center's format)."""
    fmt = output_format or default_format(report.center)
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
    engine: str = "auto",
    dpi: int = 200,
    lang: str = "en",
) -> ConversionResult:
    """OCR a 3500A PDF and convert it to E2B(R2) or MDR XML."""
    ocr = ocr_pdf(pdf_path, dpi=dpi, lang=lang, engine=engine)
    report = build_report(
        ocr.lines,
        center=center,
        stage=stage,
        ocr_engine=ocr.engine,
        pages=ocr.pages,
    )
    fmt = output_format or default_format(report.center)
    return ConversionResult(report=report, ocr=ocr, xml=render_xml(report, fmt), output_format=fmt)


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


CENTERS = (CENTER_CDER, CENTER_CDRH)
