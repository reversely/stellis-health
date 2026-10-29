"""Tests for the guided tour on a docked call."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import documents
from stellis import intake
from stellis import notebooks
from stellis import server
from stellis import tour


class TourTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)

    def say(self, text, answer=("Notebooks group records.",)):
        async def model(messages, tools=None):
            for piece in answer:
                yield piece

        tour.add_person_turn(text)
        with mock.patch.object(intake, "stream_tokens", model):
            async def pieces():
                return [piece async for piece in tour.reply()]
            return asyncio.run(pieces())

    def test_the_tour_does_not_insert_patient_records_or_notebooks(self):
        first = tour.begin()["sample"]
        second = tour.begin()["sample"]
        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertEqual(notebooks.list_notebooks(), [])
        self.assertEqual(documents.list_all(), [])

    def test_next_walks_every_step_then_sums_up_and_hangs_up(self):
        tour.begin()
        self.assertEqual(tour.opening_actions()[0]["selector"], "#health-dashboard")
        tour.opening()
        shown = []
        for _ in tour.STEPS[1:]:
            pieces = self.say("Okay, next")
            shown.append(pieces[0]["path"])
            self.assertIsInstance(pieces[1], str)

        self.assertEqual(shown, ["/", "/", "/chat", "/trends", "/"])
        last = self.say("Got it")
        self.assertEqual(last[0]["path"], "/")
        self.assertEqual(last[-2], "Have a nice day.")
        self.assertEqual(last[-1], tour.END_MARK)

    def test_the_tour_offers_the_example_records(self):
        step = next(step for step in tour.STEPS if step.key == "examples")
        self.assertIn("#example-workspace-bar .example-bar-add", step.selector)
        self.assertIn("li.chat-idea[data-command='example']", step.selector)
        self.assertIn("approve the notebooks I propose", step.line)
        self.assertIn("notebooks in families", tour.closing())

    def test_a_question_is_answered_and_the_tour_waits(self):
        tour.begin()
        tour.opening()

        pieces = self.say("What goes in a notebook?")

        self.assertEqual(pieces, ["Notebooks group records.", tour.WAIT])
        self.assertEqual(tour.load()["step"], 0)

    def test_stop_ends_the_tour_early(self):
        tour.begin()
        tour.opening()

        self.assertEqual(self.say("Please stop the tour")[-1], tour.END_MARK)

    def test_the_closing_names_the_goals_from_the_onboarding_call(self):
        self.assertIn("Add your first goal", tour.closing())
        intake.save_notes([{"heading": "Goals", "items": ["Walk after dinner",
                                                          "Lower my blood pressure."]}], 0)

        self.assertIn("You told me your goals: walk after dinner and lower my blood pressure.",
                      tour.closing())

    def test_intent(self):
        self.assertEqual(tour.intent("Next"), "next")
        self.assertEqual(tour.intent("okay sounds good"), "next")
        self.assertEqual(tour.intent("Yes, but how do I add a record from my doctor?"), "question")
        self.assertEqual(tour.intent("I'm done, thanks"), "stop")


class TourSocketTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name,
                                                   "STELLIS_CALL_MODELS": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_the_first_step_is_shown_before_the_first_line(self):
        with self.client.websocket_connect("/api/v1/tour/call") as socket:
            messages = [socket.receive_json() for _ in range(3)]
            socket.send_json({"type": "text", "text": "Next"})
            while not (messages[-1]["type"] == "action" and messages[-1]["step"] == "examples"):
                messages.append(socket.receive_json())

        self.assertEqual([m["type"] for m in messages[:3]], ["ready", "action", "say"])
        self.assertEqual(messages[1]["step"], "overview")
        self.assertTrue(messages[2]["text"].startswith("Hi again"))

    def test_text_only_tour_skips_models_and_retains_origin_checks(self):
        from stellis import intake_voice
        from starlette.websockets import WebSocketDisconnect
        with mock.patch.object(intake_voice, 'acquire') as acquire:
            with self.client.websocket_connect('/api/v1/tour/call?voice=0') as socket:
                self.assertFalse(socket.receive_json()['voice'])
            acquire.assert_not_called()
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect('/api/v1/tour/call?voice=0', headers={'origin':'https://unrelated.invalid'}) as socket:
                socket.receive_json()
        self.assertEqual(caught.exception.code, 4401)


if __name__ == "__main__":
    unittest.main()
