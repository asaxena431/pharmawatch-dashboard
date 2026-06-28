"""
medspacy_narrative.py
=====================
Extract drugs and adverse reactions from a clinical narrative using medspaCy
(spaCy + ConText), link each reaction to the drug that most likely caused it,
and emit JSON with a confidence score (as a percentage) per link.

The drug / reaction vocabularies are your existing files:
  - drugs.txt           (one drug name per line; includes biologics)
  - reactions.txt       (MedDRA LLT / PT pairs)
  - biologics_map.json  (brand -> generic, for dedup)

medspaCy's ConText provides the clinical assertion (negated / historical /
hypothetical / family); that, combined with drug<->reaction proximity, drives
the confidence percentage.

Usage:
  python medspacy_narrative.py --file narrative.txt
  python medspacy_narrative.py --narrative "Patient took aspirin and had blurry vision."
  echo "..." | python medspacy_narrative.py            # read stdin

From Python:
  from medspacy_narrative import analyze_narrative
  result = analyze_narrative(text)        # -> dict (json-serialisable)
"""

import os
import sys
import json
import argparse

# Silence PyRuSH/medspaCy debug logging so stdout stays clean JSON.
try:
    from loguru import logger as _loguru_logger
    _loguru_logger.remove()
except Exception:
    pass

import medspacy
from medspacy.ner import TargetRule

# Reuse the vocab loaders and false-positive filters already built/tested.
from extract_narrative import (
    DRUGS_FILE,
    REACTIONS_FILE,
    load_drugs,
    load_reactions,
    _dedup_drugs,
    _load_trade_to_generic,
    _NON_REACTION_TERMS,
)


# ── Pipeline (built once, cached) ─────────────────────────────────────────────

_PIPELINE = None


def _build_pipeline(drugs_file: str = DRUGS_FILE,
                    reactions_file: str = REACTIONS_FILE):
    """Build a medspaCy pipeline with DRUG/REACTION target rules + ConText."""
    nlp = medspacy.load(enable=["medspacy_pyrush",
                                "medspacy_target_matcher",
                                "medspacy_context"])

    matcher = nlp.get_pipe("medspacy_target_matcher")
    # Case-insensitive phrase matching against the vocab files.
    try:
        matcher.phrase_matcher_attr = "LOWER"
    except Exception:
        pass

    llt_to_pt = load_reactions(reactions_file)

    rules = []
    for drug in load_drugs(drugs_file):
        if len(drug) >= 3:
            rules.append(TargetRule(drug, "DRUG"))
    for llt in llt_to_pt:
        if llt and llt not in _NON_REACTION_TERMS:
            rules.append(TargetRule(llt, "REACTION"))
    matcher.add(rules)

    return nlp, llt_to_pt


def _get_pipeline(drugs_file: str = DRUGS_FILE,
                  reactions_file: str = REACTIONS_FILE):
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = _build_pipeline(drugs_file, reactions_file)
    return _PIPELINE


# ── Confidence scoring ────────────────────────────────────────────────────────

def _confidence(assertion: dict, same_sentence: bool, linked: bool) -> int:
    """Confidence (0-100%) that a reaction is a real event caused by the drug."""
    score = 100
    if assertion["historical"]:
        score -= 30
    if assertion["hypothetical"]:
        score -= 40
    if assertion["uncertain"]:
        score -= 20
    if not linked:
        score -= 25            # no drug to attribute it to
    elif not same_sentence:
        score -= 15            # drug found, but in a different sentence
    return max(0, min(100, score))


def _label(pct: int) -> str:
    if pct >= 85:
        return "HIGH"
    if pct >= 60:
        return "MEDIUM"
    if pct >= 30:
        return "LOW"
    return "VERY LOW"


# ── Main API ──────────────────────────────────────────────────────────────────

