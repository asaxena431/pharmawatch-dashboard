"""Fill the genuine FDA Form 3500A (MedWatch mandatory reporting) PDF.

The blank fillable form published by FDA (``Form FDA-3500A MedWatch (09/2025)``,
OMB No. 0910-0291) is downloaded once and cached.  Sample reports are produced by
writing values into its AcroForm fields, so the OCR input is the real boxed form
- checkboxes, grid cells and all - rather than a facsimile.

Two samples are provided:

* ``cder_premarket``  - drug/biologic IND safety report (CDER) -> E2B(R2)
* ``cdrh_postmarket`` - manufacturer medical device report (CDRH) -> MDR XML
"""

from __future__ import annotations

import os
import urllib.request
from typing import Dict, List

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

FORM_URL = "https://www.fda.gov/media/69876/download?attachment"
FORM_FILENAME = "form_fda_3500a_blank.pdf"
CHECKED = "/1"

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "medwatch_ocr")

CDER_PREMARKET: Dict[str, object] = {
    "center": "CDER",
    "stage": "premarket",
    "text": {
        # A. Patient information / B. Adverse event  (page 1)
        "p1.mfr": "US-VTX-2025-004871",
        "p1.patID": "SUBJ-2210-0148",
        "p1.patAge": "63",
        "p1.patDOB": "11-Mar-1962",
        "p1.patWeight": "78.5",
        "p1.dateAdvEvent": "04-Nov-2025",
        "p1.dateReport": "07-Nov-2025",
        "p1.advEvDesc": (
            "On study day 26 the subject presented to the emergency department with progressive "
            "jaundice, right upper quadrant pain and confusion. Serum ALT was 1840 U/L and total "
            "bilirubin 6.2 mg/dL. Investigational product was permanently discontinued on "
            "04-Nov-2025 and the subject was admitted for supportive care. Liver enzymes trended "
            "down after withdrawal and the subject was discharged on 12-Nov-2025. The investigator "
            "assessed the event as probably related to the investigational product."
        ),
        # C. Relevant tests / history  (page 3)
        "p3.testData1": "ALT 1840 U/L",
        "p3.testDDate1": "04-Nov-2025",
        "p3.testData2": "AST 1102 U/L",
        "p3.testDDate2": "04-Nov-2025",
        "p3.testData3": "Total bilirubin 6.2 mg/dL",
        "p3.testDDate3": "04-Nov-2025",
        "p3.testData4": "INR 1.8",
        "p3.testDDate4": "04-Nov-2025",
        "p3.otherHist": "Type 2 diabetes mellitus; hypertension; former smoker; no known alcohol use.",
        "p3.addComm": (
            "Hepatitis A/B/C serology negative. Abdominal ultrasound without biliary obstruction. "
            "Event terms: Acute hepatic failure; Jaundice; Elevated ALT."
        ),
        # C. Suspect product  (page 4)
        "p4.prodName1": "HEPAXOLIB (investigational) [hepaxolib mesylate]",
        "p4.prodStr1": "200",
        "p4.ndc1": "IND 128944",
        "p4.manu1": "Vantera Therapeutics Inc.",
        "p4.lot1": "HPX-2025-A17",
        "p4.dose1": "200",
        "p4.start1Date": "10-Oct-2025",
        "p4.end1Date": "04-Nov-2025",
        "p4.duration1": "26",
        "p4.diagnosis1": "Metastatic colorectal carcinoma",
        "p4.expDate1": "31-Aug-2026",
        # E. Initial reporter  (page 7)
        "p7.reportFirst": "Alicia",
        "p7.reportLast": "Grant",
        "p7.reportAddr": "1400 Research Park Drive",
        "p7.reportCity": "Columbus",
        "p7.reportSt": "OH",
        "p7.reportZip": "43210",
        "p7.reportPhone": "+1-614-555-0142",
        "p7.reportEmail": "agrant@northsidecru.example",
        # G. All manufacturers  (page 8)
        "p8.manuName": "Vantera Therapeutics Inc.",
        "p8.manuAddr": "550 Harbor Boulevard, Cambridge, MA 02142, US",
        "p8.manuPhone": "+1-617-555-0199",
        "p8.manuEmail": "drugsafety@vantera.example",
        "p8.manuRepNum": "US-VTX-2025-004871",
        "p8.reportManuRecDate": "07-Nov-2025",
        "p8.numIND": "128944",
        "p8.protNum": "VTX-HEPA-301",
        "p8.advTerms": "Acute hepatic failure; Jaundice; Elevated ALT",
    },
    "choices": {
        "p4.prodUnit1": "MILLIGRAM(S) - MG",
        "p4.doseUnit1": "MILLIGRAM(S) - MG",
        "p4.freq1": "Daily",
        "p4.route1": "Oral",
        "p4.durUnit1": "Day(s)",
        "p7.repOccupation": "Physician",
        "p7.reportCountry": "UNITED STATES",
    },
    "checks": [
        "p1.ageYrs",
        "p1.sexM",
        "p1.weightKG",
        "p1.white",
        "p1.adverse",
        "p1.hospital",
        "p1.lifeThr",
        "p4.abate1Yes",
        "p4.reappear1NA",
        "p7.repHPY",
        "p7.reportFDAN",
        "p8.rptsrcStu",
        "p8.reptInitial",
        "p8.rep15",
    ],
}

