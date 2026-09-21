"""Validate the vich-hl7 message against FDA CVM's published VICH schemas.

Every message the converter writes is validated before it is handed over; a
message the schemas reject is an error, not an output.  The schema set is
FDA's, published at ``accessdata.fda.gov/icsr/schema/cvm/schemas/vich/``.  It
is looked for in a ``vich-schemas`` folder next to the package, then in
``~/.cache/cvm_aer/vich-schemas``, and is downloaded there once when neither
has it (``CVM_AER_SCHEMA_DIR`` names another folder).

    python -m cvm_aer validate out.xml          # re-check a message by hand
"""

import os
import posixpath
import re
import urllib.request
from typing import List, Optional, Sequence, Set

from lxml import etree

BASE = "https://www.accessdata.fda.gov/icsr/schema/cvm/schemas/vich/"
ENTRY = "multicacheschemas/MCCI_IN200100UV01.xsd"
AGENT = "Mozilla/5.0 (compatible; cvm_aer schema fetch)"
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vich-schemas")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "cvm_aer", "vich-schemas")

_SCHEMAS: dict = {}


class SchemaError(Exception):
    """The message does not validate; ``complaints`` lists what the schemas said."""

    def __init__(self, complaints: Sequence[str]):
        super().__init__("; ".join(complaints))
        self.complaints = list(complaints)


def default_schema_dir() -> str:
    """``CVM_AER_SCHEMA_DIR``, else the bundled folder when present, else the user cache."""
    configured = os.environ.get("CVM_AER_SCHEMA_DIR")
    if configured:
        return configured
    return BUNDLED if os.path.exists(os.path.join(BUNDLED, ENTRY)) else CACHE


def fetch(directory: Optional[str] = None, entry: str = ENTRY) -> str:
    """Make sure the schema set is in ``directory`` and return the entry schema's path."""
    directory = directory or default_schema_dir()
    seen: Set[str] = set()

    def get(relative: str) -> None:
        relative = posixpath.normpath(relative)
        if relative in seen:
            return
        seen.add(relative)
        path = os.path.join(directory, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            request = urllib.request.Request(BASE + relative, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                with open(path, "wb") as handle:
                    handle.write(response.read())
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        for reference in re.findall(r'schemaLocation="([^"]+)"', text):
            if not reference.startswith("http"):
                get(posixpath.join(posixpath.dirname(relative), reference))

    get(entry)
    return os.path.join(directory, entry)


def schema(directory: Optional[str] = None) -> etree.XMLSchema:
    """The compiled entry schema, fetched if need be and kept for the process."""
    path = fetch(directory)
    if path not in _SCHEMAS:
        _SCHEMAS[path] = etree.XMLSchema(etree.parse(path))
    return _SCHEMAS[path]


def complaints(xml: str, directory: Optional[str] = None) -> List[str]:
    """What the schemas object to in ``xml``; empty when it validates."""
    validator = schema(directory)
    document = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    if validator.validate(document):
        return []
    return [f"line {error.line}: {error.message}" for error in validator.error_log]


def validate_xml(xml: str, directory: Optional[str] = None) -> None:
    """Raise ``SchemaError`` unless ``xml`` validates."""
    found = complaints(xml, directory)
    if found:
        raise SchemaError(found)


def validate_files(paths: Sequence[str], directory: Optional[str] = None) -> List[str]:
    """Complaints for each file, prefixed with its name."""
    found: List[str] = []
    for path in paths:
        with open(path, "rb") as handle:
            found += [f"{path}: {line}" for line in complaints(handle.read(), directory)]
    return found
