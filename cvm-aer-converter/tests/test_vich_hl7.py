"""The vich-hl7 output: CVM's HL7 v3 message with the documents embedded, and its validation."""

import base64
import os
import xml.etree.ElementTree as ET

import pytest

from cvm_aer.pipeline import FORMAT_GL42, FORMAT_VICH_HL7, FORMATS, convert_pdf, render_xml
from cvm_aer.validate import ENTRY, fetch, validate
from tests.test_xfa_1932a import _xfa_pdf

HL7 = {"h": "urn:hl7-org:v3"}
SCHEMA_DIR = os.environ.get("CVM_AER_SCHEMA_DIR", os.path.join(os.path.expanduser("~"), "vich-schemas"))


def _titles(xml: str):
    root = ET.fromstring(xml)
    return [element.text for element in root.iterfind(".//h:title", HL7)]


def _texts(xml: str):
    root = ET.fromstring(xml)
    return [element.text for element in root.iterfind(".//h:text[@representation='B64']", HL7)]


def test_both_formats_are_offered():
    assert FORMATS == (FORMAT_GL42, FORMAT_VICH_HL7)


def test_a_1932a_case_becomes_an_hl7_message_with_its_files_embedded(tmp_path):
    attachment = tmp_path / "lab report.pdf"
    attachment.write_bytes(b"%PDF-1.4 attachment")
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf, output_format=FORMAT_VICH_HL7, attachments=[str(attachment)])
    assert result.output_format == FORMAT_VICH_HL7
    root = ET.fromstring(result.xml)
    assert root.tag == "{urn:hl7-org:v3}MCCI_IN200100UV01"
    assert "Librela" in result.xml
    titles = _titles(result.xml)
    assert "case.pdf" in titles and "lab report.pdf" in titles
    assert base64.b64decode(_texts(result.xml)[-1]) == b"%PDF-1.4 attachment"


def test_the_readable_gl42_stays_the_default(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    assert convert_pdf(pdf).output_format == FORMAT_GL42
    assert "urn:vich:gl42:aer" in convert_pdf(pdf).xml


def test_an_unknown_format_is_refused(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    report = convert_pdf(pdf).report
    with pytest.raises(ValueError, match="gl42, vich-hl7"):
        render_xml(report, "emdr-hl7")


def test_the_message_validates_against_the_fda_schemas(tmp_path):
    if not os.path.exists(os.path.join(SCHEMA_DIR, ENTRY)):
        pytest.skip(f"FDA VICH schemas not cached at {SCHEMA_DIR}")
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    out = tmp_path / "case.xml"
    out.write_text(convert_pdf(pdf, output_format=FORMAT_VICH_HL7).xml, encoding="utf-8")
    schema = fetch(SCHEMA_DIR)
    complaints = validate([str(out)], schema)
    if complaints is None:
        pytest.skip("neither xmllint nor lxml is installed")
    assert complaints == []
