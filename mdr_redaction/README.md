# mdr_redaction — automated eMDR redaction & reportable detection

Prototype that automates the manual editor workflow described in the FDA/CDRH
SOP *"Medical Device Reporting Redaction – eMDR Editing & Redaction Process"*
(v4.0, 02/01/2025). Standalone package; stdlib only (the optional review UI uses Flask,
which the main app already depends on).

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
| Untitled person names (Appendix 6: patient / reporter / personnel) | notice while reading | `ner.find_person_names()` — HuggingFace NER if `HUGGINGFACE_API_KEY` set, spaCy if installed, else role-cue heuristic |
| Linking across reports (same event reported by UF and MFR) | only if the editor happens to see a quoted number | `linking.find_links()` — batch-wide: quoted numbers → confirmed; same manufacturer + lot/serial/UDI/model + event date ±30d + narrative similarity → scored candidate; "Multiple Linking" groups |
| Trainee → Inbox → STL QC → Complete | click through eMDR, email manual log | `review_app` — inbox in priority order, original vs. redacted side-by-side, accept/reject each finding, edit text, Complete / Return |

Appendix 6 ((B)(6)) and Appendix 7 ((B)(4)) item lists are encoded in `rules.py`.

## Run

```bash
python -m mdr_redaction.cli mdr_redaction/sample_reports.json -o /tmp/mdr_out
python -m unittest discover -s mdr_redaction/tests -t .

# human review UI (http://127.0.0.1:5051)
python -m mdr_redaction.review_app mdr_redaction/sample_reports.json
```

NER backend: auto (`hf` → `spacy` → `heuristic`) or force with `MDR_NER_BACKEND=heuristic|spacy|hf`;
HF model defaults to `dslim/bert-base-NER` (`MDR_HF_NER_MODEL`).

Input: JSON list of reports
```json
{"report_number": "1219913-2022-00282", "report_type": "MFR", "outcome": "IN",
 "manufacturer": "...", "days_in_inbox": 12, "code_blue": false,
 "brand_name": "...", "model": "...", "lot": "...", "serial": "...", "udi": "...", "event_date": "2024-03-11",
 "sections": {"B5": "...", "D11": "...", "H11": "..."}}
```
`brand_name`…`event_date` are optional and only used for structured linking.

Output: `redacted_reports.json` (redacted sections + per-finding audit trail),
`reportable_log.csv`, `emails.txt` (reportables, possible-linking candidates, Multiple Linking),
`link_candidates.json`. The review UI writes `<input>.decisions.json`
(approved text, rejected findings, status, reviewer note per report).

## Known gaps (information not in the SOP)
- No eMDR / ESG connector — the SOP screenshots show a legacy web grid; input is file-based here.
- Code Blue criteria live in FDA Doc 06254 (not provided); `CODE_BLUE_TERMS` is a placeholder.
- The "Editors' Guideline / Trade Secrets folder" is not provided; paragraph-level (B)(4) uses a keyword heuristic and marks the report for human review. An LLM classifier can be plugged in at `redactor._redact_trade_secret_paragraphs`.
- Without an NER model/API key the name heuristic only catches Title-Case names next to a role cue or credentials; ALL-CAPS narratives need the `hf`/`spacy` backend.
- Structured linking needs the structured MedWatch fields (lot, serial, model, event date) in the input; with narrative only, it falls back to quoted numbers / "Per MW" wording. Linking weights in `linking.score_pair` are untuned guesses until real linked pairs are available.
