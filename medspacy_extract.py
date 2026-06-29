#!/usr/bin/env python3
"""
medspacy_extract.py
===================
Extract drugs and reactions from clinical narratives.
Pure regex - no NLP model, no external API calls.

Loads vocabularies from external files (drugs.txt, reactions.txt) for broad
coverage; falls back to a built-in list if files are not found.

Output: JSON dict where result[drug][reaction] -> confidence score,
        or "NOT FOUND" if the pair doesn't exist.

Files (optional, placed next to this script):
  drugs.txt          - one drug name per line
  reactions.txt      - MedDRA format: line1=LLT, line2=PT, blank separator
  orange_book.json   - {"tradeToGeneric": {"aleve": "naproxen", ...}}
  biologics_map.json - {"tradeToGeneric": {"dupixent": "dupilumab", ...}}

Usage:
  python medspacy_extract.py "Patient took amoxicillin and developed rash."
  python medspacy_extract.py --file note.txt
  python medspacy_extract.py                          # demo narrative
  python medspacy_extract.py --full                   # full JSON with all fields

From Python:
  from medspacy_extract import extract_medspacy
  result = extract_medspacy("Patient took amoxicillin...")
  drm = result["drug_reaction_map"]
  print(drm["Amoxicillin"]["Rash"])            # {'score': 1.0, 'verdict': 'HIGH'}
  print(drm["Amoxicillin"].get("Nausea", "NOT FOUND"))  # 'NOT FOUND'
"""

import sys
import os
import json
import re

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ── File paths (next to this script) ─────────────────────────────────────────

_SCRIPT_DIR        = os.path.dirname(os.path.abspath(__file__))
DRUGS_FILE         = os.path.join(_SCRIPT_DIR, "drugs.txt")
REACTIONS_FILE     = os.path.join(_SCRIPT_DIR, "reactions.txt")
ORANGE_BOOK_FILE   = os.path.join(_SCRIPT_DIR, "orange_book.json")
BIOLOGICS_MAP_FILE = os.path.join(_SCRIPT_DIR, "biologics_map.json")

# ── Built-in fallback vocabularies ────────────────────────────────────────────

_BUILTIN_DRUGS = [
    "tylenol", "acetaminophen", "lipitor", "atorvastatin", "amoxicillin", "ibuprofen",
    "advil", "motrin", "warfarin", "coumadin", "aspirin", "metformin", "lisinopril",
    "omeprazole", "metoprolol", "amlodipine", "albuterol", "prednisone", "gabapentin",
    "hydrocodone", "oxycodone", "morphine", "citalopram", "sertraline", "fluoxetine",
    "simvastatin", "levothyroxine", "azithromycin", "ciprofloxacin", "doxycycline",
    "penicillin", "cephalexin", "clindamycin", "vancomycin", "lorazepam", "diazepam",
    "alprazolam", "zolpidem", "quetiapine", "risperidone", "olanzapine", "haloperidol",
    "insulin", "methotrexate", "naproxen", "naproxen sodium", "aleve",
    "aleve caplets",
]

_BUILTIN_REACTIONS = {
    "nausea": "Nausea", "vomiting": "Vomiting", "diarrhea": "Diarrhoea",
    "diarrhoea": "Diarrhoea", "headache": "Headache", "dizziness": "Dizziness",
    "fatigue": "Fatigue", "rash": "Rash", "itching": "Pruritus", "pruritus": "Pruritus",
    "liver damage": "Hepatotoxicity", "hepatotoxicity": "Hepatotoxicity",
    "myalgia": "Myalgia", "muscle weakness": "Muscular Weakness",
    "muscle pain": "Myalgia", "elevated liver enzymes": "Hepatic Enzymes Increased",
    "jaundice": "Jaundice", "abdominal pain": "Abdominal Pain",
    "chest pain": "Chest Pain", "shortness of breath": "Dyspnoea",
    "dyspnea": "Dyspnoea", "dyspnoea": "Dyspnoea", "anaphylaxis": "Anaphylactic Reaction",
    "urticaria": "Urticaria", "angioedema": "Angioedema",
    "renal failure": "Renal Failure", "kidney failure": "Renal Failure",
    "seizure": "Seizure", "confusion": "Confusional State",
    "hallucination": "Hallucination", "insomnia": "Insomnia",
    "depression": "Depression", "anxiety": "Anxiety",
    "palpitations": "Palpitations", "tachycardia": "Tachycardia",
    "bradycardia": "Bradycardia", "hypertension": "Hypertension",
    "hypotension": "Hypotension", "bleeding": "Haemorrhage",
    "bruising": "Contusion", "thrombosis": "Thrombosis", "stroke": "Cerebrovascular Accident",
    "back pain": "Back Pain", "joint pain": "Arthralgia", "arthralgia": "Arthralgia",
    "swelling": "Swelling", "edema": "Oedema", "fever": "Pyrexia", "pyrexia": "Pyrexia",
    "chills": "Chills", "weight gain": "Weight Increased",
    "weight loss": "Weight Decreased", "hair loss": "Alopecia", "alopecia": "Alopecia",
    "blurred vision": "Vision Blurred", "blurry vision": "Vision Blurred",
    "tinnitus": "Tinnitus", "hearing loss": "Deafness",
    "pancreatitis": "Pancreatitis", "peripheral neuropathy": "Peripheral Neuropathy",
    "stomach bleeding": "Gastrointestinal Haemorrhage",
    "allergic reaction": "Hypersensitivity", "difficulty breathing": "Dyspnoea",
    "dark urine": "Chromaturia", "loss of appetite": "Decreased Appetite",
}