CDRH_POSTMARKET: Dict[str, object] = {
    "center": "CDRH",
    "stage": "postmarket",
    "text": {
        # A. Patient information / B. Adverse event  (page 1)
        "p1.mfr": "2183456-2026-00037",
        "p1.patID": "MDR-PT-778201",
        "p1.patAge": "71",
        "p1.patDOB": "22-Jun-1954",
        "p1.patWeight": "64.0",
        "p1.dateAdvEvent": "19-Jan-2026",
        "p1.dateReport": "23-Jan-2026",
        "p1.advEvDesc": (
            "During a routine pacemaker interrogation the implanted pulse generator was found to be "
            "in back-up pacing mode after an unexpected battery voltage drop. The patient reported "
            "two syncopal episodes in the preceding week. Telemetry confirmed intermittent loss of "
            "ventricular pacing output. The device was explanted on 21-Jan-2026 and replaced with a "
            "new pulse generator without further complication. The explanted device was returned to "
            "the manufacturer for analysis."
        ),
        # C. Relevant tests / history  (page 3)
        "p3.testData1": "Device interrogation battery voltage 2.41 V",
        "p3.testDDate1": "19-Jan-2026",
        "p3.testData2": "12-lead ECG sinus bradycardia 38 bpm",
        "p3.testDDate2": "19-Jan-2026",
        "p3.testData3": "Chest radiograph intact lead position",
        "p3.testDDate3": "19-Jan-2026",
        "p3.otherHist": "Complete heart block; hypertension; chronic kidney disease stage 3.",
        "p3.addComm": (
            "Elective replacement indicator triggered. Event terms: Device malfunction; "
            "Bradycardia; Syncope."
        ),
        # D. Suspect medical device  (page 6)
        "p6.brandName": "CARDIOSTEP XR2 Implantable Pulse Generator",
        "p6.commonName": "Pacemaker, implantable",
        "p6.proCode": "LWP",
        "p6.manuNameAddr": "Meridian Cardiac Systems LLC, 2200 Innovation Way, Minneapolis, MN 55401, US",
        "p6.modelNum": "XR2-3120",
        "p6.catNum": "CS-XR2-3120-US",
        "p6.serNum": "SN-4471902",
        "p6.lotNum": "LOT-2023-0914",
        "p6.expDate": "30-Sep-2028",
        "p6.udi": "(01)00812345600012(17)280930(21)SN-4471902",
        "p6.cProdName1": "metoprolol 25 mg oral BID",
        "p6.cProdName2": "apixaban 5 mg oral BID",
        # D. Device use / E. Initial reporter  (page 7)
        "p7.implantDate": "02-Nov-2023",
        "p7.explantDate": "21-Jan-2026",
        "p7.returnDate": "26-Jan-2026",
        "p7.reportFirst": "Daniel",
        "p7.reportLast": "Ortega",
        "p7.reportAddr": "980 Lakeshore Avenue",
        "p7.reportCity": "Chicago",
        "p7.reportSt": "IL",
        "p7.reportZip": "60611",
        "p7.reportPhone": "+1-312-555-0177",
        "p7.reportEmail": "dortega@lakeshoremc.example",
        # G. All manufacturers  (page 8)
        "p8.manuName": "Meridian Cardiac Systems LLC",
        "p8.manuAddr": "2200 Innovation Way, Minneapolis, MN 55401, US",
        "p8.manuPhone": "+1-612-555-0104",
        "p8.manuEmail": "vigilance@meridiancardiac.example",
        "p8.manuRepNum": "2183456-2026-00037",
        "p8.reportManuRecDate": "23-Jan-2026",
        "p8.userAwareDate": "23-Jan-2026",
        "p8.numPMA": "P980021/S045",
        "p8.ageYears": "2",
        "p8.devProbCode": "1570",
        "p8.advTerms": "Device malfunction; Bradycardia; Syncope",
        # H. Device manufacturers only  (page 9)
        "p9.devDate": "26-Jan-2026",
        # Boxes 6 findings/conclusions are single-line code fields on the paper form.
        "p9.invFindings": "Battery depletion - feedthrough leak",
        "p9.invConc": "Analysis in progress; CAPA open",
        "p9.addNarr": (
            "Preliminary review suggests premature battery depletion caused by an internal "
            "feedthrough leak; pacing output circuitry intact. Device explanted and replaced; "
            "field analysis initiated. Manufacturer received the report from the treating "
            "facility on 23-Jan-2026."
        ),
    },
    "choices": {
        "p7.repOccupation": "Physician",
        "p7.reportCountry": "UNITED STATES",
    },
    "checks": [
        "p1.ageYrs",
        "p1.sexF",
        "p1.weightKG",
        "p1.adverse",
        "p1.prodProblem",
        "p1.hospital",
        "p1.reqInterv",
        "p7.devOpHP",
        "p7.singleUseN",
        "p7.evalRet",
        "p7.repHPY",
        "p7.reportFDAN",
        "p8.repsrcUF",
        "p8.reptInitial",
        "p8.rep30",
        "p8.deviceAgeYears",
        "p8.locHosp",
        "p9.eventMal",
        "p9.eventSInj",
        "p9.evalManuY",
        "p9.remReplace",
        "p9.useInit",
    ],
}

