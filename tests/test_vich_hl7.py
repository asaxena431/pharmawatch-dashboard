"""The CVM HL7 v3 submission message written from a veterinary report."""

import xml.etree.ElementTree as ET

from medwatch_ocr import vich_hl7
from medwatch_ocr.models import (
    ActiveIngredient,
    Animal,
    ClinicalSign,
    Organisation,
    Person,
    VeterinaryEvent,
    VeterinaryOutcome,
    VeterinaryProduct,
    VeterinaryReport,
)
from medwatch_ocr.xml_diff import flatten, message_format

NS = {"v3": "urn:hl7-org:v3"}


def _report() -> VeterinaryReport:
    return VeterinaryReport(
        report_identifier="N141448",
        first_received_date="2025-07-23",
        submission_date="2025-09-03",
        type_of_information="Adverse event",
        submission_types=["Periodic"],
        marketing_authorisation_holder=Organisation(
            name="LLOYD, Inc.",
            street="604 West Thomas Ave",
            city="Shenandoah",
            state="IA",
            postcode="51601",
            country="USA",
        ),
        mah_contact=Person(given_name="Amanda", family_name="Gladman", phone="7122464000", email="agladman@lloydinc.com"),
        primary_reporter=Person(
            given_name="Evelyn",
            family_name="Nelson",
            category="Veterinarian",
            phone="4196519680",
            email="evheap@aol.com",
            organisation=Organisation(country="USA"),
        ),
        animal=Animal(
            species="Dog",
            breeds=["Retriever - Labrador"],
            gender="Female",
            reproductive_status="Neutered",
            number_treated="1",
            number_affected="1",
            weight_min_kg="28.2",
            weight_max_kg="28.2",
            weight_basis="Measured",
            age_min="9",
            age_min_unit="Year",
            age_basis="Measured",
        ),
        product=VeterinaryProduct(
            brand_name="Thyro-Tabs Canine",
            dosage_form="Tablet",
            route="Oral",
            registration_id="USA-USFDACVM-N141448",
            administration_interval="12",
            administration_interval_unit="Hour",
            administered_by="Owner",
            atc_vet_code="QH03AA01",
            active_ingredients=[
                ActiveIngredient(name="levothyroxine sodium", code="9J765S329G", strength_value="0.4", strength_unit="mg")
            ],
        ),
        event=VeterinaryEvent(
            onset_date="2024-10-01",
            narrative="Dermal lesion after treatment.",
            serious="Yes",
            treated="Yes",
            signs=[ClinicalSign(term="Folliculitis", animals_affected="1")],
            outcome=VeterinaryOutcome(ongoing="1"),
            previous_exposure="Yes",
            dechallenge="No",
            rechallenge="Not relevant",
        ),
    )


def _xml() -> ET.Element:
    return ET.fromstring(vich_hl7.to_xml_string(_report()))


def test_message_is_the_vich_aer_batch():
    root = _xml()
    assert root.tag == "{urn:hl7-org:v3}MCCI_IN200100UV01"
    assert root.find("v3:versionCode", NS).get("code") == "VICHAER1.0.0"
    message = root.find("v3:PORR_IN049006UV", NS)
    assert message.find("v3:profileId", NS).get("extension") == vich_hl7.PROFILE_ID
    assert root.find("v3:receiver//v3:representedOrganization/v3:id", NS).get("root") == "USFDA"


def test_animal_and_reaction_carry_their_codes():
    player = _xml().find(".//v3:player2", NS)
    assert player.find("v3:code", NS).get("code") == "DOG"
    assert player.find("v3:administrativeGenderCode", NS).get("code") == "C16576"
    assert player.find("v3:genderStatusCode", NS).get("code") == "C54011"

    values = flatten(_xml())
    weight = [path for path in values if path.endswith("value/low@value") and values[path] == "28.2"]
    assert weight, "the weight is written as an IVL_PQ in kilograms"


def test_uncoded_terms_keep_their_wording():
    """The 1932a has no VeDDRA code, so the reported term travels as originalText."""
    texts = [element.text for element in _xml().iter("{urn:hl7-org:v3}originalText")]
    assert "Folliculitis" in texts
    assert "Retriever - Labrador" in texts


def test_product_ingredient_and_answers():
    administration = _xml().find(".//v3:substanceAdministration", NS)
    assert administration.find("v3:routeCode", NS).get("code") == "C38288"
    kind = administration.find(".//v3:kindOfProduct", NS)
    assert kind.find("v3:name", NS).text == "Thyro-Tabs Canine"
    assert kind.find("v3:formCode", NS).get("code") == "C42998"
    assert kind.find(".//v3:ingredientSubstance/v3:name", NS).text == "levothyroxine sodium"
    assert kind.find(".//v3:generalizedMaterialKind/v3:name", NS).text == "QH03AA01"

    answers = {
        observation.find("v3:code", NS).get("code"): observation.find("v3:value", NS).get("value")
        or observation.find("v3:value", NS).get("nullFlavor")
        for observation in administration.findall("v3:outboundRelationship2/v3:observation", NS)
    }
    assert answers["T95024"] == "true"  # previous exposure
    assert answers["T95026"] == "false"  # dechallenge
    assert answers["T95027"] == "NA"  # rechallenge: not relevant


def test_attachments_are_embedded_base64():
    xml = vich_hl7.to_xml_string(_report(), documents=[("report.pdf", b"%PDF-1.4 hello")])
    document = ET.fromstring(xml).find(".//v3:reference/v3:document", NS)
    assert document.find("v3:title", NS).text == "report.pdf"
    assert document.find("v3:text", NS).get("representation") == "B64"
    assert document.find("v3:text", NS).text == "JVBERi0xLjQgaGVsbG8="


def test_expected_message_is_recognised_as_the_hl7_profile():
    assert message_format(vich_hl7.to_xml_string(_report())) == "vich-hl7"


def test_diff_compares_attributes_not_only_text():
    """HL7 keeps its data in attributes, so the diff has to compare them."""
    ours = vich_hl7.to_xml_string(_report())
    changed = ours.replace('code="DOG"', 'code="CAT"')
    from medwatch_ocr.xml_diff import diff_xml

    result = diff_xml(changed, ours)
    assert any(entry["path"].endswith("player2/code@code") for entry in result.different)
