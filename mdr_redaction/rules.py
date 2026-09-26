"""
Redaction rules derived from the FDA/CDRH "Medical Device Reporting Redaction
Procedures" SOP (v4.0, 02/01/2025).

Two FOIA exemption codes are applied:
  (B)(6) -- personal privacy: patient / reporter / clinician identifiers
  (B)(4) -- trade secret & confidential commercial information (manufacturer)

Each rule is (name, compiled regex, replacement).  The replacement may use
backreferences so that context words survive (e.g. "Dr. (B)(6)").
"""
import re

B6 = "(B)(6)"
B4 = "(B)(4)"

_MONTHS = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
           r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")

# Dates the SOP says to reduce to their year:  "June 07, 2015" -> "(B)(6) 2015",
# "2015-06-25" -> "(B)(6) 2015", "06/25/2015" -> "(B)(6) 2015".
DATE_PATTERNS = [
    re.compile(rf"\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),      # June 07, 2015
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\.?,?\s+(\d{{4}})\b", re.I),      # 07 June 2015
    re.compile(r"\b(\d{4})-\d{1,2}-\d{1,2}\b"),                                          # 2015-06-25
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](\d{4})\b"),                                  # 06/25/2015
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](\d{2})\b"),                                  # 06/25/15
]

AGE_CUTOFF = 89   # HHS Safe Harbor: ages > 89 and any date element indicating it

