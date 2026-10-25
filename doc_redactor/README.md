# Document Redactor

Upload a PDF, Word (.docx), Excel (.xlsx/.xlsm) or text/CSV file and get back a redacted copy.
What gets redacted is controlled by a YAML rules file: `config/rules.yaml` (editable in the web page too).

- PDFs: matched text is **removed** from the file (true redaction), not just covered with a box.
- Word: body, tables, headers/footers. Excel: every text/date cell and comments (formulas left as-is, with a warning).
- Scanned (image-only) PDF pages cannot be read without OCR; they are flagged with a warning.
- Old `.doc` / `.xls` files: save as `.docx` / `.xlsx` first.

## Rule types (see comments in `config/rules.yaml`)

| type | what it does | example |
|---|---|---|
| `builtin` | ready-made pattern: ssn, email, phone, date, credit_card, ip_address, zip_code, url | `{name: ssn, type: builtin}` |
| `keywords` | list of words/phrases | `values: ["Acme Pharma"]` |
| `label` | value after a label | `labels: ["Patient Name"]` redacts `Patient Name: John Doe` |
| `regex` | your own regular expression | `pattern: '\bLOT-([A-Z0-9]+)'`, `group: 1` |

Options per rule: `enabled`, `replacement`, `case_sensitive`, `group`. Global `settings`: `replacement`, `mask_char`, `pdf_label`, `scrub_metadata`.

## How to run on your computer

You need Python 3.11+ (https://www.python.org/downloads/ - on Windows tick "Add Python to PATH").

**Mac**
1. Open Terminal, go to the folder that contains `doc_redactor` (the unzipped project folder):
   `cd ~/Downloads/pharmawatch-dashboard`
2. `python3 -m venv .venv`
3. `source .venv/bin/activate`
4. `pip install -r doc_redactor/requirements.txt`
5. `python -m doc_redactor.web_app`
6. Open http://127.0.0.1:5050 in your browser. Stop with Ctrl+C.

**Windows** (Command Prompt)
1. `cd %USERPROFILE%\Downloads\pharmawatch-dashboard`
2. `py -m venv .venv`
3. `.venv\Scripts\activate`
4. `pip install -r doc_redactor\requirements.txt`
5. `python -m doc_redactor.web_app`
6. Open http://127.0.0.1:5050.

**Command line (no browser)**
`python -m doc_redactor.cli myfile.pdf other.xlsx --rules doc_redactor/config/rules.yaml --out-dir redacted`
writes `redacted/myfile_redacted.pdf` plus a `.report.json` listing every redaction.

Tests: `pip install pytest` then `python -m pytest doc_redactor/tests`.

## Deploy (Render)

Web service from this repo, root of repo:
- Build: `pip install -r doc_redactor/requirements.txt`
- Start: `gunicorn doc_redactor.web_app:app --workers 1 --threads 4 --timeout 120`
- Env: `PYTHON_VERSION=3.11.9` (optional `REDACTOR_MAX_MB`, default 20)
