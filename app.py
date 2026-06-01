from flask import Flask, render_template, request, jsonify
import requests
import json
import re
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

app = Flask(__name__)

# ── Shared lists ─────────────────────────────────────────────────────────────
KNOWN_DRUGS = [
    "tylenol","acetaminophen","lipitor","atorvastatin","amoxicillin","ibuprofen",
    "advil","motrin","warfarin","coumadin","aspirin","metformin","lisinopril",
    "omeprazole","metoprolol","amlodipine","albuterol","prednisone","gabapentin",
    "hydrocodone","oxycodone","morphine","citalopram","sertraline","fluoxetine",
    "simvastatin","levothyroxine","azithromycin","ciprofloxacin","doxycycline",
    "penicillin","cephalexin","clindamycin","vancomycin","lorazepam","diazepam",
    "alprazolam","zolpidem","quetiapine","risperidone","olanzapine","haloperidol",
    "insulin","methotrexate"
]

KNOWN_REACTIONS = [
    "nausea","vomiting","diarrhea","diarrhoea","headache","dizziness","fatigue",
    "rash","itching","pruritus","liver damage","hepatotoxicity","myalgia",
    "muscle weakness","muscle pain","elevated liver enzymes","elevated alt",
    "elevated ast","elevated ck","jaundice","abdominal pain","chest pain",
    "shortness of breath","dyspnea","dyspnoea","anaphylaxis","urticaria",
    "angioedema","stevens-johnson syndrome","toxic epidermal necrolysis",
    "renal failure","kidney failure","seizure","confusion","hallucination",
    "insomnia","depression","anxiety","palpitations","tachycardia","bradycardia",
    "hypertension","hypotension","bleeding","bruising","thrombosis","stroke",
    "myocardial infarction","heart attack","back pain","joint pain","arthralgia",
    "swelling","edema","fever","pyrexia","chills","night sweats","weight gain",
    "weight loss","hair loss","alopecia","blurred vision","tinnitus","hearing loss",
    "blistering","mucosal involvement","muscle spasm","myopathy","rhabdomyolysis",
    "pancreatitis","peripheral neuropathy",
    "liver damage","skin reddening","stomach bleeding","allergic reaction",
    "difficulty breathing","serious skin reactions","dark urine","clay-colored stools",
    "loss of appetite","upper stomach pain"
]

DOSE_PATTERN     = re.compile(r'\b(\d+\.?\d*\s*(?:mg|mcg|ug|g|ml|units?|IU|mEq)(?:\s*/\s*(?:day|daily|kg|dose))?)\b', re.IGNORECASE)
SEVERITY_PATTERN = re.compile(r'\b(mild|moderate|severe|serious|fatal|life[\s-]threatening)\b', re.IGNORECASE)
OUTCOME_PATTERN  = re.compile(r'\b(resolv\w+|recover\w+|discharged|improved|died|fatal|death|ongoing|persistent|hospitali\w+)\b', re.IGNORECASE)
AGE_PATTERN      = re.compile(r'\b(\d+)[\s-]*(year|yr)s?[\s-]*old\b', re.IGNORECASE)
SEX_PATTERN      = re.compile(r'\b(male|female|man|woman|boy|girl)\b', re.IGNORECASE)
CAUSALITY_PATTERN= re.compile(r'\b(probable|possible|unlikely|definite|suspected|associated with|caused by)\b', re.IGNORECASE)

WARNING_KEYWORDS = [
    "Liver warning","Allergy alert","Do not use","Ask a doctor before use",
    "Ask a doctor or pharmacist before use","Stop use and ask a doctor",
    "Overdose warning","Keep out of reach","If pregnant or breast-feeding",
    "When using this product","Sore throat warning","Stomach bleeding warning",
    "Heart attack and stroke warning","Warnings"
]