OFFICIAL_SAMPLES: Dict[str, Dict[str, object]] = {
    "cder_premarket": CDER_PREMARKET,
    "cdrh_postmarket": CDRH_POSTMARKET,
}


def blank_form_path(cache_dir: str | None = None, download: bool = True) -> str:
    """Return the local path of the blank FDA 3500A form, downloading it if needed."""
    directory = cache_dir or os.environ.get("MEDWATCH_FORM_CACHE") or DEFAULT_CACHE_DIR
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, FORM_FILENAME)
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    if not download:
        raise FileNotFoundError(f"blank FDA 3500A form not cached at {path}")
    request = urllib.request.Request(FORM_URL, headers={"User-Agent": "medwatch-ocr/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, open(path, "wb") as handle:
        handle.write(response.read())
    return path


def _on_state(reader: PdfReader, page_index: int, field_name: str) -> str:
    """Return the checkbox 'on' state (e.g. ``/1`` or ``/Yes``) for ``field_name``."""
    page = reader.pages[page_index]
    for annotation in page.get("/Annots") or []:
        widget = annotation.get_object()
        parent = widget.get("/Parent")
        name = widget.get("/T") or (parent.get("/T") if parent else None)
        if name != field_name:
            continue
        appearances = widget.get("/AP")
        if appearances and "/N" in appearances:
            for state in appearances["/N"].keys():
                if state != "/Off":
                    return state
    return CHECKED


def _page_number(key: str) -> int:
    page, _, _ = key.partition(".")
    return int(page.lstrip("p"))


def _field_name(key: str) -> str:
    _, _, name = key.partition(".")
    return f"{name}[0]"


def fill_official_form(spec: Dict[str, object], output_path: str, blank_path: str | None = None) -> str:
    """Write ``spec`` into the official 3500A AcroForm and save it to ``output_path``."""
    reader = PdfReader(blank_path or blank_form_path())
    writer = PdfWriter(clone_from=reader)

    acroform = writer._root_object["/AcroForm"]
    if "/XFA" in acroform:  # keep viewers on the AcroForm rendering path
        del acroform[NameObject("/XFA")]
    acroform[NameObject("/NeedAppearances")] = BooleanObject(True)

    by_page: Dict[int, Dict[str, str]] = {}
    for source in ("text", "choices"):
        for key, value in dict(spec.get(source, {})).items():  # type: ignore[arg-type]
            by_page.setdefault(_page_number(key), {})[_field_name(key)] = value
    checks_by_page: Dict[int, Dict[str, str]] = {}
    for key in list(spec.get("checks", [])):  # type: ignore[arg-type]
        page_number = _page_number(key)
        field = _field_name(key)
        state = _on_state(reader, page_number - 1, field)
        checks_by_page.setdefault(page_number, {})[field] = state

    for page_number, values in by_page.items():
        writer.update_page_form_field_values(writer.pages[page_number - 1], values, auto_regenerate=True)
    for page_number, values in checks_by_page.items():
        writer.update_page_form_field_values(writer.pages[page_number - 1], values)

    directory = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(directory, exist_ok=True)
    with open(output_path, "wb") as handle:
        writer.write(handle)
    return output_path


def generate_official_samples(output_dir: str, blank_path: str | None = None) -> Dict[str, str]:
    """Write both filled official 3500A PDFs into ``output_dir``."""
    os.makedirs(output_dir, exist_ok=True)
    blank = blank_path or blank_form_path()
    written: Dict[str, str] = {}
    for name, spec in OFFICIAL_SAMPLES.items():
        path = os.path.join(output_dir, f"FDA-3500A_{name}.pdf")
        fill_official_form(spec, path, blank_path=blank)
        written[name] = path
    return written


def expected_values(name: str) -> Dict[str, str]:
    """Flat ``field key -> value`` view of a sample, used as OCR ground truth in tests."""
    spec = OFFICIAL_SAMPLES[name]
    values: Dict[str, str] = {}
    values.update(dict(spec.get("text", {})))  # type: ignore[arg-type]
    values.update(dict(spec.get("choices", {})))  # type: ignore[arg-type]
    return values


def expected_checks(name: str) -> List[str]:
    return list(OFFICIAL_SAMPLES[name].get("checks", []))  # type: ignore[arg-type]
