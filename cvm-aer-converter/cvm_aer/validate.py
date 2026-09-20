"""Validate a ``vich-hl7`` message against FDA CVM's published VICH schemas.

The schema set is FDA's, published at
``accessdata.fda.gov/icsr/schema/cvm/schemas/vich/``.  It is looked for in a
``vich-schemas`` folder next to the package (shipped in the distribution),
then in ``~/.cache/cvm_aer/vich-schemas``, and is downloaded there once when
neither has it.

    python -m cvm_aer validate out.xml
    python -m cvm_aer validate out.xml --schema-dir /path/to/vich-schemas

Validation runs through ``xmllint`` (``apt install libxml2-utils``) or ``lxml``
(``pip install lxml``); without either the schemas are still fetched and the
command says so.
"""

import os
import posixpath
import re
import shutil
import subprocess
import urllib.request
from typing import List, Optional, Set

BASE = "https://www.accessdata.fda.gov/icsr/schema/cvm/schemas/vich/"
ENTRY = "multicacheschemas/MCCI_IN200100UV01.xsd"
AGENT = "Mozilla/5.0 (compatible; cvm_aer schema fetch)"
BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vich-schemas")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "cvm_aer", "vich-schemas")


def default_schema_dir() -> str:
    """The bundled folder when the distribution ships it, else the user cache."""
    return BUNDLED if os.path.exists(os.path.join(BUNDLED, ENTRY)) else CACHE


def fetch(directory: Optional[str] = None, entry: str = ENTRY) -> str:
    """Make sure the schema set is in ``directory`` and return the entry schema."""
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


def validate(paths: List[str], schema: str) -> Optional[List[str]]:
    """The validator's complaints, an empty list when valid, ``None`` without a validator."""
    if shutil.which("xmllint"):
        result = subprocess.run(
            ["xmllint", "--noout", "--schema", schema, *paths],
            capture_output=True,
            text=True,
        )
        return [line for line in result.stderr.splitlines() if line.strip()] if result.returncode else []
    try:
        from lxml import etree
    except ImportError:
        return None
    validator = etree.XMLSchema(etree.parse(schema))
    complaints: List[str] = []
    for path in paths:
        if not validator.validate(etree.parse(path)):
            complaints += [f"{path}: {error.line}: {error.message}" for error in validator.error_log]
    return complaints