# ── Common stop words (never match as drug names) ─────────────────────────────

_STOP_WORDS = {
    "a", "an", "as", "at", "by", "do", "go", "he", "if", "in", "is", "it",
    "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we",
    "and", "are", "but", "can", "did", "for", "had", "has", "her", "him",
    "his", "how", "its", "may", "not", "now", "off", "one", "our", "out",
    "own", "per", "she", "the", "too", "two", "use", "was", "who", "why",
    "with", "from", "have", "been", "that", "this", "then", "they", "were",
    "also", "both", "each", "into", "more", "than", "them", "when", "your",
}

# Route / admin / device words that are never adverse events
_NON_REACTION_TERMS = {
    "injection", "injections", "infusion", "infusions", "syringe", "syringes",
    "subcutaneous", "intravenous", "intramuscular", "oral", "tablet", "tablets",
    "capsule", "capsules", "dose", "doses", "device",
    "sodium", "potassium", "chloride", "calcium", "magnesium", "phosphate",
    "withdrawn", "withdrew", "discontinued", "discontinuation", "stopped",
    "continued", "rechallenged", "rechallenge", "dechallenge",
}

# ── File loaders ──────────────────────────────────────────────────────────────

def load_drugs(path=DRUGS_FILE):
    """Load drug names from file (one per line). Falls back to built-in list."""
    if not os.path.exists(path):
        return list(_BUILTIN_DRUGS)
    with open(path, encoding="utf-8") as f:
        drugs = [
            l.strip().lower() for l in f
            if l.strip() and len(l.strip()) >= 3 and l.strip().lower() not in _STOP_WORDS
        ]
    return drugs if drugs else list(_BUILTIN_DRUGS)


def load_reactions(path=REACTIONS_FILE):
    """Load MedDRA reactions (line1=LLT, line2=PT, blank separator).
    Returns {llt_lower: PT_Title}. Falls back to built-in dict."""
    if not os.path.exists(path):
        return dict(_BUILTIN_REACTIONS)
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
    return llt_to_pt if llt_to_pt else dict(_BUILTIN_REACTIONS)


_BUILTIN_TRADE_TO_GENERIC = {
    "tylenol": "acetaminophen", "advil": "ibuprofen", "motrin": "ibuprofen",
    "lipitor": "atorvastatin", "coumadin": "warfarin", "aleve": "naproxen",
    "aleve caplets": "naproxen", "naproxen sodium": "naproxen",
}


def _load_trade_to_generic():
    """Trade->generic mapping from orange_book.json and biologics_map.json.
    Falls back to built-in mapping for common drugs."""
    t2g = dict(_BUILTIN_TRADE_TO_GENERIC)
    for path, key in ((ORANGE_BOOK_FILE, "tradeToGeneric"),
                      (BIOLOGICS_MAP_FILE, "tradeToGeneric")):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                t2g.update(json.load(f).get(key, {}))
    return t2g


def _dedup_drugs(found, trade_to_generic):
    """If multiple found drugs resolve to the same generic, keep only one
    (prefer the shortest / most recognisable trade name)."""
    # Group by canonical generic
    generic_groups = {}
    for d in found:
        gen = trade_to_generic.get(d.lower(), d.lower())
        generic_groups.setdefault(gen, []).append(d)
    # For each group with >1 member, keep the shortest name (trade name)
    keep = set()
    for gen, members in generic_groups.items():
        if len(members) == 1:
            keep.add(members[0])
        else:
            keep.add(min(members, key=len))
    return [d for d in found if d in keep]


