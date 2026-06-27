"""
extract_narrative.py
====================
Extract drugs and reactions from clinical narratives using your own lists.
Pure regex — no NLP model, no external API calls. Fast even with 200k reactions.

Files needed:
  drugs.txt      - one drug name per line
  reactions.txt  - MedDRA format: line1=LLT, line2=PT, blank separator

Usage:
  python extract_narrative.py --narrative "Patient took amoxicillin and developed rash."
  python extract_narrative.py --file note.txt
  python extract_narrative.py --file note.txt --analyze
  python extract_narrative.py --file note.txt --analyze --drugs drugs.txt --reactions reactions.txt

From Python:
  from extract_narrative import parse_narrative
  result = parse_narrative("Patient took amoxicillin...", analyze=True)
  print(result["drugs"])
  print(result["reactions"])
  print(result["by_drug"])
"""

import sys
import os
import json
import re
import argparse

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

DRUGS_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
REACTIONS_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reactions.txt")
ORANGE_BOOK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orange_book.json")


def _load_trade_to_generic() -> dict:
    """Load trade->generic mapping from orange_book.json if available."""
    if not os.path.exists(ORANGE_BOOK_FILE):
        return {}
    with open(ORANGE_BOOK_FILE, encoding="utf-8") as f:
        ob = json.load(f)
    return ob.get("tradeToGeneric", {})


def _dedup_drugs(found: list, trade_to_generic: dict) -> list:
    """
    If both a trade name and its generic are found, keep only the trade name.
    e.g. found=["Ibuprofen", "Advil"] -> ["Advil"]  (Advil's generic is ibuprofen)
    """
    # Build set of generics that have a trade name present
    found_lower   = {d.lower() for d in found}
    generics_covered = set()
    for d in found:
        generic = trade_to_generic.get(d.lower(), "")
        if generic and generic.lower() in found_lower:
            generics_covered.add(generic.lower())
    return [d for d in found if d.lower() not in generics_covered]

# Common English words — never match as drug names
_STOP_WORDS = {
    "a", "an", "as", "at", "by", "do", "go", "he", "if", "in", "is", "it",
    "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
    "and", "are", "but", "can", "did", "for", "had", "has", "her", "him",
    "his", "how", "its", "may", "not", "now", "off", "one", "our", "out",
    "own", "per", "she", "the", "too", "two", "use", "was", "who", "why",
    "with", "from", "have", "been", "that", "this", "then", "they", "were",
    "also", "both", "each", "into", "more", "than", "them", "when", "your",
}


# ── File loaders ──────────────────────────────────────────────────────────────

