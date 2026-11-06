"""Generate filled-in sample FDA Form 3500A (MedWatch) PDFs.

Two samples are produced:

* ``cder_premarket``  - a drug/biologic report from an investigational study
  (IND safety report, CDER, premarket) that is converted to ICH E2B(R2).
* ``cdrh_postmarket`` - a medical-device report from a manufacturer
  (CDRH, postmarket MDR) that is converted to FDA MDR XML.

The layout mirrors the blocks of the paper form: every data item is rendered
as ``<item number> <Label>: <value>`` so that the OCR output of a scanned or
faxed copy remains parseable field-by-field.
"""

from typing import Dict, List, Sequence, Tuple

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

Section = Tuple[str, Sequence[Tuple[str, str]]]

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch
LINE_H = 13
LABEL_FONT = ("Helvetica-Bold", 8)
VALUE_FONT = ("Helvetica", 8.5)


CDER_PREMARKET: Dict[str, object] = {
    "header": "FDA Form 3500A - MedWatch Mandatory Reporting",
    "subheader": "Drug / Biologic - CDER - Premarket (IND Safety Report)",
    "sections": [
        (
            "A. PATIENT INFORMATION",
            [
                ("A.1 Patient Identifier", "SUBJ-2210-0148"),
                ("A.1a Patient Initials", "R.K.M."),
                ("A.2 Age At Time Of Event", "63 Year"),
                ("A.2a Date Of Birth", "1962-03-11"),
                ("A.3 Sex", "Male"),
                ("A.4 Weight", "78.5 kg"),
                ("A.5 Ethnicity", "Not Hispanic or Latino"),
            ],
        ),
        (
            "B. ADVERSE EVENT, PRODUCT PROBLEM OR ERROR",
            [
                ("B.1 Event Type", "Adverse Event"),
                ("B.2 Outcome Attributed To Adverse Event", "Hospitalization; Life-threatening"),
                ("B.3 Date Of Event", "2025-11-04"),
                ("B.4 Date Of This Report", "2025-11-07"),
                ("B.5 Adverse Event Term", "Acute hepatic failure; Jaundice; Elevated ALT"),
                (
                    "B.5 Describe Event Problem Or Product Use Error",
                    "On study day 26 the subject presented to the emergency department with "
                    "progressive jaundice, right upper quadrant pain and confusion. Serum ALT "
                    "was 1840 U/L and total bilirubin 6.2 mg/dL. Investigational product was "
                    "permanently discontinued on 2025-11-04 and the subject was admitted for "
                    "supportive care. Liver enzymes trended down after withdrawal and the "
                    "subject was discharged on 2025-11-12. The investigator assessed the event "
                    "as probably related to the investigational product.",
                ),
                (
                    "B.6 Relevant Tests Laboratory Data",
                    "ALT 1840 U/L (2025-11-04); AST 1102 U/L (2025-11-04); "
                    "Total bilirubin 6.2 mg/dL; INR 1.8; Hepatitis A/B/C serology negative; "
                    "Abdominal ultrasound without biliary obstruction.",
                ),
                (
                    "B.7 Other Relevant History",
                    "Type 2 diabetes mellitus; hypertension; former smoker; no known alcohol use.",
                ),
            ],
        ),
        (
            "C. SUSPECT PRODUCTS",
            [
                ("C.1 Suspect Product Name", "HEPAXOLIB (investigational)"),
                ("C.1a Active Substance", "hepaxolib mesylate"),
                ("C.2 Dose", "200 mg"),
                ("C.2a Frequency", "Once daily"),
                ("C.2b Route Used", "Oral"),
                ("C.3 Therapy Start Date", "2025-10-10"),
                ("C.3a Therapy Stop Date", "2025-11-04"),
                ("C.4 Diagnosis For Use Indication", "Metastatic colorectal carcinoma"),
                ("C.5 Event Abated After Use Stopped", "Yes"),
                ("C.6 Event Reappeared After Reintroduction", "Not applicable - not reintroduced"),
                ("C.7 NDC Number Or Unique ID", "IND 128944"),
                ("C.8 Lot Number", "HPX-2025-A17"),
                ("C.9 Expiration Date", "2026-08-31"),
                ("C.10 Concomitant Medical Products", "metformin 1000 mg oral BID; lisinopril 10 mg oral daily"),
            ],
        ),
        (
            "E. INITIAL REPORTER",
            [
                ("E.1 Reporter Name", "Alicia Grant"),
                ("E.1a Reporter Organization", "Northside Clinical Research Unit"),
                ("E.2 Address", "1400 Research Park Drive, Columbus, OH 43210, US"),
                ("E.3 Phone Number", "+1-614-555-0142"),
                ("E.3a Email", "agrant@northsidecru.example"),
                ("E.4 Health Professional", "Yes"),
                ("E.5 Occupation", "Physician"),
                ("E.6 Initial Reporter Also Sent Report To FDA", "No"),
            ],
        ),
        (
            "G. ALL MANUFACTURERS",
            [
                ("G.1 Manufacturer Name", "Vantera Therapeutics Inc."),
                ("G.1a Contact Office", "Global Drug Safety and Pharmacovigilance"),
                ("G.1b Address", "550 Harbor Boulevard, Cambridge, MA 02142, US"),
                ("G.1c Phone Number", "+1-617-555-0199"),
                ("G.2 Report Source", "Study - clinical trial"),
                ("G.3 Date Received By Manufacturer", "2025-11-07"),
                ("G.4 Manufacturer Report Number", "US-VANTERA-2025-004871"),
                ("G.5 Report Type", "Initial"),
                ("G.6 Adverse Event Type", "Serious injury - life threatening"),
                ("G.7 IND Or NDA Number", "IND 128944"),
                ("G.8 Study Name", "VTX-HEPA-301 Phase 3 Study Of Hepaxolib In mCRC"),
                ("G.9 Sponsor Study Number", "VTX-HEPA-301"),
            ],
        ),
    ],
}


