"""Benchmark the redactor against narratives that a human editor already redacted.

Input: an .xlsx / .csv with (at least) an original-text column and a human-redacted
column (defaults match the FDA export: COMPANY_NARRATIVE / REDACTED_NARRATIVE).
For every row the original is redacted, compared with the human version and the
difference is explained with a reason category so a reviewer can see *why* the
automation and the editor disagree.

    python -m mdr_redaction.compare redacted_narratives.xlsx -o compare_out.xlsx
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

from . import rules as R
from .redactor import redact_section

_TOKEN = re.compile(r"\(b\)\s*\)?\((\d)\)", re.I)          # (b)(6), (B)(4), (b))(6) typo
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[.,;:]+(?=\s|$)")

_DATE_LIKE = re.compile(
    rf"\b(?:\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}-{R._MONTHS}-\d{{2,4}}"
    rf"|{R._MONTHS}\.?\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{R._MONTHS}\.?,?\s+\d{{4}})\b", re.I)


# SOP citations shown next to every difference. Key = topic; text = where in the SOP
# ("MEDICAL DEVICE REPORTING REDACTION PROCEDURES" v4.0, 02/01/2025) and what it says.
SOP_REFS: dict[str, str] = {
    "date": ("SOP Appendix 6 (B)(6) code sheet, 'Dates' rows (implant/explant, admission/discharge, "
             "test, procedure/surgery dates): redact day and month, keep the year - example "
             "'June 07, 2015' -> '(B)(6) 2015'."),
    "year": ("SOP Appendix 6 (B)(6): only day and month are redacted, the year stays ('(B)(6) 2015'). "
             "The year is removed only for patients over 89 (Procedure step 9.1 / Appendix 6 'DOB')."),
    "age": ("SOP Procedure step 9 (p.6): 'when the person is over the age of 89 ... their age should also be "
            "redacted ... may be aggregated into a single category of age 90 or older'."),
    "dob": "SOP Appendix 6 (B)(6) 'Date of Birth': redact day and month, keep year; over 89 -> redact entire DOB (step 9.1).",
    "name": ("SOP Appendix 6 (B)(6) 'Patient/reporter names and initials' and 'Medical personnel names' "
             "- example 'Dr. Smith' -> 'Dr. (B)(6)'."),
    "facility": ("SOP Appendix 6 (B)(6) 'Hospital / facility names': redact the name, keep the facility type "
                 "- example 'Shady Grove Hospital' -> '(B)(6) Hospital'."),
    "location": "SOP Appendix 6 (B)(6) 'Location information' (addresses, city/state of patient or facility).",
    "identifier_b6": ("SOP Appendix 6 (B)(6) 'Patient ID / MRN / account numbers' and 'Serial, transmitter, analyzer "
                      "numbers'; Procedure step 5.4 / 6.1.2: D11/F11 tabs use (B)(6) for serial numbers."),
    "identifier_b4": ("SOP Appendix 7 (B)(4) code sheet: complaint/tracking/internal reference numbers, NCR/CFN/RAE, "
                      "IDE#, EUA#, UDI/DI, BSC ID/TW#, CMS#, protocol numbers, UF/MedSun report numbers."),
    "lot": ("SOP Appendix 7 (B)(4): UDI/DI and internal product identifiers are (B)(4); lot/model/catalog numbers are "
            "NOT listed explicitly in Appendix 7 - treat as a judgment call (Trade Secrets folder, step 5.5)."),
    "commercial": ("SOP Appendix 7 (B)(4) 'Trade secrets, financial info, manufacturing procedures, production statistics, "
                   "contractor/distributor/supplier names, manufacturer rep names, product analysis'; "
                   "worked examples in Appendix 8 (MED-4854 / MAF#1281 paragraph; Integra complaint-rate paragraph)."),
    "profanity": "SOP Procedure step 8: 'Replace the profane word with PROFANITY'.",
    "boilerplate": ("SOP Procedure step 7: delete 'Refer to additional documents in I2K', 'A copy of the literature is "
                    "attached', 'See Attached'."),
    "wording": ("SOP Procedure step 10: 'Correct grammar errors and misspelled words using electronic spell check tool' "
                "- wording edits are allowed and are not redactions."),
    "token_format": ("SOP Appendix 6/7 write the exemption as '(B)(6)' / '(B)(4)' followed by the kept year, e.g. "
                     "'(B)(6) 2015'. Casing/spacing variants in the editor text are cosmetic."),
    "none": "No SOP rule covers this span; not listed in Appendix 6 (B)(6) or Appendix 7 (B)(4).",
}

_RULE_TOPIC = {
    "date": "date", "partial_date": "date", "dob": "dob", "age_over_89": "age",
    "clinician_name": "name", "titled_name": "name", "patient_initials": "name", "person_name": "name",
    "facility": "facility", "address": "location", "zip": "location",
    "ssn": "identifier_b6", "mrn": "identifier_b6", "phone": "identifier_b6", "email": "identifier_b6",
    "serial": "identifier_b6", "insurance": "identifier_b6", "money": "identifier_b6", "workers_comp": "identifier_b6",
    "complaint_no": "identifier_b4", "ncr_cfn_rae": "identifier_b4", "ide": "identifier_b4", "eua": "identifier_b4",
    "clinical_trial": "identifier_b4", "udi": "identifier_b4", "udi_word": "identifier_b4", "bsc_tw": "identifier_b4",
    "cms": "identifier_b4", "uf_medsun_report": "identifier_b4", "lot": "lot",
    "mfr_rep": "commercial", "supplier": "commercial", "production_stats": "commercial", "rate": "commercial",
    "percent_rate": "commercial", "trade_secret_paragraph": "commercial",
    "profanity": "profanity", "boilerplate": "boilerplate",
}


def _cite(*keys: str) -> str:
    seen = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    return "\n".join(f"[{k}] {SOP_REFS[k]}" for k in seen)


def _norm(s: str | None) -> str:
    s = _TOKEN.sub(lambda m: f"(b)({m.group(1)})", s or "")
    s = re.sub(r"\(b\)\((\d)\)(?=\S)", r"(b)(\1) ", s)            # "(b)(6)2026" -> "(b)(6) 2026"
    s = re.sub(r"(\d{4})\s*-?\s*\(b\)\((\d)\)", r"(b)(\2) \1", s)  # "2026-(b)(6)" -> "(b)(6) 2026"
    s = _PUNCT.sub("", s)
    return _WS.sub(" ", s).strip().lower()


def _tokens(s: str) -> list[str]:
    return _norm(s).split(" ")


@dataclass
class RowResult:
    record_id: str
    status: str            # MATCH | FORMAT_ONLY | AUTO_MISSED | AUTO_EXTRA | BOTH | HUMAN_INCONSISTENT
    reason: str
    sop_reference: str     # where in the SOP the applicable rule is, and what it says
    auto_only: str         # text the automation redacted but the editor kept
    human_only: str        # text the editor redacted but the automation kept
    original: str
    human_redacted: str
    auto_redacted: str
    findings: str


def _redacted_spans(original: str, redacted: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Align the original with a redacted version.

    Returns (redactions, edits): `redactions` are (original words, replacement) pairs where the
    replacement contains a (b)(n) token; `edits` are other wording changes (typo fixes,
    punctuation, added words) that are not redactions.
    """
    to, tr = _tokens(original), _tokens(redacted)
    sm = difflib.SequenceMatcher(None, to, tr, autojunk=False)
    redactions, edits = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        src, dst = " ".join(to[i1:i2]), " ".join(tr[j1:j2])
        if "(b)(" in dst:
            redactions.append((src, dst))
        elif src.strip("-*") or dst.strip("-*"):
            edits.append(f"'{src}' -> '{dst}'")
    return redactions, edits


