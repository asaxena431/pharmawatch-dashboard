"""
get_fda_events.py
=================
Calls the openFDA API for a drug and returns all warnings and
adverse reactions as Python lists.
 
Usage:
  python get_fda_events.py tylenol
  python get_fda_events.py --list drugs.txt
  python get_fda_events.py --list drugs.txt --api-key KEY
"""
 
import sys
import os
import json
import re
import time
import threading
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
 
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
 
OPENFDA_URL     = "https://api.fda.gov/drug/label.json"
REACTIONS_FILE  = "reactions.txt"
OPENFDA_API_KEY = "zb5dtOaZ94XB883L1VhChdqVDB6nPOeq4D2sLrus"
CACHE_FILE      = "drug_reactions_cache.json"
 
DRUG_NAME_MAP = {
    "quetiapina":     "quetiapine",
    "ibuprofeno":     "ibuprofen",
    "paracetamol":    "acetaminophen",
    "amoxicilina":    "amoxicillin",
    "atorvastatina":  "atorvastatin",
    "metformina":     "metformin",
    "aspirina":       "aspirin",
    "warfarina":      "warfarin",
    "ciprofloxacina": "ciprofloxacin",
    "prednisona":     "prednisone",
    "lisinopril":     "lisinopril",
    "paracétamol":    "acetaminophen",
    "ibuprofène":     "ibuprofen",
    "amoxicilline":   "amoxicillin",
    "ácido acetilsalicílico": "aspirin",
}
 
 
def normalize_drug_name(drug_name: str) -> str:
    return DRUG_NAME_MAP.get(drug_name.lower().strip(), drug_name.strip())
 
 
_rate_lock     = threading.Lock()
_request_times = []
 
 
def _rate_limit(max_per_minute: int = 230):
    with _rate_lock:
        now = time.time()
        _request_times[:] = [t for t in _request_times if now - t < 60]
        if len(_request_times) >= max_per_minute:
            sleep_for = 60 - (now - _request_times[0]) + 0.1
            if sleep_for > 0:
                time.sleep(sleep_for)
        _request_times.append(time.time())
 
 
