"""PaddleOCR-based FDA 3500A (MedWatch) reader with E2B(R2) and FDA MDR output."""

from .models import (
    CENTER_CDER,
    CENTER_CDRH,
    STAGE_POSTMARKET,
    STAGE_PREMARKET,
    MedWatchReport,
)
from .official_form import generate_official_samples
from .pipeline import (
    FORMAT_E2B,
    FORMAT_MDR,
    LAYOUT_FLAT,
    LAYOUT_OFFICIAL,
    convert_lines,
    convert_pdf,
    render_xml,
)
from .samples import generate_samples

__all__ = [
    "CENTER_CDER",
    "CENTER_CDRH",
    "STAGE_PREMARKET",
    "STAGE_POSTMARKET",
    "MedWatchReport",
    "FORMAT_E2B",
    "FORMAT_MDR",
    "LAYOUT_OFFICIAL",
    "LAYOUT_FLAT",
    "convert_pdf",
    "convert_lines",
    "render_xml",
    "generate_samples",
    "generate_official_samples",
]
