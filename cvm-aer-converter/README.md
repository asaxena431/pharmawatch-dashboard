# cvm-aer — Form FDA 1932 / 1932a → CVM VICH HL7 message

Command-line converter for FDA CVM veterinary adverse event reports. It reads a
filled **Form FDA 1932** (static, fillable or scanned) or **Form FDA 1932a**
(dynamic XFA) PDF and writes CVM's electronic submission message, **`vich-hl7`**:
the VICH GL42 data elements carried in HL7 v3 (`MCCI_IN200100UV01`, VICH GL35),
with the form PDF and every supporting document embedded base64.

Every message is **validated automatically** against FDA's published VICH
schemas before it is written; a message the schemas reject is an error, never an
output.

No web server, no GUI — a single Python package (`cvm_aer`) with a CLI and an
optional folder service.

```
cvm-aer-converter/
  cvm_aer/                  the package
    cli.py                  command line (python -m cvm_aer ...)
    pipeline.py             PDF -> VeterinaryReport -> vich-hl7, validated
    xfa_1932a.py            dynamic Form 1932a: reads the XFA dataset
    vet_extract.py          static Form 1932: AcroForm or PaddleOCR + field template
    form_1932.py            Form 1932 field map, sample filler
    form_geometry.py        rectangle / checkbox helpers for the template reader
    ocr.py                  PaddleOCR + pypdfium2 wrapper
    models.py               VeterinaryReport and its parts
    vich_hl7.py             HL7 v3 (GL35) message serialiser
    validate.py             FDA VICH schema validation (fetch, cache, check)
    service.py              folder service: ZIP in -> XML out
    templates/fda_1932_2023.json
  tests/
  cvm-aer-service.ini.sample
  requirements.txt          core dependencies (pypdf, pypdfium2, Pillow, numpy, lxml)
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
# 1932a (dynamic XFA) or 1932 (static) — the layout is detected; the message is
# schema-validated before it is written
python -m cvm_aer convert case.pdf -o out/case.xml

# with the case's supporting documents (embedded in the message with the form)
python -m cvm_aer convert case.pdf --attach "lab report.pdf" --attach photo.jpg -o out.xml

# re-check a message that is already on disk
python -m cvm_aer validate out.xml

# also dump the parsed report as JSON
python -m cvm_aer convert case.pdf -o out.xml --json out.json

# see what was read off the form
python -m cvm_aer ocr case.pdf
```

Options: `--layout auto|1932|1932a`, `--engine text-layer|auto|paddleocr`
(`text-layer` reads a fillable PDF's AcroForm directly; use `auto` or
`paddleocr` for a printed/scanned copy, ~30 s per page), `--dpi`, `--lang`.

Exit codes: `0` ok, `2` bad input (file not found, OCR failure, wrong option),
`3` the generated message does not validate (the complaints are printed, nothing
is written). `--no-validate` writes the message anyway, for diagnosing such a case.

## Schema validation

Every `convert` and every case the folder service handles is checked against
FDA's VICH schema set (entry `multicacheschemas/MCCI_IN200100UV01.xsd`) with
`lxml`; `python -m cvm_aer validate out.xml [more.xml ...]` re-checks messages
already on disk (exit `0` valid, `1` complaints listed).

The schemas are HL7-licensed and not shipped in this folder: on first use they
are downloaded from `accessdata.fda.gov/icsr/schema/cvm/schemas/vich/` into
`~/.cache/cvm_aer/vich-schemas` (63 files, ~1.3 MB) — so the first run needs
internet access, or give the box a copy: a `vich-schemas/` folder next to
`cvm_aer/`, the `CVM_AER_SCHEMA_DIR` environment variable, or `--schema-dir`
on `validate`.

## Sample form

```bash
python -m cvm_aer samples --output-dir samples
python -m cvm_aer convert samples/FDA-1932_cvm_veterinary.pdf -o out/cvm_veterinary.xml
```
`samples` downloads the blank Form FDA 1932 from fda.gov once (cached under
`~/.cache/cvm_aer`) and fills it with a synthetic case.

## Folder service

One ZIP = one case: the 1932/1932a PDF inside becomes the message, the other
files are its supporting documents, embedded. The message is schema-validated,
then written to `outbound` and the ZIP moves to `processed`; if the case cannot
be read or its message does not validate, the ZIP moves to `error` with an
e-mail note (SMTP, Outlook or a `.eml` drop folder) and nothing is written to
`outbound`.

```bash
cp cvm-aer-service.ini.sample cvm-aer-service.ini      # edit the folders
python -m cvm_aer --once                  # one sweep
python -m cvm_aer                         # keep watching
```
The service is the default command (`python -m cvm_aer service ...` also works). The ini is
`cvm-aer-service.ini` in the current folder unless you pass `--config <path>`.
`--inbound/--outbound/--processed/--error` override the folders in the ini.

## Tests

```bash
pip install pytest ruff
python -m pytest
ruff check .
```
