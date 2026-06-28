"""
match_db_events.py
==================
Check whether reactions/events extracted from a narrative are present in an
existing DB reaction (event) list.

Matching is case-insensitive and whitespace-normalized. Optionally, a MedDRA
LLT->PT map (reactions.txt, same format used elsewhere in this repo) can be
supplied so synonymous lowest-level terms collapse to their preferred term
before comparison.

From Python:
    from match_db_events import match_events
    result = match_events(["Vision Blurred"], ["Vision Blurred"])
    # [{"event": "Vision Blurred", "in_db": True, "matched_on": "vision blurred"}]

CLI:
    python match_db_events.py --narrative "Vision Blurred" --db "Vision Blurred"
"""

import os
import re

REACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "reactions.txt")


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def load_llt_to_pt(path: str = REACTIONS_FILE) -> dict:
    """Load MedDRA map (line1=LLT, line2=PT, blank separator). Returns {llt: pt}."""
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


def _canonical(term: str, llt_to_pt: dict) -> str:
    """Normalize a term, mapping LLT -> PT when a MedDRA map is provided."""
    t = _norm(term)
    return llt_to_pt.get(t, t)


def match_events(narrative_events: list, event_list: list,
                 reactions_file: str = None) -> list:
    """
    For each reaction from the narrative, report whether it exists in the DB
    event list. If reactions_file is given, terms are collapsed to their MedDRA
    preferred term first.

    Returns a list of {"event", "in_db", "matched_on"}.
    """
    llt_to_pt = load_llt_to_pt(reactions_file) if reactions_file else {}
    event_db = {_canonical(e, llt_to_pt) for e in event_list}
    results = []
    for e in narrative_events:
        key = _canonical(e, llt_to_pt)
        present = key in event_db
        results.append({"event": e, "in_db": present,
                        "matched_on": key if present else None})
    return results


def match_events_map(narrative_events: list, event_list: list,
                     reactions_file: str = None) -> dict:
    """
    Like match_events but returns an {event_name: "yes"/"no"} map, so callers can
    do result["Vision Blurred"] -> "yes".
    """
    return {r["event"]: ("yes" if r["in_db"] else "no")
            for r in match_events(narrative_events, event_list, reactions_file)}


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Match narrative reactions against a DB event list.")
    parser.add_argument("--narrative", required=True,
                        help="Comma-separated reaction terms from the narrative.")
    parser.add_argument("--db", required=True,
                        help="Comma-separated DB event list.")
    parser.add_argument("--reactions", default=None,
                        help="Optional MedDRA reactions.txt for LLT->PT mapping.")
    args = parser.parse_args()

    narrative = [e.strip() for e in args.narrative.split(",") if e.strip()]
    db        = [e.strip() for e in args.db.split(",") if e.strip()]
    print(json.dumps(match_events(narrative, db, args.reactions), indent=2))
