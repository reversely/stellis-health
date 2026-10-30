"""Tests for coach calls: the morning summary, tools, endings and the call socket."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

import datetime

from stellis import care_events
from stellis import coach_call
from stellis import goals
from stellis import trackers
from stellis import intake
from stellis import server

TODAY = "2026-10-22"
WALK = {"activity": "walk", "amount": 30, "times_per_week": 4, "reminder": "07:00"}
METFORMIN = {"activity": "medicine", "medicine": "metformin", "dose_amount": 500,
             "dose_unit": "mg", "times_per_week": 7, "reminder": "20:00"}


class Model:
    """Replaces the streamed model reply with scripted rounds.

    Each round is a list of text pieces and tool calls ({name, arguments}).
    """

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.sent = []

    async def stream_tokens(self, messages, tools=None):
        self.sent.append((messages, tools))
        for index, piece in enumerate(self.rounds.pop(0)):
            if isinstance(piece, dict):
                yield {"id": f"call_{len(self.sent)}_{index}", **piece}
            else:
                yield piece


class CoachCallTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        clock=mock.patch.object(goals,"_now",return_value=datetime.datetime.fromisoformat(TODAY+"T08:00:00+00:00"));clock.start();self.addCleanup(clock.stop)

    async def start(self, kind="morning", now="08:00"):
        await coach_call.begin(kind, TODAY, now, 0)
        return coach_call.opening()

    async def turn(self, text, model):
        coach_call.add_person_turn(text)
        with mock.patch.object(intake, "stream_tokens", model.stream_tokens):
            pieces = [piece async for piece in coach_call.reply()]
        coach_call.add_agent_turn(" ".join(p for p in pieces if isinstance(p, str)))
        return pieces

    async def test_the_morning_check_in_opens_with_the_day_from_data(self):
        goals.create(WALK)
        goals.create(METFORMIN)

        opening = await self.start()

        self.assertEqual(opening, (
            "For Thursday, October 22, it's Stellis with your check-in. Walk 30 minutes, 0 of 4 this week. "
            "Take 500 mg of metformin, 0 of 7 this week. Your reminders are walk 30 minutes at 7 am and "
            "take 500 mg of metformin at 8 pm. What would you like to review for that day?"))
        self.assertFalse(coach_call.morning_due(TODAY, "09:00"))

    async def test_a_call_the_person_starts_asks_how_to_help(self):
        self.assertEqual(await self.start("call", "19:30"),
                         "For Thursday, October 22, it's Stellis. What can I help you with?")
        self.assertTrue(coach_call.morning_due(TODAY, "09:00"))

    async def test_a_demo_call_opens_on_todays_topics(self):
        # Owner, 2026-10-30 (#270): the demo call reads as an ongoing conversation, with the check-in card.
        goals.create(WALK)
        goals.create(METFORMIN)
        with mock.patch.object(coach_call.storage, "in_demo", return_value=True):
            opening = await self.start("call", "19:30")
            actions = coach_call.opening_actions()

        self.assertEqual(opening, "For Thursday, October 22, it's Stellis. Walk 30 minutes, 0 of 4 this week. "
                                  "Take 500 mg of metformin, 0 of 7 this week. "
                                  "Shall we go through these, or is there something else on your mind?")
        self.assertEqual(actions[0]["action"], "agenda")
        self.assertEqual(coach_call.opening_actions(), [])

    async def test_native_appointment_is_included_without_an_external_calendar_setup_pitch(self):
        care_events.add('Review blood results',TODAY+'T09:30:00+00:00','scheduled')
        care_events.add('Repeat tests',TODAY+'T11:00:00+00:00','planned')
        opening=await self.start()
        self.assertIn('Review blood results at 9:30 am',opening)
        self.assertNotIn('Connect your calendar',opening)
        self.assertEqual([i['title'] for i in coach_call.load()['context']['appointments']['items']],['Review blood results'])

    async def test_appointments_are_read_in_local_time(self):
        events = [{"title": "Dr. Patel", "start": "2026-10-22T13:30:00Z", "end": ""}]
        with mock.patch.object(coach_call.calendar, "connections",
                               return_value=[{"provider": "google", "connected": True}]), \
                mock.patch.object(coach_call.calendar, "events", mock.AsyncMock(return_value=events)):
            await coach_call.begin("morning", TODAY, "08:00", -240)

        self.assertIn("You have 1 appointment today: Dr. Patel at 9:30 am.", coach_call.opening())

    def test_the_morning_check_in_is_offered_once_each_morning(self):
        self.assertFalse(coach_call.morning_due(TODAY, "04:59"))
        self.assertTrue(coach_call.morning_due(TODAY, "05:00"))
        self.assertFalse(coach_call.morning_due(TODAY, "12:00"))
        coach_call.answer_morning(TODAY, "declined")
        self.assertFalse(coach_call.morning_due(TODAY, "08:00"))
        self.assertTrue(coach_call.morning_due("2026-10-23", "08:00"))

    def test_a_snooze_rings_again_at_its_time(self):
        coach_call.answer_morning(TODAY, "snoozed", "08:00")
        self.assertFalse(coach_call.morning_due(TODAY, "08:29"))
        self.assertTrue(coach_call.morning_due(TODAY, "08:30"))
        self.assertTrue(coach_call.morning_due(TODAY, "13:00"))
        coach_call.answer_morning(TODAY, "snoozed", "23:45")
        self.assertFalse(coach_call.morning_due(TODAY, "23:59"))
        with self.assertRaises(ValueError):
            coach_call.answer_morning(TODAY, "snoozed", None)

    async def test_the_agenda_comes_from_courses_plans_and_goals(self):
        walk = goals.create(WALK)["id"]
        start = (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=60)).isoformat()
        course = trackers.save("medicine", {"name": "Ferrous sulfate", "dose_amount": 300, "dose_unit": "mg",
                                            "schedule_type": "every", "every_days": 2, "start": start,
                                            "units_per_fill": 45})
        plan = care_events.add("Repeat CBC and ferritin", "2026-11-10T09:00:00+00:00", "planned")
        context = (await coach_call.begin("morning", TODAY, "08:00", 0))["context"]
        keys = [item["key"] for item in context["agenda"]]
        self.assertEqual(keys, [f"dose:{course['id']}", f"derived:{course['id']}:fill-1",
                                f"plan:{plan['id']}", f"goal:{walk}"])
        self.assertEqual(context["agenda"][0]["label"], "Medicine")
        self.assertIn("runs out around", context["agenda"][1]["title"])
        self.assertEqual(context["agenda"][0]["title"], "Take 300 mg of Ferrous sulfate today")
        self.assertEqual(context["agenda"][2]["title"], "Repeat CBC and ferritin, no date booked")
        opening = coach_call.opening()
        self.assertIn("Repeat CBC and ferritin is planned for November, with no date booked.", opening)
        self.assertIn({"item": f"goal:{walk}", "title": "Walk 30 minutes", "type": "done"}, context["loggable"])

    def test_dates_resolve_against_the_local_day(self):
        self.assertEqual(coach_call.resolve_day("yesterday", TODAY), "2026-10-21")
        self.assertEqual(coach_call.resolve_day("Today", TODAY), TODAY)
        self.assertEqual(coach_call.resolve_day("2026-10-20", TODAY), "2026-10-20")
        for bad in ("tomorrow", "2026-10-23", "2026-10-01", "last week"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                coach_call.resolve_day(bad, TODAY)

    def test_the_persons_words_decide_the_day_over_the_models_reading(self):
        self.assertEqual(coach_call.spoken_day("I did all my shots tonight.", TODAY), TODAY)
        self.assertEqual(coach_call.spoken_day("Yes, tonight.", TODAY), TODAY)
        self.assertEqual(coach_call.spoken_day("I took them last night", TODAY), "2026-10-21")
        self.assertIsNone(coach_call.spoken_day("I took them", TODAY))
        walk = goals.create(WALK)["id"]
        context = {"today": TODAY, "person_words": "Yes, tonight.",
                   "loggable": [{"item": f"goal:{walk}", "title": "Walk", "type": "done"}]}
        # The model read the clock as the next morning and chose yesterday; the person said tonight.
        _, action = coach_call.run_tool({"name": "log_entry", "arguments": {
            "item": f"goal:{walk}", "when": "yesterday", "evidence": "Yes, tonight."}}, context)
        self.assertEqual(action["day"], TODAY)

    async def test_a_named_goal_fills_the_form_and_a_done_activity_is_logged(self):
        walk = goals.create(WALK)["id"]
        await self.start("call", "10:00")
        model = Model(
            [{"name": "fill_goal", "arguments": {"activity": "stretch", "amount": 10,
                                                 "times_per_week": 3}},
             "I filled in a stretching goal on your home page. Add it with Add goal."],
            [{"name": "log_entry", "arguments": {"item": "goal:" + walk, "when": "yesterday",
                                                 "evidence": "I walked last night"}},
             {"name": "topic_done", "arguments": {"key": "goal:" + walk}}],
            ["Last night was Wednesday, October 21. Check it before I log it."])

        first = await self.turn("Add stretching ten minutes three times a week", model)
        second = await self.turn("I walked last night", model)

        self.assertEqual(first[0]["action"], "fill_goal")
        self.assertEqual(first[0]["values"]["title"], "Stretch 10 minutes")
        proposal = next(p for p in second if isinstance(p, dict) and p.get('action') == 'propose_log')
        self.assertEqual(proposal["action"], "propose_log")
        self.assertEqual((proposal["day"], proposal["day_text"], proposal["tool"]),
                         ("2026-10-21", "Wednesday, October 21", "log_entry"))
        self.assertEqual(proposal["proposals"], [{"item": "goal:" + walk, "value": True,
                                                  "evidence": "I walked last night"}])
        self.assertFalse(any(isinstance(piece,dict) and piece.get("action")=="covered" for piece in second))
        # Nothing is logged until the person confirms on screen.
        self.assertEqual(goals.overview("2026-10-21", 0, "09:00")["goals"][0]["done_today"], False)
        # The second round, after a reply of only tool calls, reads their results.
        messages, tools = model.sent[-1]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("logging needs confirmation", messages[-1]["content"])
        self.assertIn("Waiting for the person to confirm Walk 30 minutes on Wednesday, October 21",
                      messages[-2]["content"])
        self.assertIn("fill_goal",[tool["function"]["name"] for tool in tools])
        self.assertIn("log_entry",[tool["function"]["name"] for tool in tools])
        # Later turns see each earlier tool call and its result.
        history = coach_call.conversation(coach_call.load())
        self.assertEqual([m["role"] for m in history[-5:]],
                         ["user", "assistant", "tool", "tool", "assistant"])

    async def test_the_morning_check_in_speaks_only_its_script(self):
        # Owner, 2026-10-28: check-in calls run a predefined script. A yes logs without the model.
        walk = goals.create(WALK)["id"]
        await self.start()
        tired = await self.turn("Pretty tired", Model(["The model's words are never spoken here."]))
        self.assertEqual([p for p in tired if isinstance(p, str)],
                         [coach_call.SCRIPT["thanks"], "Did you walk 30 minutes today?"])
        said_yes = await self.turn("yes", Model())
        proposal = next(p for p in said_yes if isinstance(p, dict))
        self.assertEqual((proposal["action"], proposal["day"], proposal["proposals"][0]["item"]),
                         ("propose_log", TODAY, "goal:" + walk))
        self.assertEqual([p for p in said_yes if isinstance(p, str)], [
            "I've put Walk 30 minutes for Thursday, October 22 on screen. Check it, then press Log selected.",
            "Once you log it, that's 1 of 4 this week.", coach_call.SCRIPT["anything_else"]])
        done = await self.turn("no thanks", Model())
        self.assertEqual(done, [coach_call.SCRIPT["goodbye"], coach_call.END_MARK])
        self.assertEqual(goals.overview(TODAY, 0, "09:00")["goals"][0]["done_today"], False)

    async def test_the_script_reads_a_mixed_answer_and_asks_again_once(self):
        walk = goals.create(WALK)["id"]
        goals.create(METFORMIN)
        await self.start()
        walked = await self.turn("Fine, I walked this morning", Model(
            [{"name": "log_entry", "arguments": {"item": "goal:" + walk, "when": "today", "evidence": "I walked this morning"}}]))
        self.assertEqual(next(p for p in walked if isinstance(p, dict))["proposals"][0]["item"], "goal:" + walk)
        # The walk is not asked again; the next due item is.
        self.assertEqual([p for p in walked if isinstance(p, str)][-1], "Did you take 500 mg of metformin today?")
        unclear = await self.turn("the weather is nice", Model([]))
        self.assertEqual([p for p in unclear if isinstance(p, str)],
                         ["Sorry, I didn't catch that. Did you take 500 mg of metformin today?"])
        again = await self.turn("the weather is nice", Model([]))
        self.assertEqual([p for p in again if isinstance(p, str)],
                         [coach_call.SCRIPT["later"], coach_call.SCRIPT["anything_else"]])

    async def test_the_script_carries_on_when_the_model_is_unavailable(self):
        goals.create(WALK)
        await self.start()
        class Down(Model):
            async def stream_tokens(self, messages, tools=None):
                raise intake.IntakeError("unavailable")
                yield
        pieces = await self.turn("I'm fine", Down())
        self.assertEqual([p for p in pieces if isinstance(p, str)],
                         [coach_call.SCRIPT["thanks"], "Did you walk 30 minutes today?"])

    async def test_stellis_says_goodbye_and_hangs_up_when_asked(self):
        await self.start("call", "10:00")

        pieces = await self.turn("No, that's all", Model([{"name": "end_call", "arguments": {}}]))

        self.assertEqual(pieces[-1], coach_call.END_MARK)
        self.assertIn("My Health", " ".join(pieces))
        self.assertIn("Goodbye", " ".join(pieces))

    async def test_bad_tool_calls_do_nothing(self):
        await self.start("call", "10:00")
        model = Model([{"name": "log_entry", "arguments": {"item": "goal:nope", "when": "today", "evidence": "x"}},
                       {"name": "topic_done", "arguments": {"key": "nope"}},
                       {"name": "fill_goal", "arguments": {"activity": "steps"}},
                       {"name": "delete_everything", "arguments": {}}],
                      ["Sorry, I couldn't do that."])

        pieces = await self.turn("Log my steps", model)

        self.assertEqual(pieces, ["Sorry, I couldn't do that."])
        results = [m["content"] for m in model.sent[-1][0] if m["role"] == "tool"]
        self.assertTrue(all(result.startswith("Not") for result in results), results)

    async def test_a_results_question_is_answered_from_the_reports(self):
        # Owner, 2026-10-30 (#270): the call refused "help me understand my most recent results".
        await self.start("call", "10:00")
        model = Model(["Your ferritin of 14 is low, so your iron stores are low."])
        with mock.patch.object(coach_call.call_records, "answer",
                               mock.AsyncMock(return_value="Ferritin 14 ug/L is low: low iron stores.")) as read:
            pieces = await self.turn("Can you help me understand my most recent results for a blood report?", model)

        read.assert_awaited_once()
        self.assertEqual(" ".join(pieces), "Your ferritin of 14 is low, so your iron stores are low.")
        tool = [m["content"] for m in model.sent[0][0] if m["role"] == "tool"]
        self.assertEqual(tool, ["Ferritin 14 ug/L is low: low iron stores."])

    async def test_a_sentence_naming_a_tool_is_not_spoken(self):
        await self.start("call", "10:00")
        model = Model(["We need to call ask_records with the question. ", "Anything else?"])

        pieces = await self.turn("How is my walking going?", model)

        self.assertEqual(pieces, ["Anything else?"])

    async def test_a_long_call_wraps_up_then_ends(self):
        await self.start("call", "10:00")
        for n in range(coach_call.MAX_ANSWERS):
            coach_call.add_person_turn(f"Question {n}")
            coach_call.add_agent_turn("An answer.")
        self.assertTrue('"wrap_up": true' in coach_call.conversation(coach_call.load())[0]["content"])

        pieces = await self.turn("One more", Model(["Here you go."]))

        self.assertEqual(pieces[-1], coach_call.END_MARK)


class CoachCallSocketTest(unittest.TestCase):

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

    def test_a_typed_call_sends_actions_and_ends(self):
        model = Model(
            [{"name": "fill_goal", "arguments": {"activity": "meditate", "amount": 10,
                                                 "times_per_week": 5}},
             "Done, it's on your home page."],
            ["Have a nice day.", {"name": "end_call", "arguments": {}}])
        path = f"/api/v1/coach/call?kind=call&today={TODAY}&now=10:00&utc_offset=0"
        with mock.patch.object(intake, "stream_tokens", model.stream_tokens), \
                self.client.websocket_connect(path) as socket:
            self.assertFalse(socket.receive_json()["voice"])
            messages = []
            for text in ("Meditate ten minutes on weekdays", "That's all"):
                socket.send_json({"type": "text", "text": text})
                while not messages or messages[-1]["type"] not in {"ended"}:
                    messages.append(socket.receive_json())
                    if messages[-1] == {"type": "state", "state": "listening"} and \
                            any(m["type"] == "heard" and m["text"] == text for m in messages):
                        break
            while messages[-1]["type"] != "ended":
                messages.append(socket.receive_json())

        actions = [m for m in messages if m["type"] == "action"]
        self.assertEqual(actions[0]["values"]["title"], "Meditate 10 minutes")
        latest = self.client.get("/api/v1/coach", params={"today": TODAY, "now": "10:30"}).json()
        self.assertTrue(latest["latest"]["ended_at"])
        self.assertIn("Goodbye", latest["latest"]["turns"][-1]["text"])
        self.assertEqual(len(model.sent), 1)
        self.assertEqual(self.client.post("/api/v1/coach/morning",
                                          json={"day": TODAY, "answer": "declined"}).status_code, 200)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(Exception):
            with self.client.websocket_connect("/api/v1/coach/call?kind=sales&today=x") as socket:
                socket.receive_json()


if __name__ == "__main__":
    unittest.main()
