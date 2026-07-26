# medwatch_ocr — FDA 3500A (MedWatch) OCR → E2B(R2) / MDR XML

Reads a filled FDA Form 3500A PDF with **PaddleOCR** and emits a regulatory XML
message:

| Sample | Center / stage | Output |
| --- | --- | --- |
| `FDA-3500A_cder_premarket.pdf` | CDER, premarket (IND safety report, drug) | ICH **E2B(R2)** ICSR (`<ichicsr>`) |
| `FDA-3500A_cdrh_postmarket.pdf` | CDRH, postmarket (manufacturer MDR, device) | FDA **MDR** XML (`<mdrReports>`) |

## Sample inputs are the real FDA form

`official_form.py` downloads the genuine fillable **Form FDA-3500A MedWatch
(09/2025)** (`fda.gov/media/69876`, OMB 0910-0291), caches it under
`~/.cache/medwatch_ocr/`, and fills its AcroForm fields with synthetic case
data. The OCR input is therefore the real 9-page boxed form — official cells,
checkboxes, dropdowns and pagination — not a facsimile. Checkbox "on" states are
looked up per field (the form mixes `/1` and `/Yes`).

The older flat `label: value` facsimile (`samples.py`) is still available via
`--facsimile` and is handled by the line parser.

All data is synthetic; no real patients.

## Install

```bash
pip install -r requirements.txt              # flask, reportlab, pypdfium2, pypdf
pip install -r requirements-medwatch.txt     # paddleocr + paddlepaddle (~1 GB)
```

Without the OCR extras everything still runs with `--engine text-layer`
(embedded PDF text) or `--engine auto` (PaddleOCR, falling back to the text
layer).

## CLI

```bash
# 1. fill the genuine FDA 3500A form with both sample cases
python -m medwatch_ocr.cli samples --output-dir samples
python -m medwatch_ocr.cli samples --output-dir samples --facsimile   # flat layout instead

# 2. CDER premarket drug report -> E2B(R2) ICSR
python -m medwatch_ocr.cli convert samples/FDA-3500A_cder_premarket.pdf \
    --center CDER --stage premarket --engine paddleocr \
    -o out/cder_premarket_e2b_r2.xml --json out/cder_premarket_fields.json

# 3. CDRH postmarket device report -> FDA MDR XML
python -m medwatch_ocr.cli convert samples/FDA-3500A_cdrh_postmarket.pdf \
    --center CDRH --stage postmarket --engine paddleocr \
    -o out/cdrh_postmarket_mdr.xml

# raw OCR text only
python -m medwatch_ocr.cli ocr samples/FDA-3500A_cdrh_postmarket.pdf --engine paddleocr

# everything at once (samples + both conversions)
python -m medwatch_ocr.cli demo --output-dir out --engine paddleocr
```

`--layout` picks the input geometry — `official` (the FDA form template), `flat`
(label/value facsimile) or `auto`, the default, which detects the official form
from its AcroForm fields or its 9-page letter-size shape.

`--center` / `--stage` / `--format` are optional: the center is inferred from
the form content (device blocks ⇒ CDRH) and the output format defaults to
E2B(R2) for CDER and MDR for CDRH. Either format can be forced with
`--format e2b-r2|mdr`.

## Flask demo GUI

The GUI calls the same `medwatch_ocr.pipeline` functions as the CLI.

```bash
# mounted on the existing PharmaWatch app
python app.py            # -> http://localhost:5050/medwatch

# or standalone
python -m medwatch_ocr.web   # -> http://localhost:5060/medwatch
```

The page lets you pick either sample (or upload your own 3500A PDF), choose the
OCR engine and output format, and shows the generated XML, the parsed 3500A
fields and the raw OCR text, with a download button.

API: `POST /api/medwatch/convert` with `sample=cder_premarket|cdrh_postmarket`
or a `pdf` file part, plus optional `center`, `stage`, `format`, `engine`,
`dpi`, `layout`, `facsimile`. Returns `{summary, xml, fields, ocr_text}`.
`GET /medwatch/sample/<name>` serves the filled official form
(`?layout=facsimile` for the flat one).

## How the extraction works

There are two extraction paths behind one `pipeline.convert_pdf()`; the layout is
detected automatically.

### Official form (template-guided, `form_extract.py`)

1. `templates/fda_3500a_2025.json` holds the page, name, type, rectangle and
   checked-state of all 353 widgets of the blank form, generated from the
   published PDF. Pushbuttons (Reset Form) are excluded so they can't be read as
   checkboxes.
2. Each page is rasterised (pdfium form environment initialised, otherwise
   filled values are not drawn) and run through PP-OCR to get word boxes.
3. Every word is assigned to the field rectangle it overlaps most (≥50% of the
   word's area), so labels above/left of a cell never bleed into its value.
4. Checkbox state is read from pixels: the box is cropped, inset by 20% to drop
   its border, and counts as marked above 5% dark ink.
5. Values map onto `MedWatchReport` by FDA field name (`patID`, `advEvDesc`,
   `brandName`, `eventMal`, …), which is deterministic — no label matching.

A fillable (not yet printed/scanned) official form can also be read directly
from its AcroForm values with `--engine text-layer`, which is what the test
suite uses.

Round-trip check over both samples with real PaddleOCR — 109 text fields and 36
checkboxes, all matching what was written into the form:

```bash
python scripts/verify_official_roundtrip.py
```

### Flat facsimile (line parser)

1. `ocr.py` — pages are rasterised with pypdfium2 (200 dpi default) and run
   through PP-OCR detection + recognition; recognised boxes are regrouped into
   reading-order lines from their bounding boxes. Document unwarping and
   orientation classification are disabled (they can crop text at the page
   edge), and oneDNN kernels are disabled because they abort on some CPU
   builds of paddlepaddle 3.x.
2. `parser.py` — each line is split into `label: value`, the label is
   normalised (item numbers dropped, OCR `:`/`;` confusion tolerated) and
   matched against a field registry with fuzzy matching, using the block letter
   (`A`…`H`) to disambiguate labels that repeat across blocks (e.g.
   "Manufacturer Name" in D.3 vs G.1). Free-text blocks absorb continuation
   lines; repeated page headers/footers are dropped fuzzily.
3. `models.py` — the parsed values populate a `MedWatchReport` dataclass that
   mirrors the form blocks.
4. `e2b_r2.py` / `mdr_xml.py` — serialisation, including coded values: E2B date
   format 102/204, age unit (801/802/…), sex (1/2), route (048 oral, 042 IV …),
   reporter qualification, seriousness flags from block B.2 outcomes,
   `reporttype` 2 + `studytype` 1 for premarket study reports; MDR event type
   (D/IN/M/O), report source and outcome codes for CDRH.

The MDR document follows FDA's eMDR **3500A content model** (one `<mdrReport>`
per form, blocks kept as separate elements); the ESG/AS2 transport envelope is
out of scope. The E2B(R2) file is an `ichicsr` message with the standard
message header, `safetyreport`, `primarysource`, `patient`, `reaction`, `test`,
`drug` and `summary` sections.

## Tests

```bash
python -m pytest tests -q            # parsing + XML assertions (no PaddleOCR needed)
python -m medwatch_ocr.cli demo -d /tmp/out --engine paddleocr   # real OCR run
python scripts/verify_official_roundtrip.py                      # OCR vs. filled values
```

The PaddleOCR run of both samples produces XML identical to the direct field
read, i.e. OCR of the official form is lossless for every mapped field and
checkbox. Note that a full 9-page OCR pass takes a few minutes per document on
CPU.