_FILLER_ONLY = re.compile(r"[\s,.;:()-]*(?:(?:on|in|at|the|of|and|dated|since|from|to|by)[\s,.;:()-]*)*", re.I)
_FACILITY_WORD = re.compile(r"\b(hospital|medical|center|clinic|dental|health|surgery|surgical|nursing|university|office)\b", re.I)


def _classify_missed(span: str) -> tuple[str, str]:
    if _DATE_LIKE.search(span):
        return f"date format not recognised by automation: '{span}'", "date"
    if re.fullmatch(rf"(?:{R._MONTHS}\.?\s*)?\d{{0,2}}\s*(?:of\s+)?(?:19|20)?\d{{0,4}}", span, re.I) and re.search(r"[a-z]", span, re.I):
        return f"partial date (month/day without full date) redacted by editor: '{span}'", "date"
    if re.fullmatch(r"(19|20)\d{2}", span):
        return f"editor removed the year '{span}' (SOP keeps the year unless patient >89)", "year"
    if re.search(r"\d", span):
        return f"editor redacted identifier/number not covered by a rule: '{span}'", "identifier_b6"
    if _FACILITY_WORD.search(span):
        return f"editor redacted a facility name that no rule detects: '{span}'", "facility"
    return f"editor redacted free text (name/location?) that no rule detects: '{span}'", "name"


