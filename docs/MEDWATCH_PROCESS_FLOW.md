# medwatch_ocr — codebase walkthrough and process flow

How a filled FDA adverse-event form PDF becomes a regulatory XML message, which
module owns each step, and where to change things.

- [1. What the system does](#1-what-the-system-does)
- [2. End-to-end flow](#2-end-to-end-flow)
- [3. Step 1 — deciding how to read the PDF](#3-step-1--deciding-how-to-read-the-pdf)
- [4. Step 2 — the four readers](#4-step-2--the-four-readers)
- [5. Step 3 — the report model](#5-step-3--the-report-model)
- [6. Step 4 — the six serialisers](#6-step-4--the-six-serialisers)
- [7. Comparing against an expected message](#7-comparing-against-an-expected-message)
- [8. Validating a VICH HL7 message](#8-validating-a-vich-hl7-message)
- [9. The two front ends](#9-the-two-front-ends)
- [10. Sample forms](#10-sample-forms)
- [11. Tests, scripts, commands](#11-tests-scripts-commands)
- [12. How to extend it](#12-how-to-extend-it)

---

## 1. What the system does

| Input document | Center | Output message |
| --- | --- | --- |
| Form FDA-3500A (drug) | CDER | `e2b-r2` — ICH E2B(R2) ICSR, or `e2b-r2-fda` — FDA's extended 3500A profile |
| Form FDA-3500A (device) | CDRH | `mdr` — FDA MDR report, or `emdr-hl7` — the eMDR submission carried in HL7 v3 (`PORR_IN040001UV01`), source PDF embedded |
| Form FDA 1932 (static) | CVM | `gl42` — compact VICH GL42 AER |
| Form FDA 1932a (dynamic XFA) | CVM | `pvx1932a` — the message CVM's upload service takes, or `vich-hl7` — VICH GL42 carried in HL7 v3 (`MCCI_IN200100UV01`) |

Everything is one backend: the CLI and the Flask GUI both call
`medwatch_ocr.pipeline.convert_pdf()`.

## 2. End-to-end flow

```
                       ┌──────────────────────── pipeline.convert_pdf() ───────────────────────┐
 PDF ──▶ layout        │                                                                       │
         detection ────┼─▶ reader ──▶ ExtractedForm / Xfa1932a ──▶ report model ──▶ serialiser ─┼──▶ XML
         (§3)          │   (§4)          (field name → value)        (§5)            (§6)       │
                       └───────────────────────────────────────────────────────────────────────┘
                                                                              │
 expected XML ──────────────────────────────────────────────────────────────▶ xml_diff.diff_xml() ──▶ matched / different / missing / extra (§7)
```

`convert_pdf()` returns a `ConversionResult` with five things: the `report`
model, the `ocr` result (engine used, page count, extracted lines), the `xml`
string, the `output_format` it chose, and the `layout` it detected.
`ConversionResult.summary` is the short dict the CLI prints and the GUI shows.

## 3. Step 1 — deciding how to read the PDF

`pipeline.convert_pdf()` picks the reader, in this order — the first match wins:

| Order | Condition | Reader |
| --- | --- | --- |
| 1 | `xfa_1932a.is_1932a_form()` — the PDF carries an XFA `datasets` packet | dynamic 1932a reader |
| 2 | `vet_extract.is_1932_form()`, or `--center CVM` | FDA 1932 template reader |
| 3 | `label_extract.detect_variant()` says it is a known odd 3500A layout **and** the AcroForm holds no values | caption-anchored reader |
| 4 | `form_extract.is_official_form()` — the current 3500A revision | 3500A template reader |
| 5 | otherwise | flat line parser (`parser.build_report`) |

`--layout {auto,official,1932,1932a,labelled,flat}` forces any of them.

The **engine** is a separate axis, and the PDF text layer is the default:

| `--engine` | Behaviour |
| --- | --- |
| `text-layer` (default) | read the embedded text layer / AcroForm values — exact and instant on a fillable or digitally produced PDF |
| `auto` | run PaddleOCR, fall back to the text layer |
| `paddleocr` | force PaddleOCR — needed only for a scanned or printed copy with no text layer |

`ocr.py` owns this: `text_layer_page_words()` (via pypdfium2) and
`ocr_page_words()` (PaddleOCR on a rasterised page) both return
`(x0, y0, x1, y1, word)` tuples, so everything downstream is geometry, not pixels.

## 4. Step 2 — the four readers

**`form_extract.py` — template-guided (current 3500A revision).** The blank
form's widget rectangles are known, so each field is "the typed words inside
this rectangle". Also reads AcroForm field values directly when the copy is
still fillable, and checkbox states per field (the form mixes `/1` and `/Yes`).
Produces an `ExtractedForm` (`fields`, `checkboxes`, `products`, …).

**`label_extract.py` + `anchors_3500a.py` — caption-anchored (any other 3500A).**
For older revisions and vendor "3500A facsimiles", where no template fits:
fonts split the blank form's printed words from the typed values; each printed
caption ("1. Patient Identifier", "3. Date of Event") is located; the smallest
ruled box enclosing the caption is the field, and the value is the typed words
inside it. `anchors_3500a.py` holds the caption tables, repeated product rows,
`(continued)` sections and the re-flow of text carried onto continuation pages.
Emits the same `ExtractedForm` shape, so nothing downstream changes.

**`vet_extract.py` — FDA 1932 template reader.** The same idea as
`form_extract` for the 9-page static veterinary form, whose boxes map directly
onto the GL42 data elements (A.1.1, B.1.x, B.2.x …).

**`xfa_1932a.py` — dynamic XFA 1932a.** The fillable 1932a has no page content
and no AcroForm outside Adobe Reader, so nothing can be read off the page. A
submitted copy, though, carries its data in the XFA `datasets` packet as a
`<pvx1932a>` element — which *is* CVM's upload message — with the report PDF and
its attachments appended as base64 `DOCUMENTS`. This module reads the dataset,
re-serialises it as `pvx1932a`, and maps the same values onto a
`VeterinaryReport` so the case can equally be written as GL42 or HL7 v3. This is
why the four reference CVM cases reproduce byte-for-byte.

## 5. Step 3 — the report model

`models.py` is the neutral middle: readers fill it, serialisers read it.

- Human reports — `MedWatchReport`: `Patient`, `AdverseEvent`,
  `SuspectProduct[]`, `SuspectDevice`, `Reporter`, `ManufacturerInfo`.
- Veterinary reports — `VeterinaryReport`: `Animal`, `VeterinaryProduct`
  (+ `ActiveIngredient[]`), `ClinicalSign[]`, `VeterinaryOutcome[]`,
  `VeterinaryEvent`, `ProductDefect`, `Person`/`Organisation` parties.

Mapping form field → model happens in the reader (`*_extract.py`,
`xfa_1932a.py`); mapping model → element happens in the serialiser. A field that
appears in the wrong place in the output is a serialiser problem; a field that is
empty is a reader problem.

## 6. Step 4 — the seven serialisers

| Module | Format | Root element |
| --- | --- | --- |
| `e2b_r2.py` | `e2b-r2` | `<ichicsr>` |
| `e2b_r2_fda.py` | `e2b-r2-fda` | `<ichicsr>` with FDA's DTD, `FDA-CDER-OSC`/`ZZFDA`, `formtype`, fixed element order (~686 elements) |
| `mdr_xml.py` | `mdr` | `<mdrReports>` |
| `emdr_hl7.py` | `emdr-hl7` | `<PORR_IN040001UV01>` (`Con170227.xsd`), values against NCI Thesaurus codes, form PDF base64 in `message/attachment` |
| `gl42.py` | `gl42` | `<vichAdverseEventReport>` |
| `xfa_1932a.py` | `pvx1932a` | `<pvx1932a>` (flat, one element per form field, `DOCUMENTS/FILE_DATA` base64) |
| `vich_hl7.py` | `vich-hl7` | `<MCCI_IN200100UV01>` carrying `<PORR_IN049006UV>` |

`pipeline.render_xml()` routes model → serialiser and rejects impossible pairs
(e.g. `mdr` for a veterinary report). `default_format()` gives the per-center
default: CDER → `e2b-r2`, CDRH → `mdr`, CVM → `gl42`.

### The CDRH eMDR serialiser (`emdr_hl7.py`)

A scanned 09/2025 3500A (image only, no text layer, no AcroForm) is read by
`form_extract.extract_form_scan()`: PaddleOCR reads the page, the page is
registered against the blank official form, and each recognised character is
placed in the field whose box it falls in — so a caption and the value printed
over it (`"2. Age37"`) still yield `37`.

The serialiser reproduces the conventions of CDRH's own OCR pipeline, including
the ones a fresh implementation would do differently: a date of birth is written
with the day forced to the 1st, an absent date as `19000101`, and a structural
element with no value is written empty rather than omitted. `--format emdr-hl7`
embeds the source PDF (and any `--attach` file) byte for byte under
`message/attachment/text representation="B64"`.

### The HL7 v3 serialiser (`vich_hl7.py`)

Worth knowing because HL7 v3 is structural, not flat:

- envelope: batch (`id`, `creationTime`, `versionCode VICHAER1.0.0`) → message
  `PORR_IN049006UV` (`profileId AES.FDA.SRPRGL42.M.V1.ACCOUNT.AE`, USFDA
  `receiver`, `sender`) → `controlActProcess` → `investigationEvent` (narrative,
  submission date, base64 documents) → `adverseEventAssessment`;
- the animal is `subject1/primaryRole/player2`; reactions, weight, age, outcome
  and the exposure/dechallenge/rechallenge answers are one
  `subjectOf2/observation` each, coded with the VICH code system;
- the product is a `substanceAdministration` with `routeCode`,
  `doseCheckQuantity`, and `consumable/instanceOfKind` →
  `productInstanceInstance` (lot, expiry) + `kindOfProduct`
  (`code, name, formCode, asManufacturedProduct, instanceOfKind,
  asSpecializedKind, ingredient` — that order is required);
- helpers enforce the datatype rules: `_element()` drops any attribute without a
  value, `_string()` writes `nullFlavor="NI"` instead of empty text (a string
  datatype has `minLength 1`), `_coded()` emits `nullFlavor="NI"` where no code
  exists, and reported wording then goes into `<originalText>` — only under a
  concept descriptor (`CD`/`CE`/`SC`/`CS`), never under a `PQ` or `BL`.

## 7. Comparing against an expected message

`xml_diff.py` flattens both documents to `path → value` pairs, indexes repeated
blocks (`reaction[2]`, `drug[3]`, `observation[7]`), normalises whitespace,
compares HL7 attributes (`code`, `codeSystem`, `displayName`, `value`, `unit`,
`xsi:type`, `nullFlavor`) and ignores per-transmission elements
(`messagenumb`, `messagedate`, `transmissiondate`, `receivedate`,
`firstprocessdate`, HL7 `creationTime`). It reports **matched / different /
missing / extra**.

`message_format()` recognises which profile an expected file is in (plain vs FDA
extended E2B, MDR, GL42, `pvx1932a`, `MCCI_IN200100UV01` → `vich-hl7`) so the
GUI can compare like with like. Precedence: an explicitly chosen output format
always wins; auto-detection applies only when the selector is left on *auto*.

## 8. Validating a VICH HL7 message

Two levels, because FDA's schemas are not redistributable:

```bat
REM 1. rules that bite, no download needed (empty strings, misplaced originalText,
REM    and — with -r — element vocabulary/order taken from a known-valid message)
python scripts\check_vich_hl7.py out.xml [-r a_valid_message.xml]

REM 2. the real thing: caches the 63 published schemas, then validates
python scripts\validate_vich_schema.py out.xml
```

`validate_vich_schema.py` downloads
`accessdata.fda.gov/icsr/schema/cvm/schemas/vich/multicacheschemas/MCCI_IN200100UV01.xsd`
and every schema it references into `~/.cache/medwatch_ocr/vich-schemas`, then
validates with `xmllint` (`apt install libxml2-utils`) or `lxml`.
`tests/test_vich_schema.py` runs the same check when the cache exists.

### CDRH eMDR messages

`emdr-hl7` output is a different schema: `Con170227.xsd`, which FDA publishes
only inside the [eMDR Implementation
Package](https://www.fda.gov/medical-devices/mandatory-reporting-requirements-manufacturers-importers-and-device-user-facilities/health-level-seven-hl7-individual-case-safety-reporting-icsr-files).

```bat
python scripts\validate_emdr_schema.py out.xml
```

The script downloads that package once, unpacks its `XML schemas` folder into
`~/.cache/medwatch_ocr/emdr-schemas` and validates the same way;
`tests/test_emdr_schema.py` runs the check when the cache exists. Validating an
eMDR message against the VICH set (or the reverse) fails on the root element
alone, so pick the script that matches the format.

## 9. The two front ends

**CLI — `cli.py`**

```bat
python -m medwatch_ocr.cli samples -d samples      REM fill the genuine FDA forms
python -m medwatch_ocr.cli ocr FORM.pdf            REM just show what was read
python -m medwatch_ocr.cli convert FORM.pdf --format e2b-r2-fda -o out.xml
python -m medwatch_ocr.cli convert CASE.pdf --format vich-hl7 --attach "a1.pdf" -o out.xml
python -m medwatch_ocr.cli demo -d out             REM all samples + their XML
```
Useful flags: `--center`, `--stage`, `--layout`, `--engine`, `--dpi`,
`--attach` (repeatable), `--json` (the extracted fields), `--quiet`.

**GUI — `web.py` + `templates/medwatch.html`**, mounted by `app.py`:

- `GET /medwatch` — the page;
- `GET /api/medwatch/sample/<name>` — the built-in sample PDFs;
- `POST /api/medwatch/convert` — fields: `pdf`, `attachments` (multiple),
  `expected_xml`, `format`, `center`, `stage`, `layout`, `engine`, `dpi`,
  `destination`. Returns `{summary, xml, output_format, documents, delivered_to, diff}`.

```bat
set PORT=8080
python app.py
REM http://127.0.0.1:8080/medwatch
```
The page has tabs for the extraction summary, the XML, and the diff against the
uploaded expected message.

**Delivering the message — `delivery.py`**

`destination` (GUI "Deliver the message to", CLI `--deliver`) is `none` — the
message stays in the browser — or `dev`, which writes it to the DEV gateway's
inbound folder
`\\FDSWV26252\lsmvdev\aersesubdev\inbound\cvm-drug\xml_cvm-drug`, overridable
with the `MEDWATCH_DEV_INBOUND` environment variable or CLI `--deliver-dir`.
Each run takes its own filename — the form's name, the time to the millisecond
and a random suffix — so re-submitting a case never overwrites a message that is
still waiting to be picked up.

**The folder service — `service.py`**

A third front end for unattended running: ZIPs in, XML out. One ZIP is one case
— the FDA form PDF inside it becomes the message, every other file in it is
embedded as an attachment.

```bash
cp medwatch-service.ini.sample medwatch-service.ini   # edit the folders
python -m medwatch_ocr.cli service --config medwatch-service.ini          # as a service
python -m medwatch_ocr.cli service --config medwatch-service.ini --once   # one sweep
```

Per sweep of `[folders] inbound`, oldest ZIP first: a ZIP whose size is still
changing is left for the next sweep (`settle_seconds`), the XML is written to
`outbound` under the same unique name `delivery.py` builds, and the ZIP moves to
`processed` — or to `error`, with the traceback mailed to `[email] recipients`,
when anything fails. Nothing is deleted and nothing is overwritten: a repeated
ZIP name becomes `case-2.zip`. The `[conversion]` section fixes the format
(`emdr-hl7`, `vich-hl7`, …); `format = auto` instead reads each form first and
writes the format named for the center it turns out to belong to, so one folder
can take device and veterinary cases together:

```ini
[conversion]
format = auto
format_cdrh = emdr-hl7
format_cvm = vich-hl7
```

Without that, a veterinary ZIP in a `format = emdr-hl7` folder fails with
"veterinary reports are only serialised as gl42 or vich-hl7" and lands in
`error`. As a systemd unit:

```ini
[Service]
WorkingDirectory=/opt/medwatch-forms-ocr
ExecStart=/opt/medwatch-forms-ocr/.venv/bin/python -m medwatch_ocr.cli service --config /etc/medwatch-service.ini
Restart=always
```

## 10. Sample forms

`official_form.py` downloads the genuine fillable **Form FDA-3500A (09/2025)**
and fills its AcroForm with synthetic case data; `form_1932.py` does the same
with **FORM FDA 1932 (8/23)**; `samples.py` keeps the older flat facsimile
(`--facsimile`) for the line parser. Downloads are cached under
`~/.cache/medwatch_ocr/`. All sample data is synthetic.

## 11. Tests, scripts, commands

```bash
python -m pytest tests -q
ruff check --select E,F,W --line-length 140 medwatch_ocr tests scripts
```

| Script | Purpose |
| --- | --- |
| `scripts/compare_e2b_reference.py` | one PDF + expected XML → diff table (`--attach` supported) |
| `scripts/compare_cvm_cases.py` | every CVM case in a folder → one summary table |
| `scripts/check_vich_hl7.py` | schema rules without the schemas (`-r` for order/vocabulary) |
| `scripts/validate_vich_schema.py` | validate VICH HL7 against FDA's published schemas |
| `scripts/validate_emdr_schema.py` | validate CDRH eMDR HL7 against `Con170227.xsd` |
| `scripts/build_1932_template.py`, `build_caption_vocabulary.py` | regenerate the field template / caption vocabulary from a blank form |
| `scripts/verify_official_roundtrip.py`, `verify_1932_roundtrip.py` | fill a form, read it back, compare |

| Test file | Covers |
| --- | --- |
| `test_medwatch_ocr.py` | line parser, models, E2B/MDR serialisation |
| `test_official_form.py`, `test_text_layer_form.py` | template reader, text-layer/AcroForm reading |
| `test_caption_anchored.py` | caption-anchored reader, continuation pages |
| `test_form_1932.py`, `test_xfa_1932a.py` | 1932 template reader, XFA dataset reader, `pvx1932a` |
| `test_vich_hl7.py` | HL7 envelope, codes, datatype rules, diff attributes |
| `test_vich_schema.py`, `test_emdr_schema.py` | validation against FDA's schemas when cached |
| `test_service.py` | the folder service: config, case split, processed/error, mail |

## 12. How to extend it

- **A new output format**: add the serialiser module, a `FORMAT_*` constant and
  the route in `pipeline.render_xml()`, then the CLI choice and the GUI
  `<option>`; teach `xml_diff.message_format()` to recognise its root.
- **A new form layout**: add captions/anchors in `anchors_3500a.py` (no new
  reader needed) or, for a genuinely different form, a reader that returns an
  `ExtractedForm` and a branch in `convert_pdf()`.
- **A field is empty in the output**: the reader — dump it with
  `--json fields.json` and check the field name there first.
- **A field is in the wrong place, or rejected by a validator**: the serialiser
  — reproduce with `scripts/validate_vich_schema.py`, then fix the element order
  or datatype in the serialiser.
- **Scanned copies**: `pip install -r requirements-medwatch.txt` and
  `--engine paddleocr`; keep `text-layer` for everything else.
