"""Form FDA 1932a submitted as a dynamic XFA PDF, read back as a pvx1932a message."""

import os
import sys
import xml.etree.ElementTree as ET

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from medwatch_ocr.pipeline import FORMAT_GL42, FORMAT_PVX_1932A, LAYOUT_1932A, convert_pdf  # noqa: E402
from medwatch_ocr.xfa_1932a import is_1932a_form, read_1932a, to_xml_string  # noqa: E402
from medwatch_ocr.xml_diff import diff_xml, message_format  # noqa: E402

DATASET = """<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/"><xfa:data><pvx1932a>
<reporttype_ae>Y</reporttype_ae><pvcaseno/><receiveddate>2026-01-15</receiveddate>
<rep01categorisation>OWNE</rep01categorisation><rep01firstname>CRYSTAL</rep01firstname>
<rep01lastname>JORDAN</rep01lastname><rep01city>BUCKHANNON</rep01city><rep01countrycode>US</rep01countrycode>
<manufacturerref>04591625</manufacturerref><p01brandname>Librela</p01brandname>
<p01administrationroute>SCU</p01administrationroute><p01formulation>INJE</p01formulation>
<p01administrationvmp>AVET</p01administrationvmp><p01treatmentstartdate>2025-12-29</p01treatmentstartdate>
<species>DOG</species><sex>F</sex><weight>44.00000000</weight><weightunit>LBS</weightunit>
<outcometodate>DEAD</outcometodate><reactednumber>1.00000000</reactednumber>
<concurrentproblemstext>Arthritis&#xD;Hypothyroidism</concurrentproblemstext>
<narrativeadr>Ariel was more sore than usual.</narrativeadr>
</pvx1932a></xfa:data></xfa:datasets>"""


def _xfa_pdf(path: str) -> str:
    """A one-page PDF carrying ``DATASET`` the way a submitted 1932a does."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(DATASET.encode("utf-8"))
    acroform = DictionaryObject()
    acroform[NameObject("/XFA")] = ArrayObject([TextStringObject("datasets"), writer._add_object(stream)])
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(acroform)
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


def test_a_submitted_1932a_is_recognised_and_read(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    assert is_1932a_form(pdf)

    form = read_1932a(pdf)
    assert form.get("rep01firstname") == "CRYSTAL"
    assert form.get("p01brandname") == "Librela"
    # The form stores a line break as a carriage return; the message uses \n.
    assert form.get("concurrentproblemstext") == "Arthritis\nHypothyroidism"
    # The message carries the report itself as its first document.
    assert [document.name for document in form.documents] == ["case.pdf"]


def test_the_message_repeats_the_dataset_and_embeds_every_file(tmp_path):
    attachment = tmp_path / "case-attachment 1.pdf"
    attachment.write_bytes(b"%PDF-1.4 attachment")
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))

    root = ET.fromstring(to_xml_string(read_1932a(pdf, [str(attachment)])))
    assert root.tag == "pvx1932a"
    assert root.findtext("firstprocessdate")
    assert root.findtext("uploadid") == "1"
    assert root.findtext("p01brandname") == "Librela"
    assert root.findtext("pvcaseno") == ""  # an unanswered item is still named, but empty
    names = [element.text for element in root.iter("FILE_NAME")]
    assert names == ["case.pdf", "case-attachment 1.pdf"]
    assert [element.text for element in root.iter("FILE_DESCRIPTION")] == ["case", "case-attachment 1"]


def test_a_submission_round_trips_through_the_pipeline(tmp_path):
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf)
    assert result.layout == LAYOUT_1932A
    assert result.output_format == FORMAT_PVX_1932A

    # Comparing the message with itself leaves only the processing stamp, which is dynamic.
    diff = diff_xml(result.xml, result.xml)
    assert not diff.different and not diff.missing and not diff.extra

    gl42 = convert_pdf(pdf, output_format=FORMAT_GL42)
    assert "Librela" in gl42.xml
    assert "Subcutaneous" in gl42.xml  # SCU, in the words GL42 uses
    assert "<species" in gl42.xml and "Dog" in gl42.xml


def test_a_1932a_message_selects_its_own_format():
    assert message_format("<pvx1932a><p01brandname>Librela</p01brandname></pvx1932a>") == FORMAT_PVX_1932A