def load_llt_to_pt(path: str = REACTIONS_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    llt_to_pt = {}
    with open(path, encoding="utf-8") as f:
        lines = [l.rstrip("\n") for l in f.readlines()]
    i = 0
    while i < len(lines):
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        if i >= len(lines):
            break
        llt = lines[i].strip().lower()
        i += 1
        pt = lines[i].strip().lower() if i < len(lines) and lines[i].strip() else llt
        if i < len(lines) and lines[i].strip():
            i += 1
        if llt:
            llt_to_pt[llt] = pt
        while i < len(lines) and lines[i].strip() == "":
            i += 1
    return llt_to_pt
 
 
def extract_reaction_terms(text_list: list, llt_to_pt: dict) -> list:
    combined = " ".join(text_list).lower()
    matched_pts = []
    for llt in sorted(llt_to_pt.keys(), key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(llt) + r"(?!\w)"
        if re.search(pattern, combined):
            pt = llt_to_pt[llt]
            if pt not in matched_pts:
                matched_pts.append(pt)
    return matched_pts
 
 
def _label_has_content(label: dict) -> bool:
    """True if the label has any adverse-reaction or warning text we can parse."""
    for field in ("adverse_reactions", "warnings",
                  "warnings_and_cautions", "boxed_warning"):
        raw = label.get(field, [])
        if isinstance(raw, list):
            text = " ".join(raw)
        elif isinstance(raw, str):
            text = raw
        else:
            text = ""
        if text.strip():
            return True
    return False


def fetch_fda_label(drug_name: str, api_key: str = OPENFDA_API_KEY) -> dict:
    queries = [
        f'openfda.brand_name:"{drug_name}"',
        f'openfda.generic_name:"{drug_name}"',
        f'openfda.substance_name:"{drug_name}"',
        f'openfda.brand_name:{drug_name}',
        f'openfda.generic_name:{drug_name}',
    ]
    key_param = f"&api_key={api_key}" if api_key else ""
    max_rpm = 950 if api_key else 230
    fallback = {}
    for q in queries:
        _rate_limit(max_rpm)
        # Fetch multiple records: the first match is often a minimal SPL with
        # empty warnings/adverse_reactions, so pick one that actually has content.
        url = f"{OPENFDA_URL}?search={urllib.parse.quote(q)}&limit=50{key_param}"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception:
            continue
        results = data.get("results") or []
        if not results:
            continue
        if not fallback:
            fallback = results[0]
        for label in results:
            if _label_has_content(label):
                return label
    return fallback
 
 
def extract_events(label: dict) -> dict:
    adverse_reactions = []
    warnings = []
    ar_raw = label.get("adverse_reactions", [])
    if isinstance(ar_raw, list) and ar_raw:
        text = " ".join(ar_raw)
    elif isinstance(ar_raw, str):
        text = ar_raw
    else:
        text = ""
    if text:
        items = re.split(r"[,;\n•·]", text)
        for item in items:
            item = re.sub(r"\s+", " ", item).strip(" .()\t")
            if len(item) > 2:
                adverse_reactions.append(item)
    for field in ["warnings", "warnings_and_cautions", "boxed_warning"]:
        raw = label.get(field, [])
        if isinstance(raw, list) and raw:
            text = " ".join(raw)
        elif isinstance(raw, str):
            text = raw
        else:
            continue
        if text:
            items = re.split(r"(?<=[.!?])\s+|[\n•·]", text)
            for item in items:
                item = re.sub(r"\s+", " ", item).strip(" ()\t")
                if len(item) > 5:
                    warnings.append(item)
    adverse_reactions = list(dict.fromkeys(adverse_reactions))
    warnings          = list(dict.fromkeys(warnings))
    all_events        = list(dict.fromkeys(adverse_reactions + warnings))
    return {
        "adverse_reactions": adverse_reactions,
        "warnings":          warnings,
        "all_events":        all_events,
    }
 
 
def get_drug_events(drug_name: str,
                    reactions_file: str = REACTIONS_FILE,
                    api_key: str = OPENFDA_API_KEY) -> dict:
    drug_name    = drug_name.strip().lower()
    english_name = normalize_drug_name(drug_name)
    label = fetch_fda_label(english_name, api_key)
    if not label:
        return {
            "drug":                    drug_name,
            "found":                   False,
            "adverse_reactions":       [],
            "warnings":                [],
            "all_events":              [],
            "reactions_from_warnings": [],
        }
    events    = extract_events(label)
    llt_to_pt = load_llt_to_pt(reactions_file)
    source    = events["adverse_reactions"] if events["adverse_reactions"] else events["warnings"]
    reactions_from_warnings = extract_reaction_terms(source, llt_to_pt)
    return {
        "drug":                    drug_name,
        "found":                   True,
        "brand_name":              label.get("openfda", {}).get("brand_name", []),
        "generic_name":            label.get("openfda", {}).get("generic_name", []),
        "adverse_reactions":       events["adverse_reactions"],
        "warnings":                events["warnings"],
        "all_events":              events["all_events"],
        "reactions_from_warnings": reactions_from_warnings,
    }
 
 
class _CaseInsensitiveCache(dict):
    """dict whose key lookups are case-insensitive (keys stored lowercased)."""

    @staticmethod
    def _norm(key):
        return key.lower() if isinstance(key, str) else key

    def __init__(self, data=None):
        super().__init__()
        if data:
            for k, v in data.items():
                self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(self._norm(key), value)

    def __getitem__(self, key):
        return super().__getitem__(self._norm(key))

    def __contains__(self, key):
        return super().__contains__(self._norm(key))

    def get(self, key, default=None):
        return super().get(self._norm(key), default)


def load_cache(cache_file: str = CACHE_FILE) -> dict:
    if os.path.exists(cache_file):
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        return _CaseInsensitiveCache(data)
    return _CaseInsensitiveCache()
 
 
def get_from_cache(drug_name: str, cache_file: str = CACHE_FILE) -> list:
    cache = load_cache(cache_file)
    return cache.get(drug_name.strip().lower(), [])
 
 
def save_cache(cache: dict, cache_file: str = CACHE_FILE):
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
 
 
def bulk_fetch(drug_list: list,
               reactions_file: str = REACTIONS_FILE,
               api_key: str = OPENFDA_API_KEY,
               max_workers: int = 50,
               cache_file: str = CACHE_FILE) -> dict:
    cache     = load_cache(cache_file)
    llt_to_pt = load_llt_to_pt(reactions_file)
    pending   = [d for d in drug_list if d.lower() not in cache]
    print(f"Total drugs : {len(drug_list)}")
    print(f"Cached      : {len(drug_list) - len(pending)}")
    print(f"To fetch    : {len(pending)}")
    print(f"Workers     : {max_workers}")
    print(f"API key     : {'YES' if api_key else 'NO (240 req/min limit)'}")
 
    def fetch_one(drug):
        english = normalize_drug_name(drug)
        label   = fetch_fda_label(english, api_key)
        if not label:
            return drug.lower(), []
        events = extract_events(label)
        source = events["adverse_reactions"] if events["adverse_reactions"] else events["warnings"]
        pts    = extract_reaction_terms(source, llt_to_pt)
        return drug.lower(), pts
 
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fetch_one, d): d for d in pending}
        for future in as_completed(futures):
            drug, pts = future.result()
            cache[drug] = pts
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(pending)} fetched...")
            if done % 500 == 0:
                save_cache(cache, cache_file)
                print(f"  Cache saved ({done} done)")
    save_cache(cache, cache_file)
    print(f"Done. Cache saved to {cache_file}")
    return cache
 
 
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("drug",          nargs="?", default=None)
    parser.add_argument("--list",  "-l", default=None)
    parser.add_argument("--api-key","-k", default=OPENFDA_API_KEY)
    parser.add_argument("--workers","-w", type=int, default=50)
    parser.add_argument("--cache",  "-c", default=CACHE_FILE)
    args = parser.parse_args()
 
    if args.list:
        if not os.path.exists(args.list):
            print(f"Error: file not found: {args.list}", file=sys.stderr)
            sys.exit(1)
        with open(args.list, encoding="utf-8") as f:
            drug_list = [line.strip().lower() for line in f if line.strip()]
        print(f"Loaded {len(drug_list)} drugs from {args.list}")
        bulk_fetch(drug_list, api_key=args.api_key,
                   max_workers=args.workers, cache_file=args.cache)
 
    elif args.drug:
        result = get_drug_events(args.drug, api_key=args.api_key)
        cache = load_cache(args.cache)
        cache[result["drug"]] = result["reactions_from_warnings"]
        save_cache(cache, args.cache)
        print(f"\nDrug     : {result['drug']}")
        print(f"Found    : {result['found']}")
        print(f"Brand    : {result['brand_name']}")
        print(f"Generic  : {result['generic_name']}")
        print(f"\nAdverse Reactions ({len(result['adverse_reactions'])}):")
        for r in result["adverse_reactions"]:
            print(f"  - {r}")
        print(f"\nWarnings ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"  - {w}")
        print(f"\nReaction PT terms ({len(result['reactions_from_warnings'])}):")
        for pt in result["reactions_from_warnings"]:
            print(f"  - {pt}")
 
    else:
        parser.print_help()
