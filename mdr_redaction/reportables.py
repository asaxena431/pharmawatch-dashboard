"""
Detection of "Reportables" (SOP: Identify Code Blue / Reportables) and
generation of the STL email subject + Reportable Log rows (Appendices 9-12).

  ANIMAL            keywords animal / canine / veterinary ...
  LINKING           the narrative quotes another report's number
  POSSIBLE_LINKING  wording implies another report exists ("Per MW", "MedWatch", ...)
  CODE_BLUE         keyword list is a placeholder -- real criteria live in FDA Doc 06254
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

REPORT_NUMBER = re.compile(r"\b(\d{7,10}-\d{4}-\d{4,6}(?:-\d{1,2})?)\b")
MEDWATCH_NUMBER = re.compile(r"\bMW\s?(\d{6,8})\b", re.I)

ANIMAL_TERMS = re.compile(r"\b(animal|canine|feline|equine|bovine|veterinar(?:y|ian)|dog|cat|horse|pet)\b", re.I)

POSSIBLE_LINKING_TERMS = re.compile(
    r"\b(per\s+MW|MedWatch|Med\s*Watch|MedSun|user\s+facility\s+(?:reported|filed|submitted)|"
    r"risk\s+manager\s+reported|reported\s+via\s+(?:a\s+)?voluntary|voluntary\s+report|"
    r"hospital\s+(?:filed|submitted)\s+(?:a\s+)?(?:report|MDR)|FDA\s+notif(?:ied|ying)|"
    r"received\s+(?:an\s+)?(?:email|notification)\s+from\s+(?:the\s+)?FDA|importer\s+reported)\b",
    re.I,
)

# Placeholder until Doc 06254 criteria are available.
CODE_BLUE_TERMS = {
    "FIRE": re.compile(r"\b(fire|flames?|ignit(?:ed|ion)|caught\s+fire)\b", re.I),
    "SUICIDE": re.compile(r"\b(suicide|self[- ]harm|intentional\s+overdose)\b", re.I),
    "EXPLOSION": re.compile(r"\b(explo(?:ded|sion))\b", re.I),
    "PEDIATRIC DEATH": re.compile(r"\b(infant|neonate|child|pediatric)\b.*\b(died|death|expired)\b", re.I | re.S),
}

STL_RECIPIENTS = ["Jones, Caprice", "Bond, Marcia", "Goss, Terrel", "Noel, Tiffany", "Smith, Samaad"]
CVM_MAILBOX = "CVM-Device-Reports@fda.hhs.gov"


@dataclass
class Reportable:
    report_number: str
    kind: str                 # ANIMAL | LINKING | POSSIBLE_LINKING | CODE_BLUE
    linked_numbers: list[str]
    evidence: str
    email_subject: str
    email_to: list[str]
    code_blue_type: str = ""

    def log_row(self) -> dict:
        label = self.kind.replace("_", " ")
        if self.kind == "CODE_BLUE":
            label = f"CODE BLUE {self.code_blue_type}"
        ids = self.report_number
        if self.linked_numbers:
            ids = f"{self.report_number}/{', '.join(self.linked_numbers)}"
        return {"type": label, "report_numbers": ids}


def _snippet(text: str, m: re.Match, width: int = 60) -> str:
    return text[max(0, m.start() - width): m.end() + width].replace("\n", " ").strip()


def detect(report: dict) -> list[Reportable]:
    own = str(report.get("report_number", "")).strip()
    sections = report.get("sections") or {}
    text = "\n".join(v for v in sections.values() if v)
    found: list[Reportable] = []

    # -- linking: other report numbers quoted in the narrative
    others = []
    for m in REPORT_NUMBER.finditer(text):
        if m.group(1) != own and not own.startswith(m.group(1).rsplit("-", 1)[0]):
            others.append((m.group(1), m))
    for m in MEDWATCH_NUMBER.finditer(text):
        others.append(("MW" + m.group(1), m))
    if others:
        nums = sorted({n for n, _ in others})
        found.append(Reportable(own, "LINKING", nums, _snippet(text, others[0][1]),
                                f"Linking - {own} / {' / '.join(nums)}", STL_RECIPIENTS))
    else:
        m = POSSIBLE_LINKING_TERMS.search(text)
        if m:
            found.append(Reportable(own, "POSSIBLE_LINKING", [], _snippet(text, m),
                                    f"Possible Linking - {own}", STL_RECIPIENTS))

    m = ANIMAL_TERMS.search(text)
    if m:
        found.append(Reportable(own, "ANIMAL", [], _snippet(text, m), f"Animal - {own}",
                                STL_RECIPIENTS + [CVM_MAILBOX]))

    for cb_type, pat in CODE_BLUE_TERMS.items():
        m = pat.search(text)
        if m:
            found.append(Reportable(own, "CODE_BLUE", [], _snippet(text, m),
                                    f"CODE BLUE - {own} - {cb_type}", STL_RECIPIENTS, cb_type))
            break
    return found


def detect_dicts(report: dict) -> list[dict]:
    return [asdict(r) | {"log_row": r.log_row()} for r in detect(report)]
