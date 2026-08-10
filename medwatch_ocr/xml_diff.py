"""Semantic comparison of two XML messages (generated output vs. expected file).

Byte comparison is useless here: the message number, message date and the
line-wrapping inside long free-text fields differ per transmission.  Both
documents are therefore flattened to ``path -> value`` pairs, repeated blocks
(``reaction``, ``drug``, ...) get an index, whitespace is normalised and the
per-transmission elements are ignored.  The result feeds both the CLI comparison
script and the ``/medwatch`` GUI's diff panel.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Elements that legitimately differ between two runs of the same report.
VOLATILE_ELEMENTS = frozenset(
    {
        "messagenumb",
        "messagedate",
        "transmissiondate",
        "transmissiondateformat",
        "receivedate",
        "receivedateformat",
        "firstprocessdate",  # when the upload service processed a 1932a submission
        "creationTime",  # HL7 batch/message creation stamp
    }
)

# Elements only the FDA extended 3500A profile of E2B (R2) carries.
FDA_PROFILE_ELEMENTS = frozenset({"formtype", "pre-1938", "manufacturerOTC", "combinationProduct", "tendayreporttype"})


@dataclass
class XmlDiff:
    matched: List[str] = field(default_factory=list)
    different: List[Dict[str, str]] = field(default_factory=list)  # path, expected, actual
    missing: List[Dict[str, str]] = field(default_factory=list)  # in expected, not generated
    extra: List[Dict[str, str]] = field(default_factory=list)  # generated, not in expected

    @property
    def compared(self) -> int:
        return len(self.matched) + len(self.different) + len(self.missing)

    def as_dict(self) -> dict:
        return {
            "compared": self.compared,
            "matched": len(self.matched),
            "different": self.different,
            "missing": self.missing,
            "extra": self.extra,
            "matched_paths": self.matched,
        }


def message_format(expected: str) -> Optional[str]:
    """The output format an expected message is written in, so like is compared with like.

    The FDA extended E2B (R2) profile carries elements plain E2B does not; comparing
    a plain ``ichicsr`` against one of those reports every extended element missing.
    """
    try:
        root = ET.fromstring(expected.strip())
    except ET.ParseError:
        return None
    root.tag = _local(root.tag)
    if root.tag == "MCCI_IN200100UV01":
        return "vich-hl7"
    if root.tag == "pvx1932a":
        return "pvx1932a"
    if root.tag == "mdrReports":
        return "mdr"
    if root.tag in {"AER", "aer", "vichaer"}:
        return "gl42"
    if root.tag != "ichicsr":
        return None
    tags = {element.tag for element in root.iter()}
    return "e2b-r2-fda" if tags & FDA_PROFILE_ELEMENTS else "e2b-r2"


def _local(tag: str) -> str:
    """A tag without its namespace: HL7 v3 messages put every element in one."""
    return tag.rsplit("}", 1)[-1]


def flatten(element: ET.Element, prefix: str = "") -> Dict[str, str]:
    """Flatten an element tree to ``path -> value``, indexing repeated children.

    HL7 v3 keeps its data in attributes (``<value code="DOG"/>``), so each
    attribute is a comparable ``path@attribute`` of its own.
    """
    values: Dict[str, str] = {}
    counters: Dict[str, int] = {}
    tags = [_local(child.tag) for child in element]
    for child, tag in zip(element, tags):
        counters[tag] = counters.get(tag, 0) + 1
        name = f"{tag}[{counters[tag]}]" if tags.count(tag) > 1 else tag
        path = f"{prefix}/{name}" if prefix else name
        for attribute, value in child.attrib.items():
            values[f"{path}@{_local(attribute)}"] = re.sub(r"\s+", " ", value).strip()
        if len(child):
            values.update(flatten(child, path))
        else:
            values[path] = re.sub(r"\s+", " ", (child.text or "")).strip()
    return values


def _volatile(path: str) -> bool:
    """True for a per-transmission element, whichever attribute of it is compared."""
    name = path.split("/")[-1].split("@")[0]
    return re.sub(r"\[\d+\]$", "", name) in VOLATILE_ELEMENTS


def diff_xml(generated: str, expected: str) -> XmlDiff:
    """Compare two XML documents element by element."""
    ours = flatten(ET.fromstring(generated.strip()))
    theirs = flatten(ET.fromstring(expected.strip()))
    result = XmlDiff()

    for path, value in theirs.items():
        if _volatile(path):
            continue
        if path not in ours:
            result.missing.append({"path": path, "expected": value})
        elif ours[path] == value or (not ours[path] and not value):
            result.matched.append(path)
        else:
            result.different.append({"path": path, "expected": value, "actual": ours[path]})

    for path, value in ours.items():
        if path not in theirs and not _volatile(path):
            result.extra.append({"path": path, "actual": value})
    return result
