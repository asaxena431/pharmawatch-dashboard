#!/usr/bin/env python3
"""
Standalone medspaCy-style clinical narrative extractor.

No external dependencies required — uses only Python standard library (re, json, sys).

Output: JSON with drugs, drug->reaction mapping, and confidence score.

Usage:
    python medspacy_extract.py                             # demo narrative
    python medspacy_extract.py "A 65-year-old female..."   # inline text
    python medspacy_extract.py --file notes.txt            # read from file
    python medspacy_extract.py --full                      # full JSON with all fields
"""

import re
import json
import sys

# ── Known drug and reaction vocabularies ──────────────────────────────────────

KNOWN_DRUGS = [
    "tylenol", "acetaminophen", "lipitor", "atorvastatin", "amoxicillin", "ibuprofen",
    "advil", "motrin", "warfarin", "coumadin", "aspirin", "metformin", "lisinopril",
    "omeprazole", "metoprolol", "amlodipine", "albuterol", "prednisone", "gabapentin",
    "hydrocodone", "oxycodone", "morphine", "citalopram", "sertraline", "fluoxetine",
    "simvastatin", "levothyroxine", "azithromycin", "ciprofloxacin", "doxycycline",
    "penicillin", "cephalexin", "clindamycin", "vancomycin", "lorazepam", "diazepam",
    "alprazolam", "zolpidem", "quetiapine", "risperidone", "olanzapine", "haloperidol",
    "insulin", "methotrexate",
]

KNOWN_REACTIONS = [
    "nausea", "vomiting", "diarrhea", "diarrhoea", "headache", "dizziness", "fatigue",
    "rash", "itching", "pruritus", "liver damage", "hepatotoxicity", "myalgia",
    "muscle weakness", "muscle pain", "elevated liver enzymes", "elevated alt",
    "elevated ast", "elevated ck", "jaundice", "abdominal pain", "chest pain",
    "shortness of breath", "dyspnea", "dyspnoea", "anaphylaxis", "urticaria",
    "angioedema", "stevens-johnson syndrome", "toxic epidermal necrolysis",
    "renal failure", "kidney failure", "seizure", "confusion", "hallucination",
    "insomnia", "depression", "anxiety", "palpitations", "tachycardia", "bradycardia",
    "hypertension", "hypotension", "bleeding", "bruising", "thrombosis", "stroke",
    "myocardial infarction", "heart attack", "back pain", "joint pain", "arthralgia",
    "swelling", "edema", "fever", "pyrexia", "chills", "night sweats", "weight gain",
    "weight loss", "hair loss", "alopecia", "blurred vision", "tinnitus", "hearing loss",
    "blistering", "mucosal involvement", "muscle spasm", "myopathy", "rhabdomyolysis",
    "pancreatitis", "peripheral neuropathy",
    "liver damage", "skin reddening", "stomach bleeding", "allergic reaction",
    "difficulty breathing", "serious skin reactions", "dark urine", "clay-colored stools",
    "loss of appetite", "upper stomach pain",
]

# ── Regex patterns ────────────────────────────────────────────────────────────

DOSE_PATTERN = re.compile(
    r'\b(\d+\.?\d*\s*(?:mg|mcg|ug|g|ml|units?|IU|mEq)(?:\s*/\s*(?:day|daily|kg|dose))?)\b',
    re.IGNORECASE,
)
SEVERITY_PATTERN = re.compile(
    r'\b(mild|moderate|severe|serious|fatal|life[\s-]threatening)\b', re.IGNORECASE
)
OUTCOME_PATTERN = re.compile(
    r'\b(resolv\w+|recover\w+|discharged|improved|died|fatal|death|ongoing|persistent|hospitali\w+)\b',
    re.IGNORECASE,
)
AGE_PATTERN = re.compile(r'\b(\d+)[\s-]*(year|yr)s?[\s-]*old\b', re.IGNORECASE)
SEX_PATTERN = re.compile(r'\b(male|female|man|woman|boy|girl)\b', re.IGNORECASE)
CAUSALITY_PATTERN = re.compile(
    r'\b(probable|possible|unlikely|definite|suspected|associated with|caused by)\b',
    re.IGNORECASE,
)
NEGATION_PATTERN = re.compile(
    r'\b(no|not|without|denies|denied|deny|absence of|absent|free of|'
    r'negative for|never|ruled out|unremarkable for|fails to|'
    r'did not|does not|was not|were not|is not|are not)\b',
    re.IGNORECASE,
)