# ── Core functions ────────────────────────────────────────────────────────────
def get_fda_label(drug_name):
    r = requests.get("https://api.fda.gov/drug/label.json",
                     params={"search": f'openfda.brand_name:"{drug_name}"', "limit": 1})
    data = r.json()
    if "results" not in data:
        return None
    label = data["results"][0]
    openfda = label.get("openfda", {})

    # Parse warnings into sections
    warnings_raw = label.get("warnings", [None])[0]
    warnings_structured = {}
    if warnings_raw:
        pattern = '|'.join(re.escape(k) for k in WARNING_KEYWORDS)
        parts = re.split(f'({pattern})', warnings_raw.strip())
        current_key = "general"
        buffer = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if re.fullmatch(pattern, part, re.IGNORECASE):
                if buffer:
                    warnings_structured[current_key] = " ".join(buffer).strip()
                    buffer = []
                current_key = part
            else:
                buffer.append(part)
        if buffer:
            warnings_structured[current_key] = " ".join(buffer).strip()
        warnings_structured.pop("general", None)

    BOILERPLATE = [
        "to report", "contact ", "call ", "fda at ", "1-800", "www.",
        "postmarketing", "the following", "table ", "clinical trial",
        "in clinical", "adverse reactions reported", "because these reactions",
        "voluntarily reported", "cannot be reliably", "adverse reaction reporting",
        "were white", "were black", "were female", "were male", "were hispanic",
        "were asian", "% white", "% black", "% female", "% male", "% hispanic",
        "% asian", "median age", "mean age", "age range", "years of age",
        "patients were", "subjects were", "participants were", "n =", "n=",
        "placebo", "treatment group", "study population", "enrolled",
        "randomized", "double-blind", "open-label", "most common",
        "incidence of", "occurred in", "reported in", "observed in",
        "compared to", "compared with", "versus", "vs.", "per 100",
        "rate of", "frequency of", "proportion of",
        "off label", "off-label", "label use", "prescribing information",
        "see full", "full prescribing", "medication guide", "package insert",
        "see section", "refer to", "please see", "for more information",
        "not established", "not been established", "has not been", "have not been",
        "is not known", "are not known", "was not evaluated", "were not evaluated",
        "in patients with", "in patients who", "in subjects", "in healthy",
        "doses of", "dose of", "mg dose", "mg/day", "mg/kg",
        "once daily", "twice daily", "three times", "four times",
        "clinical study", "clinical studies", "controlled trial", "controlled study",
        "safety and efficacy", "efficacy and safety", "safety profile",
        "no clinically", "not clinically", "not statistically", "statistically significant",
        "spontaneous report", "post-market", "postmarket", "surveillance"
    ]

    # Words/phrases that indicate a fragment is NOT a clinical reaction
    NON_CLINICAL = re.compile(
        r'^(and|or|in|of|at|to|the|a|an|with|for|by|as|on|also|respectively|'
        r'including|such as|following|both|either|neither|however|therefore|'
        r'additionally|furthermore|moreover|although|whereas|while|since|because|'
        r'compared|between|among|during|within|after|before|above|below|'
        r'these|those|this|that|which|who|than|then|when|where|'
        r'upon|if|when|should|must|do not|do|use|take|avoid|monitor|'
        r'consider|consult|contact|inform|instruct|advise|recommend|'
        r'discontinu|interrupt|reduc|increas|adjust|withhold|resume|'
        r'administer|prescri|dispens|store|keep|discard)\b',
        re.IGNORECASE
    )

    def _clean_items(raw_text):
        # Split only on sentence boundaries and semicolons — NOT on commas
        items = re.split(r'(?<=[.!?])\s+(?=[A-Z])|;\s*', raw_text.strip())
        cleaned = []
        for i in items:
            i = i.strip().rstrip('.,;')
            if len(i) < 6:
                continue
            if any(bp in i.lower() for bp in BOILERPLATE):
                continue
            # Drop percentage stats
            if re.search(r'\d+\s*%', i):
                continue
            # Drop pure numbers/punctuation
            if re.match(r'^[\d\s,\.\-]+$', i):
                continue
            # Drop dosage fragments: "10 mg", "and 80 mg", "40 and 80 mg", "10, 20, and 40 mg"
            if re.match(r'^[\w\s,\-]*\d+\s*(mg|mcg|ug|g|ml|units?|IU)\b[\w\s,\-]*$', i, re.IGNORECASE):
                continue
            # Drop items starting with non-clinical connector words
            if NON_CLINICAL.match(i):
                continue
            # Must have at least one meaningful clinical word (4+ letters)
            if not re.search(r'[a-zA-Z]{4,}', i):
                continue
            cleaned.append(i)
        return cleaned

    ar_raw = label.get("adverse_reactions", [None])[0]
    ar_list = _clean_items(ar_raw) if ar_raw else []

    # Build structured side_effects: {section, items[]}
    # Rx drugs: from adverse_reactions field
    # OTC drugs (null AR): extract symptom phrases from warnings
    REACTION_KEYWORDS = [
        "skin reddening", "blisters", "rash", "liver damage", "nausea", "vomiting",
        "stomach bleeding", "ulcer", "allergic reaction", "swelling", "hives",
        "difficulty breathing", "anaphylaxis", "dizziness", "headache", "fatigue",
        "ringing in ears", "tinnitus", "hearing loss", "vision", "kidney",
        "heart attack", "stroke", "serious skin reactions", "stevens-johnson",
        "toxic epidermal", "jaundice", "dark urine", "clay-colored", "itching",
        "bruising", "bleeding", "muscle pain", "weakness"
    ]

    side_effects = []

    # Section 1: Adverse Reactions
    if ar_list:
        side_effects.append({"section": "Adverse Reactions", "items": ar_list})

    # Section 2: Warnings — each item carries its sub-section name
    warning_items = []
    for sec_name, sec_text in warnings_structured.items():
        warning_items.append({"label": sec_name, "text": sec_text})
    if warning_items:
        side_effects.append({"section": "Warnings", "items": warning_items})

    return {
        "brand_name":     openfda.get("brand_name", ["N/A"]),
        "generic_name":   openfda.get("generic_name", ["N/A"]),
        "manufacturer":   openfda.get("manufacturer_name", ["N/A"]),
        "route":          openfda.get("route", ["N/A"]),
        "product_type":   openfda.get("product_type", ["N/A"]),
        "adverse_reactions": ar_list,
        "adverse_reactions_raw": ar_raw or "",
        "warnings_raw":   label.get("warnings", [None])[0] or label.get("warnings_and_precautions", [None])[0] or "",
        "side_effects":   side_effects,
        "warnings":       warnings_structured,
        "boxed_warning":  label.get("boxed_warning", [None])[0],
        "contraindications": label.get("contraindications", [None])[0],
        "drug_interactions": label.get("drug_interactions", [None])[0],
        "indications":    label.get("indications_and_usage", [None])[0],
        "dosage":         label.get("dosage_and_administration", [None])[0],
    }


