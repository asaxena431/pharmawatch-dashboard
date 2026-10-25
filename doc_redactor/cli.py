"""Command line: python -m doc_redactor.cli FILE [FILE ...] --rules rules.yaml --out-dir redacted/"""
import argparse
import collections
import json
import os
import sys

from .engine import RulesError, load_rules_file
from .formats import UnsupportedFile, output_name, redact_file

DEFAULT_RULES = os.path.join(os.path.dirname(__file__), "config", "rules.yaml")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Redact PDF / Word / Excel / text files using a YAML rules file.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--rules", default=os.environ.get("REDACTOR_RULES", DEFAULT_RULES))
    ap.add_argument("--out-dir", default="redacted")
    args = ap.parse_args(argv)
    try:
        rules = load_rules_file(args.rules)
    except (OSError, RulesError) as exc:
        print(f"rules error: {exc}", file=sys.stderr)
        return 2
    os.makedirs(args.out_dir, exist_ok=True)
    status = 0
    for path in args.files:
        try:
            with open(path, "rb") as fh:
                res = redact_file(path, fh.read(), rules)
        except (OSError, UnsupportedFile) as exc:
            print(f"{path}: SKIPPED - {exc}", file=sys.stderr)
            status = 1
            continue
        out = os.path.join(args.out_dir, output_name(path))
        with open(out, "wb") as fh:
            fh.write(res.data)
        with open(out + ".report.json", "w", encoding="utf-8") as fh:
            json.dump({"file": path, "hits": [h.__dict__ for h in res.hits], "warnings": res.warnings}, fh, indent=2)
        counts = collections.Counter(h.rule for h in res.hits)
        summary = ", ".join(f"{k}={v}" for k, v in counts.most_common()) or "nothing matched"
        print(f"{path} -> {out}  [{summary}]")
        for w in res.warnings:
            print(f"  warning: {w}")
    return status


if __name__ == "__main__":
    sys.exit(main())
