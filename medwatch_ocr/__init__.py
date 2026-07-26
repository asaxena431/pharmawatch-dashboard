"""PaddleOCR-based FDA 3500A (MedWatch) reader with E2B(R2) and FDA MDR output."""

from .models import (
    CENTER_CDER,
    CENTER_CDRH,
    STAGE_POSTMARKET,
    STAGE_PREMARKET,
    MedWatchReport,
)
from .pipeline import FORMAT_E2B, FORMAT_MDR, convert_lines, convert_pdf, render_xml
from .samples import generate_samples

__all__ = [
    "CENTER_CDER",
    "CENTER_CDRH",
    "STAGE_PREMARKET",
    "STAGE_POSTMARKET",
    "MedWatchReport",
    "FORMAT_E2B",
    "FORMAT_MDR",
    "convert_pdf",
    "convert_lines",
    "render_xml",
    "generate_samples",
]