# ── Negation / context detection ──────────────────────────────────────────────

_NEGATION  = {"no", "not", "without", "denied", "denies", "negative", "absent", "never", "none"}
_UNCERTAIN = {"possible", "possibly", "probable", "probably", "may", "might", "suspected", "likely"}
_FAMILY    = {"mother", "father", "sister", "brother", "family", "parent", "grandfather", "grandmother"}
_WINDOW    = 8

_HISTORY_RE = re.compile(r"\bhistory\s+of\b", re.IGNORECASE)
_INDICATION_RE = re.compile(
    r"\b(for|to\s+treat|to\s+relieve|indicated\s+for|prescribed\s+for|used\s+for|taken\s+for)\s+$",
    re.IGNORECASE,
)
_MED_HISTORY_RE = re.compile(
    r"(medical\s+history|past\s+medical\s+history|historical\s+diagnosis|concomitant\s+condition|"
    r"pre-?existing|background\s+condition|prior\s+condition|history\s+includes?)\s*[:\-]?",
    re.IGNORECASE,
)


def _med_history_ranges(text):
    """Return (start, end) char ranges that are medical history sections."""
    ranges = []
    for m in _MED_HISTORY_RE.finditer(text):
        start = m.start()
        rest = text[m.end():]
        period = rest.find(".")
        end = m.end() + period + 1 if period != -1 else m.end() + 200
        ranges.append((start, end))
    return ranges


def _in_med_history(char_pos, ranges):
    return any(s <= char_pos <= e for s, e in ranges)


def _context(tokens, idx, text="", char_pos=0):
    window = set(t.lower() for t in tokens[max(0, idx - _WINDOW):idx])
    preceding_text = text[max(0, char_pos - 60):char_pos] if text else ""
    preceding_stripped = preceding_text.rstrip()
    return {
        "negated":    bool(window & _NEGATION),
        "uncertain":  bool(window & _UNCERTAIN),
        "family":     bool(window & _FAMILY),
        "history":    bool(_HISTORY_RE.search(preceding_text)),
        "indication": bool(_INDICATION_RE.search(preceding_stripped + " ")),
    }


def _score(flags):
    s = 100
    if flags["negated"]:   s -= 60
    if flags["uncertain"]: s -= 20
    if flags["family"]:    s -= 30
    return max(0, min(100, s))


def _confidence_label(score):
    if score >= 90: return "HIGH"
    if score >= 60: return "MEDIUM"
    if score >= 30: return "LOW"
    return "VERY LOW"


# ── Pattern cache ─────────────────────────────────────────────────────────────

_cache = {}


def _get_patterns(drugs_file, reactions_file):
    """Compile regex patterns once per file pair, then serve from cache."""
    key = (drugs_file, reactions_file)
    if key not in _cache:
        drugs = load_drugs(drugs_file)
        llt_to_pt = load_reactions(reactions_file)

        drug_pat = re.compile(
            r"(?<![\w-])(?:" +
            "|".join(re.escape(d) for d in sorted(drugs, key=len, reverse=True)) +
            r")(?![\w-])",
            re.IGNORECASE,
        ) if drugs else None

        rxn_pat = re.compile(
            r"(?<![\w-])(?:" +
            "|".join(re.escape(l) for l in sorted(llt_to_pt.keys(), key=len, reverse=True)) +
            r")(?![\w-])",
            re.IGNORECASE,
        ) if llt_to_pt else None

        _cache[key] = (drugs, llt_to_pt, drug_pat, rxn_pat)
    return _cache[key]


# Preload default files at import time
_get_patterns(DRUGS_FILE, REACTIONS_FILE)
_TRADE_TO_GENERIC = _load_trade_to_generic()


# ── Main extraction function ─────────────────────────────────────────────────

