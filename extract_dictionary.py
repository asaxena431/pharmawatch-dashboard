"""Method 1 - Dictionary-based drug / reaction extraction (single self-contained file).

Put this script in the SAME FOLDER as your reference files and run it:

    drugs.txt            one drug name per line          (required)
    reactions.txt        MedDRA terms, LLT/PT pairs       (required)
    orange_book.json     trade<->generic mapping          (optional)

The reaction file may be either:
  * the raw dump - blocks of two lines (LLT then PT) separated by blank lines, or
  * a TSV with a "LLT<TAB>PT" header and one pair per line.

It matches every drug name and every reaction term against the narrative using
fast multi-pattern matching (flashtext), applies regex-only context filters
(medical history / indication / drug-name overlap / negation / blocklist) to cut
false positives, and prints two Python lists: drugs and reactions (PT terms).

Usage:
    python extract_dictionary.py --narrative case.txt
    python extract_dictionary.py --text "Patient took warfarin and developed a rash."
    python extract_dictionary.py --narrative case.txt --raw            # no filters
    python extract_dictionary.py --narrative case.txt --show-filtered   # show drops
    cat case.txt | python extract_dictionary.py
"""

import argparse
import json
import os
import re
import sys

from flashtext import KeywordProcessor

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DRUGS_FILE = os.path.join(HERE, "drugs.txt")
REACTIONS_FILE = os.path.join(HERE, "reactions.txt")
ORANGE_BOOK_FILE = os.path.join(HERE, "orange_book.json")

MIN_TERM_LEN = 3  # ignore very short names ("a/t/s") that cause false positives

# Salt / hydrate suffixes to strip so a bare ingredient ("warfarin") matches a
# reference entry stored only in salt form ("warfarin sodium").
SALT_SUFFIXES = {
    "hydrochloride", "hydrobromide", "hcl", "sodium", "potassium", "calcium",
    "magnesium", "sulfate", "sulphate", "acetate", "citrate", "phosphate",
    "tartrate", "bitartrate", "succinate", "maleate", "mesylate", "besylate",
    "fumarate", "bromide", "chloride", "nitrate", "gluconate", "lactate",
    "stearate", "palmitate", "valerate", "propionate", "dipropionate",
    "furoate", "xinafoate", "pamoate", "embonate", "tosylate", "edisylate",
    "monohydrate", "dihydrate", "trihydrate", "anhydrous", "hemihydrate",
}

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

