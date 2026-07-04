#!/usr/bin/env python3
"""
Standalone OpenAI clinical narrative extraction tool.

Usage:
    python extract_openai_standalone.py <case_id> <narrative_file>
    python extract_openai_standalone.py <case_id> --text "narrative text here"

Results are appended to output/results.json.
"""

import sys
import os
import json
import re
import argparse

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ── De-identification ────────────────────────────────────────────────────────
_DEID_RULES = [
    (re.compile(r'\b\d{3}-\d{2}-\d{4}\b'), '[SSN]'),
    (re.compile(r'\b(?:MRN|Patient\s*ID|Chart\s*(?:No|#)?)[:\s#]*\d{4,12}\b', re.IGNORECASE), '[MRN]'),
    (re.compile(r'\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b'), '[PHONE]'),
    (re.compile(r'\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b'), '[EMAIL]'),
    (re.compile(r'\b(?:\d{1,2}[/-])?\d{1,2}[/-]\d{2,4}\b'), '[DATE]'),
    (re.compile(r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
                r'Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
                r'\s+\d{1,2},?\s+\d{4}\b', re.IGNORECASE), '[DATE]'),
    (re.compile(r'\bborn\s+in\s+\d{4}\b', re.IGNORECASE), 'born in [YEAR]'),
    (re.compile(r'\b(?:DOB|Date\s+of\s+Birth)[:\s]+[\w/,-]+', re.IGNORECASE), '[DOB]'),
    (re.compile(r'\b(?:Mr\.?|Mrs\.?|Ms\.?|Miss|Dr\.?|Prof\.?)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b'), '[NAME]'),
    (re.compile(r'\b\d{1,5}\s+[A-Z][a-zA-Z\s]{2,30}(?:Street|St|Avenue|Ave|Road|Rd|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Way)\b',
                re.IGNORECASE), '[ADDRESS]'),
    (re.compile(r'\b\d{5}(?:-\d{4})?\b'), '[ZIP]'),
    (re.compile(r'\b[A-Z][a-zA-Z\s]{2,40}(?:Hospital|Clinic|Medical\s+Center|Health\s+System|'
                r'Medical\s+Group|Healthcare)\b'), '[FACILITY]'),
]


def deidentify_text(text):
    """Remove PHI/PII from text before sending to external AI APIs."""
    redacted = text
    findings = []
    for pattern, label in _DEID_RULES:
        matches = pattern.findall(redacted)
        if matches:
            for m in matches:
                findings.append({"original": m if isinstance(m, str) else m[0], "replaced_with": label})
            redacted = pattern.sub(label, redacted)
    return redacted, findings


# ── OpenAI Extraction ────────────────────────────────────────────────────────
OPENAI_EXTRACT_SYSTEM = """You are a clinical pharmacovigilance expert. Extract structured data from the clinical narrative.

Return ONLY valid JSON with this exact structure:
{
  "drugs": [{"name": "", "dose": null, "route": null, "indication": null}],
  "reactions": [{"reaction": "", "drug": null, "severity": null, "onset": null, "outcome": "unknown"}],
  "patient": {"age": null, "sex": null, "relevant_history": null},
  "causality": null,
  "overall_severity": null,
  "notes": ""
}

Rules:
- drugs[].name: lowercase drug name
- drugs[].dose: dosage string or null
- drugs[].route: one of "oral", "iv", "intravenous", "im", "subcutaneous", "sc", "topical", "inhaled" or null
- drugs[].indication: reason for use or null
- reactions[].reaction: lowercase reaction name
- reactions[].drug: name of the drug most likely responsible (lowercase) or null
- reactions[].severity: one of "mild", "moderate", "severe", "life-threatening" (lowercase) or null
- reactions[].onset: time after drug administration (e.g. "2 days") or null
- reactions[].outcome: one of "recovered", "recovering", "not recovered", "fatal", "unknown" (lowercase)
- patient.age: age as string (e.g. "65") or null
- patient.sex: "male" or "female" (lowercase) or null
- patient.relevant_history: brief relevant medical history or null
- causality: one of "certain", "probable", "possible", "unlikely", "conditional", "unassessable" (lowercase)
- overall_severity: one of "mild", "moderate", "severe", "life-threatening" (lowercase) or null
- notes: brief extraction summary
"""


