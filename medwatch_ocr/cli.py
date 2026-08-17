"""Command line interface for the MedWatch OCR -> XML pipeline.

Examples::

    # fill the genuine FDA forms with the sample cases (3500A x2, 1932 x1)
    python -m medwatch_ocr.cli samples --output-dir samples

    # CDER premarket drug report -> ICH E2B(R2) ICSR
    python -m medwatch_ocr.cli convert samples/FDA-3500A_cder_premarket.pdf \
        --center CDER --stage premarket -o out/cder_premarket_e2b_r2.xml

    # CDRH postmarket device report -> FDA MDR XML
    python -m medwatch_ocr.cli convert samples/FDA-3500A_cdrh_postmarket.pdf \
        --center CDRH --stage postmarket -o out/cdrh_postmarket_mdr.xml

    # CVM veterinary report (Form FDA 1932) -> VICH GL42 AER XML
    python -m medwatch_ocr.cli convert samples/FDA-1932_cvm_veterinary.pdf \
        --center CVM -o out/cvm_veterinary_gl42.xml

    # inspect the raw OCR text only
    python -m medwatch_ocr.cli ocr samples/FDA-3500A_cdrh_postmarket.pdf
"""

import argparse
import json
import os
import sys
from typing import List, Optional

from .delivery import DESTINATION_NONE, DESTINATIONS, deliver
from .form_1932 import generate_1932_samples
from .models import CENTER_CDER, CENTER_CDRH, CENTER_CVM, STAGE_POSTMARKET, STAGE_PREMARKET
from .ocr import OcrError, ocr_pdf
from .official_form import generate_official_samples
from .pipeline import (
    ENGINE_TEXT_LAYER,
    ENGINES,
    FORMAT_E2B,
    FORMAT_E2B_FDA,
    FORMAT_GL42,
    FORMAT_MDR,
    FORMAT_PVX_1932A,
    FORMAT_VICH_HL7,
    LAYOUT_AUTO,
    LAYOUTS,
    convert_pdf,
)
from .samples import generate_samples


def _add_common_ocr_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", choices=list(ENGINES), default=ENGINE_TEXT_LAYER,
                        help="extraction engine (default: text-layer, the PDF text layer / AcroForm; "
                             "'paddleocr' runs OCR, 'auto' runs OCR and falls back to the text layer)")
    parser.add_argument("--dpi", type=int, default=200, help="rasterisation DPI for PaddleOCR (default: 200)")
    parser.add_argument("--lang", default="en", help="PaddleOCR recognition language (default: en)")


def _add_layout_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--layout", choices=list(LAYOUTS), default=LAYOUT_AUTO,
                        help="input geometry: 'official' (genuine FDA 3500A field template), "
                             "'1932' (genuine FDA 1932 veterinary template), 'flat' (label/value facsimile), "
                             "'auto' (default, detects which official form was supplied)")


def _add_sample_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--facsimile", action="store_true",
                        help="generate the flat label/value facsimile instead of the genuine FDA form")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="medwatch_ocr",
        description="Read FDA 3500A MedWatch PDFs with PaddleOCR and emit E2B(R2) or FDA MDR XML.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    samples = sub.add_parser(
        "samples",
        help="generate the sample PDFs (3500A CDER premarket, 3500A CDRH postmarket, 1932 CVM veterinary)",
    )
    samples.add_argument("--output-dir", "-d", default="samples", help="directory for the generated PDFs")
    _add_sample_args(samples)

    ocr = sub.add_parser("ocr", help="run OCR on a 3500A PDF and print the extracted lines")
    ocr.add_argument("pdf")
    ocr.add_argument("--output", "-o", help="write the OCR text to this file instead of stdout")
    _add_common_ocr_args(ocr)

    convert = sub.add_parser("convert", help="OCR a 3500A PDF and convert it to XML")
    convert.add_argument("pdf")
    convert.add_argument("--center", choices=[CENTER_CDER, CENTER_CDRH, CENTER_CVM],
                         help="FDA center; inferred from the form when omitted")
    convert.add_argument("--stage", choices=[STAGE_PREMARKET, STAGE_POSTMARKET],
                         help="premarket or postmarket; inferred from the form when omitted")
    convert.add_argument("--format", "-f", dest="output_format",
                         choices=[FORMAT_E2B, FORMAT_E2B_FDA, FORMAT_MDR, FORMAT_GL42, FORMAT_PVX_1932A, FORMAT_VICH_HL7],
                         help="output format (default: e2b-r2 for CDER, mdr for CDRH, gl42 for CVM, "
                              "pvx1932a for a 1932a submission)")
    convert.add_argument("--attach", action="append", default=[], metavar="FILE",
                         help="a file the message carries; repeat per attachment (1932a submissions)")
    convert.add_argument("--deliver", choices=list(DESTINATIONS), default=DESTINATION_NONE,
                         help="also copy the message to a gateway inbound folder (default: none)")
    convert.add_argument("--deliver-dir", help="the inbound folder to deliver to, overriding the destination's")
    convert.add_argument("--output", "-o", help="write the XML here instead of stdout")
    convert.add_argument("--json", dest="json_path", help="also write the parsed 3500A fields as JSON")
    convert.add_argument("--quiet", "-q", action="store_true", help="suppress the extraction summary on stderr")
    _add_common_ocr_args(convert)
    _add_layout_arg(convert)

    batch = sub.add_parser("demo", help="generate both samples and convert them end-to-end")
    batch.add_argument("--output-dir", "-d", default="out", help="directory for samples and XML output")
    _add_common_ocr_args(batch)
    _add_sample_args(batch)

    return parser


