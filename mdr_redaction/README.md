# mdr_redaction — automated eMDR redaction & reportable detection

Prototype that automates the manual editor workflow described in the FDA/CDRH
SOP *"Medical Device Reporting Redaction – eMDR Editing & Redaction Process"*
(v4.0, 02/01/2025). Standalone package; stdlib only.

## What the SOP has editors do, and what this module does instead

| SOP step | Manual today | Module |
|---|---|---|
| 4. Triage inbox: Code Blue > Death > Injury > Voluntary > UF > IMP > Malfunction > Supplement | sort/page through a 300k-row grid | `triage.prioritize()` |
| 5.3 / 6.x Redact B5 with (B)(6)+(B)(4) | read & type codes | `redactor.redact_section("B5", …)` |
| 5.4 Redact D11/F11 serial numbers with (B)(6) | " | `redact_section("D11"/"F11", …)` |
| 5.5 / 5.6 Redact H11 trade secrets / CCI with (B)(4) | " + consult Trade Secrets folder | pattern rules + paragraph-level trade-secret detector (flags `needs_human_review`) |
| 7. Delete "See attached" etc. | " | `rules.BOILERPLATE` |
| 8. Replace profanity with "PROFANITY" | " | `rules.PROFANITY` |
| 9. Age > 89: redact full DOB, age → "90 years or older" | " | `redactor._redact_ages` / date year dropped |
| Identify Animal / Linking / Possible Linking / Code Blue | notice while reading, email STL | `reportables.detect()` → email subject + recipients |
| Editors' Reportable Log (Excel) | type into spreadsheet | `reportable_log.csv` |

Appendix 6 ((B)(6)) and Appendix 7 ((B)(4)) item lists are encoded in `rules.py`.

## Run

```bash
python -m mdr_redaction.cli mdr_redaction/sample_reports.json -o /tmp/mdr_out
python -m unittest mdr_redaction.tests.test_redaction
```

Input: JSON list of reports
```json
{"report_number": "1219913-2022-00282", "report_type": "MFR", "outcome": "IN",
 "days_in_inbox": 12, "code_blue": false,
 "sections": {"B5": "...", "D11": "...", "H11": "..."}}
```
Output: `redacted_reports.json` (redacted sections + per-finding audit trail),
`reportable_log.csv`, `emails.txt`.

## Known gaps (information not in the SOP)
- No eMDR / ESG connector — the SOP screenshots show a legacy web grid; input is file-based here.
- Code Blue criteria live in FDA Doc 06254 (not provided); `CODE_BLUE_TERMS` is a placeholder.
- The "Editors' Guideline / Trade Secrets folder" is not provided; paragraph-level (B)(4) uses a keyword heuristic and marks the report for human review. An LLM classifier can be plugged in at `redactor._redact_trade_secret_paragraphs`.
- Names without a title (e.g. bare "John Smith") need an NER model (the main app already uses HuggingFace/medspaCy).
- Linking today is text-only (quoted report numbers / "Per MW"). Cross-report matching on device + event date + manufacturer is a natural next step.
