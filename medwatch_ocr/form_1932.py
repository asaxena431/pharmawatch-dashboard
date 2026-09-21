"""Fill the genuine FDA Form 1932 (veterinary adverse experience report) PDF.

``FORM FDA 1932 (8/23)`` - "Veterinary Adverse Drug Reaction, Lack of
Effectiveness, Product Defect Report", OMB No. 0910-0284 - is the CVM report
whose sections are laid out directly on the VICH GL42 data elements (A.1.1,
B.2.1.1, ...).  The blank published form is downloaded once and cached; the
sample report is produced by writing values into its AcroForm fields, so the
OCR input is the real boxed form.

FDA also publishes Form 1932a (the voluntary version) but only as a *dynamic
XFA* PDF: it carries no page content and no AcroForm fields, so it can neither
be filled nor rendered outside Adobe Reader.  The static 1932 carries the same
report content and is used here instead.
"""

from __future__ import annotations

import os
import re
import urllib.request
from typing import Dict, List, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

FORM_URL = "https://www.fda.gov/media/124792/download?attachment"
FORM_FILENAME = "form_fda_1932_blank.pdf"
CHECKED = "/1"

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "medwatch_ocr")
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "fda_1932_2023.json")

_INDEX_RE = re.compile(r"\[\d+\]")
_PAGE_RE = re.compile(r"^Page\d+$")
_UNESCAPED_DOT_RE = re.compile(r"(?<!\\)\.")