CDRH_POSTMARKET: Dict[str, object] = {
    "header": "FDA Form 3500A - MedWatch Mandatory Reporting",
    "subheader": "Medical Device - CDRH - Postmarket (Medical Device Report)",
    "sections": [
        (
            "A. PATIENT INFORMATION",
            [
                ("A.1 Patient Identifier", "MDR-PT-778201"),
                ("A.1a Patient Initials", "T.J.L."),
                ("A.2 Age At Time Of Event", "71 Year"),
                ("A.2a Date Of Birth", "1954-06-22"),
                ("A.3 Sex", "Female"),
                ("A.4 Weight", "64.0 kg"),
            ],
        ),
        (
            "B. ADVERSE EVENT, PRODUCT PROBLEM OR ERROR",
            [
                ("B.1 Event Type", "Adverse Event and Product Problem"),
                ("B.2 Outcome Attributed To Adverse Event", "Required Intervention; Hospitalization"),
                ("B.3 Date Of Event", "2026-01-19"),
                ("B.4 Date Of This Report", "2026-01-23"),
                ("B.5 Adverse Event Term", "Device malfunction; Bradycardia; Syncope"),
                (
                    "B.5 Describe Event Problem Or Product Use Error",
                    "During a routine pacemaker interrogation the implanted pulse generator was "
                    "found to be in back-up pacing mode after an unexpected battery voltage drop. "
                    "The patient reported two syncopal episodes in the preceding week. Telemetry "
                    "confirmed intermittent loss of ventricular pacing output. The device was "
                    "explanted on 2026-01-21 and replaced with a new pulse generator without "
                    "further complication. The explanted device was returned to the manufacturer "
                    "for analysis.",
                ),
                (
                    "B.6 Relevant Tests Laboratory Data",
                    "Device interrogation: battery voltage 2.41 V, elective replacement indicator "
                    "triggered; 12-lead ECG showed sinus bradycardia at 38 bpm; chest radiograph "
                    "showed intact lead position.",
                ),
                (
                    "B.7 Other Relevant History",
                    "Complete heart block; hypertension; chronic kidney disease stage 3.",
                ),
            ],
        ),
        (
            "D. SUSPECT MEDICAL DEVICE",
            [
                ("D.1 Brand Name", "CARDIOSTEP XR2 Implantable Pulse Generator"),
                ("D.2 Common Device Name", "Pacemaker, permanent, implantable"),
                ("D.2a Product Code", "LWP"),
                ("D.3 Manufacturer Name", "Meridian Cardiac Systems LLC"),
                ("D.3a Manufacturer Address", "2200 Innovation Way, Minneapolis, MN 55401, US"),
                ("D.4 Model Number", "XR2-3120"),
                ("D.4a Catalog Number", "CS-XR2-3120-US"),
                ("D.4b Serial Number", "SN-4471902"),
                ("D.4c Lot Number", "LOT-2023-0914"),
                ("D.4d Unique Device Identifier", "(01)00812345600012(17)280930(21)SN-4471902"),
                ("D.4e Expiration Date", "2028-09-30"),
                ("D.4f Other Identifying Number", "UDI-DI 00812345600012"),
                ("D.5 Operator Of Device", "Health Professional"),
                ("D.6 Implant Date", "2023-11-02"),
                ("D.6a Explant Date", "2026-01-21"),
                ("D.7 Single Use Device Reprocessed", "No"),
                ("D.8 Device Available For Evaluation", "Returned to manufacturer"),
                ("D.8a Date Device Returned To Manufacturer", "2026-01-26"),
                ("D.9 Concomitant Medical Products", "metoprolol 25 mg oral BID; apixaban 5 mg oral BID"),
            ],
        ),
        (
            "E. INITIAL REPORTER",
            [
                ("E.1 Reporter Name", "Daniel Ortega"),
                ("E.1a Reporter Organization", "Lakeshore Regional Medical Center"),
                ("E.2 Address", "980 Lakeshore Avenue, Chicago, IL 60611, US"),
                ("E.3 Phone Number", "+1-312-555-0177"),
                ("E.3a Email", "dortega@lakeshoremc.example"),
                ("E.4 Health Professional", "Yes"),
                ("E.5 Occupation", "Cardiac Electrophysiologist"),
                ("E.6 Initial Reporter Also Sent Report To FDA", "No"),
            ],
        ),
        (
            "G. ALL MANUFACTURERS",
            [
                ("G.1 Manufacturer Name", "Meridian Cardiac Systems LLC"),
                ("G.1a Contact Office", "Postmarket Surveillance and Vigilance"),
                ("G.1b Address", "2200 Innovation Way, Minneapolis, MN 55401, US"),
                ("G.1c Phone Number", "+1-612-555-0104"),
                ("G.1d Manufacturer Registration Number", "2183456"),
                ("G.2 Report Source", "User facility"),
                ("G.3 Date Received By Manufacturer", "2026-01-23"),
                ("G.4 Manufacturer Report Number", "2183456-2026-00037"),
                ("G.5 Report Type", "Initial"),
                ("G.6 Adverse Event Type", "Serious injury and malfunction"),
                ("G.7 PMA Or 510k Number", "P980021/S045"),
                ("G.8 Remedial Action Taken", "Device explanted and replaced; field analysis initiated"),
                (
                    "H. Device Evaluation Conclusion",
                    "Analysis in progress. Preliminary review suggests premature battery "
                    "depletion caused by an internal feedthrough leak; corrective action "
                    "evaluation is open.",
                ),
            ],
        ),
    ],
}

