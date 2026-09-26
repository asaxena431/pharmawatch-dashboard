"""
Core redaction engine: applies (B)(6) / (B)(4) rules to the free-text sections
of a Medical Device Report exactly the way the SOP tells human editors to.

Section -> exemptions applied (SOP steps 5.3-5.6 / 6.x):
  B5  Event description ............ (B)(6) + (B)(4)
  D11 / F11 Concomitant products ... (B)(6) serial numbers only
  H11 Additional MFR narrative ..... (B)(4) trade secrets/CCI + (B)(6)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import rules as R

SECTION_POLICY = {
    "B5": {"b6": True, "b4": True, "trade_secret_paragraphs": False},
    "D11": {"b6": True, "b4": False, "trade_secret_paragraphs": False, "serial_only": True},
    "F11": {"b6": True, "b4": False, "trade_secret_paragraphs": False, "serial_only": True},
    "H11": {"b6": True, "b4": True, "trade_secret_paragraphs": True},
    "H10": {"b6": True, "b4": True, "trade_secret_paragraphs": True},
}


@dataclass
class Finding:
    section: str
    rule: str
    exemption: str
    original: str
    replacement: str


@dataclass
class SectionResult:
    section: str
    original: str
    redacted: str
    findings: list[Finding] = field(default_factory=list)


def _apply(pattern: re.Pattern, repl, text: str, section: str, rule: str, exemption: str, findings: list) -> str:
    def _sub(m: re.Match) -> str:
        new = m.expand(repl) if isinstance(repl, str) else repl(m)
        if new != m.group(0):
            findings.append(Finding(section, rule, exemption, m.group(0), new))
        return new
    return pattern.sub(_sub, text)


# ---------------------------------------------------------------- ages -------
def _redact_ages(text: str, section: str, findings: list) -> str:
    def _sub(m: re.Match) -> str:
        age = int(next(g for g in m.groups() if g))
        if age > R.AGE_CUTOFF:
            findings.append(Finding(section, "age_over_89", R.B6, m.group(0), R.AGE_REPLACEMENT))
            return R.AGE_REPLACEMENT
        return m.group(0)
    for p in R.AGE_PATTERNS:
        text = p.sub(_sub, text)
    return text


def _patient_over_89(text: str) -> bool:
    for p in R.AGE_PATTERNS:
        for m in p.finditer(text):
            if int(next(g for g in m.groups() if g)) > R.AGE_CUTOFF:
                return True
    return False


# ---------------------------------------------------------------- dates ------
def _redact_dates(text: str, section: str, findings: list, keep_year: bool) -> str:
    def _sub(m: re.Match) -> str:
        year = m.group(1)
        if len(year) == 2:
            year = ("19" if int(year) > 30 else "20") + year
        new = f"{R.B6} {year}" if keep_year else R.B6
        findings.append(Finding(section, "date", R.B6, m.group(0), new))
        return new
    for p in R.DATE_PATTERNS:
        text = p.sub(_sub, text)
    return text


# ---------------------------------------------------------- trade secrets ----
def _redact_trade_secret_paragraphs(text: str, section: str, findings: list) -> str:
    out = []
    for para in re.split(r"(\n\s*\n)", text):
        low = para.lower()
        hits = [t for t in R.TRADE_SECRET_TERMS if t in low]
        if para.strip() and len(hits) >= 2:
            findings.append(Finding(section, "trade_secret_paragraph", R.B4, para.strip(), R.B4))
            out.append(R.B4)
        else:
            out.append(para)
    return "".join(out)


# ---------------------------------------------------------------- main -------
def redact_section(section: str, text: str) -> SectionResult:
    section = section.upper()
    policy = SECTION_POLICY.get(section, SECTION_POLICY["B5"])
    findings: list[Finding] = []
    t = text

    if policy.get("serial_only"):
        for name, pat, repl in R.B6_RULES:
            if name == "serial":
                t = _apply(pat, repl, t, section, name, R.B6, findings)
        return SectionResult(section, text, t, findings)

    over_89 = _patient_over_89(t)

    if policy["trade_secret_paragraphs"]:
        t = _redact_trade_secret_paragraphs(t, section, findings)

    if policy["b4"]:
        for name, pat, repl in R.B4_RULES:
            t = _apply(pat, repl, t, section, name, R.B4, findings)

    if policy["b6"]:
        # DOB before generic dates so the whole DOB (incl. year) is removed
        for name, pat, repl in R.B6_RULES:
            if name == "dob":
                t = _apply(pat, repl, t, section, name, R.B6, findings)
        t = _redact_ages(t, section, findings)
        t = _redact_dates(t, section, findings, keep_year=not over_89)
        for name, pat, repl in R.B6_RULES:
            if name != "dob":
                t = _apply(pat, repl, t, section, name, R.B6, findings)

    for pat in R.BOILERPLATE:
        t = _apply(pat, "", t, section, "boilerplate", "delete", findings)
    t = _apply(R.PROFANITY, R.PROFANITY_TOKEN, t, section, "profanity", "replace", findings)

    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;])", r"\1", t).strip()
    return SectionResult(section, text, t, findings)


def redact_report(report: dict) -> dict:
    """
    report = {"report_number": ..., "report_type": "MFR|UF|MEDSUN|IMP|VOL",
              "sections": {"B5": "...", "D11": "...", "H11": "..."}}
    Returns a copy with sections redacted plus a flat findings list.
    """
    out = dict(report)
    out["sections"] = {}
    out["findings"] = []
    for sec, text in (report.get("sections") or {}).items():
        res = redact_section(sec, text or "")
        out["sections"][sec] = res.redacted
        out["findings"].extend(f.__dict__ for f in res.findings)
    out["needs_human_review"] = any(f["rule"] == "trade_secret_paragraph" for f in out["findings"])
    return out
