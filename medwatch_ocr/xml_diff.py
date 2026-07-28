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
from typing import Dict, List

# Elements that legitimately differ between two runs of the same report.
VOLATILE_ELEMENTS = frozenset({"messagenumb", "messagedate", "transmissiondate", "transmissiondateformat"})


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


def flatten(element: ET.Element, prefix: str = "") -> Dict[str, str]:
    """Flatten an element tree to ``path -> text``, indexing repeated children."""
    values: Dict[str, str] = {}
    counters: Dict[str, int] = {}
    for child in element:
        counters[child.tag] = counters.get(child.tag, 0) + 1
        siblings = sum(1 for other in element if other.tag == child.tag)
        name = f"{child.tag}[{counters[child.tag]}]" if siblings > 1 else child.tag
        path = f"{prefix}/{name}" if prefix else name
        if len(child):
            values.update(flatten(child, path))
        else:
            values[path] = re.sub(r"\s+", " ", (child.text or "")).strip()
    return values


def diff_xml(generated: str, expected: str) -> XmlDiff:
    """Compare two XML documents element by element."""
    ours = flatten(ET.fromstring(generated.strip()))
    theirs = flatten(ET.fromstring(expected.strip()))
    result = XmlDiff()

    for path, value in theirs.items():
        if path.split("/")[-1] in VOLATILE_ELEMENTS:
            continue
        if path not in ours:
            result.missing.append({"path": path, "expected": value})
        elif ours[path] == value or (not ours[path] and not value):
            result.matched.append(path)
        else:
            result.different.append({"path": path, "expected": value, "actual": ours[path]})

    for path, value in ours.items():
        if path not in theirs and path.split("/")[-1] not in VOLATILE_ELEMENTS:
            result.extra.append({"path": path, "actual": value})
    return result
