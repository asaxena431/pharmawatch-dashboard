"""
download_orange_book.py
=======================
Downloads products.txt from the FDA Orange Book and extracts:
  - Generic name  (Ingredient column)
  - Trade name    (Trade_Name column)

Saves two files:
  drugs.txt         - all unique generic + trade names (for use in extract_narrative.py)
  orange_book.json  - full mapping { trade_name: generic_name, ... }

Usage:
  python download_orange_book.py
  python download_orange_book.py --out-drugs drugs.txt --out-json orange_book.json
"""

import urllib.request
import zipfile
import io
import csv
import json
import os
import sys
import argparse

ORANGE_BOOK_ZIP = "https://www.fda.gov/media/76860/download"
OUT_DRUGS       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drugs.txt")
OUT_JSON        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orange_book.json")


def download_products() -> list[dict]:
    """Download Orange Book zip and parse products.txt. Returns list of row dicts."""
    print("Downloading Orange Book from FDA...", flush=True)
    req = urllib.request.Request(
        ORANGE_BOOK_ZIP,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    print(f"Downloaded {len(data):,} bytes.", flush=True)

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        print(f"Files in zip: {names}")
        # products.txt is the main file
        products_name = next((n for n in names if "product" in n.lower()), None)
        if not products_name:
            raise FileNotFoundError(f"products.txt not found in zip. Files: {names}")
        with z.open(products_name) as f:
            content = f.read().decode("latin-1")

    rows = []
    reader = csv.DictReader(io.StringIO(content), delimiter="~")
    for row in reader:
        rows.append(row)
    print(f"Parsed {len(rows):,} product rows.", flush=True)
    return rows


def extract_names(rows: list[dict]) -> tuple[dict, list[str]]:
    """
    Extract generic and trade names.
    Returns:
      mapping   : { trade_name_lower: generic_name_lower }
      all_names : sorted unique list of all generic + trade names
    """
    mapping   = {}
    all_names = set()

    for row in rows:
        # Column names may have spaces/case variations — normalise
        keys = {k.strip().lower().replace(" ", "_"): k for k in row.keys()}

        ingredient  = row.get(keys.get("ingredient", ""), "").strip().lower()
        trade_name  = row.get(keys.get("trade_name", ""), "").strip().lower()

        if not ingredient and not trade_name:
            continue

        # Ingredients can be multi (semicolon separated) — split each
        for ing in ingredient.split(";"):
            ing = ing.strip()
            if ing and len(ing) >= 3:
                all_names.add(ing)

        if trade_name and len(trade_name) >= 3:
            all_names.add(trade_name)
            if ingredient:
                mapping[trade_name] = ingredient

    return mapping, sorted(all_names)


def save_drugs(all_names: list[str], path: str):
    with open(path, "w", encoding="utf-8") as f:
        for name in all_names:
            f.write(name + "\n")
    print(f"Saved {len(all_names):,} drug names -> {path}")


def build_generic_to_brands(mapping: dict) -> dict:
    """Build reverse mapping: { generic_name: [brand1, brand2, ...] }"""
    generic_to_brands = {}
    for trade, generic in mapping.items():
        for ing in generic.split(";"):
            ing = ing.strip()
            if not ing:
                continue
            generic_to_brands.setdefault(ing, [])
            if trade not in generic_to_brands[ing]:
                generic_to_brands[ing].append(trade)
    return generic_to_brands


def save_json(mapping: dict, path: str):
    """Save full mapping with both directions:
       trade_to_generic: { brand -> generic }
       generic_to_brands: { generic -> [brand1, brand2, ...] }
    """
    generic_to_brands = build_generic_to_brands(mapping)
    full = {
        "tradeToGeneric":  mapping,
        "genericToBrands": generic_to_brands,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(mapping):,} trade->generic + {len(generic_to_brands):,} generic->brands -> {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Download FDA Orange Book and build drug name list."
    )
    parser.add_argument("--out-drugs", default=OUT_DRUGS,
                        help=f"Output drugs list file (default: {OUT_DRUGS})")
    parser.add_argument("--out-json",  default=OUT_JSON,
                        help=f"Output JSON mapping file (default: {OUT_JSON})")
    args = parser.parse_args()

    rows             = download_products()
    mapping, names   = extract_names(rows)

    save_drugs(names,   args.out_drugs)
    save_json(mapping,  args.out_json)

    print(f"\nDone. {len(names):,} unique names (generic + trade).")
    print(f"Sample generic names : {[n for n in names[:5]]}")
    trade_samples = list(mapping.items())[:5]
    print(f"Sample trade->generic: {trade_samples}")


if __name__ == "__main__":
    main()
