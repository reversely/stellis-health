"""Tests for goals Stellis fills in from a person's words."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import coach
from stellis import server


def call(name, **arguments):
    return {"type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply(*calls, content=""):
    """A model message that makes ``calls``."""
    return mock.AsyncMock(return_value={"content": content, "tool_calls": list(calls)})


class DraftGoalsTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_each_tool_call_becomes_a_checked_draft(self):
        model = reply(
            call("fill_goal", activity="walk", amount=30, times_per_week=4, reminder="07:00"),
            call("fill_goal", activity="medicine", medicine="metformin", dose_amount=500,
                 dose_unit="mg", times_per_week=7.0, reminder=None))
        with mock.patch.object(coach, "_complete", model):
            result = await coach.draft_goals("Walk half an hour 4 days a week at 7, "
                                             "and metformin 500 mg daily")

        self.assertEqual([d["title_parts"] for d in result["drafts"]],
                         [["Walk", "30 minutes"], ["Take", "500 mg", "of", "metformin"]])
        self.assertEqual(result["drafts"][0]["reminder"], "07:00")
        self.assertEqual(result["drafts"][1]["times_per_week"], 7)
        sent = model.call_args.args
        self.assertEqual(sent[1], coach.GOAL_TOOLS)
        self.assertIn("metformin 500 mg", sent[0][1]["content"])

    async def test_values_outside_the_selectors_are_skipped(self):
        model = reply(call("fill_goal", activity="walk", amount=25, times_per_week=4),
                      call("fill_goal", activity="steps", times_per_week=7),
                      call("delete_goal", goal_id="x"),
                      call("fill_goal", activity="stretch", amount=10, times_per_week=3))
        with mock.patch.object(coach, "_complete", model):
            result = await coach.draft_goals("Walk 25 minutes, steps, and stretch")

        self.assertEqual([d["title"] for d in result["drafts"]], ["Stretch 10 minutes"])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertIn("amount for walk", result["skipped"][0])

    async def test_a_reply_without_tool_calls_is_passed_on(self):
        model = reply(content="Stellis tracks walks, exercise, medicines and sleep.")
        with mock.patch.object(coach, "_complete", model):
            result = await coach.draft_goals("Read more books")

        self.assertEqual(result["drafts"], [])
        self.assertIn("tracks walks", result["reply"])

    def test_unreadable_arguments_are_refused_by_the_checks(self):
        message = {"tool_calls": [{"function": {"name": "fill_goal", "arguments": "{oops"}}]}

        name, arguments = coach.tool_calls(message)[0]

        self.assertEqual((name, arguments), ("fill_goal", {}))
        with self.assertRaisesRegex(ValueError, "activity"):
            coach.check_goal(arguments)

    def test_the_tool_offers_each_selector_choice(self):
        schema = coach.FILL_GOAL["function"]["parameters"]["properties"]

        self.assertIn("pulse", schema["activity"]["enum"])
        self.assertIn("ml", schema["dose_unit"]["enum"])
        self.assertIn("walk: 10, 15, 20, 30, 45, 60 minutes", schema["amount"]["description"])


class DraftGoalsApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_drafts_are_returned_and_nothing_is_saved(self):
        model = reply(call("fill_goal", activity="sleep", amount=7.5, times_per_week=5))
        with mock.patch.object(coach, "_complete", model):
            response = self.client.post("/api/v1/goals/drafts", json={"text": "Sleep 7.5 hours"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["drafts"][0]["title"], "Sleep at least 7.5 hours")
        listed = self.client.get("/api/v1/goals", params={"today": "2026-10-22", "now": "09:00"})
        self.assertEqual(listed.json()["goals"], [])

    def test_empty_text_is_a_400_and_a_failed_model_a_503(self):
        self.assertEqual(self.client.post("/api/v1/goals/drafts", json={"text": " "}).status_code, 400)
        failing = mock.AsyncMock(side_effect=coach.CoachError("Stellis is unavailable. Try again."))
        with mock.patch.object(coach, "_complete", failing):
            response = self.client.post("/api/v1/goals/drafts", json={"text": "Walk"})
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
