"""Compare a directory of CVM 1932a submissions against their expected messages.

Usage::

    python scripts/compare_cvm_cases.py DIRECTORY [-o OUTPUT_DIR]

Every ``CASE.xml`` in the directory is one case: the report is ``CASE.pdf`` and
every other PDF whose name starts with ``CASE`` is one of the files the message
carries, in the order the expected message names them.  Prints one row per case
plus the differing elements.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.pipeline import convert_pdf  # noqa: E402
from medwatch_ocr.xml_diff import diff_xml, message_format  # noqa: E402


def _attachments(directory: str, expected: str, report: str) -> list:
    """The attachment paths named by the expected message, report PDF excluded."""
    names = [element.text or "" for element in ET.parse(expected).getroot().iter("FILE_NAME")]
    paths = []
    for name in names[1:] if names else []:
        path = os.path.join(directory, name)
        if os.path.exists(path) and os.path.abspath(path) != os.path.abspath(report):
            paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("-o", "--output-dir", help="write the generated messages here")
    args = parser.parse_args()

    rows = []
    for expected_path in sorted(glob.glob(os.path.join(args.directory, "*.xml"))):
        case = os.path.splitext(os.path.basename(expected_path))[0]
        report = os.path.join(args.directory, f"{case}.pdf")
        if not os.path.exists(report):
            print(f"{case}: no {case}.pdf beside the expected message, skipped")
            continue
        with open(expected_path, "r", encoding="utf-8") as handle:
            expected = handle.read()
        result = convert_pdf(
            report,
            output_format=message_format(expected),
            attachments=_attachments(args.directory, expected_path, report),
        )
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            with open(os.path.join(args.output_dir, f"{case}.ours.xml"), "w", encoding="utf-8") as handle:
                handle.write(result.xml)
        diff = diff_xml(result.xml, expected)
        rows.append((case, diff))

    print(f"\n{'case':52} {'match':>7} {'differ':>7} {'missing':>8} {'extra':>6}")
    for case, diff in rows:
        print(f"{case[:52]:52} {len(diff.matched):>7} {len(diff.different):>7} {len(diff.missing):>8} {len(diff.extra):>6}")
    for case, diff in rows:
        for entry in diff.different:
            print(f"\n{case} {entry['path']}\n    expected: {entry['expected'][:160]!r}\n    ours    : {entry['actual'][:160]!r}")
        for entry in diff.missing:
            print(f"\n{case} {entry['path']}: only in expected ({entry['expected'][:100]!r})")
        for entry in diff.extra:
            print(f"\n{case} {entry['path']}: only in ours ({entry['actual'][:100]!r})")


if __name__ == "__main__":
    main()
