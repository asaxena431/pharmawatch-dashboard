"""Validate a generated eMDR message against FDA's schema, when it is cached.

``Con170227.xsd`` ships inside FDA's eMDR Implementation Package rather than
being ours to redistribute, so these tests run only after
``python scripts/validate_emdr_schema.py`` has cached it and only where a
validator is installed.
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from scripts.validate_emdr_schema import CACHE, ENTRY  # noqa: E402
from scripts.validate_vich_schema import validate  # noqa: E402
from tests.test_emdr_hl7 import _report  # noqa: E402
from medwatch_ocr import emdr_hl7  # noqa: E402

SCHEMA = os.path.join(CACHE, ENTRY)
uncached = pytest.mark.skipif(
    not os.path.exists(SCHEMA),
    reason="run scripts/validate_emdr_schema.py to cache the FDA schema",
)
unvalidatable = pytest.mark.skipif(
    not shutil.which("xmllint"), reason="xmllint (libxml2-utils) is not installed"
)


@uncached
@unvalidatable
def test_the_message_validates_against_the_published_schema(tmp_path):
    path = tmp_path / "message.xml"
    path.write_text(
        emdr_hl7.to_xml_string(_report(), documents=[("case.pdf", b"%PDF-1.4 case")]),
        encoding="utf-8",
    )
    assert validate([str(path)], SCHEMA) == []


@uncached
@unvalidatable
def test_the_validator_rejects_a_message_the_schema_forbids(tmp_path):
    """A guard on the guard: the schema has to reject a broken message."""
    broken = emdr_hl7.to_xml_string(_report()).replace(
        "<controlActProcess", "<batchComment /><controlActProcess", 1
    )
    path = tmp_path / "broken.xml"
    path.write_text(broken, encoding="utf-8")
    assert validate([str(path)], SCHEMA)
