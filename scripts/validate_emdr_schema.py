#!/usr/bin/env python3
"""Validate a CDRH eMDR HL7 v3 message against FDA's published schema.

``Con170227.xsd`` is not served on its own: it ships inside FDA's eMDR
Implementation Package, so the package is downloaded once, its ``XML schemas``
folder unpacked into ``~/.cache/medwatch_ocr/emdr-schemas``, and reused.

    python scripts/validate_emdr_schema.py out.xml

Validation needs either ``xmllint`` (``apt install libxml2-utils``) or
``lxml``; without both, the schemas are still fetched and the tool says so.
"""

import argparse
import io
import os
import sys
import urllib.request
import zipfile

from validate_vich_schema import validate

PACKAGE = "https://www.fda.gov/media/120509/download?attachment"
FOLDER = "XML schemas/"
ENTRY = "Con170227.xsd"
AGENT = "Mozilla/5.0 (compatible; medwatch_ocr schema fetch)"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "medwatch_ocr", "emdr-schemas")


def fetch(directory: str = CACHE) -> str:
    """Unpack the schema folder of the implementation package into ``directory``."""
    entry = os.path.join(directory, ENTRY)
    if os.path.exists(entry):
        return entry
    os.makedirs(directory, exist_ok=True)
    request = urllib.request.Request(PACKAGE, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            head, _, tail = name.partition(FOLDER)
            if not tail or name.endswith("/"):
                continue
            path = os.path.join(directory, tail)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(archive.read(name))
    if not os.path.exists(entry):
        raise SystemExit(f"{ENTRY} is not in {PACKAGE}")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="+", help="the message(s) to validate")
    parser.add_argument(
        "--schema-dir",
        default=CACHE,
        help=f"where the schemas are cached (default: {CACHE})",
    )
    arguments = parser.parse_args()

    schema = fetch(arguments.schema_dir)
    complaints = validate(arguments.xml, schema)
    if complaints is None:
        print(f"schemas cached under {arguments.schema_dir}")
        print("no validator found: install libxml2-utils (xmllint) or lxml")
        return 2
    for line in complaints:
        print(line)
    if not complaints:
        for path in arguments.xml:
            print(f"{path}: validates against {ENTRY}")
    return 1 if complaints else 0


if __name__ == "__main__":
    sys.exit(main())
