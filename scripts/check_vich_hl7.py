#!/usr/bin/env python3
"""Report what a schema validator would reject in a VICH HL7 v3 message.

The FDA schemas are not redistributable, so this checks the rules that
actually bite: a string datatype has ``minLength 1``, so no element text or
attribute may be empty, and every absent value has to carry a null flavour.

    python scripts/check_vich_hl7.py message.xml
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from typing import List, Tuple

CODED = {"CD", "CE", "SC", "CS"}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def problems(path: str) -> List[Tuple[str, str]]:
    """Every place the message states a value the schema will not accept."""
    found: List[Tuple[str, str]] = []
    for element in ET.parse(path).getroot().iter():
        name = _local(element.tag)
        for attribute, value in element.attrib.items():
            if not value.strip():
                found.append((f"{name}@{_local(attribute)}", "empty attribute: use nullFlavor instead"))
        if len(element) == 0 and element.text is not None and not element.text.strip():
            if "nullFlavor" not in element.attrib:
                found.append((name, "empty text: a string datatype needs one character or a nullFlavor"))
        if any(_local(child.tag) == "originalText" for child in element):
            if element.get(XSI_TYPE) not in CODED:
                kind = element.get(XSI_TYPE) or "an untyped element"
                found.append((name, f"originalText under {kind}: only a concept descriptor carries it"))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", nargs="+", help="the message(s) to check")
    arguments = parser.parse_args()
    failed = False
    for path in arguments.xml:
        found = problems(path)
        failed = failed or bool(found)
        print(f"{path}: {'OK' if not found else str(len(found)) + ' problem(s)'}")
        for where, why in found:
            print(f"  {where}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
