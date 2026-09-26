"""
Person-name detection for names that carry no title (Appendix 6: patient /
reporter / medical-personnel names are (B)(6) even without "Dr." or "Mr.").

Backends, tried in order (first one available wins unless MDR_NER_BACKEND is set):
  hf         HuggingFace Inference API (same mechanism as app.py) -- needs HUGGINGFACE_API_KEY
  spacy      local spaCy model if installed (en_core_web_sm / md / lg)
  heuristic  stdlib regex: Title-Case names following a role cue ("reported by", "nurse",
             "patient", ...) or followed by clinical credentials ("Jane Doe, RN")

Every backend returns [(start, end, text)] spans over the ORIGINAL string.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

HF_NER_MODEL = os.environ.get("MDR_HF_NER_MODEL", "dslim/bert-base-NER")

_NAME = r"[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?"
_FULL_NAME = rf"{_NAME}(?:\s+{_NAME}){{1,2}}"

_ROLE_CUE = re.compile(
    rf"\b(?i:reported\s+by|reporter(?:\s+name)?:?|contact(?:\s+person)?:?|per|from|"
    rf"(?:the\s+)?(?:patient|pt|nurse|physician|surgeon|clinician|technician|technologist|"
    rf"biomed|risk\s+manager|sales\s+rep(?:resentative)?|field\s+rep(?:resentative)?|"
    rf"representative|engineer|caregiver|spouse|wife|husband|mother|father|daughter|son))"
    rf"\s*,?\s+(?i:named\s+|is\s+|was\s+)?({_FULL_NAME})\b"
)
_CREDENTIALED = re.compile(
    rf"\b({_FULL_NAME}),?\s+(?:RN|MD|DO|PA|NP|LPN|CRNA|PharmD|RT|BSN|MSN|CNM|DDS|DVM|PhD)\b"
)
_NAME_IS = re.compile(rf"\bname\s+(?:is|was|of)\s+({_FULL_NAME})\b", re.I)

# Title-Case words that look like names but never are, in this domain
_STOP = {
    "Medical", "Device", "Report", "Hospital", "Center", "Centre", "Clinic", "Health", "System",
    "Medtronic", "Abbott", "Boston", "Scientific", "Siemens", "Healthineers", "Ethicon", "Integra",
    "United", "States", "North", "South", "East", "West", "New", "York", "Carolina", "General",
    "Adverse", "Event", "Not", "Unknown", "Serial", "Number", "Lot", "Model", "Catalog",
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "Food", "Drug", "Administration", "User", "Facility", "Risk", "Manager",
}


def _ok(name: str) -> bool:
    return not any(w in _STOP for w in name.split())


def heuristic(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for pat in (_ROLE_CUE, _CREDENTIALED, _NAME_IS):
        for m in pat.finditer(text):
            if _ok(m.group(1)):
                spans.append((m.start(1), m.end(1), m.group(1)))
    return spans


def spacy_backend(text: str) -> list[tuple[int, int, str]] | None:
    try:
        import spacy  # type: ignore
    except ImportError:
        return None
    nlp = None
    for model in ("en_core_web_sm", "en_core_web_md", "en_core_web_lg"):
        try:
            nlp = spacy.load(model)
            break
        except OSError:
            continue
    if nlp is None:
        return None
    return [(e.start_char, e.end_char, e.text) for e in nlp(text).ents if e.label_ == "PERSON"]


def hf_backend(text: str) -> list[tuple[int, int, str]] | None:
    api_key = os.environ.get("HUGGINGFACE_API_KEY")
    if not api_key:
        return None
    req = urllib.request.Request(
        f"https://api-inference.huggingface.co/models/{HF_NER_MODEL}",
        data=json.dumps({"inputs": text[:2000], "parameters": {"aggregation_strategy": "simple"}}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ents = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None
    if not isinstance(ents, list):
        return None
    return [
        (int(e["start"]), int(e["end"]), text[int(e["start"]):int(e["end"])])
        for e in ents
        if str(e.get("entity_group", e.get("entity", ""))).endswith("PER") and "start" in e
    ]


BACKENDS = {"hf": hf_backend, "spacy": spacy_backend, "heuristic": heuristic}


def find_person_names(text: str, backend: str | None = None) -> list[tuple[int, int, str]]:
    """Return non-overlapping name spans sorted by position. Never raises."""
    order = [backend] if backend else [os.environ.get("MDR_NER_BACKEND")] if os.environ.get("MDR_NER_BACKEND") \
        else ["hf", "spacy", "heuristic"]
    spans: list[tuple[int, int, str]] | None = None
    for name in order:
        fn = BACKENDS.get(name)
        if fn is None:
            continue
        spans = fn(text)
        if spans is not None:
            # model backends miss cue-based cases occasionally; union with heuristic
            if name != "heuristic":
                spans = spans + heuristic(text)
            break
    spans = spans or []
    spans.sort()
    merged: list[tuple[int, int, str]] = []
    for s in spans:
        if merged and s[0] < merged[-1][1]:
            continue
        merged.append(s)
    return merged
