"""Conservative typo correction for narratives.

Only words in KNOWN_TYPOS are changed (built from misspellings seen in the FDA exports plus
common English ones), so medical terms, product names and abbreviations are never touched.
Case of the original word is preserved (all-caps narratives stay all-caps).
"""
from __future__ import annotations

import re

KNOWN_TYPOS: dict[str, str] = {
    # seen in the 2024 eMDR export
    "unspecifed": "unspecified", "recieved": "received", "recieve": "receive", "occured": "occurred",
    "ocurred": "occurred", "occurence": "occurrence", "inpact": "impact", "patien": "patient",
    "paitent": "patient", "pateint": "patient", "patinet": "patient", "patietn": "patient",
    "omplications": "complications", "cathether": "catheter", "catheder": "catheter", "unknow": "unknown",
    "implan": "implant", "ollowed": "followed", "leckage": "leakage", "continu": "continue",
    "proceedure": "procedure", "procedire": "procedure", "procedue": "procedure", "sugery": "surgery",
    "surgey": "surgery", "hemorrage": "hemorrhage", "hemmorhage": "hemorrhage", "anestesia": "anesthesia",
    "rythm": "rhythm", "diarrea": "diarrhea", "desease": "disease", "medecine": "medicine",
    "physican": "physician", "pysician": "physician", "hopsital": "hospital", "hosptial": "hospital",
    "infecton": "infection", "infeciton": "infection", "sympton": "symptom", "symptons": "symptoms",
    "reciept": "receipt", "manufacturor": "manufacturer", "manufaturer": "manufacturer", "maufacturer": "manufacturer",
    "complaintant": "complainant", "evalution": "evaluation", "evaulation": "evaluation", "investigaton": "investigation",
    "informaton": "information", "informtion": "information", "additonal": "additional", "addtional": "additional",
    "aditional": "additional", "availble": "available", "avaliable": "available", "experianced": "experienced",
    "experienceed": "experienced", "reportd": "reported", "repoted": "reported", "repored": "reported",
    "devcie": "device", "deivce": "device", "devic": "device", "replacment": "replacement", "replaced ": "replaced ",
    "removel": "removal", "funtion": "function", "fuction": "function", "functon": "function",
    "malfuction": "malfunction", "malfunciton": "malfunction", "treament": "treatment", "treatement": "treatment",
    # common English
    "teh": "the", "seperate": "separate", "seperately": "separately", "definately": "definitely", "untill": "until",
    "wich": "which", "becuase": "because", "becasue": "because", "adress": "address", "begining": "beginning",
    "beleive": "believe", "noticable": "noticeable", "recomend": "recommend", "recomended": "recommended",
    "refered": "referred", "reffered": "referred", "succesful": "successful", "sucessful": "successful",
    "succesfully": "successfully", "sucessfully": "successfully", "transfered": "transferred", "writen": "written",
    "acheive": "achieve", "accross": "across", "agressive": "aggressive", "aquire": "acquire", "aquired": "acquired",
    "calender": "calendar", "catagory": "category", "comming": "coming", "commited": "committed",
    "concious": "conscious", "unconcious": "unconscious", "dependant": "dependent", "dissapear": "disappear",
    "enviroment": "environment", "equiptment": "equipment", "equipement": "equipment", "existance": "existence",
    "experiance": "experience", "familar": "familiar", "finaly": "finally", "foward": "forward", "happend": "happened",
    "immediatly": "immediately", "independant": "independent", "knowlege": "knowledge", "lenght": "length",
    "maintainance": "maintenance", "maintenence": "maintenance", "neccessary": "necessary", "necessery": "necessary",
    "occassion": "occasion", "posession": "possession", "posible": "possible", "probaly": "probably",
    "relevent": "relevant", "simular": "similar", "strenght": "strength", "truely": "truly", "unfortunatly": "unfortunately",
    "usefull": "useful", "wether": "whether", "recive": "receive", "thier": "their", "exept": "except",
    "alot": "a lot", "futher": "further", "furthur": "further", "throught": "through", "thru": "through",
    "appartus": "apparatus", "apparant": "apparent", "apparantly": "apparently", "visable": "visible",
    "intermittant": "intermittent", "consistant": "consistent", "persistant": "persistent", "resistent": "resistant",
    "assesment": "assessment", "assessement": "assessment", "diagnoised": "diagnosed", "diagnosied": "diagnosed",
    "complaing": "complaining", "complane": "complain", "loosing": "losing", "leeking": "leaking",
    "dammage": "damage", "damge": "damage", "damged": "damaged",
    "preformed": "performed", "perfomed": "performed", "performend": "performed", "recieving": "receiving",
}

_WORD_RE = re.compile(r"[A-Za-z]+")


def _match_case(src: str, fix: str) -> str:
    if src.isupper():
        return fix.upper()
    if src[0].isupper():
        return fix[0].upper() + fix[1:]
    return fix


def fix_typos(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (corrected text, [(wrong, right), ...]). Only KNOWN_TYPOS entries are changed."""
    fixes: list[tuple[str, str]] = []

    def repl(m: re.Match) -> str:
        w = m.group(0)
        fix = KNOWN_TYPOS.get(w.lower())
        if not fix:
            return w
        out = _match_case(w, fix)
        fixes.append((w, out))
        return out

    return _WORD_RE.sub(repl, text), fixes