def analyze_narrative(text: str,
                      drugs_file: str = DRUGS_FILE,
                      reactions_file: str = REACTIONS_FILE) -> dict:
    """
    Parse a narrative and return a json-serialisable dict:

        {
          "drugs":     ["Aleve", "Aspirin"],
          "reactions": ["Vision Blurred"],
          "links": [
            {"drug": "Aspirin", "reaction": "Vision Blurred",
             "confidence": 85, "confidencePct": "85%",
             "confidenceLabel": "HIGH", "sameSentence": true,
             "assertion": "present"}
          ],
          "byDrug":       {"Aspirin": [...], "Aleve": []},
          "unattributed": []
        }
    """
    nlp, llt_to_pt = _get_pipeline(drugs_file, reactions_file)
    doc = nlp(text)

    # Collect drug + reaction entities with char offsets.
    drug_ents = []
    for ent in doc.ents:
        if ent.label_ == "DRUG":
            name = ent.text.lower()
            if len(name) < 3:
                continue
            drug_ents.append({
                "display": name.title(),
                "start":   ent.start_char,
                "end":     ent.end_char,
                "sent":    ent.sent.start_char,
            })

    drug_spans = [(d["start"], d["end"]) for d in drug_ents]

    reaction_ents = []
    for ent in doc.ents:
        if ent.label_ != "REACTION":
            continue
        llt = ent.text.lower()
        if llt in _NON_REACTION_TERMS:
            continue
        # Skip reaction terms that sit inside a matched drug name
        # (e.g. "sodium" in "naproxen sodium").
        if any(s <= ent.start_char < e for s, e in drug_spans):
            continue
        # ConText assertion attributes.
        if ent._.is_negated or ent._.is_family:
            continue                      # not the patient's own event
        reaction_ents.append({
            "pt":    llt_to_pt.get(llt, ent.text.title()),
            "llt":   ent.text.title(),
            "start": ent.start_char,
            "sent":  ent.sent.start_char,
            "assertion": {
                "historical":   bool(ent._.is_historical),
                "hypothetical": bool(ent._.is_hypothetical),
                "uncertain":    bool(ent._.is_uncertain),
                "family":       bool(ent._.is_family),
            },
        })

    # Dedup drugs (keep brand over generic via orange_book + biologics_map).
    raw_drugs = []
    for d in drug_ents:
        if d["display"] not in raw_drugs:
            raw_drugs.append(d["display"])
    kept_drugs = _dedup_drugs(raw_drugs, _load_trade_to_generic())
    drug_ents  = [d for d in drug_ents if d["display"] in kept_drugs]

    # Score every reaction occurrence, then keep the single best candidate per
    # reaction PT (a term mentioned several times shouldn't appear twice).
    best = {}  # pt -> candidate dict
    reactions_out = []
    for r in reaction_ents:
        if r["pt"] not in reactions_out:
            reactions_out.append(r["pt"])

        preceding = [d for d in drug_ents if d["start"] < r["start"]]
        same_sentence_drugs = [d for d in preceding if d["sent"] == r["sent"]]
        chosen = None
        same_sentence = False
        if same_sentence_drugs:
            chosen = max(same_sentence_drugs, key=lambda d: d["start"])
            same_sentence = True
        elif preceding:
            chosen = max(preceding, key=lambda d: d["start"])

        pct = _confidence(r["assertion"], same_sentence, linked=chosen is not None)
        cand = {
            "drug":            chosen["display"] if chosen else None,
            "reaction":        r["pt"],
            "llt":             r["llt"],
            "confidence":      pct,
            "confidencePct":   f"{pct}%",
            "confidenceLabel": _label(pct),
            "sameSentence":    same_sentence,
            "assertion":       _assertion_str(r["assertion"]),
        }
        # Prefer a linked occurrence, then higher confidence.
        prev = best.get(r["pt"])
        if (prev is None
                or (cand["drug"] is not None and prev["drug"] is None)
                or (cand["confidence"] > prev["confidence"]
                    and not (prev["drug"] is not None and cand["drug"] is None))):
            best[r["pt"]] = cand

    by_drug = {d: [] for d in kept_drugs}
    links, unattributed = [], []
    for pt in reactions_out:
        cand = best[pt]
        drug = cand.pop("drug")
        if drug is not None and drug in by_drug:
            links.append({"drug": drug, **cand})
            by_drug[drug].append(cand)
        else:
            unattributed.append(cand)

    return {
        "drugs":        kept_drugs,
        "reactions":    reactions_out,
        "links":        links,
        "byDrug":       by_drug,
        "unattributed": unattributed,
    }


def _assertion_str(a: dict) -> str:
    if a["hypothetical"]:
        return "hypothetical"
    if a["historical"]:
        return "historical"
    if a["uncertain"]:
        return "uncertain"
    return "present"


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract drugs, reactions, and drug->reaction links (with "
                    "confidence %) from a clinical narrative using medspaCy."
    )
    parser.add_argument("--narrative", "-n", default=None,
                        help="Narrative text as a quoted string.")
    parser.add_argument("--file", "-f", default=None,
                        help="Path to a narrative text file.")
    parser.add_argument("--drugs", default=DRUGS_FILE)
    parser.add_argument("--reactions", default=REACTIONS_FILE)
    args = parser.parse_args()

    if args.narrative:
        text = args.narrative
    elif args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    if not text.strip():
        print("Error: no narrative provided.", file=sys.stderr)
        sys.exit(1)

    result = analyze_narrative(text, drugs_file=args.drugs,
                               reactions_file=args.reactions)
    print(json.dumps(result, indent=2, ensure_ascii=False))