CVM_VETERINARY: Dict[str, object] = {
    "center": "CVM",
    "text": {
        # A.1 Regulatory authority / A.2 marketing authorisation holder (page 1)
        "p1.RAname": "FDA Center for Veterinary Medicine",
        "p1.RAadd": "7500 Standish Place (HFV-210)",
        "p1.RAcity": "Rockville",
        "p1.RAstate": "MD",
        "p1.RAzip": "20855",
        "p1.RAcontry": "USA",
        "p1.MAHname": "Cascade Animal Health Inc.",
        "p1.MAHadd": "4100 Cedar Mill Parkway",
        "p1.MAHcity": "Boise",
        "p1.MAHstate": "ID",
        "p1.MAHzip": "83702",
        "p1.MAHcountry": "USA",
        "p1.title1": "Dr.",
        "p1.firstname1": "Priya",
        "p1.lastname1": "Raman",
        "p1.Phone1": "+1-208-555-0163",
        "p1.faxNo1": "+1-208-555-0164",
        "p1.email1": "pharmacovigilance@cascadeanimalhealth.example",
        # A.3.1 Primary reporter (page 1)
        "p1.lastname2": "Whitfield",
        "p1.firstname2": "Marcus",
        "p1.Phone2": "+1-503-555-0128",
        "p1.email2": "mwhitfield@riverbendvet.example",
        "p1.PRname": "Riverbend Veterinary Clinic",
        "p1.PRadd": "812 Willamette Avenue",
        "p1.PRcity": "Eugene",
        "p1.PRstate": "OR",
        "p1.PRzip": "97401",
        "p1.PRcountry": "USA",
        # A.4 AER identification / B.1 animal (page 2)
        "p2.AERID": "US-CAH-2026-000318",
        "p2.dayA42": "03",
        "p2.monthA42": "02",
        "p2.yearA42": "2026",
        "p2.dayA43": "09",
        "p2.monthA43": "02",
        "p2.yearA43": "2026",
        "p2.typeinfo": "Adverse event following label use of an approved veterinary medicinal product in a single dog.",
        "p2.animtreat": "1",
        "p2.animaffect": "1",
        "p2.assesment": (
            "Healthy adult dog treated for chronic allergic dermatitis. The wellness examination two "
            "months before treatment was unremarkable and no concurrent medication was given."
        ),
        "p2.species": "Dog",
        "p2.purebreed1": "Labrador Retriever",
        # B.1 animal detail / B.2 product (page 3)
        "p3.minikilos": "28.4",
        "p3.maxikilos": "28.4",
        "p3.miniage": "4",
        "p3.maxiage1_9_3": "4",
        "p3.brandname": "DERMAQUELL CHEWABLE TABLETS",
        "p3.prodcode": "CAH-DQ-016",
        "p3.regID": "NADA 141-999",
        "p3.ATCvet": "QD11AH90",
        "p3.compMAH": "Cascade Animal Health Inc.",
        "p3.MAHassessment": "Probable",
        "p3.RAassessment": "Probable",
        "p3.Explanation": "Positive dechallenge, plausible time to onset, no concurrent medication.",
        "p3.route": "Oral",
        "p3.dosenumvalue": "16",
        "p3.dosenumunits": "mg",
        "p3.dosedenomvalue": "1",
        "p3.dosedenomunits": "animal",
        # B.2 product detail (page 4)
        "p4.intervalofadmin": "12",
        "p4.dayfirstexp": "26",
        "p4.monthfirstexp": "01",
        "p4.yearfirstexp": "2026",
        "p4.daylastexp": "30",
        "p4.monthlastexp": "01",
        "p4.yearlastexp": "2026",
        "p4.activeingred1": "veloxacitinib maleate",
        "p4.strengthnumvalue1": "16",
        "p4.strengthnumunits1": "mg",
        "p4.strengthdenomvalue1": "1",
        "p4.strengthdenomunits1": "tablet",
        "p4.activeingredcode1": "CAH-VLX-001",
        "p4.dosageform": "Chewable tablet",
        "p4.lotnumb": "LOT-VLX-2025-114",
        "p4.day": "31",
        "p4.month": "08",
        "p4.year": "2027",
        # B.3 narrative (page 5)
        "p5.narrative": (
            "A four-year-old spayed female Labrador Retriever received DERMAQUELL CHEWABLE TABLETS "
            "16 mg orally every 12 hours from 26-Jan-2026 for chronic allergic dermatitis. The owner "
            "administered the tablets at home according to the label. On 29-Jan-2026, three days after "
            "the first dose, the dog vomited twice, became lethargic and refused food."
        ),
        # B.3 narrative continuation and clinical signs (page 6)
        "p6.narrativecont": (
            "The dog was presented to Riverbend Veterinary Clinic on 30-Jan-2026. Physical examination "
            "showed mild dehydration and abdominal discomfort; temperature, pulse and respiration were "
            "within normal limits. Serum chemistry on 30-Jan-2026 showed alanine aminotransferase "
            "412 U/L (reference 10-125 U/L) with normal bilirubin and no azotaemia. The product was "
            "discontinued on 30-Jan-2026 and the dog received maropitant and intravenous fluids for "
            "48 hours. Vomiting stopped within 24 hours of discontinuation and appetite returned on "
            "01-Feb-2026. Repeat chemistry on 04-Feb-2026 showed alanine aminotransferase 96 U/L. The "
            "dog had fully recovered by 04-Feb-2026 and the product was not reintroduced. The attending "
            "veterinarian assessed the event as probably related to the product."
        ),
        "p6.Table1.Row1.adverse1": "Vomiting",
        "p6.Table1.Row1.number1": "1",
        "p6.Table1.Row2.adverse2": "Lethargy",
        "p6.Table1.Row2.number2": "1",
        "p6.Table1.Row3.adverse3": "Anorexia",
        "p6.Table1.Row3.number3": "1",
        "p6.Table1.Row4.adverse4": "Alanine aminotransferase increased",
        "p6.Table1.Row4.number4": "1",
        # B.3 onset, duration, outcome (page 7)
        "p7.day": "29",
        "p7.month": "01",
        "p7.year": "2026",
        "p7.duration1": "6",
        "p7.recovnormal": "1",
        "p7.Table2.Row1.attached1": "riverbend-serum-chemistry-2026-02-04.pdf",
        "p7.Table2.Row1.types1": "Laboratory report",
        # C. Message / report identification (page 8)
        "p8.messagenumroot": "2.16.840.1.113883.3.989.5.1.1",
        "p8.messagenumext": "US-CAH-2026-000318-01",
        "p8.messagesendtitle": "Dr.",
        "p8.messagesendlast": "Raman",
        "p8.messagesendfirst": "Priya",
        "p8.messagesendtele": "+1-208-555-0163",
        "p8.messagesendfax": "+1-208-555-0164",
        "p8.messagesendemail": "pharmacovigilance@cascadeanimalhealth.example",
        "p8.messcreateday": "09",
        "p8.messcreatemonth": "02",
        "p8.messcreateyear": "2026",
        "p8.repident": "US-CAH-2026-000318",
    },
    "checks": [
        "p1.Lrep.vet.veter",  # A.3.1.1 primary reporter category: veterinarian
        "p2.LsubT.exp.exped",  # A.4.4 submission type: expedited
        "p3.Lb15.fem.female",  # B.1.5 gender
        "p3.Lb16.neu.neutered",  # B.1.6 reproductive status
        "p3.Lb17.na.nonapp",  # B.1.7 physiological status
        "p3.Lb181.meas.meas",  # B.1.8 weight measured
        "p3.Lb19.meas.measage",  # B.1.9 age measured
        "p3.Lb1921.yr.year",  # B.1.9.2.1 minimum age unit
        "p3.Lb1931.yr.year2",  # B.1.9.3.1 maximum age unit
        "p4.Lb2171311.hr.hour2",  # B.2.1.7.1.3.1.1 interval unit: hours
        "p5.Lb24.own.owner",  # B.2.4 administered by the animal owner
        "p5.Lb25.yes.yes1",  # B.2.5 used according to label
        "p5.Lb2511.no.no2",  # B.2.5.1.1 target species off label
        "p5.Lb2512.no.no3",  # B.2.5.1.2 route off label
        "p5.Lb2513.no.no4",  # B.2.5.1.3 overdose
        "p5.Lb2514.no.no5",  # B.2.5.1.4 underdose
        "p5.Lb2515.no.no6",  # B.2.5.1.5 treatment regime off label
        "p5.Lb2516.no.no7",  # B.2.5.1.6 indication off label
        "p5.Lb2517.no.no8",  # B.2.5.1.7 storage off label
        "p5.Lbi2518.no.no9",  # B.2.5.1.8 expired product
        "p5.Lb2519.no.no10",  # B.2.5.1.9 other off label issue
        "p6.Table1.Row1.acc1.act.actual1",
        "p6.Table1.Row2.acc2.act.actual2",
        "p6.Table1.Row3.acc3.act.actual3",
        "p6.Table1.Row4.acc4.act.actual4",
        "p7.Lb34.day7.day7",  # B.3.4 time to onset < 7 days
        "p7.Lb3511.day.day2",  # B.3.5.1.1 duration unit: days
        "p7.Lb36.no.noserious",  # B.3.6 serious: no
        "p7.Lb37.yes.yesAE",  # B.3.7 adverse event treated
        "p7.Lb39.no.no1",  # B.3.9 previous exposure
        "p7.Lb310.no.no3",  # B.3.10 previous reaction
        "p7.Lb41.yes.yes2",  # B.4.1 dechallenge positive
        "p7.Lb42.na.nonapp4",  # B.4.2 rechallenge not applicable
        "p7.Lb51.prob.prob",  # B.5.1 attending veterinarian assessment: probable
        "p8.Lb826.dom.domes",  # C.2.6 report category: domestic
        "p8.Lb827.ae.adverse",  # C.2.7 profile identifier: adverse event
    ],
}

