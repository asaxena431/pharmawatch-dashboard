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

ORANGE_BOOK_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "orange_book.json")
BIOLOGICS_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "biologics_map.json")

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


def load_orange_book(path: str = ORANGE_BOOK_FILE,
                     biologics_path: str = BIOLOGICS_MAP_FILE) -> tuple:
    """Return (tradeToGeneric, genericToBrands), merging orange_book.json
    (small molecules) with biologics_map.json (biologics, e.g. Dupixent<->dupilumab)."""
    t2g, g2b = {}, {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            ob = json.load(f)
        t2g.update(ob.get("tradeToGeneric", {}))
        g2b.update(ob.get("genericToBrands", {}))
    # Merge biologics: it only stores brand->generic, so build the reverse
    # (generic->brands) too, enabling matches in both directions.
    if os.path.exists(biologics_path):
        with open(biologics_path, encoding="utf-8") as f:
            bio = json.load(f).get("tradeToGeneric", {})
        for brand, generic in bio.items():
            t2g.setdefault(brand, generic)
            g2b.setdefault(generic, [])
            if brand not in g2b[generic]:
                g2b[generic] = list(g2b[generic]) + [brand]
    return t2g, g2b


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


def _resolve_generic(name: str, t2g: dict, g2b: dict) -> str:
    """Return the generic name for a drug, or the name itself if it is already generic."""
    n = _norm(name)
    # If it's a trade name, look up its generic
    generic = t2g.get(n, "")
    if generic:
        return generic.split(";")[0].strip()
    # If it's already a generic (has brands mapped), return as-is
    if n in g2b:
        return n
    # Try salt-stripped form
    stripped = _strip_salt(n)
    generic = t2g.get(stripped, "")
    if generic:
        return generic.split(";")[0].strip()
    if stripped in g2b:
        return stripped
    return n


def match_drugs_map(narrative_drugs: list, drug_list: list,
                    drug_roles=None,
                    orange_book_path: str = ORANGE_BOOK_FILE) -> dict:
    """
    Like match_drugs but returns a {drug_name: {"role": ..., "generic": ...}} map.

    Parameters
    ----------
    narrative_drugs : list of str
        Drug names extracted from the narrative.
    drug_list : list of str
        Drug names present in the DB.
    drug_roles : dict or list, optional
        Mapping of DB drug name -> role string.  Accepts a dict
        (e.g. {"Aspirin": "primary suspect"}) or a list parallel to
        drug_list (e.g. ["primary suspect", "concomitant"]).
    orange_book_path : str
        Path to the orange_book.json file.

    Returns
    -------
    dict
        {drug_name: {"role": role_str, "generic": generic_name}}
        role is the role passed for the drug (matched by DB name, narrative
        name, or generic) when one was supplied; otherwise "yes" if the drug is
        in the DB but no role was given, or "no" if it is not in the DB.
        generic is the resolved generic name from orange_book / biologics_map.

        Example:
            drug_results["Aleve"]["role"]    -> "primary suspect"
            drug_results["Aleve"]["generic"] -> "naproxen"
    """
    # drug_roles can be a dict {drug_name: role} or a list parallel to drug_list
    if drug_roles and isinstance(drug_roles, list):
        roles_lower = {_norm(drug_list[i]): drug_roles[i]
                       for i in range(min(len(drug_list), len(drug_roles)))
                       if drug_roles[i]}
    elif drug_roles and isinstance(drug_roles, dict):
        roles_lower = {_norm(k): v for k, v in drug_roles.items()}
    else:
        roles_lower = {}
    t2g, g2b = load_orange_book(orange_book_path)
    results = {}
    for r in match_drugs(narrative_drugs, drug_list, orange_book_path):
        drug = r["drug"]
        generic = _resolve_generic(drug, t2g, g2b)
        # Resolve the role from any of the drug's known names: the matched DB
        # names, the narrative drug name itself, or its resolved generic. This
        # is done regardless of DB membership so an explicitly passed role is
        # always honored.
        role = None
        for name in list(r["matched_on"]) + [drug, generic]:
            role = roles_lower.get(_norm(name))
            if role:
                break
        if role:
            results[drug] = {"role": role, "generic": generic}
        else:
            results[drug] = {"role": "yes" if r["in_db"] else "no", "generic": generic}
    return results


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
