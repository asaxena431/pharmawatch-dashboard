"""
Batch processor: reads a JSON list of MDR reports, triages, redacts, detects
reportables and writes:
  <out>/redacted_reports.json    redacted sections + per-finding audit trail
  <out>/reportable_log.csv       Editors' Reportable Log rows (Appendix 12)
  <out>/emails.txt               STL email subjects (Appendices 10-11)

Usage:
  python -m mdr_redaction.cli mdr_redaction/sample_reports.json -o /tmp/mdr_out
"""
from __future__ import annotations

import argparse
import csv
import json
import os

from .redactor import redact_report
from .reportables import detect_dicts
from .triage import prioritize


def process(reports: list[dict]) -> tuple[list[dict], list[dict]]:
    redacted, reportables = [], []
    for r in prioritize(reports):
        reportables.extend(detect_dicts(r))
        redacted.append(redact_report(r))
    return redacted, reportables


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Automated eMDR redaction & reportable detection")
    ap.add_argument("input", help="JSON file: list of reports")
    ap.add_argument("-o", "--out", default="mdr_out")
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8") as fh:
        reports = json.load(fh)
    redacted, reportables = process(reports)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "redacted_reports.json"), "w", encoding="utf-8") as fh:
        json.dump(redacted, fh, indent=2)
    with open(os.path.join(args.out, "reportable_log.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["type", "report_numbers"])
        w.writeheader()
        w.writerows(r["log_row"] for r in reportables)
    with open(os.path.join(args.out, "emails.txt"), "w", encoding="utf-8") as fh:
        for r in reportables:
            fh.write(f"To: {'; '.join(r['email_to'])}\nSubject: {r['email_subject']}\nEvidence: {r['evidence']}\n\n")

    print(f"{len(redacted)} reports processed in priority order:")
    for r in redacted:
        n = len(r["findings"])
        flag = " [REVIEW]" if r["needs_human_review"] else ""
        print(f"  {r['triage_category']:<12} {r['report_number']:<28} {n:>3} redactions{flag}")
    print(f"{len(reportables)} reportables -> {args.out}/reportable_log.csv, emails.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
