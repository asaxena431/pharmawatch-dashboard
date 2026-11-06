#!/usr/bin/env python3
"""Validate a VICH HL7 v3 message against FDA's published CVM schemas.

The schema set lives at
``accessdata.fda.gov/icsr/schema/cvm/schemas/vich/`` and is not
redistributable, so it is downloaded once (following every
``schemaLocation``) into ``~/.cache/medwatch_ocr/vich-schemas`` and reused.

    python scripts/validate_vich_schema.py out.xml

Validation needs either ``xmllint`` (``apt install libxml2-utils``) or
``lxml``; without both, the schemas are still fetched and the tool says so.
"""

import argparse
import os
import posixpath
import re
import shutil
import subprocess
import sys
import urllib.request
from typing import List, Optional, Set

BASE = "https://www.accessdata.fda.gov/icsr/schema/cvm/schemas/vich/"
ENTRY = "multicacheschemas/MCCI_IN200100UV01.xsd"
AGENT = "Mozilla/5.0 (compatible; medwatch_ocr schema fetch)"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "medwatch_ocr", "vich-schemas")


def fetch(directory: str = CACHE, entry: str = ENTRY) -> str:
    """Download the schema set into ``directory`` and return the entry schema."""
    seen: Set[str] = set()

    def get(relative: str) -> None:
        relative = posixpath.normpath(relative)
        if relative in seen:
            return
        seen.add(relative)
        path = os.path.join(directory, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            request = urllib.request.Request(BASE + relative, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                with open(path, "wb") as handle:
                    handle.write(response.read())
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        for reference in re.findall(r'schemaLocation="([^"]+)"', text):
            if not reference.startswith("http"):
                get(posixpath.join(posixpath.dirname(relative), reference))

    get(entry)
    return os.path.join(directory, entry)


def validate(paths: List[str], schema: str) -> Optional[List[str]]:
    """The validator's complaints, an empty list when valid, ``None`` without one."""
    if shutil.which("xmllint"):
        result = subprocess.run(
            ["xmllint", "--noout", "--schema", schema, *paths],
            capture_output=True,
            text=True,
        )
        return [line for line in result.stderr.splitlines() if line.strip()] if result.returncode else []
    try:
        from lxml import etree
    except ImportError:
        return None
    validator = etree.XMLSchema(etree.parse(schema))
    complaints: List[str] = []
    for path in paths:
        if not validator.validate(etree.parse(path)):
            complaints += [f"{path}: {error.line}: {error.message}" for error in validator.error_log]
    return complaints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="+", help="the message(s) to validate")
    parser.add_argument("--schema-dir", default=CACHE, help=f"where the schemas are cached (default: {CACHE})")
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