def get_dailymed_narrative(drug_name):
    """Fetch structured narrative sections from openFDA drug label API."""
    try:
        r = requests.get(
            "https://api.fda.gov/drug/label.json",
            params={"search": f'openfda.brand_name:"{drug_name}"', "limit": 1},
            timeout=10
        )
        data = r.json()
        if "results" not in data:
            # fallback: generic name search
            r2 = requests.get(
                "https://api.fda.gov/drug/label.json",
                params={"search": f'openfda.generic_name:"{drug_name}"', "limit": 1},
                timeout=10
            )
            data = r2.json()
        if "results" not in data:
            return {"error": f"No label found for '{drug_name}'"}

        label  = data["results"][0]
        openfda = label.get("openfda", {})
        brand  = openfda.get("brand_name", [drug_name])[0]
        generic = openfda.get("generic_name", [""])[0]
        title  = f"{brand} ({generic})" if generic else brand

        # Map of openFDA field → display section name
        FIELD_MAP = [
            ("warnings_and_precautions",  "Warnings and Precautions"),
            ("warnings",                  "Warnings"),
            ("boxed_warning",             "Boxed Warning"),
            ("adverse_reactions",         "Adverse Reactions"),
            ("contraindications",         "Contraindications"),
            ("drug_interactions",         "Drug Interactions"),
            ("indications_and_usage",     "Indications and Usage"),
            ("dosage_and_administration", "Dosage and Administration"),
            ("clinical_pharmacology",     "Clinical Pharmacology"),
            ("mechanism_of_action",       "Mechanism of Action"),
            ("pharmacokinetics",          "Pharmacokinetics"),
            ("use_in_specific_populations", "Use in Specific Populations"),
            ("nursing_mothers",           "Nursing Mothers"),
            ("pediatric_use",             "Pediatric Use"),
            ("geriatric_use",             "Geriatric Use"),
        ]

        narrative_sections = []
        for field, display_name in FIELD_MAP:
            raw = label.get(field, [None])[0]
            if not raw:
                continue
            clean = re.sub(r'\s+', ' ', raw.strip())
            if len(clean) > 20:
                narrative_sections.append({"section": display_name, "text": clean})

        # Build DailyMed link via setid if available
        setid = label.get("set_id", "")
        dailymed_url = (
            f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"
            if setid else
            f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={drug_name}"
        )

        return {
            "drug":        drug_name,
            "title":       title,
            "sections":    narrative_sections,
            "dailymed_url": dailymed_url
        }
    except Exception as e:
        return {"error": str(e)}


def get_faers_reactions(drug_name, limit=500):
    url = f"https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:{drug_name.upper()}&count=patient.reaction.reactionmeddrapt.exact&limit={limit}"
    r = requests.get(url)
    data = r.json()
    if "results" in data:
        return [{"reaction": i["term"], "count": i["count"]} for i in data["results"]]
    return []