def load_drugs(path: str = DRUGS_FILE) -> list:
    """Load drug names from file, one per line."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [
            l.strip().lower() for l in f
            if l.strip() and len(l.strip()) >= 3 and l.strip().lower() not in _STOP_WORDS
        ]


def load_reactions(path: str = REACTIONS_FILE) -> dict:
    """Load MedDRA reactions (line1=LLT, line2=PT, blank separator). Returns {llt: pt}"""
    if not os.path.exists(path):
        return {}
    llt_to_pt = {}
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f]
    i = 0
    while i < len(lines):
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines):
            break
        llt = lines[i].strip().lower()
        i += 1
        pt = lines[i].strip().title() if i < len(lines) and lines[i].strip() else llt.title()
        if i < len(lines) and lines[i].strip():
            i += 1
        if llt:
            llt_to_pt[llt] = pt
        while i < len(lines) and not lines[i].strip():
            i += 1
    return llt_to_pt


# ── Negation / context ────────────────────────────────────────────────────────

_NEGATION  = {"no", "not", "without", "denied", "denies", "negative", "absent", "never", "none"}
_UNCERTAIN = {"possible", "possibly", "probable", "probably", "may", "might", "suspected", "likely"}
_FAMILY    = {"mother", "father", "sister", "brother", "family", "parent", "grandfather", "grandmother"}
_WINDOW    = 8  # slightly wider to catch "history of"

_HISTORY_RE      = re.compile(r"\bhistory\s+of\b", re.IGNORECASE)
_MED_HISTORY_RE  = re.compile(
    r"(medical\s+history|past\s+medical\s+history|historical\s+diagnosis|concomitant\s+condition|"
    r"pre-?existing|background\s+condition|prior\s+condition|history\s+includes?)\s*[:\-]?",
    re.IGNORECASE
)
# Pattern that marks end of a medical history section (start of a new section)
_SECTION_END_RE  = re.compile(
    r"(suspect\s+drug|concomitant\s+drug|adverse\s+event|progression\s+of|outcome\s+of|"
    r"suspect\s+drug\(s\)|administration\s+began|dose\s+of)\s*[:\-]?",
    re.IGNORECASE
)


def _med_history_ranges(text: str) -> list:
    """Return list of (start, end) char ranges that are medical history sections.
    Ends at the next sentence boundary (period) after the section keyword.
    """
    ranges = []
    for m in _MED_HISTORY_RE.finditer(text):
        start = m.start()
        # Find end of sentence (next period) — medical history is typically one sentence
        rest  = text[m.end():]
        period = rest.find(".")
        end   = m.end() + period + 1 if period != -1 else m.end() + 200
        ranges.append((start, end))
    return ranges


def _in_med_history(char_pos: int, ranges: list) -> bool:
    return any(s <= char_pos <= e for s, e in ranges)


def _context(tokens: list, idx: int, text: str = "", char_pos: int = 0) -> dict:
    window = set(t.lower() for t in tokens[max(0, idx - _WINDOW): idx])
    # Check "history of" in a 60-char window before the match position
    preceding_text = text[max(0, char_pos - 60): char_pos] if text else ""
    return {
        "negated":   bool(window & _NEGATION),
        "uncertain": bool(window & _UNCERTAIN),
        "family":    bool(window & _FAMILY),
        "history":   bool(_HISTORY_RE.search(preceding_text)),
    }


def _score(flags: dict) -> float:
    s = 1.0
    if flags["negated"]:   s -= 0.6
    if flags["uncertain"]: s -= 0.2
    if flags["family"]:    s -= 0.3
    return round(max(0.0, min(1.0, s)), 2)


def _confidence_label(score: float) -> str:
    if score >= 0.9: return "HIGH"
    if score >= 0.6: return "MEDIUM"
    if score >= 0.3: return "LOW"
    return "VERY LOW"


# ── Pattern cache — compiled once per file pair ───────────────────────────────
# Files are loaded at module import time for default paths (fast on subsequent calls).

_cache = {}


def _get_patterns(drugs_file: str, reactions_file: str):
    """Compile regex patterns once per file pair, then serve from cache."""
    key = (drugs_file, reactions_file)
    if key not in _cache:
        drugs     = load_drugs(drugs_file)
        llt_to_pt = load_reactions(reactions_file)

        drug_pat = re.compile(
            r"(?<![\w-])(?:" +
            "|".join(re.escape(d) for d in sorted(drugs, key=len, reverse=True)) +
            r")(?![\w-])",
            re.IGNORECASE
        ) if drugs else None

        rxn_pat = re.compile(
            r"(?<![\w-])(?:" +
            "|".join(re.escape(l) for l in sorted(llt_to_pt.keys(), key=len, reverse=True)) +
            r")(?![\w-])",
            re.IGNORECASE
        ) if llt_to_pt else None

        _cache[key] = (drugs, llt_to_pt, drug_pat, rxn_pat)
    return _cache[key]


# Preload default files once at import time — parse_narrative is instant from here on
_get_patterns(DRUGS_FILE, REACTIONS_FILE)
_TRADE_TO_GENERIC = _load_trade_to_generic()


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_narrative(text: str,
                    drugs_file: str     = DRUGS_FILE,
                    reactions_file: str = REACTIONS_FILE,
                    analyze: bool       = False) -> dict:
    """
    Parse clinical narrative using your drugs list and reactions file.
    Patterns compiled once and cached — fast from 2nd call onwards.

    Args:
        text:           Clinical narrative string
        drugs_file:     Path to drugs.txt (one drug per line)
        reactions_file: Path to reactions.txt (MedDRA LLT/PT format)
        analyze:        If True, return drug->reaction links with confidence scores

    Returns:
        dict with keys: drugs, reactions
        if analyze=True also: by_drug, unattributed
    """
    drugs, llt_to_pt, drug_pat, rxn_pat = _get_patterns(drugs_file, reactions_file)
    tokens = re.findall(r"[\w'\-]+", text)

    # ── Find drugs ────────────────────────────────────────────────────────────
    found_drugs  = []
    drug_matches = []
    if drug_pat:
        for m in drug_pat.finditer(text):
            name = m.group().lower()
            if len(name) < 3 or name in _STOP_WORDS:
                continue
            display = name.title()
            if display not in found_drugs:
                found_drugs.append(display)
            drug_matches.append((m.start(), display))

    # ── Find reactions ────────────────────────────────────────────────────────
    # Dedup: keep trade name, drop generic if trade name also found
    found_drugs  = _dedup_drugs(found_drugs, _TRADE_TO_GENERIC)
    drug_matches = [(pos, d) for pos, d in drug_matches if d in found_drugs]

    # Detect medical history sections to exclude pre-existing conditions
    med_history_ranges = _med_history_ranges(text)

    found_reactions = []
    reaction_hits   = []
    if rxn_pat:
        for m in rxn_pat.finditer(text):
            llt     = m.group().lower()
            pt      = llt_to_pt.get(llt, m.group().title())
            tok_idx = len(re.findall(r"[\w'\-]+", text[:m.start()]))
            flags   = _context(tokens, tok_idx, text, m.start())
            # Skip negated / family history / "history of" / medical history section
            if flags["negated"] or flags["family"] or flags["history"]:
                continue
            if _in_med_history(m.start(), med_history_ranges):
                continue
            if pt not in found_reactions:
                found_reactions.append(pt)
            reaction_hits.append((m.start(), llt, pt, flags))

    result = {"drugs": found_drugs, "reactions": found_reactions}

    if analyze:
        by_drug      = {d: [] for d in found_drugs}
        unattributed = []
        for char_pos, llt, pt, flags in reaction_hits:
            score = _score(flags)
            entry = {
                "reaction":         pt,
                "llt":              llt.title(),
                "confidence":       score,
                "confidenceLabel":  _confidence_label(score),
            }
            preceding = [(pos, d) for pos, d in drug_matches if pos < char_pos]
            if preceding:
                nearest = max(preceding, key=lambda x: x[0])[1]
                by_drug[nearest].append(entry)
            else:
                unattributed.append(entry)
        result["byDrug"]      = by_drug
        result["unattributed"] = unattributed

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract drugs and reactions from a clinical narrative using your own lists."
    )
    parser.add_argument("--narrative", "-n", default=None, help="Narrative as quoted string")
    parser.add_argument("--file",      "-f", default=None, help="Path to narrative text file")
    parser.add_argument("--drugs",     "-d", default=DRUGS_FILE,
                        help=f"Drug list file (default: {DRUGS_FILE})")
    parser.add_argument("--reactions", "-r", default=REACTIONS_FILE,
                        help=f"Reactions file (default: {REACTIONS_FILE})")
    parser.add_argument("--analyze",   "-a", action="store_true",
                        help="Match reactions to drugs with confidence scores")
    args = parser.parse_args()

    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    elif args.narrative:
        text = args.narrative
    else:
        parser.print_help()
        sys.exit(0)

    result = parse_narrative(
        text,
        drugs_file=args.drugs,
        reactions_file=args.reactions,
        analyze=args.analyze,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
