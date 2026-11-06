from cvm_aer.models import Person, Sender, VeterinaryProduct, VeterinaryReport
from cvm_aer.service import load_sender_file
from cvm_aer.vich_hl7 import nada, to_xml_string


def test_the_configured_sender_fills_the_batch_and_message_sender(tmp_path):
    ini = tmp_path / "cvm.ini"
    ini.write_text("[sender]\nid = ZZACME\norganisation = Acme PV\ngiven_name = PV\nfamily_name = Admin\n"
                   "phone = 317 433 9572\nemail = pv@acme.example\n")
    sender = load_sender_file(str(ini))
    xml = to_xml_string(VeterinaryReport(), sender=sender)
    assert xml.count('root="ZZACME" extension="Acme PV"') == 2
    # batch sender, message sender and A.2
    assert xml.count("<given>PV</given>") == 3 and xml.count('value="TEL:3174339572"') == 3
    assert xml.count('value="MAILTO:pv@acme.example"') == 3


def test_without_a_sender_the_form_sender_is_the_message_sender():
    owner = Person(given_name="Roseanna", family_name="Bronhard", phone="561-271-5732", email="r@example.com")
    xml = to_xml_string(VeterinaryReport(primary_reporter=owner), sender=Sender(contact=Person()))
    message_sender = xml.split('<sender typeCode="SND">')[1].split("</sender>")[0]
    assert "<given>Roseanna</given>" in message_sender and 'value="TEL:561-271-5732"' in message_sender
    assert xml.count("<family>Bronhard</family>") >= 3  # reporter, message sender, batch sender


def test_without_any_contact_the_sender_stays_ni():
    xml = to_xml_string(VeterinaryReport())
    assert 'root="ID-PHARMAWATCH"' in xml and "<given>" not in xml


def test_b825_is_the_nada_in_the_registration_number():
    assert nada(VeterinaryProduct(registration_id="USA-USFDACVM-N141338")) == "N141338"
    assert nada(VeterinaryProduct(registration_id="ANADA 200-999")) == "A200999"
    assert nada(VeterinaryProduct(registration_id="11 injections")) is None
    report = VeterinaryReport(product=VeterinaryProduct(registration_id="NADA 141-338"))
    assert '<value xsi:type="ST">N141338</value>' in to_xml_string(report)


def test_the_regulatory_authority_is_fda_cvm_when_the_form_has_none():
    xml = to_xml_string(VeterinaryReport())
    assert "<name>Food and Drug Administration Center for Veterinary Medicine</name>" in xml
    assert "<city>Rockville</city>" in xml and '<country nullFlavor="NI"' not in xml.split('code="T95009"')[-1].split("</addr>")[0]