def extract_from_narrative(text):
    text_lower = text.lower()
    drugs, reactions = [], []

    for drug in KNOWN_DRUGS:
        if drug in text_lower:
            m = re.search(re.escape(drug), text_lower)
            dose = None
            if m:
                ctx = text[max(0, m.start()-10):m.end()+60]
                dm = DOSE_PATTERN.search(ctx)
                dose = dm.group(0) if dm else None
            drugs.append({"name": drug, "dose": dose})

    # Negation phrases to check in the window before a reaction mention
    NEGATION_PATTERN = re.compile(
        r'\b(no|not|without|denies|denied|deny|absence of|absent|free of|'
        r'negative for|never|ruled out|unremarkable for|fails to|'
        r'did not|does not|was not|were not|is not|are not)\b',
        re.IGNORECASE
    )

    # Patterns that indicate the match is inside a dosage/drug context, not a reaction
    DOSE_CONTEXT_PATTERN = re.compile(
        r'\b\d+\s*mg\b|\b\d+\s*mcg\b|\btablet\b|\bcapsule\b|\bdose\b|\bdosage\b|'
        r'\btreatment\b|\btherapy\b|\badminister\b|\bprescri\w+\b|\bformulation\b',
        re.IGNORECASE
    )

    for reaction in KNOWN_REACTIONS:
        # Use word-boundary regex match instead of substring search
        pattern = r'(?<!\w)' + re.escape(reaction) + r'(?!\w)'
        m = re.search(pattern, text_lower)
        if not m:
            continue
        severity, onset, outcome = None, None, "unknown"
        # Check 60-char window before the reaction for negation
        pre_ctx = text[max(0, m.start()-60):m.start()]
        if NEGATION_PATTERN.search(pre_ctx):
            continue  # skip negated reaction
        # Check that surrounding context looks clinical, not a dosage line
        surrounding = text[max(0, m.start()-40):m.end()+40]
        # Skip if the match is sandwiched between dose numbers like "40 mg and 80 mg"
        if re.search(r'\d+\s*(?:mg|mcg|g|ml)\b.{0,10}' + re.escape(reaction), surrounding, re.IGNORECASE):
            continue
        ctx = text[max(0, m.start()-30):m.end()+80]
        sm = SEVERITY_PATTERN.search(ctx)
        om = OUTCOME_PATTERN.search(ctx)
        severity = sm.group(0).lower() if sm else None
        outcome  = om.group(0).lower() if om else "unknown"
        ons_m = re.search(r'after\s+([\w\s]+?)(?:,|\.|\s+(?:he|she|the|patient))', ctx, re.IGNORECASE)
        onset = ons_m.group(1).strip() if ons_m else None
        reactions.append({"reaction": reaction, "severity": severity, "onset": onset, "outcome": outcome})

    age_m  = AGE_PATTERN.search(text)
    sex_m  = SEX_PATTERN.search(text)
    caus_m = CAUSALITY_PATTERN.search(text)
    sev_m  = SEVERITY_PATTERN.search(text)

    return {
        "drugs": drugs,
        "reactions": reactions,
        "patient": {"age": age_m.group(1) if age_m else None,
                    "sex": sex_m.group(0).lower() if sex_m else None},
        "causality":        caus_m.group(0).lower() if caus_m else "unassessable",
        "overall_severity": sev_m.group(0).lower() if sev_m else None,
    }


def compare_narrative_vs_faers(narrative_reactions, faers_reactions):
    faers_map = {r["reaction"].lower(): r["count"] for r in faers_reactions}
    in_faers, not_in_faers = [], []

    for r in narrative_reactions:
        term = r["reaction"].lower()
        keywords = [kw for kw in term.replace("-", " ").split() if len(kw) > 3]
        match = term if term in faers_map else None
        if not match:
            match = next((fk for fk in faers_map if all(kw in fk for kw in keywords)), None)

        entry = {**r,
                 "faers_matched_term": match,
                 "faers_report_count": faers_map.get(match, 0) if match else 0}
        (in_faers if match else not_in_faers).append(entry)

    return {"in_faers": in_faers, "not_in_faers": not_in_faers}


def compare_narrative_vs_label(narrative_reactions, label):
    warnings = label.get("warnings") or {}
    ar_list  = label.get("adverse_reactions") or []
    results = []

    for r in narrative_reactions:
        keywords = r["reaction"].lower().split()
        matched_cats = [c for c, t in warnings.items() if any(kw in t.lower() for kw in keywords)]
        matched_ar   = [a for a in ar_list if any(kw in a.lower() for kw in keywords)]
        found = bool(matched_cats or matched_ar)
        results.append({**r,
                        "found_in_label": found,
                        "matched_warning_sections": matched_cats,
                        "matched_adverse_reactions": matched_ar})
    return results