# ---------------------------------------------------------------- (B)(6) -----
B6_RULES = [
    ("dob", re.compile(r"\b(DOB|Date\s+of\s+Birth|Birth\s*date)\s*[:#-]?\s*[\w/.-]+(?:,?\s+\d{4})?", re.I), rf"\1: {B6}"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), B6),
    ("mrn",
     re.compile(r"\b(MRN|Medical\s+Record\s+(?:Number|No\.?|#)|Patient\s+ID|Pt\s+ID|Chart\s*(?:No\.?|#)|Account\s*(?:No\.?|#))"
                r"\s*[:#]?\s*[A-Z0-9-]{3,}", re.I),
     rf"\1 {B6}"),
    ("phone", re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"), B6),
    ("email", re.compile(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", re.I), B6),
    ("clinician_name",
     re.compile(r"\b(Dr|Doctor|Nurse|RN|MD|PA|NP|Prof|Physician)(\.?)\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?"),
     rf"\1\2 {B6}"),
    ("titled_name", re.compile(r"\b(Mr|Mrs|Ms|Miss|Mx)\.?\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?"), B6),
    ("patient_initials", re.compile(r"\b(patient|pt)\s+(?:initials?\s+)?\(?[A-Z]\.?\s?[A-Z]\.?\)?(?=[\s,.;)])", re.I), rf"\1 {B6}"),
    ("facility",
     re.compile(r"\b(?:[A-Z][\w'&.-]+\s+){1,4}(Hospital|Medical\s+Center|Health\s+System|Clinic|Surgery\s+Center|Surgical\s+Center|"
                r"Nursing\s+Home|Rehabilitation\s+Center|Cancer\s+Center|Infirmary|University\s+Hospitals?)\b"),
     rf"{B6} \1"),
    ("address",
     re.compile(r"\b\d{1,5}\s+[A-Z][a-zA-Z\s]{2,30}(?:Street|St|Avenue|Ave|Road|Rd|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way)\b\.?", re.I),
     B6),
    ("zip", re.compile(r"\b(?:zip(?:\s*code)?\s*[:#]?\s*)\d{5}(?:-\d{4})?\b", re.I), B6),
    ("insurance",
     re.compile(r"\b(Medicare|Medicaid|Insurance|Policy|Member|Claim|Group)\s*(?:ID|No\.?|Number|#)\s*[:#]?\s*[A-Z0-9-]{4,}", re.I),
     rf"\1 # {B6}"),
    ("money", re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?"), B6),
    ("workers_comp", re.compile(r"\bworker'?s?\s+comp(?:ensation)?\b[^.;]*", re.I), B6),
    ("serial",
     re.compile(r"\b(S/?N|Serial\s*(?:No\.?|Number|#)?|Transmitter\s*(?:No\.?|Number|#)?|Analyzer\s*(?:No\.?|Number|#)?)"
                r"\s*[:#]?\s*[A-Z0-9-]{4,}", re.I),
     rf"\1 {B6}"),
]

# ---------------------------------------------------------------- (B)(4) -----
# identifier that must contain a digit and be separated from the label word
_NUM = r"(?:\s+|\s*[:#]\s*)(?=[A-Z0-9/-]*\d)[A-Z0-9][A-Z0-9/-]{2,}"
B4_RULES = [
    ("complaint_no",
     re.compile(rf"\b(Complaint|Tracking|Internal\s+Report|Reference|Ref|Case|Ticket|PR|CAPA|Investigation)"
                rf"\s*(?:No\.?|Number|#|ID)?\s*{_NUM}", re.I),
     rf"\1 # {B4}"),
    ("ncr_cfn_rae", re.compile(rf"\b(NCR|CFN|RAE|MAF|DHR|DMR|SCAR)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 # {B4}"),
    ("ide", re.compile(rf"\b(IDE)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 {B4}"),
    ("eua", re.compile(rf"\b(EUA)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 {B4}"),
    ("clinical_trial", re.compile(rf"\b(Clinical\s+Trial|Study|Protocol)\s*(?:No\.?|Number|#|ID)?\s*{_NUM}", re.I), rf"\1 # {B4}"),
    ("udi", re.compile(r"\b(UDI|UDI-DI|DI|GTIN)\s*[:#]?\s*\(?\d{2}\)?\d{12,}\b", re.I), rf"\1 {B4}"),
    ("udi_word", re.compile(rf"\b(UDI|GTIN)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 {B4}"),
    ("bsc_tw", re.compile(r"\b(BSC\s*ID|TW)\s*#?\s*[A-Z]?\d{5,}\b", re.I), rf"\1 # {B4}"),
    ("cms", re.compile(rf"\b(CMS)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 # {B4}"),
    ("uf_medsun_report",
     re.compile(r"\b(MedWatch|MedSun|User\s+Facility|UF|Voluntary)\s+(?:form|report|#|number|no\.?)?\s*[:#]?\s*"
                r"(\d{7,10}-\d{4}-\d{4,6}|MW\d{6,8})", re.I),
     rf"\1 report {B4}"),
    ("lot", re.compile(rf"\b(Lot|Batch|Catalog|Cat|REF|Model|Part)\s*(?:No\.?|Number|#)?\s*{_NUM}", re.I), rf"\1 # {B4}"),
    ("mfr_rep",
     re.compile(r"\b((?:sales|field|manufacturer|company|territory|clinical)\s+(?:representative|rep|specialist|engineer))"
                r"\s+[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+)?", re.I),
     rf"\1 {B4}"),
    ("supplier",
     re.compile(r"\b((?:supplier|distributor|contract\s+manufacturer|contractor|vendor|sub-?contractor)(?:\s+(?:is|was|named|,))?)"
                r"\s+[A-Z][\w&.,'-]+(?:\s+[A-Z][\w&.,'-]+){0,3}", re.I),
     rf"\1 {B4}"),
    ("production_stats",
     re.compile(r"\b\d[\d,]{3,}\s+(?:units|devices|pieces|lots?)\s+(?:were\s+|have\s+been\s+)?"
                r"(?:released|sold|distributed|produced|manufactured|shipped)[^.;]*", re.I),
     B4),
    ("rate",
     re.compile(r"\b(?:complaint|occurrence|failure|malfunction)\s+(?:occurrence\s+)?rate[^.;]*?\d+(?:\.\d+)?\s*%[^.;]*", re.I),
     B4),
    ("percent_rate", re.compile(r"\b0\.0+\d+\s*%"), B4),
]

# Paragraph-level (B)(4): if a sentence/paragraph contains these terms it is
# probably describing a manufacturing process / trade secret / product analysis.
TRADE_SECRET_TERMS = [
    "master file", "maf#", "manufacturing process", "manufacturing procedure",
    "quality control procedure", "sterilization technique", "sterilization process",
    "design enhancement", "design change", "formulation", "formula", "schematic",
    "circuit diagram", "material was identified as", "certified by the manufacturer",
    "specification", "bill of materials", "raw material", "supplier", "assembly process",
    "process validation", "yield", "reject rate", "production data", "sales data",
    "distribution data", "profit", "operating expenditure", "customer list",
    "root cause analysis determined", "failure analysis", "product analysis",
    "device evaluated by mfr",
]

# --------------------------------------------------------------- Clean-up ----
BOILERPLATE = [
    re.compile(r"\(?\s*refer\s+to\s+additional\s+documents?\s+in\s+I2K\s*\.?\)?", re.I),
    re.compile(r"\(?\s*a\s+copy\s+of\s+the\s+literature\s+is\s+attached\s*\.?\)?", re.I),
    re.compile(r"\(?\s*see\s+attached(?:\s+documents?|\s+pages?|\s+literature)?\s*\.?\)?", re.I),
    re.compile(r"\(?\s*see\s+scanned\s+pages?\s*\.?\)?", re.I),
    re.compile(r"\(?\s*please\s+see\s+attachments?\s*\.?\)?", re.I),
]

PROFANITY = re.compile(
    r"\b(fuck(?:ing|ed|er)?|shit(?:ty)?|bitch|asshole|bastard|damn(?:ed)?|goddamn|crap|piss(?:ed)?|dick|cunt)\b",
    re.I,
)
PROFANITY_TOKEN = "PROFANITY"

# Ages: "92 years old", "92-year-old", "92 y/o", "92 yo", "age 92", "aged 92"
AGE_PATTERNS = [
    re.compile(r"\b(?:(\d{2,3})\s*[- ]?(?:years?|yrs?|y)[- ]?(?:old|o)\b"
               r"|(\d{2,3})\s*y/o\b"
               r"|(?:age[d]?|aged)\s*[:=]?\s*(\d{2,3})\b(?!\s*years\s+or\s+older))", re.I),
]
AGE_REPLACEMENT = "age 90 years or older"
