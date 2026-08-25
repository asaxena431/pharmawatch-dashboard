"""FDA adverse-event form reader: 3500A -> E2B(R2)/MDR, FDA 1932 -> VICH GL42."""

from .form_1932 import generate_1932_samples
from .models import (
    CENTER_CDER,
    CENTER_CDRH,
    CENTER_CVM,
    STAGE_POSTMARKET,
    STAGE_PREMARKET,
    MedWatchReport,
    VeterinaryReport,
)
from .official_form import generate_official_samples
from .pipeline import (
    ENGINE_AUTO,
    ENGINE_PADDLEOCR,
    ENGINE_TEXT_LAYER,
    FORMAT_E2B,
    FORMAT_E2B_FDA,
    FORMAT_GL42,
    FORMAT_MDR,
    LAYOUT_1932,
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
    "CENTER_CVM",
    "STAGE_PREMARKET",
    "STAGE_POSTMARKET",
    "MedWatchReport",
    "VeterinaryReport",
    "ENGINE_TEXT_LAYER",
    "ENGINE_AUTO",
    "ENGINE_PADDLEOCR",
    "FORMAT_E2B",
    "FORMAT_E2B_FDA",
    "FORMAT_MDR",
    "FORMAT_GL42",
    "LAYOUT_OFFICIAL",
    "LAYOUT_1932",
    "LAYOUT_FLAT",
    "convert_pdf",
    "convert_lines",
    "render_xml",
    "generate_samples",
    "generate_official_samples",
    "generate_1932_samples",
]
