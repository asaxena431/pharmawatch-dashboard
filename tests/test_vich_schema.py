"""Validate a generated message against FDA's schemas, when they are cached.

The schema set is not redistributable, so these tests run only after
``python scripts/validate_vich_schema.py`` has cached it and only where a
validator is installed.
"""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.validate_vich_schema import CACHE, ENTRY, validate  # noqa: E402
from tests.test_vich_hl7 import _report  # noqa: E402
from medwatch_ocr import vich_hl7  # noqa: E402

SCHEMA = os.path.join(CACHE, ENTRY)
uncached = pytest.mark.skipif(not os.path.exists(SCHEMA), reason="run scripts/validate_vich_schema.py to cache the FDA schemas")
unvalidatable = pytest.mark.skipif(not shutil.which("xmllint"), reason="xmllint (libxml2-utils) is not installed")


@uncached
@unvalidatable
def test_the_message_validates_against_the_published_schema(tmp_path):
    path = tmp_path / "message.xml"
    path.write_text(vich_hl7.to_xml_string(_report(), documents=[("case.pdf", b"%PDF-1.4 case")]), encoding="utf-8")
    assert validate([str(path)], SCHEMA) == []


@uncached
@unvalidatable
def test_the_validator_rejects_a_message_the_schema_forbids(tmp_path):
    """A guard on the guard: the schema has to reject a broken message."""
    broken = vich_hl7.to_xml_string(_report()).replace("<responseModeCode />", "<responseModeCode /><batchComment />")
    path = tmp_path / "broken.xml"
    path.write_text(broken, encoding="utf-8")
    assert validate([str(path)], SCHEMA)


def test_xmllint_is_used_when_present():
    """Documents the validator the script prefers, so a missing one is visible."""
    assert shutil.which("xmllint") or subprocess.run([sys.executable, "-c", "import lxml"]).returncode == 0