# Indication cue: a reaction introduced by "for " is why the drug was given
# (e.g. "for shoulder pain"), not an adverse reaction.
_INDICATION = re.compile(r'\bfor\s+$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Reference-data loading
# ---------------------------------------------------------------------------
def _salt_stripped(name):
    """Return the base ingredient with a trailing salt/hydrate token removed."""
    if ";" in name:
        return None
    tokens = name.split()
    if len(tokens) >= 2 and tokens[-1] in SALT_SUFFIXES:
        base = " ".join(tokens[:-1]).strip()
        if len(base) >= MIN_TERM_LEN:
            return base
    return None


def load_drugs(path=DRUGS_FILE, orange_book=ORANGE_BOOK_FILE):
    """Return (drug_names, trade_to_generic).

    drug_names is the lowercase match set (drugs file + Orange Book + salt
    aliases); trade_to_generic maps any matched name to its generic when known.
    """
    names, trade_to_generic = set(), {}

    def _register(name, generic):
        if len(name) >= MIN_TERM_LEN:
            names.add(name)
            if generic and generic != name:
                trade_to_generic[name] = generic
        base = _salt_stripped(name)
        if base:
            names.add(base)
            trade_to_generic[base] = generic or name

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                name = line.strip().lower()
                if name:
                    _register(name, None)

    if os.path.exists(orange_book):
        with open(orange_book, encoding="utf-8") as f:
            ob = json.load(f)
        for trade, generic in ob.get("tradeToGeneric", {}).items():
            _register(trade.strip().lower(), generic.strip().lower())
        for generic in ob.get("genericToBrands", {}):
            _register(generic.strip().lower(), None)

    return names, trade_to_generic


def load_reactions(path=REACTIONS_FILE):
    """Return (llt_to_pt, reaction_terms) from either the TSV or raw dump."""
    llt_to_pt = {}
    if not os.path.exists(path):
        return llt_to_pt, set()

    with open(path, encoding="utf-8") as f:
        first = f.readline()
        if "\t" in first:  # TSV format ("LLT<TAB>PT" header + rows)
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 2 and len(parts[0]) >= MIN_TERM_LEN:
                    llt_to_pt[parts[0].strip().lower()] = parts[1].strip()
            return llt_to_pt, set(llt_to_pt)

    # Raw dump: blocks of lines separated by blank lines; a block's last line is
    # the PT, the preceding line(s) are the LLT (long LLTs wrap across lines).
    with open(path, encoding="utf-8") as f:
        groups, cur = [], []
        for raw in f:
            line = raw.rstrip("\r\n").strip()
            if line:
                cur.append(line)
            elif cur:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)

    for g in groups:
        llt = g[0] if len(g) == 1 else "".join(g[:-1])
        pt = g[-1]
        if len(llt) >= MIN_TERM_LEN:
            llt_to_pt[llt.lower()] = pt
    return llt_to_pt, set(llt_to_pt)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _history_spans(text):
    spans = []
    for m in _HISTORY_HEADER.finditer(text):
        end = text.find(".", m.end())
        spans.append((m.start(), end if end != -1 else len(text)))
    return spans


def extract(text, drug_kp, reaction_kp, trade_to_generic, llt_to_pt,
            apply_filters=True):
    drug_hits = drug_kp.extract_keywords(text, span_info=True)
    drugs, seen_drugs, drug_ranges = [], set(), []
    for _, start, end in drug_hits:
        drug_ranges.append((start, end))
        name = text[start:end].lower()
        if name in seen_drugs:
            continue
        seen_drugs.add(name)
        drugs.append({"name": name,
                      "generic": trade_to_generic.get(name, name),
                      "start": start})

    history = _history_spans(text) if apply_filters else []

    reaction_hits = reaction_kp.extract_keywords(text, span_info=True)
    reactions, seen, filtered = [], set(), []
    for _, start, end in reaction_hits:
        term = text[start:end].lower()
        if apply_filters:
            reason = None
            if term in REACTION_BLOCKLIST:
                reason = "blocklist"
            elif any(ds < end and start < de for ds, de in drug_ranges):
                reason = "overlaps-drug-name"
            elif any(s <= start < e for s, e in history):
                reason = "medical-history"
            elif _INDICATION.search(text[max(0, start - 5):start]):
                reason = "indication"
            if reason:
                filtered.append((term, reason))
                continue
        if term in seen:
            continue
        seen.add(term)
        negated = bool(_NEGATION.search(text[max(0, start - 40):start])) if apply_filters else False
        reactions.append({"reaction": term,
                          "pt": llt_to_pt.get(term, term),
                          "negated": negated})

    return {"drugs": drugs, "reactions": reactions, "filtered": filtered}


_CACHE = {}


def _processors(drugs_path, reactions_path, orange_book_path):
    """Build (and cache) the keyword processors + lookup tables for given files."""
    key = (drugs_path, reactions_path, orange_book_path)
    if key not in _CACHE:
        drug_names, trade_to_generic = load_drugs(drugs_path, orange_book_path)
        llt_to_pt, reaction_terms = load_reactions(reactions_path)

        drug_kp = KeywordProcessor(case_sensitive=False)
        for n in drug_names:
            drug_kp.add_keyword(n)
        reaction_kp = KeywordProcessor(case_sensitive=False)
        for t in reaction_terms:
            reaction_kp.add_keyword(t)

        _CACHE[key] = (drug_kp, reaction_kp, trade_to_generic, llt_to_pt)
    return _CACHE[key]