def extract_medspacy(text, drugs_file=DRUGS_FILE, reactions_file=REACTIONS_FILE):
    """
    Extract drugs and reactions from a clinical narrative.

    Default output structure:
        result["drug_reaction_map"]["Aleve"]["Vision Blurred"]
        -> {"score": 1.0, "verdict": "HIGH"}

        result["drug_reaction_map"]["Aleve"].get("Nausea", "NOT FOUND")
        -> "NOT FOUND"

    Args:
        text:           Clinical narrative string
        drugs_file:     Path to drugs.txt (one drug per line)
        reactions_file: Path to reactions.txt (MedDRA LLT/PT format)

    Returns:
        dict with drug_reaction_map, drugs, reactions, and full extraction details
    """
    drugs_list, llt_to_pt, drug_pat, rxn_pat = _get_patterns(drugs_file, reactions_file)
    tokens = re.findall(r"[\w'\-]+", text)

    # ── Find drugs ────────────────────────────────────────────────────────────
    found_drugs = []
    drug_matches = []
    drug_spans = []
    if drug_pat:
        for m in drug_pat.finditer(text):
            name = m.group().lower()
            if len(name) < 3 or name in _STOP_WORDS:
                continue
            display = name.title()
            if display not in found_drugs:
                found_drugs.append(display)
            drug_matches.append((m.start(), display))
            drug_spans.append((m.start(), m.end()))

    # Dedup: keep trade name, drop generic if both found
    found_drugs = _dedup_drugs(found_drugs, _TRADE_TO_GENERIC)
    drug_matches = [(pos, d) for pos, d in drug_matches if d in found_drugs]

    # ── Find reactions ────────────────────────────────────────────────────────
    med_history_ranges = _med_history_ranges(text)

    found_reactions = []
    reaction_hits = []
    if rxn_pat:
        for m in rxn_pat.finditer(text):
            llt = m.group().lower()
            if llt in _NON_REACTION_TERMS:
                continue
            # Skip terms inside a matched drug name span
            if any(s <= m.start() < e for s, e in drug_spans):
                continue
            pt = llt_to_pt.get(llt, m.group().title())
            tok_idx = len(re.findall(r"[\w'\-]+", text[:m.start()]))
            flags = _context(tokens, tok_idx, text, m.start())
            # Skip negated / family history / "history of" / indication / med history section
            if flags["negated"] or flags["family"] or flags["history"] or flags["indication"]:
                continue
            if _in_med_history(m.start(), med_history_ranges):
                continue
            if pt not in found_reactions:
                found_reactions.append(pt)
            reaction_hits.append((m.start(), llt, pt, flags))

    # ── Build drug[reaction] -> confidence lookup ─────────────────────────────
    drug_reaction_map = {d: {} for d in found_drugs}
    unattributed = {}

    for char_pos, llt, pt, flags in reaction_hits:
        score = _score(flags)
        conf = {"score": f"{score}%", "verdict": _confidence_label(score)}

        preceding = [(pos, d) for pos, d in drug_matches if pos < char_pos]
        if preceding:
            nearest_drug = max(preceding, key=lambda x: x[0])[1]
            if nearest_drug in drug_reaction_map:
                drug_reaction_map[nearest_drug][pt] = conf
            else:
                drug_reaction_map.setdefault(nearest_drug, {})[pt] = conf
        else:
            unattributed[pt] = conf

    if unattributed:
        drug_reaction_map["unattributed"] = unattributed

    return {
        "drug_reaction_map": drug_reaction_map,
        "drugs": found_drugs,
        "reactions": found_reactions,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

DEMO_NARRATIVE = (
    "A 65-year-old female was prescribed metformin 500 mg orally for type 2 diabetes. "
    "After 2 weeks, she developed severe nausea, vomiting, and diarrhea. "
    "She also reported moderate headache and dizziness. "
    "The patient was also taking lisinopril 10 mg for hypertension. "
    "She denied any chest pain or shortness of breath. "
    "Labs showed elevated liver enzymes. The nausea resolved after dose reduction."
)


def main():
    args = [a for a in sys.argv[1:] if a not in ("--full",)]

    drugs_file = DRUGS_FILE
    reactions_file = REACTIONS_FILE

    # Parse --drugs and --reactions flags
    filtered_args = []
    i = 0
    while i < len(args):
        if args[i] == "--drugs" and i + 1 < len(args):
            drugs_file = args[i + 1]
            i += 2
        elif args[i] == "--reactions" and i + 1 < len(args):
            reactions_file = args[i + 1]
            i += 2
        else:
            filtered_args.append(args[i])
            i += 1
    args = filtered_args

    if args and args[0] == "--file":
        if len(args) < 2:
            print("Usage: python medspacy_extract.py --file <path>", file=sys.stderr)
            sys.exit(1)
        if not os.path.exists(args[1]):
            print(f"Error: file not found: {args[1]}", file=sys.stderr)
            sys.exit(1)
        with open(args[1], "r", encoding="utf-8") as f:
            narrative = f.read()
    elif args:
        narrative = " ".join(args)
    else:
        narrative = DEMO_NARRATIVE

    result = extract_medspacy(narrative, drugs_file=drugs_file, reactions_file=reactions_file)

    if "--full" in sys.argv:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result["drug_reaction_map"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
