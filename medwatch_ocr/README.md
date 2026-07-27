# medwatch_ocr — FDA 3500A / FDA 1932 OCR → E2B(R2) / MDR / VICH GL42 XML

Reads a filled FDA adverse-event form PDF (text layer or **PaddleOCR**) and emits
a regulatory XML message:

| Sample | Center / stage | Output |
| --- | --- | --- |
| `FDA-3500A_cder_premarket.pdf` | CDER, premarket (IND safety report, drug) | ICH **E2B(R2)** ICSR (`<ichicsr>`) |
| `FDA-3500A_cdrh_postmarket.pdf` | CDRH, postmarket (manufacturer MDR, device) | FDA **MDR** XML (`<mdrReports>`) |
| `FDA-1932_cvm_veterinary.pdf` | CVM, postmarket (veterinary ADE / lack of effectiveness / product defect) | **VICH GL42** AER (`<vichAdverseEventReport>`) |

## Sample inputs are the real FDA forms

`official_form.py` downloads the genuine fillable **Form FDA-3500A MedWatch
(09/2025)** (`fda.gov/media/69876`, OMB 0910-0291), caches it under
`~/.cache/medwatch_ocr/`, and fills its AcroForm fields with synthetic case
data. The OCR input is therefore the real 9-page boxed form — official cells,
checkboxes, dropdowns and pagination — not a facsimile. Checkbox "on" states are
looked up per field (the form mixes `/1` and `/Yes`).

`form_1932.py` does the same for the veterinary case with **FORM FDA 1932
(8/23)** "Veterinary Adverse Drug Reaction, Lack of Effectiveness, Product
Defect Report" (`fda.gov/media/124792`, OMB 0910-0284) — 9 pages, 347 widgets,
172 checkboxes, laid out directly on the VICH GL42 data elements (A.1.1, B.1.x,
B.2.x …). FDA's alternative *1932a* is a dynamic **XFA** PDF: it has no page
content and no AcroForm fields outside Adobe Reader, so it cannot be filled,
rendered or OCR'd by any library; the static 1932 carries the same content and
is used instead.

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
# 1. fill the genuine FDA forms with the sample cases (3500A x2, 1932 x1)
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

# 4. CVM veterinary report (Form FDA 1932) -> VICH GL42 AER XML
python -m medwatch_ocr.cli convert samples/FDA-1932_cvm_veterinary.pdf \
    --center CVM -o out/cvm_veterinary_gl42.xml

# raw OCR text only
python -m medwatch_ocr.cli ocr samples/FDA-3500A_cdrh_postmarket.pdf --engine paddleocr

# everything at once (samples + all three conversions)
python -m medwatch_ocr.cli demo --output-dir out --engine paddleocr
```

`--layout` picks the input geometry — `official` (the FDA 3500A template),
`1932` (the FDA 1932 veterinary template), `flat` (label/value facsimile) or
`auto`, the default, which detects which official form was supplied from its
AcroForm fields or its 9-page letter-size shape.

`--engine` defaults to **`text-layer`**: the PDF text layer / AcroForm is read
directly, which is exact and takes under a second on a fillable form. Use
`--engine paddleocr` (or `auto`, which falls back to the text layer) for printed
or scanned copies, which have no text layer.

`--center` / `--stage` / `--format` are optional: the center is inferred from
the form content (device blocks ⇒ CDRH, Form 1932 ⇒ CVM) and the output format
defaults to E2B(R2) for CDER, MDR for CDRH and GL42 for CVM. It can be forced
with `--format e2b-r2|mdr|gl42`.

## Flask demo GUI

The GUI calls the same `medwatch_ocr.pipeline` functions as the CLI.

```bash
# mounted on the existing PharmaWatch app
python app.py            # -> http://localhost:5050/medwatch

# or standalone
python -m medwatch_ocr.web   # -> http://localhost:5060/medwatch
```

The page lets you pick any of the three samples (or upload your own 3500A/1932
PDF), choose the extraction engine — *PDF text layer only* is first and
selected by default — and the output format, and shows the generated XML, the
parsed fields and the raw OCR text, with a download button.

API: `POST /api/medwatch/convert` with
`sample=cder_premarket|cdrh_postmarket|cvm_veterinary`
or a `pdf` file part, plus optional `center`, `stage`, `format`, `engine`,
`dpi`, `layout`, `facsimile`. Returns `{summary, xml, fields, ocr_text}`.
`GET /medwatch/sample/<name>` serves the filled official form
(`?layout=facsimile` for the flat one).

## How the extraction works

There are three extraction paths behind one `pipeline.convert_pdf()`; the layout
is detected automatically.

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

### Veterinary form (`vet_extract.py` → `gl42.py`)

The same template-guided mechanism, driven by
`templates/fda_1932_2023.json` (347 widgets of the blank Form FDA 1932,
regenerate with `python scripts/build_1932_template.py`). Fields map onto a
dedicated `VeterinaryReport` model — animal (species, breed, sex, reproductive
and physiological status, age/weight with measured-vs-estimated basis, numbers
treated/affected), veterinary medicinal product (brand, NADA/ANADA number,
ATCvet code, active ingredients with strength numerator/denominator, dose,
route, interval, exposure dates, lot, expiry, on/off-label use), the event
(narrative, clinical manifestations with animals affected, time to onset,
duration, seriousness, outcomes, previous exposure/reaction),
dechallenge/rechallenge, and the attending-veterinarian, MAH and regulatory
authority assessments.

`gl42.py` serialises that into a VICH GL42 AER; every element carries the GL42
data-element number it came from (`<brandName gl42="B.2.1">`), which is also the
number printed on the form, so the XML can be checked against the source
document element by element.

```bash
python scripts/verify_1932_roundtrip.py     # PaddleOCR vs. the filled values
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
python scripts/verify_1932_roundtrip.py                          # same for Form FDA 1932
```

The PaddleOCR run of both samples produces XML identical to the direct field
read, i.e. OCR of the official form is lossless for every mapped field and
checkbox. Note that a full 9-page OCR pass takes a few minutes per document on
CPU.
