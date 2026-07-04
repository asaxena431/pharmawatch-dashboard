#!/usr/bin/env python3
"""
Read a case result from the extraction results JSON file.

The stored ``drug_reaction_map`` maps each drug to a *list* of reaction
entries. This reader converts it to a nested mapping keyed by drug and then
by reaction name, so that:

    result["drug_reaction_map"][drug].get(event, "NOT FOUND")

works as expected.

Usage:
    python read_case_result.py <case_id> [--results output/results.json]
    python read_case_result.py <case_id> --drug "Aspirin" --event "Nausea"
"""

import os
import sys
import json
import argparse

DEFAULT_RESULTS_FILE = os.path.join("output", "results.json")


def load_results(results_file):
    """Load all cases from the results JSON file (UTF-8)."""
    if not os.path.exists(results_file):
        raise FileNotFoundError(f"Results file not found: {results_file}")
    with open(results_file, "r", encoding="utf-8") as f:
        return json.load(f)


def index_drug_reaction_map(drug_reaction_map):
    """Convert drug -> [reaction entries] into drug -> {reaction: entry}.

    This makes ``result["drug_reaction_map"][drug].get(event, "NOT FOUND")``
    work, returning the reaction entry for that drug/event pair.
    """
    indexed = {}
    for drug, entries in (drug_reaction_map or {}).items():
        by_event = {}
        for entry in entries:
            event = entry.get("reaction")
            if event is not None:
                by_event[event] = entry
        indexed[drug] = by_event
    return indexed


def load_case_result(results_file, case_id=None):
    """Return the ``result`` dict for a case with an event-keyed map.

    If ``case_id`` is None, the first case is returned.
    """
    all_cases = load_results(results_file)

    # Support both a list of case objects and a dict keyed by case_id.
    if isinstance(all_cases, dict):
        all_cases = [
            {"case_id": k, **(v if isinstance(v, dict) else {"result": v})}
            for k, v in all_cases.items()
        ]

    if not all_cases:
        raise ValueError(f"No cases found in {results_file}")

    case = None
    if case_id is None:
        case = all_cases[0]
    else:
        for c in all_cases:
            if str(c.get("case_id")) == str(case_id):
                case = c
                break
        if case is None:
            available = [str(c.get("case_id")) for c in all_cases]
            raise KeyError(
                f"Case ID {case_id!r} not found in {results_file}. "
                f"Available case IDs: {available}"
            )

    result = case.get("result", {})
    result["drug_reaction_map"] = index_drug_reaction_map(
        result.get("drug_reaction_map", {})
    )
    return result


# Alias for convenience / backwards compatibility.
get_case_result = load_case_result


def main():
    parser = argparse.ArgumentParser(
        description="Read a case result with an event-keyed drug_reaction_map"
    )
    parser.add_argument("case_id", nargs="?", help="Case ID to read (default: first case)")
    parser.add_argument("--results", default=DEFAULT_RESULTS_FILE, help="Results JSON file path")
    parser.add_argument("--drug", help="Drug name to look up in drug_reaction_map")
    parser.add_argument("--event", help="Reaction/event name to look up for the drug")
    parser.add_argument("--list", action="store_true", help="List available case IDs and exit")
    args = parser.parse_args()

    if args.list:
        cases = load_results(args.results)
        if isinstance(cases, dict):
            ids = list(cases.keys())
        else:
            ids = [c.get("case_id") for c in cases]
        print(json.dumps(ids, indent=2, ensure_ascii=False))
        return

    result = load_case_result(args.results, args.case_id)

    if args.drug and args.event:
        drug_map = result["drug_reaction_map"].get(args.drug, {})
        print(json.dumps(drug_map.get(args.event, "NOT FOUND"), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
