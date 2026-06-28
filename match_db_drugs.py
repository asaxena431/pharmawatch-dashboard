"""
match_db_drugs.py
=================
Check whether drugs extracted from a narrative are present in an existing DB
drug list, matching on either the trade (brand) name or the generic name.

Uses the trade<->generic mapping in orange_book.json (tradeToGeneric and
genericToBrands), so a narrative "Aleve" matches a DB entry "Naproxen Sodium".

From Python:
    from match_db_drugs import match_drugs
    result = match_drugs(["Aleve", "Aspirin"], ["Naproxen Sodium", "Aspirin"])
    # [{"drug": "Aleve", "in_db": True, "matched_on": ["naproxen", "naproxen sodium"]}, ...]

CLI:
    python match_db_drugs.py --narrative "Aleve,Aspirin" --db "Naproxen Sodium,Aspirin"
"""

import os
import json

ORANGE_BOOK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "orange_book.json")

# Salt / counter-ion suffixes stripped so "naproxen sodium" also matches "naproxen".
_SALTS = ("sodium", "hydrochloride", "hcl", "sulfate", "phosphate", "potassium",
          "calcium", "magnesium", "maleate", "tartrate", "mesylate", "besylate",
          "citrate", "acetate", "succinate", "fumarate", "bromide", "chloride")


def _norm(name: str) -> str:
    return name.strip().lower()


def _strip_salt(name: str) -> str:
    parts = name.split()
    while len(parts) > 1 and parts[-1] in _SALTS:
        parts = parts[:-1]
    return " ".join(parts)


def load_orange_book(path: str = ORANGE_BOOK_FILE) -> tuple:
    """Return (tradeToGeneric, genericToBrands) from orange_book.json."""
    if not os.path.exists(path):
        return {}, {}
    with open(path, encoding="utf-8") as f:
        ob = json.load(f)
    return ob.get("tradeToGeneric", {}), ob.get("genericToBrands", {})


def drug_synonyms(name: str, trade_to_generic: dict, generic_to_brands: dict) -> set:
    """All known names for a drug: itself, its generic(s), salt-stripped base, and brands."""
    name = _norm(name)
    syns = {name, _strip_salt(name)}
    # name may be a trade name -> add its generic(s) and their brands
    generic = trade_to_generic.get(name, "")
    for g in generic.split(";"):
        g = g.strip()
        if g:
            syns.add(g)
            syns.add(_strip_salt(g))
            syns.update(generic_to_brands.get(g, []))
            syns.update(generic_to_brands.get(_strip_salt(g), []))
    # name may itself be a generic -> add its brands
    syns.update(generic_to_brands.get(name, []))
    syns.update(generic_to_brands.get(_strip_salt(name), []))
    return {s for s in syns if s}


def match_drugs(narrative_drugs: list, drug_list: list,
                orange_book_path: str = ORANGE_BOOK_FILE) -> list:
    """
    For each drug from the narrative, report whether it exists in the DB drug
    list (matched on trade or generic name).

    Returns a list of {"drug", "in_db", "matched_on"}.
    """
    t2g, g2b = load_orange_book(orange_book_path)
    drug_db = {_norm(d) for d in drug_list} | {_strip_salt(_norm(d)) for d in drug_list}
    results = []
    for d in narrative_drugs:
        matched = sorted(drug_synonyms(d, t2g, g2b) & drug_db)
        results.append({"drug": d, "in_db": bool(matched), "matched_on": matched})
    return results


def match_drugs_map(narrative_drugs: list, drug_list: list,
                    orange_book_path: str = ORANGE_BOOK_FILE) -> dict:
    """
    Like match_drugs but returns a {drug_name: "yes"/"no"} map, so callers can do
    result["Aleve"] -> "yes".
    """
    return {r["drug"]: ("yes" if r["in_db"] else "no")
            for r in match_drugs(narrative_drugs, drug_list, orange_book_path)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Match narrative drugs against a DB drug list.")
    parser.add_argument("--narrative", required=True,
                        help="Comma-separated drug names from the narrative.")
    parser.add_argument("--db", required=True,
                        help="Comma-separated DB drug list.")
    args = parser.parse_args()

    narrative = [d.strip() for d in args.narrative.split(",") if d.strip()]
    db        = [d.strip() for d in args.db.split(",") if d.strip()]
    print(json.dumps(match_drugs(narrative, db), indent=2))
