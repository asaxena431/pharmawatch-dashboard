"""Method 1 - Dictionary-based extraction.

Pure dictionary lookup: matches every drug name (drugs.txt + Orange Book) and
every MedDRA LLT reaction term against the narrative using fast multi-pattern
matching (flashtext), then links each reaction to the nearest preceding drug.

Usage:
    python extract_dictionary.py --narrative path/to/narrative.txt
    python extract_dictionary.py --text "Patient took warfarin and developed a rash."
    cat narrative.txt | python extract_dictionary.py
"""

import argparse
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


def extract(text):
    _build()

    drug_hits = _DRUG_KP.extract_keywords(text, span_info=True)
    drugs, seen_drugs = [], set()
    for matched, start, end in drug_hits:
        name = text[start:end].lower()
        if name in seen_drugs:
            continue
        seen_drugs.add(name)
        drugs.append({
            "name": name,
            "generic": to_generic(name, _TRADE_TO_GENERIC),
            "start": start,
        })

    reaction_hits = _REACTION_KP.extract_keywords(text, span_info=True)
    reactions, seen_reactions = [], set()
    for matched, start, end in reaction_hits:
        term = text[start:end].lower()
        if term in seen_reactions:
            continue
        seen_reactions.add(term)
        reactions.append({
            "reaction": term,
            "pt": _LLT_TO_PT.get(term, term),
            "start": start,
        })

    link_reactions_to_drugs(drugs, reactions)
    return {"drugs": drugs, "reactions": reactions}


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
    args = parser.parse_args()

    text = _read_input(args)
    result = extract(text)
    print_report("METHOD 1: DICTIONARY EXTRACTION", result)


if __name__ == "__main__":
    main()
