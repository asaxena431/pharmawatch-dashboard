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
from collections import defaultdict
from typing import Dict, List, Tuple

CODED = {"CD", "CE", "SC", "CS"}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_order(root: ET.Element) -> Dict[str, List[str]]:
    """The children each path holds, in the order a valid message states them."""
    order: Dict[str, List[str]] = defaultdict(list)

    def walk(element: ET.Element, path: str) -> None:
        for child in element:
            name = _local(child.tag)
            if name not in order[path]:
                order[path].append(name)
        for child in element:
            walk(child, f"{path}/{_local(child.tag)}")

    walk(root, _local(root.tag))
    return order


def against_reference(path: str, reference: str) -> List[Tuple[str, str]]:
    """Children a schema-valid message never holds there, or holds in another order."""
    order = _child_order(ET.parse(reference).getroot())
    found: List[Tuple[str, str]] = []

    def walk(element: ET.Element, path: str) -> None:
        allowed = order.get(path)
        if allowed:
            highest = -1
            for child in element:
                name = _local(child.tag)
                if name not in allowed:
                    found.append((f"{path}/{name}", "the reference never holds this element here"))
                    continue
                position = allowed.index(name)
                if position < highest:
                    found.append((f"{path}/{name}", f"stated after {allowed[highest]}, which the reference states later"))
                highest = max(highest, position)
        for child in element:
            walk(child, f"{path}/{_local(child.tag)}")

    walk(ET.parse(path).getroot(), _local(ET.parse(path).getroot().tag))
    return found


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
    parser.add_argument("-r", "--reference", help="a schema-valid message to take the element order from")
    arguments = parser.parse_args()
    failed = False
    for path in arguments.xml:
        found = problems(path)
        if arguments.reference:
            found += against_reference(path, arguments.reference)
        failed = failed or bool(found)
        print(f"{path}: {'OK' if not found else str(len(found)) + ' problem(s)'}")
        for where, why in found:
            print(f"  {where}: {why}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