SAMPLES: Dict[str, Dict[str, object]] = {
    "cder_premarket": CDER_PREMARKET,
    "cdrh_postmarket": CDRH_POSTMARKET,
}


def _wrap(text: str, width_chars: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _new_page(pdf: canvas.Canvas, spec: Dict[str, object], page_no: int) -> float:
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(MARGIN, PAGE_H - MARGIN, str(spec["header"]))
    pdf.setFont("Helvetica", 9)
    pdf.drawString(MARGIN, PAGE_H - MARGIN - 14, str(spec["subheader"]))
    pdf.setFont("Helvetica", 7)
    pdf.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN, f"Page {page_no}")
    pdf.line(MARGIN, PAGE_H - MARGIN - 20, PAGE_W - MARGIN, PAGE_H - MARGIN - 20)
    return PAGE_H - MARGIN - 40


def render_sample(spec: Dict[str, object], output_path: str) -> str:
    """Render one sample specification to a PDF at ``output_path``."""
    pdf = canvas.Canvas(output_path, pagesize=letter)
    page_no = 1
    y = _new_page(pdf, spec, page_no)

    for title, items in spec["sections"]:  # type: ignore[union-attr]
        if y < MARGIN + 4 * LINE_H:
            pdf.showPage()
            page_no += 1
            y = _new_page(pdf, spec, page_no)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(MARGIN, y, title)
        y -= LINE_H + 2

        for label, value in items:
            text = f"{label}: {value}"
            for idx, line in enumerate(_wrap(text, 104)):
                if y < MARGIN + 2 * LINE_H:
                    pdf.showPage()
                    page_no += 1
                    y = _new_page(pdf, spec, page_no)
                pdf.setFont(*(LABEL_FONT if idx == 0 else VALUE_FONT))
                pdf.drawString(MARGIN + (0 if idx == 0 else 12), y, line)
                y -= LINE_H
            y -= 2
        y -= 4

    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(MARGIN, MARGIN - 12, "Synthetic sample data for software testing - not a real patient report.")
    pdf.save()
    return output_path


def generate_samples(output_dir: str) -> Dict[str, str]:
    """Write both sample PDFs into ``output_dir`` and return their paths."""
    import os

    os.makedirs(output_dir, exist_ok=True)
    written: Dict[str, str] = {}
    for name, spec in SAMPLES.items():
        path = os.path.join(output_dir, f"3500A_{name}.pdf")
        render_sample(spec, path)
        written[name] = path
    return written
