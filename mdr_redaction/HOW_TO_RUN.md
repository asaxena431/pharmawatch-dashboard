# How to run mdr_redaction

Requirements: Python 3.10+ (no third-party packages for the CLI; the review UI needs Flask).

```bash
unzip mdr_redaction.zip && cd mdr_redaction_bundle      # folder containing mdr_redaction/
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install flask                                        # only for the review UI

# 1. batch run on the sample data (redaction + triage + reportables + linking)
python -m mdr_redaction.cli mdr_redaction/sample_reports.json -o out
#    -> out/redacted_reports.json, out/reportable_log.csv, out/emails.txt, out/link_candidates.json

# 2. human review UI, then open http://127.0.0.1:5051
python -m mdr_redaction.review_app mdr_redaction/sample_reports.json

# 3. tests
python -m unittest discover -s mdr_redaction/tests -t .
```

Your own data: a JSON list of reports in the format of `mdr_redaction/sample_reports.json`
(`report_number`, `report_type` MFR|UF|IMP|VOL|MEDSUN, `outcome` D|IN|M, `manufacturer`,
`sections` {B5, D11/F11, H11}; optional `lot`, `serial`, `model`, `brand_name`, `udi`, `event_date`
for cross-report linking).

Optional: `HUGGINGFACE_API_KEY=...` enables model-based name detection; `MDR_NER_BACKEND=heuristic|spacy|hf` forces a backend.
See `mdr_redaction/README.md` for the SOP-to-code mapping and known gaps.
