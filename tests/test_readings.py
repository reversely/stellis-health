"""Tests for vital sign readings: fact mapping, usual ranges and the API."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import documents
from stellis import readings
import sample_records
from stellis import server

EXAM_PDF = sample_records.FOLDER / "2024-03-14-annual-exam.pdf"


def _fact(**overrides) -> dict[str, object]:
    fact = {
        "id": "f1",
        "kind": "vital_sign",
        "name": "Weight",
        "value": "95.2",
        "unit": "kg",
        "date": "2024-03-14",
        "target": "/document/d1?page=1",
        "document_title": "Annual exam",
    }
    fact.update(overrides)
    return fact


def _point(day: str, value: float, value2: float | None = None):
    return {"time": day, "value": value, "value2": value2}


class FactPointTest(unittest.TestCase):

    def test_maps_names_values_and_units(self):
        weight = readings.fact_point(_fact())
        pressure = readings.fact_point(
            _fact(name="Blood pressure", value="88/64", unit="mmHg"))
        pounds = readings.fact_point(_fact(value="210", unit="lb"))
        a1c = readings.fact_point(
            _fact(kind="lab_result", name="HbA1c", value="5.4 %", unit="%"))

        self.assertEqual(
            (weight["measure"], weight["value"], weight["source"],
             weight["target"], weight["title"]),
            ("weight", 95.2, "record", "/document/d1?page=1", "Annual exam"))
        self.assertEqual((pressure["value"], pressure["value2"]), (88, 64))
        self.assertEqual(pounds["value"], 95.3)
        self.assertEqual((a1c["measure"], a1c["value"]), ("hba1c", 5.4))

    def test_legacy_impossible_dates_do_not_reach_charts(self):
        for date in ("2026-99-99", "2026-02-29", "", "2026-04-31"):
            with self.subTest(date=date):
                self.assertIsNone(readings.fact_point(_fact(date=date)))
        self.assertIsNotNone(readings.fact_point(_fact(date="2024-02-29")))

    def test_unfamiliar_numeric_metric_is_discovered_by_name_and_unit(self):
        point = readings.fact_point(_fact(name="Waist", unit="cm"))
        self.assertTrue(point['measure'].startswith('report_'))
        self.assertEqual(point['value'],95.2)
        self.assertIsNone(readings.fact_point(_fact(name="Waist",value="<95.2",unit="cm")))

    def test_ignores_facts_that_are_not_readings(self):
        for fact in (
                _fact(kind="medication"),
                _fact(value="heavy"),
                _fact(value="950"),
                _fact(name="Blood pressure", value="88"),
        ):
            with self.subTest(fact=fact):
                self.assertIsNone(readings.fact_point(fact))


class UsualRangeTest(unittest.TestCase):

    def test_middle_of_the_ninety_days_before_the_latest_reading(self):
        points = [_point("2025-01-01", 200)]  # Outside the window.
        points += [
            _point(f"2026-03-{day:02d}", value)
            for day, value in ((1, 80), (5, 81), (9, 82), (13, 83), (17, 84),
                               (21, 85))
        ]
        points.append(_point("2026-03-30", 90))

        usual = readings.usual_range(points)

        self.assertAlmostEqual(usual["low"], 80.5)
        self.assertAlmostEqual(usual["high"], 84.5)
        self.assertEqual(usual["since"], "2025-12-30")
        self.assertEqual(readings.status(points[-1], usual), "above")
        self.assertEqual(readings.status(_point("2026-03-30", 82), usual),
                         "usual")
        self.assertEqual(readings.status(_point("2026-03-30", 70), usual),
                         "below")

    def test_blood_pressure_uses_both_numbers(self):
        points = [_point(f"2026-03-{day:02d}", 120, 80) for day in range(1, 7)
                 ] + [_point("2026-03-10", 118, 95)]

        usual = readings.usual_range(points)

        self.assertEqual((usual["low2"], usual["high2"]), (80, 80))
        self.assertEqual(readings.status(points[-1], usual), "above")

    def test_a_counted_measure_compares_day_totals(self):
        points = [_point(f"2026-03-{day:02d}T0{hour}:00:00+00:00", 1)
                  for day in range(1, 8) for hour in range(8)]

        totals = readings._day_totals(points)

        self.assertEqual([point["value"] for point in totals], [8.0] * 7)
        self.assertEqual(readings.usual_range(totals)["low"], 8.0)

    def test_too_few_readings_have_no_range(self):
        points = [_point(f"2026-03-{day:02d}", 80) for day in range(1, 6)]

        self.assertIsNone(readings.usual_range(points))
        self.assertEqual(readings.status(points[-1], None), "unknown")


class ReadingsApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.document, _ = documents.create("exam.pdf", EXAM_PDF.read_bytes(),
                                            None, "2024-03-14")
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_scan_evidence_roundtrip_and_retry(self):
        body = {"measure": "pulse", "value": 72, "source": "camera", "quality": .85,
                "note": "Resting", "capture_id": "scan-267-pulse",
                "scan_evidence": {"method": "facephys", "duration": 45,
                                  "checkin": {"id": "reply-1"}}}
        first = self.client.post("/api/v1/readings", json=body)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/v1/readings", json=body)
        self.assertEqual(first.json()["id"], second.json()["id"])
        points = self.measure("pulse")["points"]
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["scan_evidence"], body["scan_evidence"])
        self.assertEqual(points[0]["note"], "Resting")
        changed = {**body, "scan_evidence": {"method": "pos"}}
        self.assertEqual(self.client.post("/api/v1/readings", json=changed).status_code, 400)
        oversized = {**body, "capture_id": "too-large", "scan_evidence": {"x": "x" * 16001}}
        self.assertEqual(self.client.post("/api/v1/readings", json=oversized).status_code, 400)

    def measure(self, key: str) -> dict[str, object]:
        measures = self.client.get("/api/v1/readings").json()["measures"]
        self.assertEqual([item["key"] for item in measures],
                         list(readings.MEASURES))
        return next(item for item in measures if item["key"] == key)

    def test_record_and_logged_readings_share_one_series(self):
        reply = json.dumps({
            "facts": [{
                "kind": "vital_sign",
                "name": "Weight",
                "value": "95.2",
                "unit": "kg",
                "date": "",
                "page": 1,
                "quote": "Weight: 95.2 kg",
            }, {
                "kind": "vital_sign",
                "name": "Blood pressure",
                "value": "88/64",
                "unit": "mmHg",
                "date": "",
                "page": 1,
                "quote": "Blood pressure: 88/64 mmHg",
            }]
        })
        with mock.patch.object(agent_gateway, "turn",
                               mock.AsyncMock(return_value=(reply, "s"))):
            self.client.post(
                f"/api/v1/documents/{self.document['id']}/facts/extract")
        self.client.post(
            f"/api/v1/documents/{self.document['id']}/facts/accept")

        logged = self.client.post("/api/v1/readings",
                                  json={
                                      "measure": "weight",
                                      "value": 94.1,
                                      "taken_at": "2024-03-20T07:30:00",
                                      "note": "  after   breakfast ",
                                  })

        self.assertEqual(logged.status_code, 200, logged.text)
        weight = self.measure("weight")
        self.assertEqual(
            [(point["source"], point["value"]) for point in weight["points"]],
            [("record", 95.2), ("self", 94.1)])
        self.assertEqual(weight["points"][0]["target"],
                         f"/document/{self.document['id']}?page=1")
        self.assertEqual(weight["points"][1]["time"],
                         "2024-03-20T07:30:00+00:00")
        self.assertEqual(weight["points"][1]["note"], "after breakfast")
        self.assertEqual(weight["status"], "unknown")
        pressure = self.measure("blood_pressure")
        self.assertEqual(
            (pressure["points"][0]["value"], pressure["points"][0]["value2"]),
            (88, 64))

        deleted = self.client.delete(f"/api/v1/readings/{logged.json()['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(self.measure("weight")["points"]), 1)

    def test_camera_readings_keep_their_quality(self):
        added = self.client.post("/api/v1/readings",
                                 json={
                                     "measure": "pulse",
                                     "value": 64,
                                     "source": "camera",
                                     "quality": 0.82,
                                 })

        point = self.measure("pulse")["points"][0]
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual((point["source"], point["quality"], point["title"]),
                         ("camera", 0.82, "Camera reading"))

    def test_invalid_readings_are_refused(self):
        for body in (
            {
                "measure": "mood",
                "value": 3
            },
            {
                "measure": "weight",
                "value": 2
            },
            {
                "measure": "weight",
                "value": 80,
                "value2": 70
            },
            {
                "measure": "blood_pressure",
                "value": 120
            },
            {
                "measure": "blood_pressure",
                "value": 80,
                "value2": 120
            },
            {
                "measure": "pulse",
                "value": 60,
                "source": "record"
            },
            {
                "measure": "pulse",
                "value": 60,
                "source": "camera",
                "quality": 2
            },
            {
                "measure": "pulse",
                "value": 60,
                "taken_at": "yesterday"
            },
            {
                "measure": "pulse",
                "value": 60,
                "taken_at": "2999-01-01T00:00:00"
            },
        ):
            with self.subTest(body=body):
                response = self.client.post("/api/v1/readings", json=body)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            self.client.delete("/api/v1/readings/missing").status_code, 404)


class AccuracyPageTest(unittest.TestCase):

    def test_the_accuracy_page_states_the_camera_error(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(
                os.environ, {"STELLIS_DATA_DIR": folder}), \
                testclient.TestClient(server.app) as client:
            page = client.get("/accuracy")

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="camera"', page.text)
        self.assertIn("12.3 beats a minute", page.text)


if __name__ == "__main__":
    unittest.main()
