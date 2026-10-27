"""Flattened (printed-to-PDF) 3500A: text-layer geometry extraction and the FDA E2B profile."""

import os
import xml.etree.ElementTree as ET

from reportlab.pdfgen import canvas

from medwatch_ocr.form_extract import extract_form_text_layer, load_template
from medwatch_ocr.pipeline import FORMAT_E2B_FDA, LAYOUT_OFFICIAL, convert_pdf
from medwatch_ocr.xml_diff import diff_xml

# A flattened copy of the form has no AcroForm values left; the typed text sits at
# the same coordinates as the widget that used to hold it.
FLAT_VALUES = {
    "p0.patID": "SUBJ-2210-0148",
    "p0.patAge": "63",
    "p0.patWeight": "81",
    "p0.dateAdvEvent": "18-Jul-2026",
    "p0.dateReport": "23-Jul-2026",
    "p0.advEvDesc": "Acute hepatic failure after the second infusion.",
    "p3.prodName1": "HEPAXOLIB",
    "p3.dose1": "200",
    "p3.diagnosis1": "Metastatic melanoma",
    "p3.start1Date": "07-Jul-2026",
    "p7.manuRepNum": "US-VANTERA-2025-004871",
    "p7.numIND": "133201",
    "p7.protNum": "VTX-HEPA-301",
    "p7.advTerms": "Acute hepatic failure",
    "p7.manuName": "Vantera Therapeutics",
}


def _flattened_form(path: str) -> str:
    """Draw ``FLAT_VALUES`` onto a blank copy of the official form's geometry."""
    template = load_template()
    rects = {f"p{spec['page']}.{spec['name']}": (spec["page"], spec["rect"]) for spec in template["fields"]}
    pages = len(template["pages"])
    page_size = (template["pages"]["0"]["width"], template["pages"]["0"]["height"])

    pdf = canvas.Canvas(path, pagesize=page_size)
    for page_index in range(pages):
        pdf.setFont("Helvetica", 8)
        for key, value in FLAT_VALUES.items():
            page, rect = rects[key]
            if page != page_index:
                continue
            pdf.drawString(rect[0] + 1, rect[1] + 2, value)
        pdf.showPage()
    pdf.save()
    return path


def test_text_layer_geometry_reads_a_flattened_form(tmp_path):
    pdf_path = _flattened_form(os.path.join(str(tmp_path), "flat_3500a.pdf"))
    form = extract_form_text_layer(pdf_path, dpi=150)

    assert form.engine == "text-layer"
    assert form.pages == 9
    for key, value in FLAT_VALUES.items():
        assert form.get(key) == value, key


def test_flattened_form_converts_to_the_fda_e2b_profile(tmp_path):
    pdf_path = _flattened_form(os.path.join(str(tmp_path), "flat_3500a.pdf"))
    result = convert_pdf(pdf_path, center="CDER", output_format=FORMAT_E2B_FDA, layout=LAYOUT_OFFICIAL, dpi=150)

    assert result.layout == LAYOUT_OFFICIAL
    assert result.ocr.engine == "text-layer"
    root = ET.fromstring(result.xml)
    assert root.findtext(".//safetyreportid") == "US-VANTERA-2025-004871-IND"
    assert root.findtext(".//formtype") == "3500A"
    assert root.findtext(".//contactmethod") == "OCR-3500A"
    assert root.findtext(".//patientinitial") == "SUBJ-2210-0148"
    assert root.findtext(".//patientonsetage") == "63"
    assert root.findtext(".//patientweight") == "81.0"
    assert root.findtext(".//primarysourcereaction") == "Acute hepatic failure"
    assert root.findtext(".//reactionstartdate") == "20260718"
    assert root.findtext(".//medicinalproduct") == "HEPAXOLIB"
    assert root.findtext(".//drugindication") == "Metastatic melanoma"
    assert root.findtext(".//manufacturerind") == "133201"
    assert root.findtext(".//manufacturerprotocolnumber") == "VTX-HEPA-301"
    # Every element of the profile is always present, empty when the box is blank.
    assert root.findtext(".//occurcountry") == ""


def test_xml_diff_ignores_volatile_elements_and_reports_differences():
    generated = """
    <ichicsr>
      <ichicsrmessageheader><messagenumb>A</messagenumb><messagedate>1</messagedate></ichicsrmessageheader>
      <safetyreport><safetyreportid>ID-1</safetyreportid><occurcountry/>
        <patient><reaction><primarysourcereaction>Rash</primarysourcereaction></reaction>
        <reaction><primarysourcereaction>Fever</primarysourcereaction></reaction></patient>
      </safetyreport>
    </ichicsr>
    """
    expected = """
    <ichicsr>
      <ichicsrmessageheader><messagenumb>B</messagenumb><messagedate>2</messagedate></ichicsrmessageheader>
      <safetyreport><safetyreportid>ID-2</safetyreportid><occurcountry/>
        <patient><reaction><primarysourcereaction>Rash</primarysourcereaction></reaction>
        <reaction><primarysourcereaction>Chills</primarysourcereaction></reaction>
        <summary><narrativeincludeclinical>Case</narrativeincludeclinical></summary></patient>
      </safetyreport>
    </ichicsr>
    """
    diff = diff_xml(generated, expected)

    assert [entry["path"] for entry in diff.different] == [
        "safetyreport/safetyreportid",
        "safetyreport/patient/reaction[2]/primarysourcereaction",
    ]
    assert [entry["path"] for entry in diff.missing] == ["safetyreport/patient/summary/narrativeincludeclinical"]
    assert diff.extra == []
    assert "safetyreport/occurcountry" not in [entry["path"] for entry in diff.different]
    assert diff.matched == ["safetyreport/occurcountry", "safetyreport/patient/reaction[1]/primarysourcereaction"]
