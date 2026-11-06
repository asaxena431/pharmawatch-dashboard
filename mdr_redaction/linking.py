"""
Batch-wide linking: find pairs of reports that describe the SAME event even
when neither narrative quotes the other's report number.

Today (SOP, Appendix 9-10) an editor only catches a link if the text in front
of them mentions another report. This module looks across the whole batch:

  1. quoted_number  narrative of A contains B's report number  -> confirmed link
  2. structured     same manufacturer, different source (MFR vs UF/VOL/MedSun/IMP)
                    and shared device identifiers / close event dates / similar text
                    -> scored candidate for the STL to confirm

Output feeds the "Linking" / "Multiple Linking" emails (Appendix 10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from itertools import combinations

from .reportables import REPORT_NUMBER, MEDWATCH_NUMBER, STL_RECIPIENTS

CONFIRMED = 1.0
THRESHOLD = 0.5
DATE_WINDOW_DAYS = 30

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "was", "were", "is", "be", "by", "for",
    "with", "that", "this", "it", "at", "as", "from", "patient", "device", "reported", "report",
    "event", "no", "not", "unknown", "information", "additional", "received", "manufacturer",
}


@dataclass
class LinkCandidate:
    report_a: str
    report_b: str
    score: float
    confirmed: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def email_subject(self) -> str:
        prefix = "Linking" if self.confirmed else "Possible Linking"
        return f"{prefix} - {self.report_a} / {self.report_b}"

    def to_dict(self) -> dict:
        return asdict(self) | {"email_subject": self.email_subject, "email_to": STL_RECIPIENTS}


# ------------------------------------------------------------ helpers --------
def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _norm_id(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _parse_date(s) -> date | None:
    if not s:
        return None
    if isinstance(s, (date, datetime)):
        return s if isinstance(s, date) else s.date()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _text(r: dict) -> str:
    return "\n".join(v for v in (r.get("sections") or {}).values() if v)


def _tokens(r: dict) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", _text(r).lower()) if w not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _source(r: dict) -> str:
    return str(r.get("report_type", "")).upper()


def _numbers_in(r: dict) -> set[str]:
    t = _text(r)
    nums = {m.group(1) for m in REPORT_NUMBER.finditer(t)}
    nums |= {"MW" + m.group(1) for m in MEDWATCH_NUMBER.finditer(t)}
    return nums


def _root(num: str) -> str:
    """strip supplement suffix: 1234567-2022-00001-2 -> 1234567-2022-00001"""
    return re.sub(r"-\d{1,2}$", "", num) if REPORT_NUMBER.fullmatch(num) else num


# ------------------------------------------------------------ scoring --------
def score_pair(a: dict, b: dict) -> LinkCandidate | None:
    na, nb = str(a.get("report_number", "")), str(b.get("report_number", ""))
    if not na or not nb or _root(na) == _root(nb):
        return None  # same report or its own supplement

    reasons: list[str] = []

    if _root(nb) in {_root(n) for n in _numbers_in(a)}:
        reasons.append(f"{na} quotes {nb}")
    if _root(na) in {_root(n) for n in _numbers_in(b)}:
        reasons.append(f"{nb} quotes {na}")
    if reasons:
        return LinkCandidate(na, nb, CONFIRMED, True, reasons)

    # structured matching only makes sense for reports of the same event from different sources
    if _source(a) == _source(b):
        return None
    if _norm(a.get("manufacturer")) and _norm(a.get("manufacturer")) != _norm(b.get("manufacturer")):
        return None

    score = 0.0
    for key, weight in (("serial", 0.5), ("lot", 0.3), ("udi", 0.4)):
        if _norm_id(a.get(key)) and _norm_id(a.get(key)) == _norm_id(b.get(key)):
            score += weight
            reasons.append(f"same {key} {a.get(key)}")
    for key, weight in (("brand_name", 0.2), ("model", 0.2)):
        if _norm(a.get(key)) and _norm(a.get(key)) == _norm(b.get(key)):
            score += weight
            reasons.append(f"same {key}")
    da, db = _parse_date(a.get("event_date")), _parse_date(b.get("event_date"))
    if da and db:
        delta = abs((da - db).days)
        if delta <= DATE_WINDOW_DAYS:
            score += 0.2 if delta <= 3 else 0.1
            reasons.append(f"event dates {delta} day(s) apart")
    if a.get("outcome") and a.get("outcome") == b.get("outcome"):
        score += 0.1
        reasons.append(f"same outcome {a['outcome']}")
    sim = _jaccard(_tokens(a), _tokens(b))
    if sim >= 0.15:
        score += min(0.3, sim)
        reasons.append(f"narrative similarity {sim:.2f}")

    if score < THRESHOLD:
        return None
    return LinkCandidate(na, nb, round(min(score, 0.99), 2), False, reasons)


def find_links(reports: list[dict]) -> list[LinkCandidate]:
    out = []
    for a, b in combinations(reports, 2):
        c = score_pair(a, b)
        if c:
            out.append(c)
    out.sort(key=lambda c: (-c.score, c.report_a, c.report_b))
    return out


def multiple_linking_groups(cands: list[LinkCandidate]) -> dict[str, list[str]]:
    """One report linked to several others -> 'Multiple Linking' email (Appendix 10)."""
    groups: dict[str, list[str]] = {}
    for c in cands:
        groups.setdefault(c.report_a, []).append(c.report_b)
        groups.setdefault(c.report_b, []).append(c.report_a)
    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}