def _write(path: Optional[str], content: str) -> None:
    if not path:
        sys.stdout.write(content)
        return
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _run_convert(args: argparse.Namespace) -> int:
    result = convert_pdf(
        args.pdf,
        center=args.center,
        stage=args.stage,
        output_format=args.output_format,
        engine=args.engine,
        dpi=args.dpi,
        lang=args.lang,
        layout=args.layout,
        attachments=args.attach,
    )
    _write(args.output, result.xml)
    if args.json_path:
        _write(args.json_path, json.dumps(result.report.to_dict(), indent=2))
    if not args.quiet:
        print(json.dumps(result.summary, indent=2), file=sys.stderr)
    if args.output:
        print(f"wrote {result.output_format} XML -> {args.output}", file=sys.stderr)
    delivered = deliver(result.xml, args.deliver, args.pdf, directory=args.deliver_dir)
    if delivered:
        print(f"delivered to {args.deliver} -> {delivered}", file=sys.stderr)
    return 0


def _make_samples(output_dir: str, facsimile: bool) -> dict:
    """Genuine FDA forms by default; the flat 3500A facsimile with ``--facsimile``."""
    if facsimile:
        return generate_samples(output_dir)
    pdfs = generate_official_samples(output_dir)
    pdfs.update(generate_1932_samples(output_dir))
    return pdfs


def _run_demo(args: argparse.Namespace) -> int:
    sample_dir = os.path.join(args.output_dir, "samples")
    pdfs = _make_samples(sample_dir, args.facsimile)
    print(f"samples: {', '.join(pdfs.values())}", file=sys.stderr)

    plan = [
        ("cder_premarket", CENTER_CDER, STAGE_PREMARKET, FORMAT_E2B, "cder_premarket_e2b_r2.xml"),
        ("cdrh_postmarket", CENTER_CDRH, STAGE_POSTMARKET, FORMAT_MDR, "cdrh_postmarket_mdr.xml"),
    ]
    if "cvm_veterinary" in pdfs:
        plan.append(("cvm_veterinary", CENTER_CVM, STAGE_POSTMARKET, FORMAT_GL42, "cvm_veterinary_gl42.xml"))
    for name, center, stage, fmt, filename in plan:
        result = convert_pdf(
            pdfs[name],
            center=center,
            stage=stage,
            output_format=fmt,
            engine=args.engine,
            dpi=args.dpi,
            lang=args.lang,
        )
        out_path = os.path.join(args.output_dir, filename)
        _write(out_path, result.xml)
        print(json.dumps(result.summary, indent=2), file=sys.stderr)
        print(f"wrote {fmt} XML -> {out_path}", file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "samples":
            for name, path in _make_samples(args.output_dir, args.facsimile).items():
                print(f"{name}: {path}")
            return 0
        if args.command == "ocr":
            result = ocr_pdf(args.pdf, dpi=args.dpi, lang=args.lang, engine=args.engine)
            _write(args.output, result.text + "\n")
            print(f"engine={result.engine} pages={result.pages} lines={len(result.lines)}", file=sys.stderr)
            return 0
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "demo":
            return _run_demo(args)
    except OcrError as exc:
        print(f"OCR error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