def build_full_comparison(narrative_reactions, label, faers_reactions):
    """Union all reactions from all 4 sources into one master list."""
    warnings = label.get("warnings") or {} if label else {}
    ar_list  = label.get("adverse_reactions") or [] if label else []
    faers_map = {r["reaction"].lower(): r["count"] for r in faers_reactions}

    # Section header names that must never appear as reaction rows
    SECTION_HEADERS = {kw.lower() for kw in WARNING_KEYWORDS}

    # Non-clinical FAERS terms to exclude from comparison rows
    NON_REACTION_FAERS = {
        "off label use", "drug ineffective", "fall", "death", "product quality issue",
        "no adverse event", "condition aggravated", "therapeutic response decreased",
        "drug interaction", "medication error", "wrong drug administered",
        "incorrect dose administered", "drug administration error",
        "intentional overdose", "accidental overdose", "overdose",
        "product use issue", "inappropriate schedule of drug administration",
        "lack of efficacy", "weight increased", "weight decreased",
    }

    # Use raw full-text fields for matching (not cleaned/truncated fragments)
    raw_ar_text   = (label.get("adverse_reactions_raw") or " ".join(ar_list)).lower()
    raw_warn_text = (label.get("warnings_raw") or " ".join(warnings.values())).lower()
    raw_label_all = raw_ar_text + " " + raw_warn_text

    rows = {}  # key = normalized reaction name

    def _label_sections(reaction_name):
        """Return Adverse Reactions and/or Warning tags.
        Requires the full term OR all meaningful keywords to appear in label text."""
        if not reaction_name:
            return []
        term = reaction_name.lower().strip()
        # Only use keywords that are long enough to be clinically meaningful (>4 chars)
        keywords = [kw for kw in re.split(r'\W+', term) if len(kw) > 4]
        sections = []

        def _matches(blob):
            # First try: exact full term match
            if term in blob:
                return True
            # Second try: ALL meaningful keywords must appear
            if keywords and all(kw in blob for kw in keywords):
                return True
            return False

        if _matches(raw_ar_text):
            sections.append("Adverse Reactions")
        if _matches(raw_warn_text):
            sections.append("Warning")
        return sections

    def _add(reaction_name, severity=None, onset=None, outcome="unknown",
             in_narrative=False, in_label=False, faers_count=0, label_sections=None):
        key = reaction_name.lower().strip()
        if key not in rows:
            rows[key] = {
                "reaction":       reaction_name,
                "severity":       severity,
                "onset":          onset,
                "outcome":        outcome,
                "in_narrative":   in_narrative,
                "found_in_label": in_label,
                "faers_count":    faers_count,
                "label_sections": label_sections or [],
            }
        else:
            if severity:      rows[key]["severity"]       = severity
            if onset:         rows[key]["onset"]          = onset
            if outcome != "unknown": rows[key]["outcome"] = outcome
            if in_narrative:  rows[key]["in_narrative"]   = True
            if in_label:      rows[key]["found_in_label"] = True
            if faers_count:   rows[key]["faers_count"]    = faers_count
            if label_sections:
                existing = rows[key]["label_sections"]
                rows[key]["label_sections"] = list(dict.fromkeys(existing + label_sections))

    # 1. Narrative reactions
    for r in narrative_reactions:
        key = r["reaction"].lower().strip()
        if key in SECTION_HEADERS:
            continue
        sections = _label_sections(r["reaction"])
        in_label = bool(sections)
        keywords = [kw for kw in key.split() if len(kw) > 3]
        fmatch = key if key in faers_map else next(
            (fk for fk in faers_map if all(kw in fk for kw in keywords if len(kw) > 3)), None)
        fc = faers_map.get(fmatch, 0) if fmatch else 0
        _add(r["reaction"], r.get("severity"), r.get("onset"), r.get("outcome", "unknown"),
             in_narrative=True, in_label=in_label, faers_count=fc, label_sections=sections)

    # 2. FDA label adverse reactions
    for ar in ar_list:
        term = ar.strip()
        if term.lower() in SECTION_HEADERS:
            continue
        if len(term) > 60:
            term = term[:60].rsplit(' ', 1)[0]
        key = term.lower()
        kws = [w for w in key.split() if len(w) > 3]
        fmatch = next((fk for fk in faers_map if all(kw in fk for kw in kws)), None) if kws else None
        fc = faers_map.get(fmatch, 0) if fmatch else 0
        warn_tag = ["Warning"] if any(any(kw in t.lower() for kw in kws) for t in warnings.values()) else []
        _add(term, in_label=True, faers_count=fc, label_sections=list(dict.fromkeys(["Adverse Reactions"] + warn_tag)))

    # 2b. Warning reactions — extract known clinical terms from all warning section texts
    # Always run so both Adverse Reactions AND Warnings appear in the comparison
    if warnings:
        for sec_name, sec_text in warnings.items():
            sec_lower = sec_text.lower()
            for reaction in KNOWN_REACTIONS:
                if reaction in sec_lower:
                    key = reaction.lower()
                    if key in SECTION_HEADERS or key in NON_REACTION_FAERS:
                        continue
                    fmatch = key if key in faers_map else next(
                        (fk for fk in faers_map if key in fk or fk in key), None)
                    fc = faers_map.get(fmatch, 0) if fmatch else 0
                    _add(reaction, in_label=True, faers_count=fc,
                         label_sections=["Warning"])

    # 3. FAERS top-50 reactions
    for fterm, fcount in list(faers_map.items())[:50]:
        if fterm.lower() in SECTION_HEADERS:
            continue
        if fterm.lower() in NON_REACTION_FAERS:
            continue
        sections = _label_sections(fterm)
        _add(fterm, in_label=bool(sections), faers_count=fcount, label_sections=sections)

    return list(rows.values())


