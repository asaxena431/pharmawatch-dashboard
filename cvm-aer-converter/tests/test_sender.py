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
    assert xml.count("<given>PV</given>") == 2 and xml.count('value="TEL:3174339572"') == 2
    assert xml.count('value="MAILTO:pv@acme.example"') == 2


def test_without_a_sender_the_contact_stays_ni():
    xml = to_xml_string(VeterinaryReport(), sender=Sender(contact=Person()))
    assert 'root="ID-PHARMAWATCH"' in xml and "<given>" not in xml


def test_b825_is_the_nada_in_the_registration_number():
    assert nada(VeterinaryProduct(registration_id="USA-USFDACVM-N141338")) == "N141338"
    assert nada(VeterinaryProduct(registration_id="ANADA 200-999")) == "A200999"
    assert nada(VeterinaryProduct(registration_id="11 injections")) is None
    report = VeterinaryReport(product=VeterinaryProduct(registration_id="NADA 141-338"))
    assert '<value xsi:type="ST">N141338</value>' in to_xml_string(report)
