# cvm-aer — Form FDA 1932 / 1932a → VICH GL42 XML

Command-line converter for FDA CVM veterinary adverse event reports. It reads a
filled **Form FDA 1932** (static, fillable or scanned) or **Form FDA 1932a**
(dynamic XFA) PDF and writes the **VICH GL42** adverse event report as XML, in
one of two shapes:

| `--format` | what it is | schema |
|---|---|---|
| `gl42` (default) | compact XML, one element per GL42 data element — for review and downstream systems | none (GL42 is a data-element guideline, VICH published no XSD) |
| `vich-hl7` | CVM's HL7 v3 submission message (`MCCI_IN200100UV01`, VICH GL35), the form PDF and every attachment embedded base64 | FDA's published VICH schemas — `python -m cvm_aer validate` |

No web server, no GUI — a single Python package (`cvm_aer`) with a CLI and an
optional folder service.

```
cvm-aer-converter/
  cvm_aer/                  the package
    cli.py                  command line (python -m cvm_aer ...)
    pipeline.py             PDF -> VeterinaryReport -> GL42 XML
    xfa_1932a.py            dynamic Form 1932a: reads the XFA dataset
    vet_extract.py          static Form 1932: AcroForm or PaddleOCR + field template
    form_1932.py            Form 1932 field map, sample filler
    form_geometry.py        rectangle / checkbox helpers for the template reader
    ocr.py                  PaddleOCR + pypdfium2 wrapper
    models.py               VeterinaryReport and its parts
    gl42.py                 compact GL42 serialiser
    vich_hl7.py             HL7 v3 (GL35) message serialiser
    validate.py             schema validation of vich-hl7 messages
    service.py              folder service: ZIP in -> XML out
    templates/fda_1932_2023.json
  tests/
  cvm-aer-service.ini.sample
  requirements.txt          core dependencies
  requirements-ocr.txt      PaddleOCR, only for image-only scans
```

## Install

Linux:
```bash
cd cvm-aer-converter
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-ocr.txt      # only if you have scanned, image-only forms (~1 GB)
```

Windows (cmd):
```bat
cd cvm-aer-converter
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ocr.txt
```

Nothing is installed into Python: run `python -m cvm_aer ...` from this folder.

## Convert one report

```bash
# 1932a (dynamic XFA) or 1932 (static) — the layout is detected
python -m cvm_aer convert case.pdf -o out/case_gl42.xml

# with the case's supporting documents (recorded in the report)
python -m cvm_aer convert case.pdf --attach "lab report.pdf" --attach photo.jpg -o out.xml

# CVM's HL7 v3 submission message, form + attachments embedded, then schema-check it
python -m cvm_aer convert case.pdf --format vich-hl7 --attach "lab report.pdf" -o out.xml
python -m cvm_aer validate out.xml

# also dump the parsed report as JSON
python -m cvm_aer convert case.pdf -o out.xml --json out.json

# see what was read off the form
python -m cvm_aer ocr case.pdf
```

Options: `--layout auto|1932|1932a`, `--engine text-layer|auto|paddleocr`
(`text-layer` reads a fillable PDF's AcroForm directly; use `auto` or
`paddleocr` for a printed/scanned copy, ~30 s per page), `--dpi`, `--lang`.

Exit codes: `0` ok, `2` bad input (file not found, OCR failure, wrong option).

## Schema validation

`python -m cvm_aer validate out.xml [more.xml ...]` checks a `vich-hl7` message
against FDA's VICH schema set (entry `multicacheschemas/MCCI_IN200100UV01.xsd`).
The schemas are HL7-licensed and not shipped in this folder: on first use they
are downloaded from `accessdata.fda.gov/icsr/schema/cvm/schemas/vich/` into
`~/.cache/cvm_aer/vich-schemas` (63 files, ~1.3 MB); a `vich-schemas/` folder
next to `cvm_aer/` or `--schema-dir <path>` is used instead when present, so an
offline box can be given a copy. The check itself runs through `xmllint`
(`apt install libxml2-utils`) or `pip install lxml` — exit `0` valid, `1`
complaints listed, `2` no validator installed. The compact `gl42` output has no
schema to validate against.

## Sample form

```bash
python -m cvm_aer samples --output-dir samples
python -m cvm_aer convert samples/FDA-1932_cvm_veterinary.pdf -o out/cvm_veterinary_gl42.xml
```
`samples` downloads the blank Form FDA 1932 from fda.gov once (cached under
`~/.cache/cvm_aer`) and fills it with a synthetic case.

## Folder service

One ZIP = one case: the 1932/1932a PDF inside becomes the message, the other
files are its supporting documents. The XML goes to `outbound`, the ZIP moves to
`processed`, or on failure to `error` with an e-mail note (SMTP, Outlook or a
`.eml` drop folder).

```bash
cp cvm-aer-service.ini.sample cvm-aer-service.ini      # edit the folders
python -m cvm_aer --once                  # one sweep
python -m cvm_aer                         # keep watching
```
The service is the default command (`python -m cvm_aer service ...` also works). The ini is
`cvm-aer-service.ini` in the current folder unless you pass `--config <path>`.
`--inbound/--outbound/--processed/--error` override the folders in the ini, `--format gl42|vich-hl7`
the `[conversion] format`.

## Tests

```bash
pip install pytest ruff
python -m pytest
ruff check .
```
