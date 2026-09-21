"""Compare PaddleOCR output of the filled FORM FDA 1932 against the values written into it.

Usage: python scripts/verify_1932_roundtrip.py [--dpi 200] [--out-dir /tmp/vet_samples]
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.form_1932 import (  # noqa: E402
    VETERINARY_SAMPLES,
    expected_checks,
    expected_values,
    generate_1932_samples,
)
from medwatch_ocr.vet_extract import extract_1932_form  # noqa: E402


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--out-dir", default="/tmp/vet_samples")
    args = parser.parse_args()

    paths = generate_1932_samples(args.out_dir)
    failures = 0
    for name in VETERINARY_SAMPLES:
        print(f"\n=== {name}  ({paths[name]})")
        form = extract_1932_form(paths[name], dpi=args.dpi)
        expected = expected_values(name)
        for key, want in sorted(expected.items()):
            got = form.values.get(key, "")
            if normalize(got) == normalize(want):
                continue
            failures += 1
            print(f"  MISMATCH {key}\n    want: {want[:110]}\n    got : {got[:110]}")
        want_checks = set(expected_checks(name))
        got_checks = set(form.checks)
        for missing in sorted(want_checks - got_checks):
            failures += 1
            print(f"  CHECK MISSED  {missing}")
        for extra in sorted(got_checks - want_checks):
            failures += 1
            print(f"  CHECK EXTRA   {extra}")
        print(f"  fields compared: {len(expected)}  checks: {len(want_checks)}")

    print(f"\n{'FAILURES: ' + str(failures) if failures else 'ALL FIELDS AND CHECKBOXES MATCH'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
