"""Method 2 - medspaCy-based extraction.

Uses medspaCy (clinical spaCy) to find drug and reaction mentions and apply
ConText so negated / hypothetical / historical mentions are flagged rather than
counted as real ADRs. Each reaction is linked to the nearest preceding drug
using token positions.

The drug / reaction terminologies are the same user-provided dictionaries used
by Method 1 (loaded via extraction_common), so the two methods are comparable;
the difference is medspaCy's tokenisation, span detection and ConText logic.

Usage:
    python extract_medspacy.py --narrative path/to/narrative.txt
    python extract_medspacy.py --text "Patient took warfarin and developed a rash."
    cat narrative.txt | python extract_medspacy.py
"""

import argparse
import sys

# PyRuSH (medspaCy's sentence splitter) logs verbose DEBUG output via loguru.
# Silence it so the extraction report is the only thing printed.
try:
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()
except Exception:
    pass

import medspacy
from spacy.matcher import PhraseMatcher
from spacy.tokens import Span
from spacy.util import filter_spans

from extraction_common import (
    load_drugs,
    load_reactions,
    to_generic,
    print_report,
)

_NLP = None
_DRUG_MATCHER = None
_REACTION_MATCHER = None
_TRADE_TO_GENERIC = None
_LLT_TO_PT = None


def _build():
    global _NLP, _DRUG_MATCHER, _REACTION_MATCHER, _TRADE_TO_GENERIC, _LLT_TO_PT
    if _NLP is not None:
        return

    # medspaCy pipeline: tokenizer + sentence segmentation + ConText.
    nlp = medspacy.load(medspacy_enable=["medspacy_pyrush", "medspacy_context"])

    drug_names, trade_to_generic = load_drugs()
    llt_to_pt, reaction_terms = load_reactions()

    drug_matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    drug_matcher.add("DRUG", list(nlp.tokenizer.pipe(drug_names)))

    reaction_matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    reaction_matcher.add("REACTION", list(nlp.tokenizer.pipe(reaction_terms)))

    _NLP = nlp
    _DRUG_MATCHER = drug_matcher
    _REACTION_MATCHER = reaction_matcher
    _TRADE_TO_GENERIC = trade_to_generic
    _LLT_TO_PT = llt_to_pt


def extract(text):
    _build()

    # Tokenise + segment without the default entity logic, then inject our own
    # spans so medspaCy ConText can reason over them.
    doc = _NLP.make_doc(text)

    drug_spans = [Span(doc, s, e, label="DRUG")
                  for _, s, e in _DRUG_MATCHER(doc)]
    reaction_spans = [Span(doc, s, e, label="REACTION")
                      for _, s, e in _REACTION_MATCHER(doc)]

    # Resolve overlaps (longest span wins) and set as entities for ConText.
    doc.ents = filter_spans(drug_spans + reaction_spans)

    # Run sentence segmentation + ConText over the doc with our entities.
    for name, pipe in _NLP.pipeline:
        if name in ("medspacy_pyrush", "medspacy_context"):
            doc = pipe(doc)

    drugs, seen_drugs = [], set()
    reactions = []

    for ent in doc.ents:
        if ent.label_ == "DRUG":
            name = ent.text.lower()
            if name in seen_drugs:
                continue
            seen_drugs.add(name)
            drugs.append({
                "name": name,
                "generic": to_generic(name, _TRADE_TO_GENERIC),
                "token_i": ent.start,
                "start": ent.start_char,
            })
        elif ent.label_ == "REACTION":
            term = ent.text.lower()
            reactions.append({
                "reaction": term,
                "pt": _LLT_TO_PT.get(term, term),
                "token_i": ent.start,
                "start": ent.start_char,
                "negated": bool(getattr(ent._, "is_negated", False)),
                "historical": bool(getattr(ent._, "is_historical", False)),
                "hypothetical": bool(getattr(ent._, "is_hypothetical", False)),
            })

    # Link each reaction to the nearest preceding drug (by token position).
    ordered_drugs = sorted(drugs, key=lambda d: d["token_i"])
    for r in reactions:
        preceding = [d for d in ordered_drugs if d["token_i"] <= r["token_i"]]
        if preceding:
            r["drug"] = preceding[-1]["name"]
        elif ordered_drugs:
            r["drug"] = min(ordered_drugs,
                            key=lambda d: abs(d["token_i"] - r["token_i"]))["name"]
        else:
            r["drug"] = None

    # De-duplicate reactions by (term, drug) keeping first occurrence.
    deduped, seen = [], set()
    for r in reactions:
        key = (r["reaction"], r.get("drug"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    return {"drugs": drugs, "reactions": deduped}


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
    parser = argparse.ArgumentParser(description="medspaCy-based drug/reaction extraction")
    parser.add_argument("--narrative", help="Path to a narrative text file")
    parser.add_argument("--text", help="Narrative text passed directly")
    args = parser.parse_args()

    text = _read_input(args)
    result = extract(text)
    print_report("METHOD 2: medspaCy EXTRACTION", result)


if __name__ == "__main__":
    main()
