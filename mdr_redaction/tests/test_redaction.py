import unittest

from mdr_redaction.redactor import redact_report, redact_section
from mdr_redaction.reportables import detect
from mdr_redaction.triage import prioritize


class B6Rules(unittest.TestCase):
    def test_dates_keep_year(self):
        self.assertEqual(redact_section("B5", "Implanted June 07, 2015.").redacted, "Implanted (B)(6) 2015.")
        self.assertEqual(redact_section("B5", "Explanted 2015-06-25.").redacted, "Explanted (B)(6) 2015.")

    def test_over_89_removes_year_and_age(self):
        out = redact_section("B5", "On October 29, 2019 the patient, 92 years old, DOB 05/04/1927, was seen.").redacted
        self.assertNotIn("2019", out)
        self.assertNotIn("1927", out)
        self.assertNotIn("92", out)
        self.assertIn("age 90 years or older", out)

    def test_under_89_keeps_age(self):
        self.assertIn("71 years old", redact_section("B5", "Patient 71 years old.").redacted)

    def test_dob_under_89_keeps_year(self):
        self.assertEqual(redact_section("B5", "DOB: 05/04/1999").redacted, "DOB: (B)(6)")

    def test_facility_and_clinician(self):
        out = redact_section("B5", "Treated at Shady Grove Hospital by Dr. Smith and Nurse Jackie.").redacted
        self.assertEqual(out, "Treated at (B)(6) Hospital by Dr. (B)(6) and Nurse (B)(6).")

    def test_profanity_and_boilerplate(self):
        out = redact_section("B5", "The damn pump failed. See attached.").redacted
        self.assertEqual(out, "The PROFANITY pump failed.")

    def test_d11_serial_only(self):
        out = redact_section("D11", "Analyzer SN 4471209, therapy dates 07/13/2021.").redacted
        self.assertEqual(out, "Analyzer SN (B)(6), therapy dates 07/13/2021.")


class B4Rules(unittest.TestCase):
    def test_identifiers(self):
        out = redact_section("H11", "IDE G123456, EUA 200123, BSC ID #A00632691/ TW #4452120, Complaint # CMP-88123.").redacted
        self.assertEqual(out, "IDE (B)(4), EUA (B)(4), BSC ID # (B)(4)/ TW # (B)(4), Complaint # (B)(4).")

    def test_common_words_not_hit(self):
        out = redact_section("B5", "The procedure was provided; the case was reviewed.").redacted
        self.assertEqual(out, "The procedure was provided; the case was reviewed.")

    def test_production_stats(self):
        out = redact_section("H11", "Approximately 42,766,831 units have been released for distribution since 2015.").redacted
        self.assertEqual(out, "Approximately (B)(4).")

    def test_trade_secret_paragraph(self):
        txt = ("The strain relief material was identified as silicone MED-4854, certified by the manufacturer. "
               "It is referenced in an FDA master file: MAF# 1281. A design enhancement has been implemented.")
        res = redact_section("H11", txt)
        self.assertEqual(res.redacted, "(B)(4)")
        self.assertTrue(any(f.rule == "trade_secret_paragraph" for f in res.findings))

    def test_uf_report_number_in_mfr_narrative(self):
        out = redact_section("B5", "Customer filed MedWatch form 3400610000-2022-8004 reporting the event.").redacted
        self.assertEqual(out, "Customer filed MedWatch report (B)(4) reporting the event.")


class Reportables(unittest.TestCase):
    def _r(self, num, text):
        return {"report_number": num, "sections": {"B5": text}}

    def test_linking(self):
        found = detect(self._r("1219913-2022-00282", "UNC filed MedWatch form 3400610000-2022-8004 reporting."))
        self.assertEqual(found[0].kind, "LINKING")
        self.assertEqual(found[0].email_subject, "Linking - 1219913-2022-00282 / 3400610000-2022-8004")
        self.assertEqual(found[0].log_row(), {"type": "LINKING", "report_numbers": "1219913-2022-00282/3400610000-2022-8004"})

    def test_possible_linking(self):
        found = detect(self._r("3012307300-2022-05431", "Per MW, patient dropped pump."))
        self.assertEqual([f.kind for f in found], ["POSSIBLE_LINKING"])
        self.assertEqual(found[0].email_subject, "Possible Linking - 3012307300-2022-05431")

    def test_animal(self):
        found = detect(self._r("3005248192-2024-00149", "An animal underwent a procedure and the suture broke."))
        self.assertEqual(found[0].kind, "ANIMAL")
        self.assertIn("CVM-Device-Reports@fda.hhs.gov", found[0].email_to)

    def test_code_blue_fire(self):
        found = detect(self._r("3005085999-2024-00011", "The concentrator caught fire."))
        self.assertEqual(found[0].email_subject, "CODE BLUE - 3005085999-2024-00011 - FIRE")

    def test_own_number_not_linked(self):
        self.assertEqual(detect(self._r("1219913-2022-00282", "Ref report 1219913-2022-00282.")), [])


class Triage(unittest.TestCase):
    def test_priority_order(self):
        reports = [
            {"report_number": "a-2024-1-1", "outcome": "M", "report_type": "MFR"},
            {"report_number": "b", "outcome": "M", "report_type": "MFR"},
            {"report_number": "c", "outcome": "M", "report_type": "UF"},
            {"report_number": "d", "outcome": "IN", "report_type": "MFR"},
            {"report_number": "e", "outcome": "D", "report_type": "MFR"},
            {"report_number": "f", "outcome": "M", "report_type": "MFR", "code_blue": True},
            {"report_number": "g", "outcome": "M", "report_type": "PRP"},
        ]
        order = [r["report_number"] for r in prioritize(reports)]
        self.assertEqual(order, ["f", "e", "d", "g", "c", "b", "a-2024-1-1"])


class EndToEnd(unittest.TestCase):
    def test_redact_report(self):
        out = redact_report({"report_number": "x", "sections": {"B5": "Dr. Who on 01/02/2020", "H11": "Lot # 55871A"}})
        self.assertEqual(out["sections"], {"B5": "Dr. (B)(6) on (B)(6) 2020", "H11": "Lot # (B)(4)"})
        self.assertEqual(len(out["findings"]), 3)
        self.assertFalse(out["needs_human_review"])


if __name__ == "__main__":
    unittest.main()