def calculate_confidence(extracted):
    score = 0
    drugs     = extracted.get("drugs", [])
    reactions = extracted.get("reactions", [])

    if drugs:     score += 15
    if any(d.get("dose") for d in drugs): score += 10
    if reactions: score += 15
    if any(r.get("severity") for r in reactions): score += 8
    if any(r.get("onset") for r in reactions):    score += 4
    if any(r.get("outcome","unknown") != "unknown" for r in reactions): score += 3
    if extracted.get("patient", {}).get("age"):  score += 10
    if extracted.get("patient", {}).get("sex"):  score += 10
    if extracted.get("causality","unassessable") != "unassessable": score += 10
    if extracted.get("overall_severity"): score += 10
    if score >= 80: verdict, needs_gpt = "HIGH", False
    elif score >= 50: verdict, needs_gpt = "MEDIUM", True
    else: verdict, needs_gpt = "LOW", True

    return {"score": score, "max": 100, "verdict": verdict, "needs_gpt": needs_gpt}


# ── medspaCy extraction (rule-based) ────────────────────────────────────────
def extract_medspacy(text):
    """Rule-based extraction using regex patterns."""
    text_lower = text.lower()
    drugs, reactions = [], []

    for drug in KNOWN_DRUGS:
        if drug in text_lower:
            m = re.search(re.escape(drug), text_lower)
            dose = route = indication = None
            if m:
                ctx = text[max(0, m.start()-10):m.end()+80]
                dm = DOSE_PATTERN.search(ctx)
                rm = re.search(r'\b(oral(?:ly)?|IV|intravenous(?:ly)?|IM|subcutaneous(?:ly)?|SC|topical(?:ly)?|inhaled?)\b', ctx, re.IGNORECASE)
                im = re.search(r'for\s+([\w\s]+?)(?:\.|,|;|$)', ctx, re.IGNORECASE)
                dose       = dm.group(0) if dm else None
                route      = rm.group(0).lower() if rm else None
                indication = im.group(1).strip() if im else None
            drugs.append({"name": drug, "dose": dose, "route": route, "indication": indication})

    for reaction in KNOWN_REACTIONS:
        if reaction in text_lower:
            m = re.search(re.escape(reaction), text_lower)
            severity = onset = None
            outcome = "unknown"
            if m:
                ctx = text[max(0, m.start()-30):m.end()+80]
                sm  = SEVERITY_PATTERN.search(ctx)
                om  = OUTCOME_PATTERN.search(ctx)
                ons = re.search(r'after\s+([\w\s]+?)(?:,|\.|\s+(?:he|she|the|patient))', ctx, re.IGNORECASE)
                severity = sm.group(0).lower() if sm else None
                outcome  = om.group(0).lower() if om else "unknown"
                onset    = ons.group(1).strip() if ons else None
            reactions.append({"reaction": reaction, "severity": severity, "onset": onset, "outcome": outcome})

    age_m  = AGE_PATTERN.search(text)
    sex_m  = SEX_PATTERN.search(text)
    caus_m = CAUSALITY_PATTERN.search(text)
    sev_m  = SEVERITY_PATTERN.search(text)

    return {
        "drugs":    drugs,
        "reactions": reactions,
        "patient":  {"age": age_m.group(1) if age_m else None,
                     "sex": sex_m.group(0).lower() if sex_m else None,
                     "relevant_history": None},
        "causality":        caus_m.group(0).lower() if caus_m else "unassessable",
        "overall_severity": sev_m.group(0).lower() if sev_m else None,
        "notes": f"Extracted using medspaCy rule-based NER. {len(drugs)} drug(s), {len(reactions)} reaction(s) found."
    }


