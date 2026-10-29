"""Tests for goals built from selectors, weekly progress and reminders."""

import datetime
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import goals
from stellis import readings
from stellis import server

TODAY = "2026-10-22"  # A Thursday; its week starts on Monday, Oct 19.
WALK = {"activity": "walk", "amount": 30, "times_per_week": 4,
        "reminder": None}
METFORMIN = {"activity": "medicine", "medicine": "metformin",
             "dose_amount": 500, "dose_unit": "mg",
             "times_per_week": 7, "reminder": "20:00"}


class GoalsTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def overview(self, now="12:00", today=TODAY, offset=0):
        return goals.overview(today, offset, now)

    def test_a_goal_takes_its_title_from_its_selectors(self):
        goals.create(WALK)
        goals.create(METFORMIN)
        goals.create({"activity": "sleep", "amount": 7, "times_per_week": 5})
        goals.create({"activity": "blood_pressure", "times_per_week": 3})

        titles = [goal["title"] for goal in self.overview()["goals"]]

        self.assertEqual(titles, ["Walk 30 minutes", "Take 500 mg of metformin",
                                  "Sleep at least 7 hours",
                                  "Check blood pressure"])

    def test_values_outside_the_selectors_are_refused(self):
        for fields, message in (
            ({**WALK, "activity": "steps"}, "activity"),
            ({**WALK, "amount": 25}, "amount"),
            ({**WALK, "times_per_week": 8}, "1 to 7"),
            ({**WALK, "reminder": "8pm"}, "reminder"),
            ({**METFORMIN, "medicine": " "}, "medicine name"),
        ):
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, message):
                    goals.create(fields)

    def test_logged_days_fill_this_week_and_the_weekly_history(self):
        goal_id = goals.create(WALK)["id"]
        for day in ("2026-10-06", "2026-10-08", "2026-10-19", "2026-10-21",
                    "2026-10-22"):
            goals.mark(goal_id, day, True)

        goal = self.overview()["goals"][0]

        self.assertEqual(goal["this_week"], 3)
        self.assertTrue(goal["done_today"])
        self.assertEqual(goal["weeks"], [0, 0, 0, 2, 0, 3])

        goals.mark(goal_id, TODAY, False)
        self.assertEqual(self.overview()["goals"][0]["this_week"], 2)

    def test_readings_count_toward_a_goal_tied_to_their_measure(self):
        goals.create({"activity": "blood_pressure", "times_per_week": 3})
        goals.create({"activity": "sleep", "amount": 7, "times_per_week": 5})
        goals.create({"activity": "pulse", "times_per_week": 7})
        readings.add("blood_pressure", 128, 82,
                     taken_at="2026-10-20T08:00:00+00:00")
        readings.add("sleep", 7.5, taken_at="2026-10-21T07:00:00+00:00")
        readings.add("sleep", 6, taken_at="2026-10-22T07:00:00+00:00")
        readings.add("pulse", 64, taken_at="2026-10-22T07:00:00+00:00")
        readings.add("pulse", 66, source="camera", quality=0.8,
                     taken_at="2026-10-21T07:00:00+00:00")

        weeks = [goal["this_week"] for goal in self.overview()["goals"]]

        # The 6-hour night misses a 7-hour goal; only camera pulses count.
        self.assertEqual(weeks, [1, 1, 1])

    def test_a_reminder_asks_from_its_time_until_the_day_is_logged(self):
        goal_id = goals.create(METFORMIN)["id"]

        self.assertEqual(self.overview("19:59")["reminders"], [])
        reminder = self.overview("20:00")["reminders"][0]
        self.assertEqual(reminder["ask"],
                         ["Did you take", "500 mg", "of", "metformin", "yet?"])
        self.assertEqual(reminder["title"], "Take 500 mg of metformin")
        self.assertEqual(reminder["state"], "ask")

        goals.not_yet(goal_id, TODAY)
        self.assertEqual(self.overview("21:00")["reminders"][0]["state"],
                         "pending")

        goals.mark(goal_id, TODAY, True)
        self.assertEqual(self.overview("21:00")["reminders"], [])
        # A new day asks again.
        tomorrow = self.overview("20:30", "2026-10-23")["reminders"][0]
        self.assertEqual(tomorrow["state"], "ask")

    def test_a_reminder_question_names_its_goal(self):
        goals.create({**WALK, "reminder": "08:00"})
        goals.create({"activity": "sleep", "amount": 7, "times_per_week": 5,
                      "reminder": "08:00"})

        asks = [r["ask"] for r in self.overview("09:00")["reminders"]]

        self.assertEqual(asks, [["Did you walk for", "30 minutes", "yet?"],
                                ["Did you log last night's sleep yet?"]])

    def test_a_dose_keeps_its_amount_and_unit_apart(self):
        goals.create({**METFORMIN, "dose_amount": 1, "dose_unit": "tablets"})
        goals.create({**METFORMIN, "medicine": "lactulose", "dose_amount": 15,
                      "dose_unit": "ml"})
        goals.create({**METFORMIN, "medicine": "vitamin D",
                      "dose_amount": None})

        parts = [g["title_parts"] for g in self.overview()["goals"]]

        self.assertEqual(parts, [["Take", "1 tablet", "of", "metformin"],
                                 ["Take", "15 ml", "of", "lactulose"],
                                 ["Take", "vitamin D"]])
        with self.assertRaisesRegex(ValueError, "dose unit"):
            goals.create({**METFORMIN, "dose_unit": "spoons"})

    def test_a_met_weekly_goal_stops_its_reminders_for_the_week(self):
        goal_id = goals.create({**WALK, "times_per_week": 2,
                                "reminder": "08:00"})["id"]
        goals.mark(goal_id, "2026-10-19", True)
        goals.mark(goal_id, "2026-10-20", True)

        self.assertEqual(self.overview("09:00")["reminders"], [])

    def test_a_late_evening_reading_counts_on_the_local_day(self):
        goals.create({"activity": "blood_pressure", "times_per_week": 3})
        # 23:30 on Oct 21 in New York (UTC-4) is Oct 22 in UTC.
        readings.add("blood_pressure", 120, 80,
                     taken_at="2026-10-22T03:30:00+00:00")

        goal = self.overview(today="2026-10-21", offset=-240)["goals"][0]

        self.assertTrue(goal["done_today"])


class GoalsApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def listed(self, now="21:00"):
        return self.client.get("/api/v1/goals", params={
            "today": datetime.date.today().isoformat(), "now": now}).json()

    def test_a_goal_is_added_answered_edited_and_deleted(self):
        created = self.client.post("/api/v1/goals", json=METFORMIN)
        self.assertEqual(created.status_code, 200, created.text)
        goal_id = created.json()["id"]
        today = datetime.date.today().isoformat()
        self.assertEqual(self.listed()["reminders"][0]["state"], "ask")

        self.client.post(f"/api/v1/goals/{goal_id}/days/{today}/not-yet")
        self.assertEqual(self.listed()["reminders"][0]["state"], "pending")
        self.client.put(f"/api/v1/goals/{goal_id}/days/{today}",
                        json={"done": True})
        self.assertEqual(self.listed()["reminders"], [])

        edited = self.client.put(f"/api/v1/goals/{goal_id}",
                                 json={**METFORMIN, "dose_amount": 1000})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.listed()["goals"][0]["title"],
                         "Take 1000 mg of metformin")
        self.assertIn("walk", [a["key"] for a in self.listed()["activities"]])

        self.assertEqual(self.client.delete(f"/api/v1/goals/{goal_id}")
                         .status_code, 200)
        self.assertEqual(self.listed()["goals"], [])

    def test_bad_values_are_a_400_and_a_missing_goal_a_404(self):
        bad = self.client.post("/api/v1/goals", json={**WALK, "amount": 25})
        self.assertEqual(bad.status_code, 400)
        missing = self.client.put("/api/v1/goals/nope/days/2026-10-22",
                                  json={"done": True})
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
