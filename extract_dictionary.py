"""Method 1 - Dictionary-based extraction.

Pure dictionary lookup: matches every drug name (drugs.txt + Orange Book) and
every MedDRA LLT reaction term against the narrative using fast multi-pattern
matching (flashtext), then links each reaction to the nearest preceding drug.

A set of lightweight, regex-only context filters reduce the false positives that
naive dictionary matching produces in pharmacovigilance narratives (medical
history, drug indication, drug-name substrings, negation). All filters are pure
string/regex logic - no NLP model is used. Pass --raw to disable them.

Usage:
    python extract_dictionary.py --narrative path/to/narrative.txt
    python extract_dictionary.py --text "Patient took warfarin and developed a rash."
    python extract_dictionary.py --narrative case.txt --raw   # no context filters
    cat narrative.txt | python extract_dictionary.py
"""

import argparse
import re
import sys

from flashtext import KeywordProcessor

from extraction_common import (
    load_drugs,
    load_reactions,
    to_generic,
    link_reactions_to_drugs,
    print_report,
)

_DRUG_KP = None
_REACTION_KP = None
_TRADE_TO_GENERIC = None
_LLT_TO_PT = None

# MedDRA LLTs that are also common English / administrative words and almost
# always appear in these reports as something other than an adverse reaction.
REACTION_BLOCKLIST = {
    "withdrawn", "fall", "falls", "scratch", "mass", "feeling abnormal",
    "no adverse event", "drug ineffective", "off label use",
}

# Section headers that introduce pre-existing conditions (not reactions).
_HISTORY_HEADER = re.compile(
    r'(?:medical|past medical|drug|surgical|family|social|clinical)\s+history'
    r'|history\s+(?:of|includes)',
    re.IGNORECASE,
)

# Negation cues to look for in the window immediately before a reaction.
_NEGATION = re.compile(
    r'\b(no|not|without|denies?|denied|deny|negative for|free of|absence of|'
    r'absent|ruled out|never|none)\b',
    re.IGNORECASE,
)

# Indication cue: a reaction mention introduced by "for " is the reason the drug
# was given (e.g. "for shoulder pain"), not an adverse reaction.
_INDICATION = re.compile(r'\bfor\s+$', re.IGNORECASE)


def _build():
    """Lazily build the flashtext keyword processors (one-time cost)."""
    global _DRUG_KP, _REACTION_KP, _TRADE_TO_GENERIC, _LLT_TO_PT
    if _DRUG_KP is not None:
        return

    drug_names, trade_to_generic = load_drugs()
    llt_to_pt, reaction_terms = load_reactions()

    drug_kp = KeywordProcessor(case_sensitive=False)
    for name in drug_names:
        drug_kp.add_keyword(name)

    reaction_kp = KeywordProcessor(case_sensitive=False)
    for term in reaction_terms:
        reaction_kp.add_keyword(term)

    _DRUG_KP = drug_kp
    _REACTION_KP = reaction_kp
    _TRADE_TO_GENERIC = trade_to_generic
    _LLT_TO_PT = llt_to_pt


def _history_spans(text):
    """Return (start, end) char ranges covering medical-history clauses.

    A history clause runs from its header to the next period (end of sentence),
    so reactions inside it can be excluded as pre-existing conditions.
    """
    spans = []
    for m in _HISTORY_HEADER.finditer(text):
        end = text.find(".", m.end())
        end = end if end != -1 else len(text)
        spans.append((m.start(), end))
    return spans


def _in_spans(pos, spans):
    return any(s <= pos < e for s, e in spans)


def extract(text, apply_filters=True):
    _build()

    drug_hits = _DRUG_KP.extract_keywords(text, span_info=True)
    drugs, seen_drugs = [], set()
    drug_ranges = []
    for _, start, end in drug_hits:
        drug_ranges.append((start, end))
        name = text[start:end].lower()
        if name in seen_drugs:
            continue
        seen_drugs.add(name)
        drugs.append({
            "name": name,
            "generic": to_generic(name, _TRADE_TO_GENERIC),
            "start": start,
        })

    history = _history_spans(text) if apply_filters else []

    reaction_hits = _REACTION_KP.extract_keywords(text, span_info=True)
    reactions, seen_reactions = [], set()
    filtered = []  # (term, reason) dropped by a filter
    for _, start, end in reaction_hits:
        term = text[start:end].lower()

        if apply_filters:
            reason = None
            if term in REACTION_BLOCKLIST:
                reason = "blocklist"
            elif any(ds < end and start < de for ds, de in drug_ranges):
                reason = "overlaps-drug-name"
            elif _in_spans(start, history):
                reason = "medical-history"
            elif _INDICATION.search(text[max(0, start - 5):start]):
                reason = "indication"
            if reason:
                filtered.append((term, reason))
                continue

        if term in seen_reactions:
            continue
        seen_reactions.add(term)

        negated = bool(_NEGATION.search(text[max(0, start - 40):start])) if apply_filters else False
        reactions.append({
            "reaction": term,
            "pt": _LLT_TO_PT.get(term, term),
            "start": start,
            "negated": negated,
        })

    link_reactions_to_drugs(drugs, reactions)
    return {"drugs": drugs, "reactions": reactions, "filtered": filtered}


def _read_input(args):
    if args.text:
        return args.text
    if args.narrative:
        with open(args.narrative, encoding="utf-8") as f:
            return f.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide --text, --narrative FILE, or pipe text via stdin.")


def main():
    parser = argparse.ArgumentParser(description="Dictionary-based drug/reaction extraction")
    parser.add_argument("--narrative", help="Path to a narrative text file")
    parser.add_argument("--text", help="Narrative text passed directly")
    parser.add_argument("--raw", action="store_true",
                        help="Disable context filters (show raw dictionary hits)")
    parser.add_argument("--show-filtered", action="store_true",
                        help="Also list reactions removed by the context filters")
    args = parser.parse_args()

    text = _read_input(args)
    result = extract(text, apply_filters=not args.raw)
    print_report("METHOD 1: DICTIONARY EXTRACTION", result, show_causality=False)

    if args.show_filtered:
        filtered = result.get("filtered")
        print("FILTERED OUT (context rules)")
        print("-" * 40)
        for term, reason in (filtered or []):
            print(f"  - {term}  [{reason}]")
        if not filtered:
            print("  (none)")
        print()


if __name__ == "__main__":
    main()
