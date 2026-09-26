import json
import os
import tempfile
import unittest

from mdr_redaction.linking import find_links, multiple_linking_groups, score_pair
from mdr_redaction.ner import find_person_names, heuristic
from mdr_redaction.redactor import redact_section

os.environ.setdefault("MDR_NER_BACKEND", "heuristic")

UF = {"report_number": "0504240000-2024-8102", "manufacturer": "Abbott Vascular", "report_type": "UF", "outcome": "IN",
      "lot": "9K33021", "model": "1145350-28", "event_date": "2024-03-11",
      "sections": {"B5": "During PCI the stent delivery balloon ruptured on inflation; additional stent required."}}
MFR = {"report_number": "2024168-2024-04411", "manufacturer": "ABBOTT VASCULAR", "report_type": "MFR", "outcome": "IN",
       "lot": "9k33021", "model": "1145350-28", "event_date": "03/12/2024",
       "sections": {"B5": "Reported that during PCI the delivery balloon ruptured during inflation. Additional stent required."}}
OTHER = {"report_number": "3001234567-2024-00001", "manufacturer": "Medtronic", "report_type": "MFR", "outcome": "M",
         "sections": {"B5": "Pump alarmed. No patient involvement."}}


class NER(unittest.TestCase):
    def test_role_cue(self):
        self.assertEqual([s[2] for s in heuristic("Risk manager Angela Ruiz reported the event.")], ["Angela Ruiz"])
        self.assertEqual([s[2] for s in heuristic("Contact person is Mark Feldman, field rep.")], ["Mark Feldman"])

    def test_credentials(self):
        self.assertEqual([s[2] for s in heuristic("Reviewed by Jane Doe, RN.")], ["Jane Doe"])

    def test_no_false_positive_on_orgs_and_all_caps(self):
        self.assertEqual(heuristic("Reported by Abbott Vascular Inc."), [])
        self.assertEqual(heuristic("REPORTED BY THE NURSE THAT THE PUMP FAILED"), [])

    def test_redactor_uses_ner(self):
        out = redact_section("B5", "Risk manager Angela Ruiz reported the failure.")
        self.assertEqual(out.redacted, "Risk manager (B)(6) reported the failure.")
        self.assertEqual(out.findings[0].rule, "person_name")

    def test_find_person_names_dedupes(self):
        spans = find_person_names("Reported by Jane Doe, RN.", backend="heuristic")
        self.assertEqual(len(spans), 1)


class Linking(unittest.TestCase):
    def test_structured_candidate(self):
        c = score_pair(UF, MFR)
        self.assertIsNotNone(c)
        self.assertFalse(c.confirmed)
        self.assertGreaterEqual(c.score, 0.5)
        self.assertTrue(any("lot" in r for r in c.reasons))
        self.assertTrue(c.email_subject.startswith("Possible Linking - "))

    def test_quoted_number_is_confirmed(self):
        a = dict(MFR, sections={"B5": "Customer filed MedWatch form 0504240000-2024-8102."})
        c = score_pair(a, UF)
        self.assertTrue(c.confirmed)
        self.assertEqual(c.email_subject, "Linking - 2024168-2024-04411 / 0504240000-2024-8102")

    def test_same_source_and_other_manufacturer_not_linked(self):
        self.assertIsNone(score_pair(MFR, OTHER))
        self.assertIsNone(score_pair(MFR, dict(MFR, report_number="2024168-2024-09999")))

    def test_supplement_of_itself_not_linked(self):
        self.assertIsNone(score_pair(MFR, dict(MFR, report_number="2024168-2024-04411-1")))

    def test_find_links_and_multiple(self):
        mfr2 = dict(MFR, report_number="2024168-2024-04412")
        links = find_links([UF, MFR, mfr2, OTHER])
        pairs = {(c.report_a, c.report_b) for c in links}
        self.assertIn(("0504240000-2024-8102", "2024168-2024-04411"), pairs)
        self.assertIn(("0504240000-2024-8102", "2024168-2024-04412"), pairs)
        groups = multiple_linking_groups(links)
        self.assertEqual(groups["0504240000-2024-8102"], ["2024168-2024-04411", "2024168-2024-04412"])


class ReviewApp(unittest.TestCase):
    def setUp(self):
        from mdr_redaction import review_app
        self.mod = review_app
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "batch.json")
        with open(self.path, "w") as fh:
            json.dump([UF, MFR, OTHER], fh)
        review_app.load(self.path)
        self.client = review_app.app.test_client()

    def test_inbox_lists_in_priority_order(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertLess(html.index("0504240000-2024-8102"), html.index("3001234567-2024-00001"))
        self.assertIn("PENDING", html)

    def test_report_page_and_save(self):
        num = "2024168-2024-04411"
        html = self.client.get(f"/report/{num}").get_data(as_text=True)
        self.assertIn("Link candidates", html)
        self.assertIn("Possible Linking", html)
        resp = self.client.post(f"/report/{num}/save",
                                data={"sec_B5": "edited text", "action": "COMPLETE", "note": "ok"})
        self.assertEqual(resp.status_code, 302)
        with open(self.path + ".decisions.json") as fh:
            dec = json.load(fh)
        self.assertEqual(dec[num]["status"], "COMPLETE")
        self.assertEqual(dec[num]["sections"]["B5"], "edited text")
        self.assertIn("COMPLETE", self.client.get("/").get_data(as_text=True))
        self.assertEqual(self.client.get("/export").status_code, 200)


if __name__ == "__main__":
    unittest.main()