def _explain(original: str, human: str, auto: str, findings) -> tuple[str, str, str, str, str]:
    """Return (status, reason, sop_reference, auto_only, human_only)."""
    if human.strip() == auto.strip():
        return "MATCH", "identical", "", "", ""
    if _norm(human) == _norm(auto):
        return ("FORMAT_ONLY", "same redactions; differs only in (b)(6) casing/spacing/punctuation",
                _cite("token_format"), "", "")

    h_red, h_edits = _redacted_spans(original, human)
    a_red, _ = _redacted_spans(original, auto)
    h_src = Counter(s for s, _ in h_red)
    a_src = Counter(s for s, _ in a_red)
    human_only = list((h_src - a_src).elements())     # editor redacted, automation kept
    auto_only = list((a_src - h_src).elements())      # automation redacted, editor kept

    reasons: list[str] = []
    refs: list[str] = []

    # Same redaction, but one side swallowed a filler word ("On 6/15/26" vs "6/15/26"),
    # usually because the editor typed "On(b)(6) 2026" without a space. Not a real difference.
    for h_span in list(human_only):
        for a_span in list(auto_only):
            short, long_ = sorted((h_span, a_span), key=len)
            if not short or short not in long_:
                continue
            rest = long_.replace(short, " ").strip(" ,.;:()-")
            if _FILLER_ONLY.fullmatch(rest) or re.fullmatch(r"[A-Za-z]+", rest):
                human_only.remove(h_span)
                auto_only.remove(a_span)
                h_edits.append(f"'{h_span}' vs '{a_span}' (spacing around the (b)(6) token)")
                break
            # editor removed the whole facility incl. its type ("Kameda Medical Center" -> "(b)(6)")
            if short == a_span and _FACILITY_WORD.search(rest) and any(f.rule == "facility" for f in findings):
                human_only.remove(h_span)
                auto_only.remove(a_span)
                reasons.append(f"editor removed the facility type as well ('{h_span}'); SOP keeps it: "
                               "'Shady Grove Hospital' -> '(B)(6) Hospital'")
                refs.append("facility")
                break
            # editor redacted only part of a date ("MARCH 25" -> "(b)(6) 25": month hidden, day kept)
            if short == h_span and re.fullmatch(rf"{R._MONTHS}\.?", h_span, re.I) and re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)?", rest):
                human_only.remove(h_span)
                auto_only.remove(a_span)
                reasons.append(f"editor redacted only the month of '{a_span}' and left the day '{rest}' visible; "
                               "SOP says redact day AND month, keep only the year")
                refs.append("date")
                break

    def _core(d: str) -> tuple:  # only the tokens and years matter, not filler words like "on"
        return tuple(re.findall(r"\(b\)\(\d\)|(?:19|20)\d{2}", d))

    # same source span, different replacement (e.g. year kept vs dropped)
    for src in (h_src & a_src):
        h_rep = {d for s, d in h_red if s == src}
        a_rep = {d for s, d in a_red if s == src}
        if {_core(d) for d in h_rep} == {_core(d) for d in a_rep}:
            if h_rep != a_rep:
                h_edits.append(f"'{src}': editor wrote {sorted(h_rep)} vs {sorted(a_rep)}")
        else:
            if any(re.search(r"(19|20)\d{2}", d) for d in a_rep) and not any(re.search(r"(19|20)\d{2}", d) for d in h_rep):
                reasons.append(f"editor dropped the year for '{src}' (SOP keeps the year unless patient >89)")
                refs.append("year")
            else:
                reasons.append(f"'{src}': editor wrote {sorted(h_rep)}, automation wrote {sorted(a_rep)}")
                refs.append("date" if _DATE_LIKE.search(src) else "token_format")

    for span in human_only:
        text, key = _classify_missed(span)
        reasons.append(text)
        refs.append(key)
    for span in auto_only:
        kinds = sorted({f.rule for f in findings if _norm(f.original) and (_norm(f.original) in span or span in _norm(f.original))})
        if _DATE_LIKE.search(span) or kinds == ["date"] or kinds == ["partial_date"]:
            reasons.append(f"automation redacted date '{span}' but editor left it "
                           "(editor inconsistency: same date type is redacted elsewhere)")
            refs.append("date")
        elif kinds:
            reasons.append(f"automation rule {kinds} fired on '{span}' but editor kept it (probable false positive)")
            refs.extend(_RULE_TOPIC.get(k, "none") for k in kinds)
        else:
            reasons.append(f"automation redacted '{span}' but editor kept it")
            refs.append("none")

    if human_only and auto_only:
        status = "BOTH"
    elif human_only:
        status = "AUTO_MISSED"
    elif auto_only:
        status = "AUTO_EXTRA"
    elif reasons:
        status = "HUMAN_INCONSISTENT"
    else:
        status = "FORMAT_ONLY"
        reasons.append("same redactions; editor only reworded/re-punctuated the text")

    if h_edits:
        reasons.append("editor wording edits (not redactions): " + ", ".join(h_edits[:5]))
        refs.append("wording")

    return status, "; ".join(reasons), _cite(*refs), " | ".join(auto_only), " | ".join(human_only)


