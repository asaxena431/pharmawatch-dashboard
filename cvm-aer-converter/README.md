# cvm-aer — Form FDA 1932 / 1932a → VICH GL42 XML

Command-line converter for FDA CVM veterinary adverse event reports. It reads a
filled **Form FDA 1932** (static, fillable or scanned) or **Form FDA 1932a**
(dynamic XFA) PDF and writes a **VICH GL42** adverse event report XML.
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
    gl42.py                 GL42 serialiser
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

Optionally `pip install .` installs a `cvm-aer` command; otherwise run
`python -m cvm_aer` from this folder.

## Convert one report

```bash
# 1932a (dynamic XFA) or 1932 (static) — the layout is detected
python -m cvm_aer convert case.pdf -o out/case_gl42.xml

# with the case's supporting documents (recorded in the report)
python -m cvm_aer convert case.pdf --attach "lab report.pdf" --attach photo.jpg -o out.xml

# also dump the parsed report as JSON
python -m cvm_aer convert case.pdf -o out.xml --json out.json

# see what was read off the form
python -m cvm_aer ocr case.pdf
```

Options: `--layout auto|1932|1932a`, `--engine text-layer|auto|paddleocr`
(`text-layer` reads a fillable PDF's AcroForm directly; use `auto` or
`paddleocr` for a printed/scanned copy, ~30 s per page), `--dpi`, `--lang`.
The only output format is `gl42`.

Exit codes: `0` ok, `2` bad input (file not found, OCR failure, wrong option).

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
python -m cvm_aer service --config cvm-aer-service.ini --once     # one sweep
python -m cvm_aer service --config cvm-aer-service.ini            # keep watching
```
`--inbound/--outbound/--processed/--error` override the folders in the ini.

## Tests

```bash
pip install pytest ruff
python -m pytest
ruff check .
```
