"""Compare a generated FDA extended E2B(R2) message against an expected message.

Usage::

    python scripts/compare_e2b_reference.py FORM.pdf EXPECTED.xml [-o OURS.xml]

The comparison is semantic (see :mod:`medwatch_ocr.xml_diff`): both messages are
flattened to ``path -> value`` pairs and the per-transmission elements (message
number/date, transmission date) are ignored.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.pipeline import FORMAT_E2B_FDA, convert_pdf  # noqa: E402
from medwatch_ocr.xml_diff import diff_xml  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("expected")
    parser.add_argument("-o", "--output", help="write the generated XML here")
    parser.add_argument("--engine", default="text-layer")
    parser.add_argument("--format", dest="output_format", default=FORMAT_E2B_FDA)
    parser.add_argument("--verbose", action="store_true", help="list matching elements too")
    args = parser.parse_args()

    result = convert_pdf(args.pdf, center="CDER", output_format=args.output_format, engine=args.engine)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result.xml)

    with open(args.expected, "r", encoding="utf-8") as handle:
        expected = handle.read()
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
