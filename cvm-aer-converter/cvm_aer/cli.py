"""Command line interface: Form FDA 1932 / 1932a PDF -> VICH GL42 XML.

Examples::

    # fill the genuine FDA Form 1932 with the sample case
    python -m cvm_aer samples --output-dir samples

    # one veterinary report -> GL42 AER XML
    python -m cvm_aer convert samples/FDA-1932_cvm_veterinary.pdf -o out/cvm_veterinary_gl42.xml

    # CVM's HL7 v3 submission message, the case's supporting documents embedded
    python -m cvm_aer convert case.pdf --format vich-hl7 --attach att1.pdf --attach att2.pdf -o out.xml
    python -m cvm_aer validate out.xml

    # see what was read off the form
    python -m cvm_aer ocr case.pdf

    # folder service: ZIPs in inbound/ -> XML in outbound/ (the default when no command is given)
    python -m cvm_aer                         # reads ./cvm-aer-service.ini, or --config <path>
    python -m cvm_aer --once                  # one sweep and exit
"""

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

from .form_1932 import generate_1932_samples
from .ocr import OcrError, ocr_pdf
from .pipeline import (
    ENGINE_TEXT_LAYER,
    ENGINES,
    FORMAT_GL42,
    FORMATS,
    LAYOUT_AUTO,
    LAYOUTS,
    convert_pdf,
)

PROG = "cvm-aer"
COMMANDS = ("samples", "ocr", "convert", "validate", "service")
DEFAULT_COMMAND = "service"


def _add_common_ocr_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", choices=list(ENGINES), default=ENGINE_TEXT_LAYER,
                        help="extraction engine (default: text-layer, the PDF text layer / AcroForm; "
                             "'paddleocr' runs OCR, 'auto' runs OCR and falls back to the text layer)")
    parser.add_argument("--dpi", type=int, default=200, help="rasterisation DPI for PaddleOCR (default: 200)")
    parser.add_argument("--lang", default="en", help="PaddleOCR recognition language (default: en)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Read Form FDA 1932 / 1932a veterinary adverse event reports and write VICH GL42 XML. "
                    f"Without a command, '{DEFAULT_COMMAND}' is run.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    samples = sub.add_parser("samples", help="fill the genuine FDA Form 1932 with the sample case")
    samples.add_argument("--output-dir", "-d", default="samples", help="directory for the generated PDF")

    ocr = sub.add_parser("ocr", help="read a form PDF and print the extracted text")
    ocr.add_argument("pdf")
    ocr.add_argument("--output", "-o", help="write the text to this file instead of stdout")
    _add_common_ocr_args(ocr)

    convert = sub.add_parser("convert", help="read a 1932 / 1932a PDF and convert it to GL42 or VICH HL7 XML")
    convert.add_argument("pdf")
    convert.add_argument("--format", "-f", dest="output_format", choices=list(FORMATS), default=FORMAT_GL42,
                         help="gl42: compact GL42 XML for review; vich-hl7: CVM's HL7 v3 submission message "
                              "with the documents embedded (default: gl42)")
    convert.add_argument("--layout", choices=list(LAYOUTS), default=LAYOUT_AUTO,
                         help="form revision: 1932 (static), 1932a (dynamic XFA) or auto (default)")
    convert.add_argument("--attach", action="append", default=[], metavar="FILE",
                         help="a supporting document of the case; repeat for more than one")
    convert.add_argument("--output", "-o", help="write the XML here instead of stdout")
    convert.add_argument("--json", dest="json_path", help="also write the parsed report as JSON")
    convert.add_argument("--quiet", "-q", action="store_true", help="suppress the extraction summary on stderr")
    _add_common_ocr_args(convert)

    validate = sub.add_parser("validate", help="check a vich-hl7 message against FDA CVM's published schemas")
    validate.add_argument("xml", nargs="+", help="the message(s) to validate")
    validate.add_argument("--schema-dir", help="where the schemas are (default: the bundled vich-schemas/, "
                                                 "else ~/.cache/cvm_aer/vich-schemas, fetched once)")

    service = sub.add_parser(
        "service",
        help="watch an inbound folder for case ZIPs and write GL42 XML to an outbound folder",
    )
    service.add_argument("--config", "-c", default="cvm-aer-service.ini",
                         help="the configuration file (default: cvm-aer-service.ini)")
    service.add_argument("--once", action="store_true",
                         help="process what is in the inbound folder now and exit instead of running forever")
    service.add_argument("--format", "-f", dest="output_format", choices=list(FORMATS),
                         help="override [conversion] format")
    for folder in ("inbound", "outbound", "processed", "error"):
        service.add_argument(f"--{folder}", help=f"override [folders] {folder}")
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
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    from .validate import ENTRY, fetch, validate

    schema = fetch(args.schema_dir)
    complaints = validate(args.xml, schema)
    if complaints is None:
        print(f"schemas under {os.path.dirname(os.path.dirname(schema))}", file=sys.stderr)
        print("no validator found: install libxml2-utils (xmllint) or lxml", file=sys.stderr)
        return 2
    for line in complaints:
        print(line)
    if not complaints:
        for path in args.xml:
            print(f"{path}: validates against {ENTRY}")
    return 1 if complaints else 0


def _run_service(args: argparse.Namespace) -> int:
    from .service import ConfigError, load_config, run_forever, run_once

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    for folder in ("inbound", "outbound", "processed", "error"):
        value = getattr(args, folder, None)
        if value:
            setattr(config, folder, os.path.expanduser(value))
    if args.output_format:
        config.output_format = args.output_format
    if not args.once:
        try:
            return run_forever(config)
        except KeyboardInterrupt:
            print("stopped", file=sys.stderr)
            return 0
    done = run_once(config)
    for entry in done:
        name = os.path.basename(entry.archive)
        print(f"{name}: {entry.xml_path}" if entry.ok else f"{name}: FAILED {entry.error} -> {entry.moved_to}")
    return 1 if any(not entry.ok for entry in done) else 0


def with_default_command(argv: List[str]) -> List[str]:
    """Prepend the default command unless one, or a help flag, was given."""
    if argv and (argv[0] in COMMANDS or argv[0] in ("-h", "--help")):
        return argv
    return [DEFAULT_COMMAND, *argv]


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(with_default_command(argv))
    try:
        if args.command == "samples":
            for name, path in generate_1932_samples(args.output_dir).items():
                print(f"{name}: {path}")
            return 0
        if args.command == "ocr":
            result = ocr_pdf(args.pdf, dpi=args.dpi, lang=args.lang, engine=args.engine)
            _write(args.output, result.text + "\n")
            print(f"engine={result.engine} pages={result.pages} lines={len(result.lines)}", file=sys.stderr)
            return 0
        if args.command == "convert":
            return _run_convert(args)
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "service":
            return _run_service(args)
    except OcrError as exc:
        print(f"OCR error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file not found: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
