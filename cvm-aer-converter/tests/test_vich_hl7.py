"""The vich-hl7 output: CVM's HL7 v3 message with the documents embedded, validated on every conversion."""

import base64
import xml.etree.ElementTree as ET

import pytest

from cvm_aer.pipeline import FORMAT_VICH_HL7, FORMATS, convert_pdf, render_xml
from cvm_aer.validate import SchemaError, complaints, validate_files, validate_xml
from tests.test_xfa_1932a import _xfa_pdf

HL7 = {"h": "urn:hl7-org:v3"}


def _titles(xml: str):
    root = ET.fromstring(xml)
    return [element.text for element in root.iterfind(".//h:title", HL7)]


def _texts(xml: str):
    root = ET.fromstring(xml)
    return [element.text for element in root.iterfind(".//h:text[@representation='B64']", HL7)]


def test_vich_hl7_is_the_only_output_and_the_default(tmp_path):
    assert FORMATS == (FORMAT_VICH_HL7,)
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf)
    assert result.output_format == FORMAT_VICH_HL7
    assert ET.fromstring(result.xml).tag == "{urn:hl7-org:v3}MCCI_IN200100UV01"


def test_a_1932a_case_becomes_an_hl7_message_with_its_files_embedded(tmp_path):
    attachment = tmp_path / "lab report.pdf"
    attachment.write_bytes(b"%PDF-1.4 attachment")
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf, attachments=[str(attachment)])
    assert "Librela" in result.xml
    titles = _titles(result.xml)
    assert "case.pdf" in titles and "lab report.pdf" in titles
    assert base64.b64decode(_texts(result.xml)[-1]) == b"%PDF-1.4 attachment"


def test_any_other_format_is_refused(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    report = convert_pdf(pdf).report
    with pytest.raises(ValueError, match="vich-hl7"):
        render_xml(report, "gl42")


def test_every_conversion_is_validated_against_the_fda_schemas(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf)
    assert result.validated is True and result.summary["schema_validated"] is True
    out = tmp_path / "case.xml"
    out.write_text(result.xml, encoding="utf-8")
    assert validate_files([str(out)]) == []


def test_a_message_the_schemas_reject_is_an_error(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    report = convert_pdf(pdf).report
    broken = render_xml(report, validate=False).replace("<creationTime", "<creationTyme", 1)
    found = complaints(broken)
    assert found and "creationTyme" in found[0]
    with pytest.raises(SchemaError) as raised:
        validate_xml(broken)
    assert raised.value.complaints == found
