"""
Narrative Drug-Reaction Parser
================================
Inputs:
  - drugs.txt        : one drug name per line (or comma-separated on one line)
  - reactions.csv    : CSV with columns  llt_name, pt_name  (header required)
  - A clinical narrative string (passed as CLI argument or via NARRATIVE variable)

Output: JSON with each drug and its associated reactions (PT terms).

Usage:
  python parse_narrative.py "Patient was given amoxicillin 500mg and developed rash and nausea."
  python parse_narrative.py --file narrative.txt
"""

import re
import csv
import json
import sys
import os
import argparse


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_drugs(path: str) -> list[str]:
    """Load drug names from a file. Supports one-per-line or comma-separated."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Drug file not found: {path}")
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    # Split on newlines AND commas, strip whitespace, drop empties
    names = [n.strip().lower() for part in raw.splitlines()
             for n in part.split(",") if n.strip()]
    return names


def load_reactions(path: str) -> list[dict]:
    """
    Load reactions from a CSV with at least columns: llt_name, pt_name
    Returns list of {"llt": str, "pt": str} dicts, all lowercased.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reactions file not found: {path}")
    reactions = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in reader.fieldnames or []]
        # Accept flexible column names
        llt_col = next((h for h in headers if "llt" in h and "name" in h), None) or \
                  next((h for h in headers if "llt" in h), None)
        pt_col  = next((h for h in headers if "pt"  in h and "name" in h), None) or \
                  next((h for h in headers if h == "pt_name"), None) or \
                  next((h for h in headers if "pt"  in h), None)
        if not llt_col or not pt_col:
            raise ValueError(
                f"Could not find llt_name / pt_name columns. "
                f"Found columns: {reader.fieldnames}"
            )
        for row in reader:
            llt = row.get(llt_col, "").strip().lower()
            pt  = row.get(pt_col,  "").strip().lower()
            if llt and pt:
                reactions.append({"llt": llt, "pt": pt})
    return reactions


def build_llt_index(reactions: list[dict]) -> dict[str, str]:
    """Build a mapping llt_term -> pt_term, sorted longest-first for greedy matching."""
    return {r["llt"]: r["pt"] for r in reactions}


NEGATION = re.compile(
    r"\b(no|not|without|denies|denied|deny|absence of|absent|free of|"
    r"negative for|never|ruled out|unremarkable for|did not|does not|"
    r"was not|were not|is not|are not|no evidence of)\b",
    re.IGNORECASE,
)


def is_negated(text: str, match_start: int, window: int = 80) -> bool:
    """Return True if a negation phrase appears within `window` chars before match."""
    pre = text[max(0, match_start - window): match_start]
    return bool(NEGATION.search(pre))


def find_spans(text: str, terms: list[str]) -> list[tuple[int, int, str]]:
    """
    Find all non-overlapping occurrences of terms (longest match wins).
    Returns list of (start, end, term) sorted by start position.
    """
    text_lower = text.lower()
    found: list[tuple[int, int, str]] = []
    occupied: set[int] = set()

    # Sort longest first so multi-word terms take priority
    for term in sorted(terms, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        for m in re.finditer(pattern, text_lower):
            s, e = m.start(), m.end()
            if any(i in occupied for i in range(s, e)):
                continue  # overlaps an already-matched span
            found.append((s, e, term))
            occupied.update(range(s, e))

    return sorted(found, key=lambda x: x[0])


def associate_drug(reaction_start: int,
                   drug_spans: list[tuple[int, int, str]]) -> str | None:
    """Return the name of the nearest drug mentioned BEFORE the reaction."""
    preceding = [(s, name) for s, _, name in drug_spans if s <= reaction_start]
    if not preceding:
        return None
    return max(preceding, key=lambda x: x[0])[1]


# ── Core parser ───────────────────────────────────────────────────────────────

def parse_narrative(narrative: str,
                    drugs: list[str],
                    llt_to_pt: dict[str, str]) -> dict:
    """
    Parse a clinical narrative and return structured JSON.

    Returns:
    {
      "narrative": "...",
      "drugs_found": ["amoxicillin", ...],
      "reactions": [
        {
          "llt_term":    "skin rash",        # term found in text
          "pt_term":     "rash",             # canonical MedDRA PT
          "matched_text": "skin rash",       # exact span from narrative
          "position":    42,                 # char offset in narrative
          "drug":        "amoxicillin",      # nearest preceding drug (or null)
          "negated":     false
        }, ...
      ],
      "by_drug": {
        "amoxicillin": ["rash", "nausea"],
        "unattributed": ["headache"]
      }
    }
    """
    # 1. Find drug spans
    drug_spans = find_spans(narrative, drugs)
    drugs_found = list(dict.fromkeys(name for _, _, name in drug_spans))  # preserve order, unique

    # 2. Find reaction (LLT) spans
    llt_terms = list(llt_to_pt.keys())
    reaction_spans = find_spans(narrative, llt_terms)

    # 3. Build reaction records
    reactions = []
    for start, end, llt in reaction_spans:
        negated  = is_negated(narrative, start)
        pt       = llt_to_pt[llt]
        drug     = associate_drug(start, drug_spans)
        reactions.append({
            "llt_term":     llt,
            "pt_term":      pt,
            "matched_text": narrative[start:end],
            "position":     start,
            "drug":         drug,
            "negated":      negated,
        })

    # 4. Group by drug (exclude negated)
    by_drug: dict[str, list[str]] = {}
    for r in reactions:
        if r["negated"]:
            continue
        key = r["drug"] or "unattributed"
        pt  = r["pt_term"]
        if pt not in by_drug.setdefault(key, []):
            by_drug[key].append(pt)

    return {
        "narrative":   narrative,
        "drugs_found": drugs_found,
        "reactions":   reactions,
        "by_drug":     by_drug,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parse a clinical narrative to extract drug-reaction pairs."
    )
    parser.add_argument("narrative", nargs="?", default=None,
                        help="Narrative text (quoted string)")
    parser.add_argument("--file", "-f", default=None,
                        help="Path to a .txt file containing the narrative")
    parser.add_argument("--drugs", "-d", default="drugs.txt",
                        help="Path to drug names file (default: drugs.txt)")
    parser.add_argument("--reactions", "-r", default="reactions.csv",
                        help="Path to reactions CSV with llt_name,pt_name (default: reactions.csv)")
    parser.add_argument("--pretty", "-p", action="store_true",
                        help="Pretty-print JSON output")
    args = parser.parse_args()

    # Resolve narrative text
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            narrative = f.read().strip()
    elif args.narrative:
        narrative = args.narrative.strip()
    else:
        print("Error: provide a narrative as a positional argument or via --file", file=sys.stderr)
        sys.exit(1)

    # Load reference data
    drugs     = load_drugs(args.drugs)
    reactions = load_reactions(args.reactions)
    llt_to_pt = build_llt_index(reactions)

    # Parse
    result = parse_narrative(narrative, drugs, llt_to_pt)

    # Output
    indent = 2 if args.pretty else None
    print(json.dumps(result["by_drug"], indent=indent, ensure_ascii=False))


if __name__ == "__main__":
    main()
