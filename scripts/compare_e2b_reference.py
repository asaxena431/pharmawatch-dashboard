"""Compare a generated message against the message a report is expected to produce.

Usage::

    python scripts/compare_e2b_reference.py FORM.pdf EXPECTED.xml [-o OURS.xml]
    python scripts/compare_e2b_reference.py 1932a.pdf EXPECTED.xml --attach a1.pdf --attach a2.pdf

The output format is taken from the expected message, so like is compared with
like (plain E2B(R2), FDA's extended profile, MDR, GL42 or a 1932a submission).

The comparison is semantic (see :mod:`medwatch_ocr.xml_diff`): both messages are
flattened to ``path -> value`` pairs and the per-transmission elements (message
number/date, transmission date) are ignored.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.models import CENTER_CDER  # noqa: E402
from medwatch_ocr.pipeline import FORMAT_E2B, FORMAT_E2B_FDA, convert_pdf  # noqa: E402
from medwatch_ocr.xml_diff import diff_xml, message_format  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("expected")
    parser.add_argument("-o", "--output", help="write the generated XML here")
    parser.add_argument("--engine", default="text-layer")
    parser.add_argument("--format", dest="output_format", default=None, help="default: the expected message's format")
    parser.add_argument("--attach", action="append", default=[], help="a file the message carries (1932a submissions)")
    parser.add_argument("--verbose", action="store_true", help="list matching elements too")
    args = parser.parse_args()

    with open(args.expected, "r", encoding="utf-8") as handle:
        expected = handle.read()
    output_format = args.output_format or message_format(expected) or FORMAT_E2B_FDA
    center = CENTER_CDER if output_format in (FORMAT_E2B, FORMAT_E2B_FDA) else None

    result = convert_pdf(
        args.pdf,
        center=center,
        output_format=output_format,
        engine=args.engine,
        attachments=args.attach,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result.xml)
    diff = diff_xml(result.xml, expected)

    print(
        f"expected elements: {diff.compared}   match: {len(diff.matched)}   "
        f"differ: {len(diff.different)}   missing: {len(diff.missing)}"
    )
    print(f"elements we emit that the expected message does not: {len(diff.extra)}")
    if diff.different:
        print("\n--- different values")
        for entry in diff.different:
            print(f"{entry['path']}\n    expected: {entry['expected'][:200]!r}\n    ours    : {entry['actual'][:200]!r}")
    if diff.missing:
        print("\n--- present in expected, absent from ours")
        for entry in diff.missing:
            print(f"{entry['path']}: expected={entry['expected'][:120]!r}")
    if diff.extra:
        print("\n--- present in ours, absent from expected")
        for entry in diff.extra:
            print(f"{entry['path']}: ours={entry['actual'][:120]!r}")
    if args.verbose:
        print("\n--- identical")
        for path in diff.matched:
            print(path)


if __name__ == "__main__":
    main()