VETERINARY_SAMPLES: Dict[str, Dict[str, object]] = {"cvm_veterinary": CVM_VETERINARY}


def field_key(qualified_name: str) -> str:
    """Template key for an AcroForm field: path inside the page, without indices.

    ``topmostSubform[0].Page3[0].Lb15[0].fem[0].female[0]`` -> ``Lb15.fem.female``.
    A few field names contain escaped dots (``maxiage1\\.9\\.3``); those become
    underscores so that the key itself stays a plain dotted path.
    """
    parts = [_INDEX_RE.sub("", part) for part in _UNESCAPED_DOT_RE.split(qualified_name)]
    parts = [part.replace("\\.", "_") for part in parts if part]
    parts = [part for part in parts if part != "topmostSubform" and not _PAGE_RE.match(part)]
    return ".".join(parts)


def blank_form_path(cache_dir: str | None = None, download: bool = True) -> str:
    """Return the local path of the blank FDA 1932 form, downloading it if needed."""
    directory = cache_dir or os.environ.get("MEDWATCH_FORM_CACHE") or DEFAULT_CACHE_DIR
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, FORM_FILENAME)
    if os.path.exists(path) and os.path.getsize(path) > 100_000:
        return path
    if not download:
        raise FileNotFoundError(f"blank FDA 1932 form not cached at {path}")
    request = urllib.request.Request(FORM_URL, headers={"User-Agent": "medwatch-ocr/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, open(path, "wb") as handle:
        handle.write(response.read())
    return path


def _page_number(key: str) -> int:
    page, _, _ = key.partition(".")
    return int(page.lstrip("p"))


def _qualified_names(reader: PdfReader) -> Dict[str, str]:
    """Map ``p{page}.{key}`` to the fully qualified AcroForm field name."""
    names: Dict[str, str] = {}
    for page_index, page in enumerate(reader.pages):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            parts: List[str] = []
            node = widget
            while node is not None:
                title = node.get("/T")
                if title:
                    parts.append(str(title))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            if not parts:
                continue
            qualified = ".".join(reversed(parts))
            names[f"p{page_index + 1}.{field_key(qualified)}"] = qualified
    return names


def _on_state(reader: PdfReader, qualified: str) -> str:
    """Return the checkbox 'on' appearance state (e.g. ``/1``) of ``qualified``."""
    for page in reader.pages:
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            parts: List[str] = []
            node = widget
            while node is not None:
                title = node.get("/T")
                if title:
                    parts.append(str(title))
                parent = node.get("/Parent")
                node = parent.get_object() if parent is not None else None
            if ".".join(reversed(parts)) != qualified:
                continue
            appearances = widget.get("/AP")
            if appearances and "/N" in appearances:
                for state in appearances["/N"].keys():
                    if state != "/Off":
                        return str(state)
    return CHECKED


def fill_1932_form(spec: Dict[str, object], output_path: str, blank_path: str | None = None) -> str:
    """Write ``spec`` into the official 1932 AcroForm and save it to ``output_path``."""
    reader = PdfReader(blank_path or blank_form_path())
    names = _qualified_names(reader)
    writer = PdfWriter(clone_from=reader)

    acroform = writer._root_object["/AcroForm"]
    if "/XFA" in acroform:  # keep viewers on the static AcroForm rendering path
        del acroform[NameObject("/XFA")]
    acroform[NameObject("/NeedAppearances")] = BooleanObject(True)

    values_by_page: Dict[int, Dict[str, str]] = {}
    for key, value in dict(spec.get("text", {})).items():  # type: ignore[arg-type]
        qualified = names.get(key)
        if qualified is None:
            raise KeyError(f"unknown FDA 1932 field: {key}")
        values_by_page.setdefault(_page_number(key), {})[qualified] = value
    checks_by_page: Dict[int, Dict[str, str]] = {}
    for key in list(spec.get("checks", [])):  # type: ignore[arg-type]
        qualified = names.get(key)
        if qualified is None:
            raise KeyError(f"unknown FDA 1932 checkbox: {key}")
        checks_by_page.setdefault(_page_number(key), {})[qualified] = _on_state(reader, qualified)

    for page_number, values in values_by_page.items():
        writer.update_page_form_field_values(writer.pages[page_number - 1], values, auto_regenerate=True)
    for page_number, values in checks_by_page.items():
        writer.update_page_form_field_values(writer.pages[page_number - 1], values)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as handle:
        writer.write(handle)
    return output_path


def generate_1932_samples(output_dir: str, blank_path: str | None = None) -> Dict[str, str]:
    """Write the filled official FDA 1932 sample(s) into ``output_dir``."""
    os.makedirs(output_dir, exist_ok=True)
    blank = blank_path or blank_form_path()
    written: Dict[str, str] = {}
    for name, spec in VETERINARY_SAMPLES.items():
        path = os.path.join(output_dir, f"FDA-1932_{name}.pdf")
        fill_1932_form(spec, path, blank_path=blank)
        written[name] = path
    return written


def expected_values(name: str) -> Dict[str, str]:
    """Flat ``field key -> value`` view of a sample, used as ground truth in tests."""
    return dict(VETERINARY_SAMPLES[name].get("text", {}))  # type: ignore[arg-type]


def expected_checks(name: str) -> List[str]:
    return list(VETERINARY_SAMPLES[name].get("checks", []))  # type: ignore[arg-type]


def sample_spec(name: str) -> Optional[Dict[str, object]]:
    return VETERINARY_SAMPLES.get(name)