def format_case(s, case="camel"):
    """Reformat a term's casing.

    "camel" -> "visionBlurred"  (camelCase, no spaces)
    "title" -> "Vision Blurred" (Title Case, spaces kept)
    "none"  -> unchanged
    """
    words = [w for w in re.split(r'\s+', s.strip()) if w]
    if not words:
        return s
    if case == "title":
        return " ".join(w[:1].upper() + w[1:] for w in words)
    if case == "camel":
        first = words[0].lower()
        rest = "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
        return first + rest
    return s


def analyze(text, drugs_path=DRUGS_FILE, reactions_path=REACTIONS_FILE,
            orange_book_path=ORANGE_BOOK_FILE, apply_filters=True,
            include_negated=False, case="camel"):
    """High-level entry point for use from other programs.

    Returns (drugs, reactions) as two plain Python lists, formatted in the
    requested case ("camel", "title", or "none"). The reference files are loaded
    once and cached, so repeated calls are fast.

        from extract_dictionary import analyze
        drugs, reactions = analyze("Patient took warfarin and developed a rash.")
    """
    drug_kp, reaction_kp, trade_to_generic, llt_to_pt = _processors(
        drugs_path, reactions_path, orange_book_path)
    result = extract(text, drug_kp, reaction_kp, trade_to_generic, llt_to_pt,
                     apply_filters=apply_filters)
    return to_lists(result, include_negated=include_negated, case=case)


def to_lists(result, include_negated=False, case="camel"):
    """Collapse trade/generic duplicates and return (drugs, reaction_pts)."""
    drugs_in = result["drugs"]
    names = {d["name"] for d in drugs_in}
    drop = {d["generic"] for d in drugs_in
            if d["generic"] != d["name"] and d["generic"] in names}
    drugs = [format_case(d["name"], case) for d in drugs_in if d["name"] not in drop]
    reactions = [format_case(r["pt"], case) for r in result["reactions"]
                 if include_negated or not r["negated"]]
    return drugs, reactions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
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
    p = argparse.ArgumentParser(description="Dictionary-based drug/reaction extraction")
    p.add_argument("--narrative", help="Path to a narrative text file")
    p.add_argument("--text", help="Narrative text passed directly")
    p.add_argument("--drugs", default=DRUGS_FILE, help="Drug list file")
    p.add_argument("--reactions", default=REACTIONS_FILE, help="Reaction terms file")
    p.add_argument("--orange-book", default=ORANGE_BOOK_FILE, help="Orange Book JSON")
    p.add_argument("--raw", action="store_true", help="Disable context filters")
    p.add_argument("--show-filtered", action="store_true", help="List filtered-out reactions")
    p.add_argument("--case", choices=["camel", "title", "none"], default="camel",
                   help="Output casing for drugs/reactions (default: camel)")
    args = p.parse_args()

    text = _read_input(args)

    drug_names, trade_to_generic = load_drugs(args.drugs, args.orange_book)
    llt_to_pt, reaction_terms = load_reactions(args.reactions)

    drug_kp = KeywordProcessor(case_sensitive=False)
    for n in drug_names:
        drug_kp.add_keyword(n)
    reaction_kp = KeywordProcessor(case_sensitive=False)
    for t in reaction_terms:
        reaction_kp.add_keyword(t)

    result = extract(text, drug_kp, reaction_kp, trade_to_generic, llt_to_pt,
                     apply_filters=not args.raw)

    drugs, reactions = to_lists(result, case=args.case)
    print(f"drugs = {drugs!r}")
    print(f"reactions = {reactions!r}")

    if args.show_filtered:
        print("\nFILTERED OUT (context rules)")
        for term, reason in (result["filtered"] or [("(none)", "")]):
            print(f"  - {term}  [{reason}]")


if __name__ == "__main__":
    main()