# ── GPT-4 extraction ─────────────────────────────────────────────────────────
GPT_SYSTEM = """You are a clinical pharmacovigilance expert.
Extract all drugs and adverse reactions from the clinical narrative.

CRITICAL RULES:
- ONLY include reactions that are AFFIRMED/PRESENT — do NOT include reactions that are negated, denied, absent, or ruled out.
- Examples of negated reactions to EXCLUDE: "no nausea", "denies headache", "without fever", "no evidence of bleeding", "ruled out hepatotoxicity".
- If a reaction is mentioned only in a negative context, omit it entirely from the output.

Return ONLY valid JSON:
{
  "drugs": [{"name": "", "dose": null, "route": null, "indication": null}],
  "reactions": [{"reaction": "", "severity": null, "onset": null, "outcome": "unknown"}],
  "patient": {"age": null, "sex": null, "relevant_history": null},
  "causality": null,
  "overall_severity": null,
  "notes": ""
}"""

def extract_gpt(text):
    """GPT-4o extraction via OpenAI API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not OPENAI_AVAILABLE:
        return None
    try:
        client = OpenAI(api_key=api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": GPT_SYSTEM},
                {"role": "user",   "content": f"Clinical Narrative:\n\n{text}"}
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=1000,
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        print(f"[GPT ERROR] {e}")
        return {"error": str(e), "drugs": [], "reactions": [], "patient": {}, "causality": None, "overall_severity": None, "notes": str(e)}


# ── Diff two extractions ──────────────────────────────────────────────────────
def diff_extractions(ms, gpt):
    if not gpt:
        return None
    ms_drugs     = {d["name"].lower() for d in ms.get("drugs", [])}
    gpt_drugs    = {d["name"].lower() for d in gpt.get("drugs", [])}
    ms_reactions = {r["reaction"].lower() for r in ms.get("reactions", [])}
    gpt_reactions= {r["reaction"].lower() for r in gpt.get("reactions", [])}

    reaction_detail_gaps = []
    for gr in gpt.get("reactions", []):
        for mr in ms.get("reactions", []):
            if gr["reaction"].lower() == mr["reaction"].lower():
                gaps = {}
                for field in ["severity", "onset", "outcome"]:
                    if mr.get(field) in (None, "unknown") and gr.get(field) not in (None, "unknown"):
                        gaps[field] = {"medspacy": mr.get(field), "gpt": gr.get(field)}
                if gaps:
                    reaction_detail_gaps.append({"reaction": gr["reaction"], "gaps": gaps})

    field_gaps = {}
    for field in ["causality", "overall_severity"]:
        mv, gv = ms.get(field), gpt.get(field)
        if mv != gv:
            field_gaps[field] = {"medspacy": mv, "gpt": gv}

    patient_gaps = {}
    for key in ["age", "sex", "relevant_history"]:
        mv = ms.get("patient", {}).get(key)
        gv = gpt.get("patient", {}).get(key)
        if mv != gv:
            patient_gaps[key] = {"medspacy": mv, "gpt": gv}

    return {
        "drugs_only_in_gpt":       sorted(gpt_drugs - ms_drugs),
        "drugs_only_in_medspacy":  sorted(ms_drugs - gpt_drugs),
        "reactions_only_in_gpt":   sorted(gpt_reactions - ms_reactions),
        "reactions_only_in_medspacy": sorted(ms_reactions - gpt_reactions),
        "reaction_detail_gaps":    reaction_detail_gaps,
        "field_gaps":              field_gaps,
        "patient_gaps":            patient_gaps,
        "summary": {
            "drugs_gpt_missed_by_medspacy":     len(gpt_drugs - ms_drugs),
            "reactions_gpt_missed_by_medspacy": len(gpt_reactions - ms_reactions),
            "total_gaps": len(field_gaps) + len(patient_gaps) + len(reaction_detail_gaps)
        }
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    from flask import make_response
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


@app.route("/api/fda-label", methods=["POST"])
def api_fda_label():
    drug = request.json.get("drug_name", "").strip()
    if not drug:
        return jsonify({"error": "Drug name required"}), 400
    label = get_fda_label(drug)
    if not label:
        return jsonify({"error": f"No FDA label found for '{drug}'"}), 404
    return jsonify(label)


@app.route("/api/dailymed", methods=["POST"])
def api_dailymed():
    drug = request.json.get("drug_name", "").strip()
    if not drug:
        return jsonify({"error": "Drug name required"}), 400
    result = get_dailymed_narrative(drug)
    return jsonify(result)


@app.route("/api/faers", methods=["POST"])
def api_faers():
    drug = request.json.get("drug_name", "").strip()
    if not drug:
        return jsonify({"error": "Drug name required"}), 400
    reactions = get_faers_reactions(drug)
    return jsonify({"drug": drug, "reactions": reactions, "total": len(reactions)})


@app.route("/api/analyze-narrative", methods=["POST"])
def api_analyze_narrative():
    data      = request.json
    narrative = data.get("narrative", "").strip()
    drug_name = data.get("drug_name", "").strip()

    if not narrative:
        return jsonify({"error": "Narrative text required"}), 400

    extracted   = extract_from_narrative(narrative)
    confidence  = calculate_confidence(extracted)
    label       = get_fda_label(drug_name) if drug_name else None
    faers       = get_faers_reactions(drug_name) if drug_name else []

    label_comparison = compare_narrative_vs_label(extracted["reactions"], label) if label else []
    faers_comparison = compare_narrative_vs_faers(extracted["reactions"], faers) if faers else {}
    full_rows        = build_full_comparison(extracted["reactions"], label or {}, faers)

    return jsonify({
        "extracted":         extracted,
        "confidence":        confidence,
        "label_comparison":  label_comparison,
        "faers_comparison":  faers_comparison,
        "full_rows":         full_rows,
        "novel_reactions":   [r for r in full_rows if not r.get("found_in_label")],
        "label_gaps": [r for r in label_comparison if not r.get("found_in_label")],
    })


@app.route("/api/compare-engines", methods=["POST"])
def api_compare_engines():
    """Run medspaCy + GPT-4 on same narrative and return side-by-side diff."""
    data      = request.json
    narrative = data.get("narrative", "").strip()
    drug_name = data.get("drug_name", "").strip()

    if not narrative:
        return jsonify({"error": "Narrative text required"}), 400

    try:
        ms_result  = extract_medspacy(narrative)
        gpt_result = extract_gpt(narrative)
        diff       = diff_extractions(ms_result, gpt_result)

        ms_conf  = calculate_confidence(ms_result)
        gpt_conf = calculate_confidence(gpt_result) if gpt_result else None

        merged = ms_result.copy()
        if gpt_result and not gpt_result.get("error"):
            # Merge ALL GPT reactions (deduplicated by name)
            ms_rxn_keys = {r["reaction"].lower() for r in ms_result.get("reactions", [])}
            all_gpt_reactions = [r for r in gpt_result.get("reactions", [])
                                 if r["reaction"].lower() not in ms_rxn_keys]
            merged["reactions"] = ms_result.get("reactions", []) + all_gpt_reactions
            # Merge drugs
            ms_drug_keys = {d["name"].lower() for d in ms_result.get("drugs", [])}
            all_gpt_drugs = [d for d in gpt_result.get("drugs", [])
                             if d["name"].lower() not in ms_drug_keys]
            merged["drugs"] = ms_result.get("drugs", []) + all_gpt_drugs
            if not merged.get("causality") or merged["causality"] == "unassessable":
                merged["causality"] = gpt_result.get("causality") or "unassessable"
            if not merged.get("overall_severity"):
                merged["overall_severity"] = gpt_result.get("overall_severity")
            if not merged.get("patient", {}).get("relevant_history"):
                merged.setdefault("patient", {})["relevant_history"] = \
                    gpt_result.get("patient", {}).get("relevant_history")

        label = get_fda_label(drug_name) if drug_name else None
        faers = get_faers_reactions(drug_name) if drug_name else []
        label_comparison = compare_narrative_vs_label(merged["reactions"], label) if label else []
        faers_comparison = compare_narrative_vs_faers(merged["reactions"], faers) if faers else {}
        full_rows        = build_full_comparison(merged["reactions"], label or {}, faers)

        return jsonify({
            "medspacy":            ms_result,
            "medspacy_confidence": ms_conf,
            "gpt":                 gpt_result,
            "gpt_confidence":      gpt_conf,
            "gpt_available":       bool(os.environ.get("OPENAI_API_KEY") and OPENAI_AVAILABLE),
            "diff":                diff,
            "merged":              merged,
            "label_comparison":    label_comparison,
            "faers_comparison":    faers_comparison,
            "full_rows":           full_rows,
            "label_gaps":          [r for r in label_comparison if not r.get("found_in_label")],
            "novel_reactions":     [r for r in full_rows if not r.get("found_in_label")],
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/check-openai", methods=["GET"])
def api_check_openai():
    available = bool(os.environ.get("OPENAI_API_KEY") and OPENAI_AVAILABLE)
    return jsonify({"available": available})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(debug=False, host="0.0.0.0", port=port)