def load_rows(path: Path, orig_col: str, red_col: str, id_col: str) -> list[dict]:
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        ws = openpyxl.load_workbook(path, read_only=True).active
        it = ws.iter_rows(values_only=True)
        header = [str(h) for h in next(it)]
        rows = [dict(zip(header, r)) for r in it]
    else:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    if rows:
        # tolerate header variants (REDACTED_NARRATIVES, lower case, spaces)
        norm = {re.sub(r"[^a-z0-9]", "", k.lower()).rstrip("s"): k for k in rows[0]}
        for c in (orig_col, red_col, id_col):
            if c not in rows[0]:
                actual = norm.get(re.sub(r"[^a-z0-9]", "", c.lower()).rstrip("s"))
                if actual is None:
                    sys.exit(f"column {c!r} not found; have {list(rows[0])}")
                for r in rows:
                    r[c] = r.pop(actual)
    return [r for r in rows if r.get(orig_col)]


def compare(rows: list[dict], orig_col: str, red_col: str, id_col: str, section: str = "B5") -> list[RowResult]:
    out = []
    for r in rows:
        orig, human = str(r[orig_col]), str(r.get(red_col) or "")
        res = redact_section(section, orig)
        status, reason, ref, auto_only, human_only = _explain(orig, human, res.redacted, res.findings)
        out.append(RowResult(str(r.get(id_col, "")), status, reason, ref, auto_only, human_only,
                             orig, human, res.redacted,
                             "; ".join(f"{f.rule}:{f.original!r}->{f.replacement!r}" for f in res.findings)))
    return out