# ── Confidence scoring ────────────────────────────────────────────────────────

def calculate_confidence(extracted):
    score = 0
    drugs = extracted.get("drugs", [])
    reactions = extracted.get("reactions", [])

    if drugs:
        score += 15
    if any(d.get("dose") for d in drugs):
        score += 10
    if reactions:
        score += 15
    if any(r.get("severity") for r in reactions):
        score += 8
    if any(r.get("onset") for r in reactions):
        score += 4
    if any(r.get("outcome", "unknown") != "unknown" for r in reactions):
        score += 3
    if extracted.get("patient", {}).get("age"):
        score += 10
    if extracted.get("patient", {}).get("sex"):
        score += 10
    if extracted.get("causality", "unassessable") != "unassessable":
        score += 10
    if extracted.get("overall_severity"):
        score += 10

    if score >= 80:
        verdict, needs_gpt = "HIGH", False
    elif score >= 50:
        verdict, needs_gpt = "MEDIUM", True
    else:
        verdict, needs_gpt = "LOW", True

    return {"score": score, "max": 100, "verdict": verdict, "needs_gpt": needs_gpt}


# ── Main extraction function ─────────────────────────────────────────────────

def extract_medspacy(text):
    """
    Rule-based extraction of drugs, adverse reactions, patient demographics,
    and confidence score from a clinical narrative.

    Returns a dict with:
        - drugs: list of detected drugs with dose/route/indication
        - drug_reaction_map: {drug_name: [reactions caused by that drug]}
        - confidence: {score, max, verdict, needs_gpt}
        - reactions, patient, causality, overall_severity (full details)
    """
    text_lower = text.lower()
    drugs, reactions = [], []

    # ── Extract drugs ──
    for drug in KNOWN_DRUGS:
        if drug in text_lower:
            m = re.search(re.escape(drug), text_lower)
            dose = route = indication = None
            if m:
                ctx = text[max(0, m.start() - 10) : m.end() + 80]
                dm = DOSE_PATTERN.search(ctx)
                rm = re.search(
                    r'\b(oral(?:ly)?|IV|intravenous(?:ly)?|IM|subcutaneous(?:ly)?|SC|topical(?:ly)?|inhaled?)\b',
                    ctx,
                    re.IGNORECASE,
                )
                im = re.search(r'for\s+([\w\s]+?)(?:\.|,|;|$)', ctx, re.IGNORECASE)
                dose = dm.group(0) if dm else None
                route = rm.group(0).lower() if rm else None
                indication = im.group(1).strip() if im else None
            drugs.append({"name": drug, "dose": dose, "route": route, "indication": indication})

    # Build drug position map for nearest-drug association
    drug_positions = []
    for d in drugs:
        dm = re.search(re.escape(d["name"]), text_lower)
        if dm:
            drug_positions.append((dm.start(), d["name"]))

    # ── Extract reactions (with negation detection) ──
    for reaction in KNOWN_REACTIONS:
        pattern = r'(?<!\w)' + re.escape(reaction) + r'(?!\w)'
        m = re.search(pattern, text_lower)
        if not m:
            continue

        severity = onset = None
        outcome = "unknown"
        associated_drug = None

        # Check for negation within the same sentence (up to 60 chars back)
        pre_ctx = text[max(0, m.start() - 60) : m.start()]
        sent_boundary = max(
            pre_ctx.rfind('. '), pre_ctx.rfind('.\n'),
            pre_ctx.rfind('! '), pre_ctx.rfind('? '),
        )
        if sent_boundary != -1:
            pre_ctx = pre_ctx[sent_boundary + 1 :]
        if NEGATION_PATTERN.search(pre_ctx):
            continue  # skip negated reaction

        # Skip if the match is sandwiched in a dosage context
        surrounding = text[max(0, m.start() - 40) : m.end() + 40]
        if re.search(
            r'\d+\s*(?:mg|mcg|g|ml)\b.{0,10}' + re.escape(reaction),
            surrounding,
            re.IGNORECASE,
        ):
            continue

        ctx = text[max(0, m.start() - 30) : m.end() + 80]
        sm = SEVERITY_PATTERN.search(ctx)
        om = OUTCOME_PATTERN.search(ctx)
        ons = re.search(
            r'after\s+([\w\s]+?)(?:,|\.|\s+(?:he|she|the|patient))', ctx, re.IGNORECASE
        )
        severity = sm.group(0).lower() if sm else None
        outcome = om.group(0).lower() if om else "unknown"
        onset = ons.group(1).strip() if ons else None

        # Associate with nearest preceding drug
        preceding = [(pos, name) for pos, name in drug_positions if pos <= m.start()]
        if preceding:
            associated_drug = max(preceding, key=lambda x: x[0])[1]

        reactions.append({
            "reaction": reaction,
            "severity": severity,
            "onset": onset,
            "outcome": outcome,
            "drug": associated_drug,
        })

    # ── Patient demographics ──
    age_m = AGE_PATTERN.search(text)
    sex_m = SEX_PATTERN.search(text)
    caus_m = CAUSALITY_PATTERN.search(text)
    sev_m = SEVERITY_PATTERN.search(text)

    # ── Build drug -> reactions mapping ──
    drug_reaction_map = {}
    for d in drugs:
        drug_reaction_map[d["name"]] = []
    for r in reactions:
        assoc = r.get("drug")
        if assoc and assoc in drug_reaction_map:
            drug_reaction_map[assoc].append(r["reaction"])
        elif assoc is None:
            drug_reaction_map.setdefault("unknown", []).append(r["reaction"])

    result = {
        "drugs": drugs,
        "reactions": reactions,
        "patient": {
            "age": age_m.group(1) if age_m else None,
            "sex": sex_m.group(0).lower() if sex_m else None,
            "relevant_history": None,
        },
        "causality": caus_m.group(0).lower() if caus_m else "unassessable",
        "overall_severity": sev_m.group(0).lower() if sev_m else None,
        "drug_reaction_map": drug_reaction_map,
        "notes": f"Extracted using medspaCy rule-based NER. {len(drugs)} drug(s), {len(reactions)} reaction(s) found.",
    }

    result["confidence"] = calculate_confidence(result)
    return result


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_report(result):
    """Print a human-readable report of the extraction results."""
    print("=" * 60)
    print("  MEDSPACY EXTRACTION REPORT")
    print("=" * 60)

    # Drugs
    print("\nDRUGS FOUND:")
    if result["drugs"]:
        for d in result["drugs"]:
            info = f"  - {d['name']}"
            if d.get("dose"):
                info += f" ({d['dose']})"
            if d.get("route"):
                info += f" [{d['route']}]"
            if d.get("indication"):
                info += f" for {d['indication']}"
            print(info)
    else:
        print("  (none detected)")

    # Drug -> Reaction mapping
    print("\nDRUG -> REACTION MAPPING:")
    drm = result["drug_reaction_map"]
    if drm:
        for drug, rxns in drm.items():
            print(f"  {drug}:")
            if rxns:
                for r in rxns:
                    sev = f" [{r['severity']}]" if r.get("severity") else ""
                    out = f" -> {r['outcome']}" if r.get("outcome") and r["outcome"] != "unknown" else ""
                    print(f"    - {r['reaction']}{sev}{out}")
            else:
                print("    (no reactions)")
    else:
        print("  (no drug-reaction associations)")

    # Patient
    patient = result.get("patient", {})
    if patient.get("age") or patient.get("sex"):
        print(f"\nPATIENT: {patient.get('age', '?')}-year-old {patient.get('sex', 'unknown')}")

    # Confidence
    c = result["confidence"]
    print(f"\nCONFIDENCE: {c['score']}/{c['max']} ({c['verdict']})")
    print("=" * 60)


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
    args = [a for a in sys.argv[1:] if a not in ("--json", "--full")]

    if args and args[0] == "--file":
        if len(args) < 2:
            print("Usage: python medspacy_extract.py --file <path>")
            sys.exit(1)
        with open(args[1], "r") as f:
            narrative = f.read()
    elif args:
        narrative = " ".join(args)
    else:
        narrative = DEMO_NARRATIVE

    result = extract_medspacy(narrative)

    if "--full" in sys.argv:
        # Full JSON with all fields
        print(json.dumps(result, indent=2))
    else:
        # Default: focused JSON with only drugs, drug->reaction map, confidence
        output = {
            "drugs": [
                {k: v for k, v in d.items() if k != "route" and v is not None}
                for d in result["drugs"]
            ],
            "drug_reaction_map": result["drug_reaction_map"],
            "confidence": result["confidence"],
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
