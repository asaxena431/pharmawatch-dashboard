"""Shared data loading and helpers for the pharmacovigilance extraction scripts.

Loads the user-provided reference data:
  * data/drugs.txt          - flat list of drug names (Orange Book + others)
  * data/orange_book.json   - trade<->generic mappings
  * data/reactions_llt_pt.tsv - MedDRA LLT -> PT mapping (deduplicated)

The same dictionaries are reused by both the dictionary-based extractor
(extract_dictionary.py) and the medspaCy-based extractor (extract_medspacy.py)
so the two methods can be compared on equal footing.
"""

import json
import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DRUGS_FILE = os.path.join(DATA_DIR, "drugs.txt")
ORANGE_BOOK_FILE = os.path.join(DATA_DIR, "orange_book.json")
REACTIONS_FILE = os.path.join(DATA_DIR, "reactions_llt_pt.tsv")

# Single-word drug names this short are almost always false positives
# (e.g. the abbreviation "a/t/s" or stray tokens), so require a minimum length.
MIN_TERM_LEN = 3

# Salt / hydrate suffixes to strip so a bare ingredient name (e.g. "warfarin")
# matches a reference entry stored only in salt form ("warfarin sodium").
SALT_SUFFIXES = [
    "hydrochloride", "hydrobromide", "hcl", "sodium", "potassium", "calcium",
    "magnesium", "sulfate", "sulphate", "acetate", "citrate", "phosphate",
    "tartrate", "bitartrate", "succinate", "maleate", "mesylate", "besylate",
    "fumarate", "bromide", "chloride", "nitrate", "gluconate", "lactate",
    "stearate", "palmitate", "valerate", "propionate", "dipropionate",
    "furoate", "xinafoate", "pamoate", "embonate", "tosylate", "edisylate",
    "monohydrate", "dihydrate", "trihydrate", "anhydrous", "hemihydrate",
]


def _salt_stripped(name):
    """Return the base ingredient with a trailing salt/hydrate token removed.

    Returns None when nothing was stripped. Only single-ingredient names are
    handled (no ';' combination products) to avoid ambiguous aliases.
    """
    if ";" in name:
        return None
    tokens = name.split()
    if len(tokens) >= 2 and tokens[-1] in SALT_SUFFIXES:
        base = " ".join(tokens[:-1]).strip()
        if len(base) >= MIN_TERM_LEN:
            return base
    return None


def load_drugs():
    """Return (drug_names, trade_to_generic).

    drug_names is the full set of lowercase names to match (Orange Book list +
    every trade and generic name in orange_book.json, plus salt-stripped base
    ingredient aliases). trade_to_generic maps any matched name to its generic.
    """
    names = set()
    trade_to_generic = {}

    def _register(name, generic):
        if len(name) >= MIN_TERM_LEN:
            names.add(name)
            if generic and generic != name:
                trade_to_generic[name] = generic
        base = _salt_stripped(name)
        if base:
            names.add(base)
            trade_to_generic[base] = generic or name

    if os.path.exists(DRUGS_FILE):
        with open(DRUGS_FILE, encoding="utf-8") as f:
            for line in f:
                name = line.strip().lower()
                if name:
                    _register(name, None)

    if os.path.exists(ORANGE_BOOK_FILE):
        with open(ORANGE_BOOK_FILE, encoding="utf-8") as f:
            ob = json.load(f)
        for trade, generic in ob.get("tradeToGeneric", {}).items():
            _register(trade.strip().lower(), generic.strip().lower())
        for generic in ob.get("genericToBrands", {}):
            _register(generic.strip().lower(), None)

    return names, trade_to_generic


def load_reactions():
    """Return (llt_to_pt, reaction_terms).

    llt_to_pt maps each lowercase LLT to its Preferred Term.
    reaction_terms is the set of all lowercase LLTs to match against text.
    """
    llt_to_pt = {}
    if os.path.exists(REACTIONS_FILE):
        with open(REACTIONS_FILE, encoding="utf-8") as f:
            header = f.readline()  # skip "LLT\tPT"
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 2:
                    continue
                llt, pt = parts[0].strip(), parts[1].strip()
                if len(llt) >= MIN_TERM_LEN:
                    llt_to_pt[llt.lower()] = pt
    return llt_to_pt, set(llt_to_pt.keys())


def to_generic(name, trade_to_generic):
    """Normalise a matched drug name to its generic name when known."""
    return trade_to_generic.get(name.lower(), name.lower())


def link_reactions_to_drugs(drug_spans, reaction_spans):
    """Associate each reaction with the nearest *preceding* drug mention.

    drug_spans and reaction_spans are lists of dicts that each carry a
    character ``start`` offset. Returns the reaction list with a ``drug`` key
    added (the associated drug name, or None when no drug precedes it).
    """
    ordered_drugs = sorted(drug_spans, key=lambda d: d["start"])
    for r in reaction_spans:
        preceding = [d for d in ordered_drugs if d["start"] <= r["start"]]
        if preceding:
            r["drug"] = preceding[-1]["name"]
        elif ordered_drugs:
            # No drug before the reaction: fall back to the nearest drug overall.
            r["drug"] = min(ordered_drugs,
                            key=lambda d: abs(d["start"] - r["start"]))["name"]
        else:
            r["drug"] = None
    return reaction_spans


def collapse_trade_generic(drugs):
    """Drop a generic-name entry when its trade name is also present.

    e.g. if both "aleve" (trade, generic=naproxen sodium) and "naproxen sodium"
    matched, keep only "aleve".
    """
    names = {d["name"] for d in drugs}
    drop = set()
    for d in drugs:
        g = d.get("generic")
        if g and g != d["name"] and g in names:
            drop.add(g)
    return [d for d in drugs if d["name"] not in drop]


def result_lists(result, include_negated=False):
    """Return (drug_names, reaction_pts) as plain Python lists.

    Drugs are collapsed to trade names; reactions are their Preferred Terms.
    Negated reactions are excluded unless include_negated is True.
    """
    drugs = [d["name"] for d in collapse_trade_generic(result.get("drugs", []))]
    reactions = []
    for r in result.get("reactions", []):
        if r.get("negated") and not include_negated:
            continue
        reactions.append(r.get("pt") or r["reaction"])
    return drugs, reactions


def print_lists(result, include_negated=False):
    """Print the drugs and reactions as Python list literals."""
    drugs, reactions = result_lists(result, include_negated=include_negated)
    print(f"drugs = {drugs!r}")
    print(f"reactions = {reactions!r}")


def print_report(method_name, result, show_causality=True):
    """Pretty-print extraction results to stdout."""
    print("=" * 70)
    print(f"  {method_name}")
    print("=" * 70)

    drugs = collapse_trade_generic(result.get("drugs", []))
    reactions = result.get("reactions", [])

    print(f"\nDRUGS ({len(drugs)})")
    print("-" * 40)
    for d in drugs:
        print(f"  - {d['name']}")

    print(f"\nREACTIONS ({len(reactions)})")
    print("-" * 40)
    for r in reactions:
        pt = r.get("pt") or r["reaction"]
        neg = "  [NEGATED]" if r.get("negated") else ""
        print(f"  - {pt}{neg}")

    if not show_causality:
        print()
        return

    print(f"\nDRUG -> REACTION CAUSALITY")
    print("-" * 40)
    any_link = False
    for r in reactions:
        if r.get("negated"):
            continue
        drug = r.get("drug")
        if drug:
            any_link = True
            pt = r.get("pt") or r["reaction"]
            print(f"  {drug}  -->  {pt}")
    if not any_link:
        print("  (no drug-reaction associations found)")
    print()
