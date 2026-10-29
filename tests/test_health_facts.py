"""Tests for health fact extraction, its page checks and the review flow."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import documents
from stellis import health_facts
from stellis import notebooks
import sample_records
from stellis import server

LAB_PDF = sample_records.FOLDER / "2026-04-02-lab-results.pdf"
PAGES = {
    1: "Creatinine [Mass/volume] in Blood: 3.4 mg/dL\nLisinopril 10 MG tablet",
    2: "Diagnosis: Essential hypertension (disorder).",
}


def _fact(**overrides) -> dict[str, object]:
    fact = {
        "kind": "lab_result",
        "name": "Creatinine",
        "value": "3.4",
        "unit": "mg/dL",
        "date": "2026-04-02",
        "page": 1,
        "quote": "Creatinine [Mass/volume] in Blood: 3.4 mg/dL",
    }
    fact.update(overrides)
    return fact


def _reply(*facts) -> str:
    return json.dumps({"facts": list(facts)})


class ParseFactsTest(unittest.TestCase):

    def test_keeps_facts_whose_quote_is_on_the_cited_page(self):
        facts, dropped = health_facts.parse_facts(
            "```json\n" + _reply(
                _fact(),
                _fact(kind="medication",
                      name="Lisinopril",
                      value="",
                      unit="",
                      date="",
                      quote="lisinopril   10 mg TABLET")) + "\n```", PAGES,
            "2026-01-01")

        self.assertEqual(dropped, 0)
        self.assertEqual([(fact.kind, fact.name, fact.date) for fact in facts],
                         [("lab_result", "Creatinine", "2026-04-02"),
                          ("medication", "Lisinopril", "2026-01-01")])

    def test_drops_unbacked_malformed_and_duplicate_proposals(self):
        proposals = [
            _fact(page=2),
            _fact(quote="Creatinine 9.9 mg/dL"),
            _fact(page=7),
            _fact(kind="surgery"),
            _fact(value=""),
            _fact(kind="vital_sign", value=""),
            _fact(name=""),
            _fact(quote="mg"),
            _fact(name=["Creatinine"]),
            "Creatinine",
            _fact(),
            _fact(),
        ]

        facts, dropped = health_facts.parse_facts(_reply(*proposals), PAGES,
                                                  "2026-01-01")

        self.assertEqual(len(facts), 1)
        self.assertEqual(dropped, len(proposals) - 1)

    def test_measured_value_and_unit_must_appear_together_in_quote(self):
        for changes in (
                {"value": "9.9"},
                {"value": "3"},
                {"value": "4"},
                {"unit": "mmol/L"},
                {"value": "10", "unit": "mg/dL"},
        ):
            with self.subTest(changes=changes):
                facts, dropped = health_facts.parse_facts(
                    _reply(_fact(**changes)), PAGES, "2026-01-01")
                self.assertEqual((facts, dropped), ([], 1))

        pages = {1: "Blood pressure 120 / 80 mmHg. Weight 80kg."}
        facts, dropped = health_facts.parse_facts(_reply(
            _fact(kind="vital_sign", name="Blood pressure", value="120/80",
                  unit="mmHg", quote="Blood pressure 120 / 80 mmHg"),
            _fact(kind="vital_sign", name="Weight", value="80", unit="kg",
                  quote="Weight 80kg.")), pages, "2026-01-01")
        self.assertEqual((len(facts), dropped), (2, 0))

    def test_dates_are_real_calendar_dates_and_missing_dates_use_fallback(self):
        for date in ("2026-99-99", "2026-02-29", "2026-04-31", "nonsense", 123):
            with self.subTest(date=date):
                facts, dropped = health_facts.parse_facts(
                    _reply(_fact(date=date)), PAGES, "2026-01-01")
                self.assertEqual((facts, dropped), ([], 1))
        facts, dropped = health_facts.parse_facts(
            _reply(_fact(date="2024-02-29"), _fact(date="")), PAGES,
            "2026-01-01")
        self.assertEqual([fact.date for fact in facts],
                         ["2024-02-29", "2026-01-01"])
        self.assertEqual(dropped, 0)
        facts, dropped = health_facts.parse_facts(
            _reply(_fact(date="")), PAGES, "2026-99-99")
        self.assertEqual((facts, dropped), ([], 1))

    def test_unreadable_reply_is_an_error(self):
        for reply in ("not json", "[]", '{"facts": "none"}'):
            with self.subTest(reply=reply):
                with self.assertRaises(health_facts.ExtractionError):
                    health_facts.parse_facts(reply, PAGES, "2026-01-01")


class BuildPromptTest(unittest.TestCase):

    def test_skips_empty_pages_and_stops_at_the_size_limit(self):
        record = {
            "title":
                "Labs",
            "record_date":
                "2026-04-02",
            "pages": [{
                "number": 1,
                "text": "a" * 10
            }, {
                "number": 2,
                "text": ""
            }, {
                "number": 3,
                "text": "b" * 10
            }],
        }

        with mock.patch.object(health_facts, "MAX_PROMPT_CHARS", 15):
            prompt = health_facts.build_prompt(record)

        context = json.loads(prompt.split("\n\n", 1)[1])
        self.assertEqual([page["page"] for page in context["pages"]], [1])
        self.assertEqual(context["record_date"], "2026-04-02")


class HealthFactsApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        book = notebooks.create("Heart")["id"]
        self.document, _ = documents.create("lab.pdf", LAB_PDF.read_bytes(),
                                            book, "2026-04-02")
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def extract(self, reply: str):
        turn = mock.AsyncMock(return_value=(reply, "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(
                f"/api/v1/documents/{self.document['id']}/facts/extract")
        return response, turn

    def test_suggestions_are_reviewed_and_rejections_are_deleted(self):
        response, turn = self.extract(
            _reply(
                _fact(),
                _fact(name="Glucose",
                      value="83.5",
                      quote="Glucose [Mass/volume] in Blood: 83.5 mg/dL"),
                _fact(name="Invented", quote="not on this page at all")))

        body = response.json()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Creatinine [Mass/volume]", turn.await_args.args[0])
        self.assertEqual(body["dropped"], 1)
        creatinine, glucose = sorted(body["facts"], key=lambda f: f["name"])
        self.assertEqual(creatinine["target"],
                         f"/document/{self.document['id']}?page=1")
        self.assertEqual(
            self.client.post(f"/api/v1/facts/{creatinine['id']}/accept").json()
            ["status"], "accepted")
        self.assertEqual(
            self.client.delete(f"/api/v1/facts/{glucose['id']}").status_code,
            200)
        accepted = self.client.get("/api/v1/facts").json()["facts"]
        self.assertEqual(
            [(fact["name"], fact["document_title"]) for fact in accepted],
            [("Creatinine", self.document['title'])])
        # The literal adapter now also recognises this source's bracketed
        # labels. Those pending candidates must not resurrect the rejected
        # model proposal or remove the explicitly accepted model fact.
        remaining = self.client.get(
            f"/api/v1/documents/{self.document['id']}/facts").json()['facts']
        self.assertNotIn(glucose['id'], [fact['id'] for fact in remaining])
        self.assertIn(creatinine['id'], [fact['id'] for fact in remaining])

    def test_rejected_measurements_and_dates_never_become_accepted_facts(self):
        response, _ = self.extract(_reply(
            _fact(value="9.9"), _fact(date="2026-99-99"), _fact()))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["dropped"], 2)
        self.assertEqual(len(response.json()["facts"]), 1)
        self.client.post(
            f"/api/v1/documents/{self.document['id']}/facts/accept")
        accepted = self.client.get("/api/v1/facts").json()["facts"]
        self.assertEqual([(fact["value"], fact["date"]) for fact in accepted],
                         [("3.4", "2026-04-02")])

    def test_accept_all_accepts_every_suggestion_of_the_document(self):
        response, _ = self.extract(
            _reply(
                _fact(),
                _fact(name="Glucose",
                      value="83.5",
                      quote="Glucose [Mass/volume] in Blood: 83.5 mg/dL")))
        self.assertEqual(len(response.json()["facts"]), 2)

        accepted = self.client.post(
            f"/api/v1/documents/{self.document['id']}/facts/accept")
        again = self.client.post(
            f"/api/v1/documents/{self.document['id']}/facts/accept")
        missing = self.client.post("/api/v1/documents/missing/facts/accept")

        self.assertEqual(accepted.json(), {"accepted": 2})
        self.assertEqual(again.json(), {"accepted": 0})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(len(self.client.get("/api/v1/facts").json()["facts"]),
                         2)

    def test_extracting_again_keeps_accepted_facts(self):
        first, _ = self.extract(_reply(_fact()))
        fact_id = first.json()["facts"][0]["id"]
        self.client.post(f"/api/v1/facts/{fact_id}/accept")

        second, _ = self.extract(_reply(_fact()))

        facts = second.json()["facts"]
        self.assertEqual([(fact["id"], fact["status"]) for fact in facts],
                         [(fact_id, "accepted")])

    def test_deleting_the_document_deletes_its_facts(self):
        self.extract(_reply(_fact()))

        documents.delete(self.document["id"])

        self.assertEqual(health_facts.list_for(self.document["id"]), [])

    def test_errors(self):
        missing = self.client.post("/api/v1/documents/missing/facts/extract")
        failing = mock.AsyncMock(
            side_effect=agent_gateway.GatewayError("Allowance used"))
        with mock.patch.object(agent_gateway, "turn", failing):
            gateway = self.client.post(
                f"/api/v1/documents/{self.document['id']}/facts/extract")
        unknown_fact = self.client.post("/api/v1/facts/missing/accept")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual((gateway.status_code, gateway.json()["detail"]),
                         (503, "Allowance used"))
        self.assertEqual(unknown_fact.status_code, 404)


if __name__ == "__main__":
    unittest.main()
