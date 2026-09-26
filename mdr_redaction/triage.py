"""
Inbox prioritisation (SOP step 4):
  Code Blues > Deaths > Injuries > Voluntaries > UF > IMP > Malfunctions > Supplementals
"""
from __future__ import annotations

PRIORITY = ["CODE_BLUE", "DEATH", "INJURY", "VOLUNTARY", "UF", "IMP", "MALFUNCTION", "SUPPLEMENT"]

_OUTCOME = {"D": "DEATH", "DEATH": "DEATH", "IN": "INJURY", "INJURY": "INJURY", "M": "MALFUNCTION",
            "MALFUNCTION": "MALFUNCTION", "O": "MALFUNCTION", "OTHER": "MALFUNCTION"}
_TYPE = {"PRP": "VOLUNTARY", "VOL": "VOLUNTARY", "VOLUNTARY": "VOLUNTARY", "UF": "UF", "MEDSUN": "UF",
         "IMP": "IMP", "IMPORTER": "IMP"}


def is_supplement(report_number: str) -> bool:
    # 2246315-2018-00316-1  -> follow-up #1 ;  MW5112852 never has a suffix
    parts = report_number.strip().split("-")
    return len(parts) == 4 and parts[-1].isdigit()


def classify(report: dict) -> str:
    if report.get("code_blue"):
        return "CODE_BLUE"
    outcome = _OUTCOME.get(str(report.get("outcome", "")).upper())
    if outcome in ("DEATH", "INJURY"):
        return outcome
    rtype = _TYPE.get(str(report.get("report_type", "")).upper())
    if rtype:
        return rtype
    if report.get("supplement") or is_supplement(str(report.get("report_number", ""))):
        return "SUPPLEMENT"
    return "MALFUNCTION"


def prioritize(reports: list[dict]) -> list[dict]:
    def key(r: dict):
        cat = classify(r)
        r["triage_category"] = cat
        return (PRIORITY.index(cat), -float(r.get("days_in_inbox", 0) or 0))
    return sorted(reports, key=key)
