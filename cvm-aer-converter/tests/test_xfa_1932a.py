"""Form FDA 1932a submitted as a dynamic XFA PDF, read and converted to the vich-hl7 message."""

import os
import sys
import xml.etree.ElementTree as ET

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cvm_aer.pipeline import FORMAT_VICH_HL7, LAYOUT_1932A, convert_pdf  # noqa: E402
from cvm_aer.xfa_1932a import is_1932a_form, read_1932a, to_xml_string  # noqa: E402

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
    attachment = tmp_path / "lab report.pdf"
    attachment.write_bytes(b"%PDF-1.4 attachment")
    pdf = _xfa_pdf(str(tmp_path / "case.pdf"))
    result = convert_pdf(pdf, attachments=[str(attachment)])
    assert result.layout == LAYOUT_1932A
    assert result.output_format == FORMAT_VICH_HL7
    assert "Librela" in result.xml
    assert "Subcutaneous" in result.xml  # SCU, in the words GL42 uses
    assert 'displayName="Dog"' in result.xml
    assert result.validated is True
    assert result.summary["attachments"] == ["lab report.pdf"]


def test_a_1932a_without_an_mah_is_not_sent_as_an_mah_report(tmp_path):
    # LSMV requires an ATCvet code (B.2.1.3) on any message with an MAH author.
    assert 'code="T95001"' not in convert_pdf(_xfa_pdf(str(tmp_path / "case.pdf"))).xml


def _message(**values) -> str:
    from cvm_aer import vich_hl7, xfa_1932a

    form = xfa_1932a.Xfa1932a(values={"reporttype_ae": "Y", "p01brandname": "Librela", **values})
    return vich_hl7.to_xml_string(xfa_1932a.map_veterinary_report(form))


def test_a_us_state_is_written_as_its_two_letter_code():
    xml = _message(rep01statetext="Florida", rep02statetext="new york", rep01countrycode="US")
    assert "<state>FL</state>" in xml and "<state>NY</state>" in xml and "Florida" not in xml


def test_the_product_is_referred_to_by_its_number():
    xml = _message()
    assert '<id root="1.2.3.4" extension="1" />' in xml and '<id extension="1" />' in xml


def test_a_dose_or_strength_without_a_unit_is_not_sent():
    xml = _message(
        p01dosagetext="02/19: Buprenorphine and 02/25: Diphenhydramine",
        p01ingredient1="Buprenorphine",
        p01ingredientstrength1="11 injections home injection",
    )
    dose = xml[xml.index("<doseCheckQuantity>") : xml.index("</doseCheckQuantity>")]
    assert "value=" not in dose and 'value="11"' not in xml and 'code="Injection"' not in xml


def test_a_measured_strength_is_sent():
    assert 'code="mg"' in _message(p01ingredient1="Buprenorphine", p01ingredientstrength1="0.3 mg/ml")


def test_the_exposure_dates_are_the_first_component_of_the_drug_period():
    xml = _message(p01treatmentstartdate="2025-02-19", p01treatmentenddate="2025-02-27")
    assert '<effectiveTime xsi:type="SXPR_TS">\n' in xml
    assert '<low value="20250219" />' in xml and '<high value="20250227" />' in xml


def test_an_initial_report_states_its_submission_type_and_receive_date():
    xml = _message(submissiontype="INIT", todaysdate="2025-03-01")
    assert 'code="C68624"' in xml and 'displayName="Expedited"' in xml
    assert '<effectiveTime value="20250301" />' in xml
    assert 'code="C68624"' not in _message(todaysdate="2025-03-01")


def test_a_country_is_written_as_its_iso_3_character_code():
    xml = _message(rep01countrycode="US", rep02countrycode="United States")
    assert xml.count("<country>USA</country>") >= 2 and "<country>US</country>" not in xml


def test_a_strength_the_form_lacks_is_sent_as_unknown():
    xml = _message(p01ingredient1="Buprenorphine", p01ingredientstrength1="11 injections home injection")
    quantity = xml[xml.index("<quantity>") : xml.index("</quantity>")]
    assert quantity.count('code="C17998"') == 2 and quantity.count('value="1"') == 2


def test_clinical_signs_are_coded_as_veddra_low_level_terms():
    xml = _message(concurrentproblemstext="Urinary Tract Infection and cystitis.\nfeeling blue")
    assert 'code="2026" codeSystem="2.16.840.1.113883.4.358" codeSystemVersion="2026"' in xml
    assert 'code="725"' in xml and 'displayName="Cystitis"' in xml
    assert "<originalText>feeling blue</originalText>" in xml