def extract_openai(text):
    """OpenAI-based clinical narrative extraction."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "drugs": [], "reactions": [], "drug_reaction_map": {},
            "patient": {"age": None, "sex": None, "relevant_history": None},
            "causality": "unassessable", "overall_severity": None,
            "notes": "OpenAI API key not configured. Set OPENAI_API_KEY environment variable."
        }
    if not OPENAI_AVAILABLE:
        return {
            "drugs": [], "reactions": [], "drug_reaction_map": {},
            "patient": {"age": None, "sex": None, "relevant_history": None},
            "causality": "unassessable", "overall_severity": None,
            "notes": "openai package not installed. Run: pip install openai"
        }

    safe_text, deid_findings = deidentify_text(text)
    try:
        client = OpenAI(api_key=api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": OPENAI_EXTRACT_SYSTEM},
                {"role": "user", "content": f"Clinical Narrative:\n\n{safe_text}"}
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=1500,
        )
        result = json.loads(resp.choices[0].message.content)

        # Normalize output structure
        drugs = []
        for d in result.get("drugs", []):
            drugs.append({
                "name": (d.get("name") or "").strip().title(),
                "dose": d.get("dose"),
                "route": (d.get("route") or "").lower().strip() or None,
                "indication": d.get("indication"),
            })

        reactions = []
        for r in result.get("reactions", []):
            reactions.append({
                "reaction": (r.get("reaction") or "").strip().title(),
                "severity": (r.get("severity") or "").lower().strip() or None,
                "onset": r.get("onset"),
                "outcome": (r.get("outcome") or "unknown").lower().strip(),
                "drug": (r.get("drug") or "").strip().title() or None,
            })

        patient = result.get("patient", {})
        age = patient.get("age")
        sex = (patient.get("sex") or "").lower().strip() or None
        relevant_history = patient.get("relevant_history")

        causality = (result.get("causality") or "unassessable").lower().strip()
        overall_severity = (result.get("overall_severity") or "").lower().strip() or None

        drug_reaction_map = build_drug_reaction_map(drugs, reactions)

        return {
            "drugs": drugs,
            "reactions": reactions,
            "drug_reaction_map": drug_reaction_map,
            "patient": {"age": age, "sex": sex, "relevant_history": relevant_history},
            "causality": causality,
            "overall_severity": overall_severity,
            "notes": f"Extracted using OpenAI GPT-4o. {len(drugs)} drug(s), {len(reactions)} reaction(s) found.",
            "deidentified_count": len(deid_findings),
        }
    except Exception as e:
        return {
            "drugs": [], "reactions": [], "drug_reaction_map": {},
            "patient": {"age": None, "sex": None, "relevant_history": None},
            "causality": "unassessable", "overall_severity": None,
            "notes": f"OpenAI extraction failed: {e}"
        }


# ── Confidence scoring ────────────────────────────────────────────────────

def calculate_pair_confidence(drug_info, reaction_info):
    """Calculate confidence score for a specific drug-reaction pair."""
    score = 0
    max_score = 100

    # Drug evidence (up to 40 points)
    score += 15                                          # drug was identified
    if drug_info.get("dose"):       score += 15          # dose found
    if drug_info.get("route"):      score += 5           # route found
    if drug_info.get("indication"): score += 5           # indication found

    # Reaction evidence (up to 35 points)
    score += 15                                          # reaction was identified
    if reaction_info.get("severity"): score += 10        # severity mentioned
    if reaction_info.get("onset"):    score += 5         # temporal info
    if reaction_info.get("outcome") and reaction_info["outcome"] != "unknown":
        score += 5                                       # outcome known

    # Association strength (up to 25 points)
    if reaction_info.get("drug") == drug_info.get("name"):
        score += 25                                      # directly associated

    if score >= 80:
        verdict = "HIGH"
    elif score >= 50:
        verdict = "MEDIUM"
    else:
        verdict = "LOW"

    return {"score": score, "max": max_score, "verdict": verdict}


def build_drug_reaction_map(drugs, reactions):
    """Build drug -> reactions mapping with per-pair confidence scores."""
    drugs_by_name = {d["name"]: d for d in drugs}
    drug_reaction_map = {}
    for d in drugs:
        drug_reaction_map[d["name"]] = []
    for r in reactions:
        assoc = r.get("drug")
        if assoc and assoc in drug_reaction_map:
            pair_conf = calculate_pair_confidence(drugs_by_name[assoc], r)
            drug_reaction_map[assoc].append({
                "reaction": r["reaction"],
                "severity": r.get("severity"),
                "onset": r.get("onset"),
                "outcome": r.get("outcome", "unknown"),
                "confidence": pair_conf,
            })
        else:
            pair_conf = calculate_pair_confidence({}, r)
            drug_reaction_map.setdefault("unattributed", []).append({
                "reaction": r["reaction"],
                "severity": r.get("severity"),
                "onset": r.get("onset"),
                "outcome": r.get("outcome", "unknown"),
                "confidence": pair_conf,
            })
    return drug_reaction_map


# ── Results file management ──────────────────────────────────────────────────
DEFAULT_RESULTS_FILE = os.path.join("output", "results.json")


def load_results(results_file):
    """Load existing results from JSON file (UTF-8)."""
    if not os.path.exists(results_file):
        return []
    with open(results_file, "r", encoding="utf-8") as f:
        return json.load(f)


def save_results(all_cases, results_file):
    """Save results to JSON file (UTF-8)."""
    os.makedirs(os.path.dirname(results_file) or ".", exist_ok=True)
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(all_cases, f, indent=2, ensure_ascii=False)


def print_summary(all_cases):
    """Print summary table of all extracted cases."""
    print("\n" + "=" * 60)
    print("Summary of All Cases")
    print("=" * 60)
    print(f"{'Case ID':<12} {'Drugs':<8} {'Reactions':<12} {'Causality':<15} {'Severity'}")
    print("-" * 60)
    for case in all_cases:
        result = case.get("result", {})
        case_id = case.get("case_id", "?")
        n_drugs = len(result.get("drugs", []))
        n_reactions = len(result.get("reactions", []))
        causality = result.get("causality", "?")
        severity = result.get("overall_severity", "?") or "?"
        print(f"{case_id:<12} {n_drugs:<8} {n_reactions:<12} {causality:<15} {severity}")
    print("-" * 60)
    print(f"Total cases: {len(all_cases)}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Extract clinical data from narrative using OpenAI GPT-4o"
    )
    parser.add_argument("case_id", help="Case ID (e.g. FAERS ISR number)")
    parser.add_argument("narrative_file", nargs="?", help="Path to text file containing the narrative")
    parser.add_argument("--text", help="Narrative text directly (alternative to file)")
    parser.add_argument("--output", default=DEFAULT_RESULTS_FILE, help="Output JSON file path")
    parser.add_argument("--no-summary", action="store_true", help="Skip printing summary table")
    args = parser.parse_args()

    # Normalize case ID (strip whitespace and any trailing ':')
    args.case_id = str(args.case_id).strip().rstrip(":").strip()

    # Determine narrative source
    if args.text:
        narrative = args.text
        print(f"[INFO] Using narrative from --text argument")
    elif args.narrative_file:
        if not os.path.exists(args.narrative_file):
            print(f"[ERROR] File not found: {args.narrative_file}", file=sys.stderr)
            sys.exit(1)
        with open(args.narrative_file, "r", encoding="utf-8") as f:
            narrative = f.read().strip()
        print(f"[INFO] Read narrative from file: {args.narrative_file}")
    else:
        print("[ERROR] Provide either a narrative file or --text argument", file=sys.stderr)
        sys.exit(1)

    if not narrative:
        print("[ERROR] Narrative is empty", file=sys.stderr)
        sys.exit(1)

    results_file = args.output

    # Display info
    print("=" * 60)
    print("OpenAI Clinical Narrative Extraction")
    print("=" * 60)
    print(f"\nCase ID: {args.case_id}")
    preview = narrative[:150] + "..." if len(narrative) > 150 else narrative
    print(f"Narrative:\n\"{preview}\"\n")
    print("-" * 60)

    # Extract
    result = extract_openai(narrative)

    # Display extraction results
    print(f"\nExtraction Results:")
    print(f"  Drugs found:    {len(result.get('drugs', []))}")
    for d in result.get("drugs", []):
        dose_info = f" ({d['dose']})" if d.get("dose") else ""
        print(f"    - {d['name']}{dose_info}")
    print(f"  Reactions found: {len(result.get('reactions', []))}")
    for r in result.get("reactions", []):
        sev = f" [{r['severity']}]" if r.get("severity") else ""
        drug = f" (from {r['drug']})" if r.get("drug") else ""
        print(f"    - {r['reaction']}{sev}{drug}")
    print(f"  Patient: age={result.get('patient', {}).get('age')}, sex={result.get('patient', {}).get('sex')}")
    print(f"  Causality: {result.get('causality')}")
    print(f"  Severity:  {result.get('overall_severity')}")

    # Display drug_reaction_map
    drm = result.get("drug_reaction_map", {})
    if drm:
        print(f"\n  Drug-Reaction Map:")
        for drug_name, rxns in drm.items():
            print(f"    {drug_name}:")
            if rxns:
                for rx in rxns:
                    conf = rx.get("confidence", {})
                    conf_str = f" [confidence: {conf.get('score', '?')}/{conf.get('max', 100)} {conf.get('verdict', '')}]"
                    sev = f" ({rx['severity']})" if rx.get("severity") else ""
                    print(f"      - {rx['reaction']}{sev}{conf_str}")
            else:
                print(f"      (no reactions)")

    print(f"\n  Notes: {result.get('notes')}")

    # Save to results file
    all_cases = load_results(results_file)

    # Update existing case or append new
    case_entry = {
        "case_id": args.case_id,
        "narrative_preview": narrative[:200],
        "result": result,
    }

    existing_idx = next((i for i, c in enumerate(all_cases) if c.get("case_id") == args.case_id), None)
    if existing_idx is not None:
        all_cases[existing_idx] = case_entry
        print(f"\n[SAVED] Case {args.case_id} -> {results_file} (updated, {len(all_cases)} total cases)")
    else:
        all_cases.append(case_entry)
        print(f"\n[SAVED] Case {args.case_id} -> {results_file} ({len(all_cases)} total cases)")

    save_results(all_cases, results_file)

    # Print summary
    if not args.no_summary:
        print_summary(all_cases)


if __name__ == "__main__":
    main()