def summary(results: list[RowResult]) -> str:
    c = Counter(r.status for r in results)
    n = len(results)
    agree = c["MATCH"] + c["FORMAT_ONLY"]
    lines = [f"rows: {n}", f"agree with editor (exact or format-only): {agree} ({agree / n:.0%})"]
    for k in ("MATCH", "FORMAT_ONLY", "AUTO_MISSED", "AUTO_EXTRA", "BOTH", "HUMAN_INCONSISTENT"):
        lines.append(f"  {k:<19}{c[k]}")
    return "\n".join(lines)


def write_xlsx(results: list[RowResult], path: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "comparison"
    cols = list(RowResult.__dataclass_fields__)
    ws.append(cols)
    for r in results:
        ws.append([asdict(r)[c] for c in cols])
    from openpyxl.styles import Alignment, Font, PatternFill
    fills = {"MATCH": "C6EFCE", "FORMAT_ONLY": "E2EFDA", "AUTO_MISSED": "F8CBAD",
             "AUTO_EXTRA": "FFE699", "BOTH": "F4B084", "HUMAN_INCONSISTENT": "D9D2E9"}
    widths = {"record_id": 14, "status": 18, "reason": 60, "sop_reference": 70, "auto_only": 25,
              "human_only": 25, "original": 60, "human_redacted": 60, "auto_redacted": 60, "findings": 40}
    for i, c in enumerate(cols, 1):
        ws.cell(row=1, column=i).font = Font(bold=True)
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = widths.get(c, 20)
    status_col = cols.index("status") + 1
    for row in ws.iter_rows(min_row=2):
        fill = fills.get(str(row[status_col - 1].value))
        if fill:
            row[status_col - 1].fill = PatternFill("solid", fgColor=fill)
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions

    ws2 = wb.create_sheet("summary")
    for line in summary(results).splitlines():
        ws2.append([line.strip()])
    ws2.append([])
    ws2.append(["status", "meaning"])
    for k, v in {
        "MATCH": "identical output",
        "FORMAT_ONLY": "same spans redacted; only (b)(6) casing/spacing/punctuation or editor wording differs",
        "AUTO_MISSED": "editor redacted something the automation kept - see reason + sop_reference",
        "AUTO_EXTRA": "automation redacted something the editor kept - see reason + sop_reference",
        "BOTH": "both of the above in one narrative",
        "HUMAN_INCONSISTENT": "same span redacted, different replacement (e.g. year dropped)",
    }.items():
        ws2.append([k, v])
    ws2.column_dimensions["A"].width = 22
    ws2.column_dimensions["B"].width = 90

    ws3 = wb.create_sheet("sop_references")
    ws3.append(["key", "SOP reference (MEDICAL DEVICE REPORTING REDACTION PROCEDURES v4.0, 02/01/2025)"])
    for k, v in SOP_REFS.items():
        ws3.append([k, v])
    ws3.column_dimensions["A"].width = 16
    ws3.column_dimensions["B"].width = 120
    for row in ws3.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("compare_out.xlsx"))
    ap.add_argument("--orig-col", default="COMPANY_NARRATIVE")
    ap.add_argument("--redacted-col", default="REDACTED_NARRATIVE")
    ap.add_argument("--id-col", default="RECORD_ID")
    ap.add_argument("--section", default="B5")
    a = ap.parse_args(argv)
    results = compare(load_rows(a.input, a.orig_col, a.redacted_col, a.id_col), a.orig_col, a.redacted_col, a.id_col, a.section)
    if a.output.suffix.lower() == ".csv":
        with open(a.output, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(RowResult.__dataclass_fields__))
            w.writeheader()
            w.writerows(asdict(r) for r in results)
    else:
        write_xlsx(results, a.output)
    print(summary(results))
    print(f"written {a.output}")


if __name__ == "__main__":
    main()
