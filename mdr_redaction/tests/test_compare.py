import unittest

from mdr_redaction.compare import _explain, compare
from mdr_redaction.redactor import redact_section


class DateFormatsFromRealExport(unittest.TestCase):
    def test_dd_mon_yyyy(self):
        self.assertEqual(redact_section("B5", "low glucose by 05-Nov-2025.").redacted, "low glucose by (B)(6) 2025.")

    def test_dd_slash_mon_slash_yyyy(self):
        self.assertEqual(redact_section("B5", "ON 05/DEC/2025, THE PATIENT").redacted, "ON (B)(6) 2025, THE PATIENT")

    def test_month_of_year(self):
        self.assertEqual(redact_section("B5", "surgery in August of 2019.").redacted, "surgery in (B)(6) of 2019.")

    def test_month_day_without_year(self):
        self.assertEqual(redact_section("B5", "PASSED AWAY ON MARCH 25 AFTER").redacted, "PASSED AWAY ON (B)(6) AFTER")


class FalsePositivesFromRealExport(unittest.TestCase):
    def test_patient_is_not_initials(self):
        t = "THE PATIENT IS MAKING A CLAIM"
        self.assertEqual(redact_section("B5", t).redacted, t)

    def test_pt_abbreviation_not_initials(self):
        t = "the patient (pt) reported pain"
        self.assertEqual(redact_section("B5", t).redacted, t)

    def test_sneezed_not_serial(self):
        t = "PATIENT SNEEZED AND WAS ADMITTED"
        self.assertEqual(redact_section("B5", t).redacted, t)

    def test_serialized_not_serial(self):
        t = "the device is serialized and tracked"
        self.assertEqual(redact_section("B5", t).redacted, t)

    def test_pain_at_rest_not_address(self):
        t = "score 93.1 pain at rest"
        self.assertEqual(redact_section("B5", t).redacted, t)

    def test_uppercase_mr_name(self):
        self.assertEqual(redact_section("B5", "MR. RAFAEL BENITEZ REPORTED").redacted, "(B)(6) REPORTED")


class Comparison(unittest.TestCase):
    def test_format_only(self):
        orig = "implant inserted 2026-02-09 in FDI 46."
        human = "implant inserted on (b)(6)2026 in FDI 46."
        auto = redact_section("B5", orig).redacted
        status, reason, _, _, _ = _explain(orig, human, auto, [])
        self.assertEqual(status, "FORMAT_ONLY")

    def test_auto_missed_free_text(self):
        orig = "seen at MIRAMAR FAMILY DENTAL OFFICE on 05/05/2026"
        human = "seen at (b)(6) OFFICE on (b)(6) 2026"
        res = redact_section("B5", orig)
        status, reason, ref, auto_only, human_only = _explain(orig, human, res.redacted, res.findings)
        self.assertEqual(status, "AUTO_MISSED")
        self.assertIn("miramar family dental", human_only)
        self.assertIn("Shady Grove Hospital", ref)

    def test_auto_extra_reports_rule(self):
        orig = "event on 2026-06-10 verified"
        human = "event on 2026-06-10 verified"      # editor left the date
        res = redact_section("B5", orig)
        status, reason, ref, auto_only, _ = _explain(orig, human, res.redacted, res.findings)
        self.assertEqual(status, "AUTO_EXTRA")
        self.assertIn("editor inconsistency", reason)
        self.assertIn("Appendix 6", ref)

    def test_compare_rows(self):
        rows = [{"RECORD_ID": "1", "COMPANY_NARRATIVE": "on 06/25/2015 pt fell", "REDACTED_NARRATIVE": "on (b)(6) 2015 pt fell"}]
        out = compare(rows, "COMPANY_NARRATIVE", "REDACTED_NARRATIVE", "RECORD_ID")
        self.assertEqual(out[0].status, "FORMAT_ONLY")


if __name__ == "__main__":
    unittest.main()
