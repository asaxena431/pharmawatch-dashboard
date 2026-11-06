"""FDA extended E2B(R2) profile for MedWatch 3500A reports (CDER).

FDA's OCR/extraction service emits ICSRs against its own extended DTD
(``Extended-ICH E2B (R2)-icsr-xml-v3.5-3500A.dtd``): the ICH R2 elements plus
3500A-specific ones (``formtype``, ``contactmethod``, the report-source flags,
``manufacturerind``, the race flags, ...), every element always present and in a
fixed order, empty when the form leaves the box blank.

This module maps a template-extracted 3500A (:class:`~.form_extract.ExtractedForm`,
keyed ``p{page}.{fieldname}``) straight onto that profile, which is how the form
boxes relate to the message: one form field to one data element.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from .e2b_r2 import ROUTE_CODES, e2b_date
from .form_extract import MAX_SUSPECTS, ExtractedForm, suspect_page, weight_in_kg

FDA_E2B_DTD = "./Extended-ICH E2B (R2)-icsr-xml-v3.5-3500A.dtd"
MESSAGE_SENDER = "FDA-CDER-OSC"
MESSAGE_RECEIVER = "ZZFDA"

# The receiver block is fixed for CDER OCR submissions.
RECEIVER: Dict[str, str] = {
    "receivertype": "2",
    "receiverorganization": "CDER-IND",
    "receiverdepartment": "Office of Surveillance and Epidemiology",
    "receivergivename": "CFSAN3500A",
    "receiverstreetaddress": "10903 New Hampshire Avenue",
    "receivercity": "Silver Spring",
    "receiverstate": "MD",
    "receiverpostcode": "20993",
    "receivercountrycode": "US",
    "receiveremailaddress": "CFSAN3500A@FDA.hhs.gov",
}

SAFETYREPORT_ORDER: Sequence[str] = (
    "safetyreportversion",
    "safetyreportid",
    "primarysourcecountry",
    "occurcountry",
    "transmissiondateformat",
    "transmissiondate",
    "reporttype",
    "formtype",
    "contactmethod",
    "adverseevent",
    "productproblem",
    "medicationerror",
    "problemwithdifferentmanufacturerofsamemedicine",
    "productavailableforevaluation",
    "productreturndateformat",
    "productreturndate",
    "serious",
    "seriousnessdeath",
    "seriousnesslifethreatening",
    "seriousnesshospitalization",
    "seriousnessdisabling",
    "seriousnesscongenitalanomali",
    "seriousnessother",
    "requiredintervention",
    "receivedateformat",
    "receivedate",
    "receiptdateformat",
    "receiptdate",
    "additionaldocument",
    "documentlist",
    "fulfillexpeditecriteria",
    "authoritynumb",
    "companynumb",
    "duplicate",
    "casenullification",
    "nullificationreason",
    "medicallyconfirm",
    "fivedayreporttype",
    "sevendayreporttype",
    "tendayreporttype",
    "fifteendayreporttype",
    "thirtydayreporttype",
    "periodicreporttype",
    "initialreporttype",
    "followupreporttype",
    "followupnumber",
    "manufacturerreportnumber",
    "mfrreceivedateformat",
    "mfrreceivedate",
)

PRIMARYSOURCE_ORDER: Sequence[str] = (
    "reportertitle",
    "reportergivename",
    "reportermiddlename",
    "reporterfamilyname",
    "reporterorganization",
    "reporterdepartment",
    "reporterstreet",
    "reportercity",
    "reporterstate",
    "reporterpostcode",
    "reportercountry",
    "reportertel",
    "reportertelextension",
    "reportertelcountrycode",
    "reporteremailaddress",
    "reportedtomanufacturercompounder",
    "reportedtouserfacility",
    "reportedtodistributorimporter",
    "qualification",
    "identitydisclosedtothemanufacturer",
    "literaturereference",
    "studyname",
    "sponsorstudynumb",
    "observestudytype",
    "healthprofessionalflag",
    "initialreporteralsosentreporttofdaflag",
)

SENDER_ORDER: Sequence[str] = (
    "sendertype",
    "senderorganization",
    "senderdepartment",
    "sendertitle",
    "sendergivename",
    "sendermiddlename",
    "senderfamilyname",
    "senderstreetaddress",
    "sendercity",
    "senderstate",
    "senderpostcode",
    "sendercountrycode",
    "sendertel",
    "sendertelextension",
    "sendertelcountrycode",
    "senderfax",
    "senderfaxextension",
    "senderfaxcountrycode",
    "senderemailaddress",
    "manufacturer503bflag",
    "outsourcingfacilityname",
    "reportsourceforeignflag",
    "reportsourcestudyflag",
    "reportsourceliteratureflag",
    "reportsourceconsumerflag",
    "reportsourcehealthprofflag",
    "reportsourceuserfacilityflag",
    "reportsourcecompanyrepflag",
    "reportsourcedistributerflag",
    "reportsourceotherflag",
    "reportsourceothertext",
    "manufacturernda",
    "manufactureranda",
    "manufacturerind",
    "manufacturerbla",
    "manufacturerpma",
    "combinationProduct",
    "preanda",
    "pre-1938",
    "manufacturerOTC",
    "manufacturerprotocolnumber",
    "compoundedproduct",
)

RECEIVER_ORDER: Sequence[str] = (
    "receivertype",
    "receiverorganization",
    "receiverdepartment",
    "receivertitle",
    "receivergivename",
    "receivermiddlename",
    "receiverfamilyname",
    "receiverstreetaddress",
    "receivercity",
    "receiverstate",
    "receiverpostcode",
    "receivercountrycode",
    "receivertel",
    "receivertelextension",
    "receivertelcountrycode",
    "receiverfax",
    "receiverfaxextension",
    "receiverfaxcountrycode",
    "receiveremailaddress",
)

PATIENT_ORDER: Sequence[str] = (
    "patientinitial",
    "patientgpmedicalrecordnumb",
    "patientspecialistrecordnumb",
    "patienthospitalrecordnumb",
    "patientinvestigationnumb",
    "patientbirthdateformat",
    "patientbirthdate",
    "patientonsetage",
    "patientonsetageunit",
    "gestationperiod",
    "gestationperiodunit",
    "patientagegroup",
    "patientweight",
    "patientheight",
    "patientsex",
    "patientethnicity",
    "raceasian",
    "raceamericanindianoralaskannative",
    "raceblack",
    "racenativehawaiianorotherpacificislander",
    "racewhite",
    "raceother",
    "patientgender",
    "patientgenderother",
    "lastmenstrualdateformat",
    "patientlastmenstrualdate",
    "patientmedicalhistorytext",
    "resultstestsprocedures",
)

REACTION_ORDER: Sequence[str] = (
    "primarysourcereaction",
    "reactionmeddraversionllt",
    "reactionmeddrallt",
    "reactionmeddraversionpt",
    "reactionmeddrapt",
    "termhighlighted",
    "reactionstartdateformat",
    "reactionstartdate",
    "reactionenddateformat",
    "reactionenddate",
    "reactionduration",
    "reactiondurationunit",
    "reactionfirsttime",
    "reactionfirsttimeunit",
    "reactionlasttime",
    "reactionlasttimeunit",
    "reactionoutcome",
)

DRUG_ORDER: Sequence[str] = (
    "drugcharacterization",
    "medicinalproduct",
    "ndcnumberoruniqueid",
    "expirationdateformat",
    "expirationdate",
    "productstrength",
    "productstrengthunit",
    "obtaindrugcountry",
    "drugbatchnumb",
    "drugauthorizationnumb",
    "drugauthorizationcountry",
    "drugauthorizationholder",
    "drugstructuredosagenumb",
    "drugstructuredosageunit",
    "frequency",
    "drugseparatedosagenumb",
    "drugintervaldosageunitnumb",
    "drugintervaldosagedefinition",
    "drugcumulativedosagenumb",
    "drugcumulativedosageunit",
    "drugdosagetext",
    "drugdosageform",
    "drugadministrationroute",
    "drugparadministration",
    "reactiongestationperiod",
    "reactiongestationperiodunit",
    "drugindicationmeddraversion",
    "drugindication",
    "eventabatedafterusestoppedordosereduced",
    "eventreappearedafterreintroduction",
    "drugstartdateformat",
    "drugstartdate",
    "drugstartperiod",
    "drugstartperiodunit",
    "druglastperiod",
    "druglastperiodunit",
    "drugenddateformat",
    "drugenddate",
    "drugtreatmentduration",
    "drugtreatmentdurationunit",
    "actiondrug",
    "drugrecurreadministration",
    "drugadditional",
    "generic",
    "biosimilar",
)

SUMMARY_ORDER: Sequence[str] = (
    "narrativeincludeclinical",
    "reportercomment",
    "senderdiagnosismeddraversion",
    "senderdiagnosis",
    "sendercomment",
    "otherremarks",
)

AGE_UNIT_CODES = {"ageYrs": "801", "ageMons": "802", "ageWks": "803", "ageDays": "804"}

# Field lengths the E2B (R2) profile fixes; a 3500A box can hold more than these.
MAX_LENGTHS = {
    "medicinalproduct": 70,
    "reportergivename": 35,
    "reporterfamilyname": 50,
    "reporterstreet": 100,
    "reportercity": 35,
    "reporterstate": 40,
    "reporterpostcode": 15,
    "senderorganization": 60,
    "sendergivename": 35,
    "senderfamilyname": 50,
    "senderstreetaddress": 100,
    "sendercity": 35,
    "senderstate": 40,
    "drugbatchnumb": 35,
    "drugdosagetext": 100,
    "drugindication": 250,
}

# Dose units a copy types in its dose box, by E2B (R2) unit code.
DOSE_UNIT_CODES = {
    "kg": "001",
    "kilogram": "001",
    "g": "002",
    "gram": "002",
    "mg": "003",
    "milligram": "003",
    "ug": "004",
    "mcg": "004",
    "microgram": "004",
    "mg/kg": "005",
    "milligram per kilogram": "005",
    "ml": "012",
    "millilitre": "012",
    "milliliter": "012",
    "l": "011",
    "iu": "024",
    "unit": "032",
    "units": "032",
}

# Countries a 3500A names in an address, by ISO 3166 code.
COUNTRY_CODES = {"united states": "US", "usa": "US", "canada": "CA", "united kingdom": "GB"}

# Qualifications that make the reporter a health professional (E2B A.2.1.4).
CLINICAL_QUALIFICATIONS = frozenset({"1", "2", "3", "7"})

# 3500A "reporter occupation" -> E2B qualification.
QUALIFICATION_BY_OCCUPATION = {
    "physician": "1",
    "pharmacist": "2",
    "other health professional": "3",
    "lawyer": "4",
    "consumer": "5",
    "nurse": "7",
}


def _flag(marked: bool, unmarked: str = "2") -> str:
    """3500A yes/no boxes map onto E2B ``1`` (yes) / ``2`` (no)."""
    return "1" if marked else unmarked


def _add(parent: ET.Element, tag: str, value: Optional[str]) -> None:
    element = ET.SubElement(parent, tag)
    if value is not None and str(value).strip() != "":
        text = str(value).strip()
        length = MAX_LENGTHS.get(tag)
        element.text = text[:length] if length else text


def _block(parent: ET.Element, tag: str, order: Sequence[str], values: Dict[str, str]) -> ET.Element:
    block = ET.SubElement(parent, tag)
    for name in order:
        _add(block, name, values.get(name))
    return block


def _date(value: Optional[str]) -> Optional[str]:
    return e2b_date(value) if value else None


def _dated(values: Dict[str, str], format_tag: str, date_tag: str, raw: Optional[str]) -> None:
    stamp = _date(raw)
    if stamp:
        values[format_tag] = "102"
        values[date_tag] = stamp


def _qualification(occupation: Optional[str]) -> Optional[str]:
    if not occupation:
        return None
    text = occupation.strip().lower()
    for name, code in QUALIFICATION_BY_OCCUPATION.items():
        if name in text:
            return code
    return "3"


def _tel(number: Optional[str]) -> Optional[str]:
    """E2B telephone numbers are digits only; a form prints them punctuated."""
    if not number:
        return None
    return re.sub(r"\D", "", number) or None


def _postcode(code: Optional[str]) -> Optional[str]:
    """A postcode carries digits; a redacted box ("Withheld") carries none."""
    if not code or not any(char.isdigit() for char in code):
        return None
    return code


def _country_code(address: Optional[str]) -> Optional[str]:
    """The ISO code of the country named at the end of a postal address."""
    if not address:
        return None
    for name, code in COUNTRY_CODES.items():
        if name in address.lower():
            return code
    return None


def _route_code(route: Optional[str]) -> Optional[str]:
    if not route:
        return None
    text = route.strip().lower()
    for name, code in ROUTE_CODES.items():
        if text.startswith(name):
            return code
    return None


def _dose_unit_code(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    return DOSE_UNIT_CODES.get(re.sub(r"\s+", " ", unit.strip().strip(".").lower()))


def _abate_code(form: ExtractedForm, prefix: str, page: int) -> Optional[str]:
    if form.checked(f"p{page}.{prefix}Yes"):
        return "1"
    if form.checked(f"p{page}.{prefix}No"):
        return "2"
    if form.checked(f"p{page}.{prefix}NA"):
        return "4"
    return None


def _lab_results(form: ExtractedForm) -> Optional[str]:
    """Rebuild block A.9 (relevant tests) as one text field, one row per line."""
    rows: List[str] = []
    for index in range(1, 9):
        parts = [
            form.get(f"p2.testData{index}"),
            form.get(f"p2.lowTestRange{index}"),
            form.get(f"p2.highTestRange{index}"),
            form.get(f"p2.testDDate{index}"),
        ]
        row = " | ".join(part for part in parts if part)
        if row:
            rows.append(row)
    return "\n".join(rows) or None


def _narrative(form: ExtractedForm) -> Optional[str]:
    parts = [form.get("p0.advEvDesc"), form.get("p1.advEvDescribe")]
    text = " ".join(part for part in parts if part)
    return text or None


def _suspect_drug(form: ExtractedForm, index: int, page: int) -> Optional[Dict[str, str]]:
    name = form.get(f"p{page}.prodName{index}")
    if not name:
        return None  # a suspect-product box read without a name holds no product
    dose = form.get(f"p{page}.dose{index}")
    values: Dict[str, str] = {"drugcharacterization": "1", "medicinalproduct": name}
    strength = form.get(f"p{page}.prodStr{index}")
    if strength:
        values["productstrength"] = strength
    unit = form.get(f"p{page}.prodUnit{index}")
    if unit and unit != "--":
        values["productstrengthunit"] = unit
    for tag, key in (
        ("ndcnumberoruniqueid", f"p{page}.ndc{index}"),
        ("drugbatchnumb", f"p{page}.lot{index}"),
        ("drugauthorizationholder", f"p{page}.manu{index}"),
        ("drugindication", f"p{page}.diagnosis{index}"),
    ):
        value = form.get(key)
        if value and value != "--":
            values[tag] = value
    if dose:
        dose_unit = form.get(f"p{page}.doseUnit{index}")
        unit_code = _dose_unit_code(dose_unit)
        if unit_code:
            # A dose whose unit E2B codes is structured; any other is free text.
            values["drugstructuredosagenumb"] = dose
            values["drugstructuredosageunit"] = unit_code
        else:
            values["drugdosagetext"] = f"{dose} {dose_unit}".strip() if dose_unit else dose
    frequency = form.get(f"p{page}.freq{index}Other") or form.get(f"p{page}.freq{index}")
    if frequency and frequency != "--":
        values["frequency"] = frequency
    route = form.get(f"p{page}.route{index}Other") or form.get(f"p{page}.route{index}")
    if route and route != "--":
        code = _route_code(route)
        values["drugadministrationroute"] = code or route
    _dated(values, "expirationdateformat", "expirationdate", form.get(f"p{page}.expDate{index}"))
    _dated(values, "drugstartdateformat", "drugstartdate", form.get(f"p{page}.start{index}Date"))
    _dated(values, "drugenddateformat", "drugenddate", form.get(f"p{page}.end{index}Date"))
    # An unanswered dechallenge/rechallenge question is reported as "unknown".
    values["eventabatedafterusestoppedordosereduced"] = _abate_code(form, f"abate{index}", page) or "4"
    values["eventreappearedafterreintroduction"] = _abate_code(form, f"reappear{index}", page) or "4"
    if form.checked(f"p{page}.Prod{index}Generic"):
        values["generic"] = "1"
    return values


def _concomitant_drugs(form: ExtractedForm) -> List[Dict[str, str]]:
    drugs: List[Dict[str, str]] = []
    for index in range(1, 31):
        name = form.get(f"p5.cProdName{index}")
        if not name:
            continue
        values: Dict[str, str] = {"drugcharacterization": "2", "medicinalproduct": name}
        _dated(values, "drugstartdateformat", "drugstartdate", form.get(f"p5.cProdStart{index}"))
        _dated(values, "drugenddateformat", "drugenddate", form.get(f"p5.cProdEnd{index}"))
        drugs.append(values)
    return drugs


def build_fda_e2b(
    form: ExtractedForm,
    message_number: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ET.Element:
    """Build the FDA extended ``<ichicsr>`` element for a 3500A extraction."""
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y%m%d%H%M%S")

    root = ET.Element("ichicsr", {"lang": "en"})
    _block(
        root,
        "ichicsrmessageheader",
        (
            "messagetype",
            "messageformatversion",
            "messageformatrelease",
            "messagenumb",
            "messagesenderidentifier",
            "messagereceiveridentifier",
            "messagedateformat",
            "messagedate",
        ),
        {
            "messagetype": "ichicsr",
            "messageformatversion": "2.1",
            "messageformatrelease": "3.2",
            "messagenumb": message_number or f"OCS_OCR_{stamp}",
            "messagesenderidentifier": MESSAGE_SENDER,
            "messagereceiveridentifier": MESSAGE_RECEIVER,
            "messagedateformat": "204",
            "messagedate": stamp,
        },
    )

    ind_number = form.get("p7.numIND")
    report_number = form.get("p7.manuRepNum") or form.get("p0.mfr")
    case_id = f"{report_number}-IND" if report_number and ind_number else report_number
    # A form naming an IND or a protocol is a study report even where the copy's
    # "study" box could not be read.
    study = form.checked("p7.rptsrcStu") or bool(ind_number) or bool(form.get("p7.protNum"))
    health_professional = form.checked("p6.repHPY") or (
        not form.checked("p6.repHPN") and _qualification(form.get("p6.repOccupation")) in CLINICAL_QUALIFICATIONS
    )
    serious_boxes = ("p0.death", "p0.lifeThr", "p0.hospital", "p0.disability", "p0.congenital", "p0.otherOutcome")

    safety_values: Dict[str, str] = {
        "safetyreportversion": "1",
        "safetyreportid": case_id,
        "primarysourcecountry": _country_code(form.get("p6.reportCountry")) or "US",
        "reporttype": "2" if study else "1",
        "formtype": "3500A",
        "contactmethod": "OCR-3500A",
        "adverseevent": _flag(form.checked("p0.adverse")),
        "productproblem": _flag(form.checked("p0.prodProblem")),
        "serious": _flag(any(form.checked(key) for key in serious_boxes)),
        "seriousnessdeath": _flag(form.checked("p0.death")),
        "seriousnesslifethreatening": _flag(form.checked("p0.lifeThr")),
        "seriousnesshospitalization": _flag(form.checked("p0.hospital")),
        "seriousnessdisabling": _flag(form.checked("p0.disability")),
        "seriousnesscongenitalanomali": _flag(form.checked("p0.congenital")),
        "seriousnessother": _flag(form.checked("p0.otherOutcome")),
        "fulfillexpeditecriteria": _flag(form.checked("p7.rep7") or form.checked("p7.rep15")),
        "companynumb": case_id,
        "fivedayreporttype": _flag(form.checked("p7.rep5")),
        "sevendayreporttype": _flag(form.checked("p7.rep7")),
        "fifteendayreporttype": _flag(form.checked("p7.rep15")),
        "thirtydayreporttype": _flag(form.checked("p7.rep30")),
        "periodicreporttype": _flag(form.checked("p7.repPer")),
        "initialreporttype": _flag(form.checked("p7.repInit")),
        "tendayreporttype": "1" if form.checked("p7.rep10") else None,
        "followupreporttype": _flag(form.checked("p7.repFollow")),
        "followupnumber": form.get("p7.repFollowNum"),
        "manufacturerreportnumber": case_id,
    }
    if form.checked("p0.reqInterv"):
        safety_values["requiredintervention"] = "1"
    report_date = form.get("p0.dateReport")
    # A.1.6 is the date the report reached the sender, i.e. the day it is converted.
    safety_values["receivedateformat"] = "102"
    safety_values["receivedate"] = moment.strftime("%Y%m%d")
    _dated(safety_values, "receiptdateformat", "receiptdate", report_date)
    _dated(safety_values, "mfrreceivedateformat", "mfrreceivedate", form.get("p7.reportManuRecDate"))

    safety = _block(root, "safetyreport", SAFETYREPORT_ORDER, safety_values)

    source_values: Dict[str, str] = {
        "reportergivename": form.get("p6.reportFirst"),
        "reporterfamilyname": form.get("p6.reportLast"),
        "reporterstreet": form.get("p6.reportAddr"),
        "reportercity": form.get("p6.reportCity"),
        "reporterstate": form.get("p6.reportSt"),
        "reporterpostcode": _postcode(form.get("p6.reportZip")),
        "reportercountry": (
            _country_code(form.get("p6.reportCountry"))
            or _country_code(form.get("p6.reportAddr"))
            or form.get("p6.reportCountry")
        ),
        "reportertel": _tel(form.get("p6.reportPhone")),
        "reporteremailaddress": form.get("p6.reportEmail"),
        "qualification": _qualification(form.get("p6.repOccupation")),
        # The profile carries "study name; study number"; a 3500A names only the IND.
        "studyname": f"{ind_number}; {ind_number}" if ind_number else None,
    }
    if study:
        source_values["observestudytype"] = "1"
    source_values["healthprofessionalflag"] = _flag(health_professional)
    source_values["initialreporteralsosentreporttofdaflag"] = _flag(form.checked("p6.reportFDAY"))
    _block(safety, "primarysource", PRIMARYSOURCE_ORDER, source_values)

    sender_values: Dict[str, str] = {
        "sendertype": "6",
        "senderorganization": form.get("p7.manuName"),
        "sendergivename": form.get("p7.manuName"),
        "senderstreetaddress": form.get("p7.manuAddr"),
        "sendercountrycode": _country_code(form.get("p7.manuAddr")),
        "sendertel": _tel(form.get("p7.manuPhone")),
        "senderemailaddress": form.get("p7.manuEmail"),
        "outsourcingfacilityname": form.get("p7.outsrcFac"),
        "reportsourceforeignflag": _flag(form.checked("p7.repsrcFor")),
        "reportsourcestudyflag": _flag(study),
        "reportsourceliteratureflag": _flag(form.checked("p7.repsrcLit")),
        "reportsourceconsumerflag": _flag(form.checked("p7.repsrcCons")),
        "reportsourcehealthprofflag": _flag(form.checked("p7.repsrcHP") or health_professional),
        "reportsourceuserfacilityflag": _flag(form.checked("p7.repsrcUF")),
        "reportsourcecompanyrepflag": _flag(form.checked("p7.repsrcCR")),
        "reportsourcedistributerflag": _flag(form.checked("p7.repsrcDI")),
        "reportsourceotherflag": _flag(form.checked("p7.repsrcOther")),
        "reportsourceothertext": form.get("p7.repsrcOtherList"),
        "manufacturernda": form.get("p7.numNDA"),
        "manufactureranda": form.get("p7.numANDA"),
        "manufacturerind": ind_number,
        "manufacturerbla": form.get("p7.numBLA"),
        "manufacturerpma": form.get("p7.numPMA"),
        "combinationProduct": _flag(form.checked("p7.combo")),
        "manufacturerprotocolnumber": form.get("p7.protNum"),
    }
    if form.checked("p7.comp503B"):
        sender_values["manufacturer503bflag"] = "1"
    if form.checked("p7.preANDA"):
        sender_values["preanda"] = "1"
    if form.checked("p7.pre1938"):
        sender_values["pre-1938"] = "1"
    if form.checked("p7.otc"):
        sender_values["manufacturerOTC"] = "1"
    if form.checked("p7.compounded"):
        sender_values["compoundedproduct"] = "1"
    _block(safety, "sender", SENDER_ORDER, sender_values)
    _block(safety, "receiver", RECEIVER_ORDER, dict(RECEIVER))

    # Every revision prints years first, so an age given without a unit is in years.
    age_unit = next((code for key, code in AGE_UNIT_CODES.items() if form.checked(f"p0.{key}")), "801")
    raced = any(form.checked(f"p0.{key}") for key in ("asian", "AmInAlNa", "black", "NaHIOtherPI", "white"))
    weight = weight_in_kg(form.get("p0.patWeight"), pounds=form.checked("p0.weightLB"))
    if weight:  # the profile reports kilograms to two decimals, always with one
        weight = f"{round(float(weight), 2):.2f}".rstrip("0")
        weight = f"{weight}0" if weight.endswith(".") else weight
    patient_values: Dict[str, str] = {
        "patientinitial": form.get("p0.patID"),
        "patientonsetage": form.get("p0.patAge"),
        "patientonsetageunit": age_unit if form.get("p0.patAge") else None,
        "patientweight": weight,
        "patientsex": "1" if form.checked("p0.sexM") else ("2" if form.checked("p0.sexF") else None),
        "raceasian": _flag(form.checked("p0.asian")) if raced else None,
        "raceamericanindianoralaskannative": _flag(form.checked("p0.AmInAlNa")) if raced else None,
        "raceblack": _flag(form.checked("p0.black")) if raced else None,
        "racenativehawaiianorotherpacificislander": _flag(form.checked("p0.NaHIOtherPI")) if raced else None,
        "racewhite": _flag(form.checked("p0.white")) if raced else None,
        "patientmedicalhistorytext": form.get("p2.otherHist"),
        "resultstestsprocedures": _lab_results(form) or form.get("p2.testData"),
    }
    if form.checked("p0.hispanic"):
        patient_values["patientethnicity"] = "1"
    _dated(patient_values, "patientbirthdateformat", "patientbirthdate", form.get("p0.patDOB"))
    patient = _block(safety, "patient", PATIENT_ORDER, patient_values)

    death_values: Dict[str, str] = {}
    _dated(death_values, "patientdeathdateformat", "patientdeathdate", form.get("p0.deathDate"))
    _block(patient, "patientdeath", ("patientdeathdateformat", "patientdeathdate"), death_values)

    event_date = _date(form.get("p0.dateAdvEvent"))
    for term in _reaction_terms(form):
        reaction_values: Dict[str, str] = {"primarysourcereaction": term}
        if event_date:
            reaction_values["reactionstartdateformat"] = "102"
            reaction_values["reactionstartdate"] = event_date
        if form.checked("p0.death"):
            reaction_values["reactionoutcome"] = "5"
        _block(patient, "reaction", REACTION_ORDER, reaction_values)

    suspects = (_suspect_drug(form, index, suspect_page(index)) for index in range(1, MAX_SUSPECTS + 1))
    drugs = [values for values in suspects if values]
    drugs += _concomitant_drugs(form)
    for values in drugs:
        _block(patient, "drug", DRUG_ORDER, values)

    _block(patient, "summary", SUMMARY_ORDER, {
        "narrativeincludeclinical": _narrative(form),
        "reportercomment": form.get("p2.addComm"),
    })
    return root


def _reaction_terms(form: ExtractedForm) -> List[str]:
    """Block B.1 holds one or more adverse event terms, one per line/semicolon."""
    text = form.get("p7.advTerms")
    if not text:
        return ["Unspecified adverse event"]
    terms = [term.strip() for term in re.split(r"[;\n]", text) if term.strip()]
    return terms or ["Unspecified adverse event"]


def to_xml_string(form: ExtractedForm, **kwargs) -> str:
    """Render a 3500A extraction as an FDA extended E2B(R2) document."""
    root = build_fda_e2b(form, **kwargs)
    ET.indent(root, space="")
    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<!DOCTYPE ichicsr SYSTEM "{FDA_E2B_DTD}">\n'
        f"{body}\n"
    )
