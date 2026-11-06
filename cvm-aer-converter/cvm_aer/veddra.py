"""VeDDRA low level terms (EMA's VeDDRA list, Rev.17 of October 2026) for coding B.3.2 clinical signs."""

import csv
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

# The VeDDRA version LSMV accepts, as in an XML LSMV took in (codeSystemVersion="20").
VERSION = "20"
CODE_SYSTEM = "2.16.840.1.113883.4.358"
_LIST = os.path.join(os.path.dirname(__file__), "data", "veddra_2026.csv")


@lru_cache(maxsize=1)
def _terms() -> Dict[str, Tuple[str, str]]:
    with open(_LIST, newline="", encoding="utf-8") as handle:
        return {row["llt"].lower(): (row["llt_key"], row["llt"]) for row in csv.DictReader(handle)}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .;,").lower()


def lookup(term: Optional[str]) -> Optional[Tuple[str, str]]:
    """The (LLT key, LLT name) of a term written exactly as a VeDDRA LLT, else None."""
    return _terms().get(_clean(term or ""))


def code(term: Optional[str]) -> List[Tuple[str, str]]:
    """The VeDDRA LLTs of a reported sign: the sign itself, or each of its parts when
    it lists several ("Urinary Tract Infection and cystitis."); [] unless all match."""
    whole = lookup(term)
    if whole:
        return [whole]
    parts = [part for part in re.split(r",|;|\band\b", term or "", flags=re.I) if _clean(part)]
    coded = [lookup(part) for part in parts]
    return coded if len(parts) > 1 and all(coded) else []  # type: ignore[return-value]
