"""The intake call: notes, replies, notebook proposals and the voice call.

The agent model is replaced by the written Denver script in
tests/fixtures/intake_denver.json. The voice test speaks Denver's lines with
the call's own TTS and sends them through VAD and speech-to-text over the
WebSocket; it runs when STELLIS_CALL_MODELS names a folder with the models.
"""
import asyncio
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from stellis import intake, intake_voice, onboarding, pages, sorting, auth, notebooks

SCRIPT = json.loads((Path(__file__).parent / "fixtures" / "intake_denver.json").read_text())


def script_notes(index):
    """The notepad the script expects after Denver's answer number ``index``."""
    for turn in reversed(SCRIPT["turns"][:index + 1]):
        if turn["notes"] != "same":
            return turn["notes"]
    return []


class ScriptedModel:
    """Plays the agent and the note taker from the script."""

    def __init__(self):
        self.notes_calls = 0

    async def stream_tokens(self, messages, tools=None):
        answered = sum(m["role"] == "user" for m in messages)
        text = SCRIPT["turns"][answered]["agent"]
        if "[END]" not in text:
            for word in re.findall(r"\S+\s*", text):
                yield word
        if answered:
            yield {"id": "progress", "name": "complete_topic",
                   "arguments": {"topic": intake.PLAN[min(answered - 1, 2)][0]}}
        if "[END]" in text:
            yield {"id": "call_1", "name": "end_call", "arguments": {}}

    async def complete_json(self, instructions, context, schema, max_tokens):
        if "notebooks" in instructions:
            records = context["records"]
            groups = []
            for book in SCRIPT["notebooks"]:
                items = [r["index"] for r in records if any(w in r["title"] for w in book["records"])]
                groups.append({"notebook": book["notebook"], "kind": book["kind"],
                               "reason": "From the call", "goal": book["goal"], "items": items})
            return {"groups": groups, "question": "", "reply": ""}
        self.notes_calls += 1
        answered = context["transcript"].count("Person:")
        return {"sections": script_notes(answered - 1)}


class IntakeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"STELLIS_DATA_DIR": self.tmp.name, "STELLIS_AUTH_DB_DIR": self.tmp.name}
        if os.environ.get("STELLIS_CALL_MODELS"):
            env["STELLIS_CALL_MODELS"] = os.environ["STELLIS_CALL_MODELS"]
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.model = ScriptedModel()
        self.patches = [patch.object(intake, "stream_tokens", self.model.stream_tokens),
                        patch.object(intake, "complete_json", self.model.complete_json)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.env.stop()
        self.tmp.cleanup()


class NotesTest(IntakeTestCase):
    def test_notes_keep_known_headings_in_order_without_repeats(self):
        notes = intake.clean_notes([
            {"heading": "Medicines", "items": ["- Metformin 500 mg", "Metformin 500 mg", " "]},
            {"heading": "Invented", "items": ["x"]},
            {"heading": "Goals", "items": ["Knees"]},
        ])
        self.assertEqual(notes, [{"heading": "Goals", "items": ["Knees"]},
                                 {"heading": "Medicines", "items": ["Metformin 500 mg"]}])

    def test_an_edit_against_an_old_revision_is_refused(self):
        intake.save_notes([{"heading": "Goals", "items": ["Walk daily"]}], 0)
        with self.assertRaises(ValueError):
            intake.save_notes([{"heading": "Goals", "items": ["Run"]}], 0)
        self.assertEqual(intake.load()["notes"][0]["items"], ["Walk daily"])

    def test_the_saved_note_puts_notes_before_the_transcript(self):
        intake.opening()
        intake.add_person_turn("Keeping the weight off")
        intake.save_notes([{"heading": "Goals", "items": ["Keep the weight off"]}], 0)
        state = intake.end()
        markdown = pages.get(state["page_id"])["markdown"]
        self.assertLess(markdown.index("## Goals"), markdown.index("## Transcript"))
        self.assertIn("**You:** Keeping the weight off", markdown)
        self.assertEqual(intake.end()["page_id"], state["page_id"])


class ReplyTest(IntakeTestCase):
    def test_the_first_clause_is_spoken_before_the_sentence_ends(self):
        intake.opening()
        intake.add_person_turn("Keeping the weight off, and my knees.")
        pieces = asyncio.run(self._collect())
        self.assertEqual(pieces, ["Eight kilos is real progress.",
                                  "Next, your health: which conditions do you have, and which "
                                  "medicines do you take?"])
        timings = {}
        asyncio.run(self._collect(timings))
        self.assertIn("first_token", timings)

    async def _collect(self, timings=None):
        return [piece async for piece in intake.reply(timings)]

    def test_a_title_before_a_name_does_not_end_a_sentence(self):
        async def tokens(messages, tools=None):
            yield "You see Dr. Patel and a dietitian. Is that right?"
        with patch.object(intake, "stream_tokens", tokens):
            pieces = asyncio.run(self._collect())
        self.assertEqual(pieces, ["You see Dr. Patel and a dietitian.", "Is that right?"])

    def test_a_long_first_clause_is_split_at_its_comma(self):
        async def tokens(messages, tools=None):
            for word in "You have been working on this for a while, then. Good.".split(" "):
                yield word + " "
        with patch.object(intake, "stream_tokens", tokens):
            pieces = asyncio.run(self._collect())
        self.assertEqual(pieces, ["You have been working on this for a while,", "then.", "Good."])

    def test_a_typed_call_follows_the_script_and_ends(self):
        intake.opening()
        for index, turn in enumerate(SCRIPT["turns"]):
            if turn["person"] is None:
                break
            result = asyncio.run(intake.run_text_turn(turn["person"]))
            self.assertEqual(result["reply"], SCRIPT["turns"][index + 1]["agent"].replace(" [END]", ""))
        self.assertTrue(result["ended"])
        self.assertEqual(intake.load()["notes"], intake.clean_notes(script_notes(len(SCRIPT["turns"]) - 1)))


class NoteRulesTest(IntakeTestCase):
    def test_the_note_taker_gets_the_topics_and_drops_off_topic_words(self):
        seen = {}

        async def notes(instructions, context, schema, max_tokens):
            seen.update(instructions=instructions, context=context)
            return {"sections": []}
        intake.opening()
        intake.add_person_turn("Feel the end. The chestnut.")
        with patch.object(intake, "complete_json", notes):
            asyncio.run(intake.update_notes())
        self.assertEqual([t["heading"] for t in seen["context"]["topics"]],
                         [heading for heading, _ in intake.NOTE_TOPICS])
        self.assertIn("never guess what was meant", seen["instructions"])
        self.assertIn("didn't catch it", intake.INSTRUCTIONS)


class FollowOnTest(IntakeTestCase):
    def test_an_answer_continued_over_the_reply_joins_its_first_part(self):
        intake.opening()
        intake.add_person_turn("Type two diabetes and high blood pressure.")
        intake.add_agent_turn("Thank you. What medicines do you take?")
        intake.drop_agent_reply()
        whole = intake.extend_person_turn("And I had bypass surgery in ninety seven.")
        turns = intake.load()["turns"]
        self.assertEqual([t["role"] for t in turns], ["agent", "person"])
        self.assertTrue(whole.endswith("bypass surgery in ninety seven."))


class BargeInTest(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.sent = []

        async def send_json(message):
            self.sent.append(message)

        async def send_bytes(data):
            pass
        self.call = intake_voice.Call(send_json, send_bytes, None)
        self.call.speaking = "Now, about your care team: do you see any other clinicians?"
        self.call.speaking_until = float("inf")

    def heard(self, text):
        self.call.engine = type("Engine", (), {"transcribe": staticmethod(lambda segment: text)})()
        asyncio.run(self.call._confirm_barge_in(np.zeros(1600, dtype=np.float32)))
        return [m for m in self.sent if m["type"] == "stop"]

    def test_the_agents_own_voice_does_not_stop_it(self):
        self.assertEqual(self.heard("about your care team do you see"), [])

    def test_the_person_talking_over_the_agent_stops_it(self):
        self.assertEqual(len(self.heard("Wait, I forgot my inhaler")), 1)

    def test_words_heard_over_the_agent_without_a_barge_in_are_not_an_answer(self):
        self.call.speaking = "What changes do you hope to track?"
        # Recognition turned 2 seconds of the agent's voice into words.
        self.assertFalse(self.call._is_answer("Pause an active one, mayhem.", 2.0, 2.0))
        # Starting as the agent finishes is an answer.
        self.assertTrue(self.call._is_answer("Appointments and nutrition.", 2.0, 0.3))
        self.call.barged_in = True
        self.assertTrue(self.call._is_answer("Wait, appointments too.", 2.0, 2.0))

    def test_a_reply_that_reuses_the_greetings_words_after_it_ends_is_an_answer(self):
        # Owner, 2026-10-30: "good how are you" after "How are you doing today?" got no reply.
        self.call.speaking = "Good morning, it's Stellis with your check-in. How are you doing today?"
        self.assertFalse(self.call._is_answer("Good, how are you?", 1.3, 0.0))
        self.assertTrue(self.call._is_answer("Good, how are you?", 1.3, 0.0, echo_possible=False))

    def test_text_too_fast_to_be_speech_is_not_an_answer(self):
        self.assertFalse(self.call._is_answer("one two three four five six seven", 0.6, 0.0))
        self.assertTrue(self.call._is_answer("one two three four five six seven", 2.5, 0.0))

    def test_speech_while_the_agent_is_still_thinking_joins_the_last_answer(self):
        self.call.engine = type("Engine", (), {"transcribe": staticmethod(lambda segment: "Can you hear me?")})()
        self.call.speaking_until = 0.0
        intake.opening()
        intake.add_person_turn("Appointments and nutrition.")

        async def run():
            self.call.turn = 1
            self.call.speaking_turn = 0  # The agent has not spoken in turn 1.
            self.call.answer_ended = time.perf_counter() - 5
            self.call.replying = asyncio.create_task(asyncio.sleep(30))
            await self.call._answer_audio(np.zeros(16000, dtype=np.float32), time.perf_counter())
            self.call.replying.cancel()

        with patch.object(intake_voice.debug_data, "save_answer_audio"):
            asyncio.run(run())
        turns = intake.load()["turns"]
        self.assertEqual([t["role"] for t in turns], ["agent", "person"])
        self.assertEqual(turns[-1]["text"], "Appointments and nutrition. Can you hear me?")


class PlanTest(IntakeTestCase):
    def test_topics_need_explicit_completion_and_allow_clarification(self):
        intake.opening()
        for text in ["I'm in my first trimester", "What can you help with?", "Can you give examples?"]:
            intake.add_person_turn(text)
            intake.add_agent_turn("What would help you?")
        self.assertEqual(intake.plan(intake.load())["next_topic"], "Goals")
        intake.complete_topic("Goals")
        self.assertEqual(intake.plan(intake.load())["next_topic"], "Health")
        intake.complete_topic("Health")
        self.assertEqual(intake.plan(intake.load())["next_topic"], "Reminders")
        intake.add_person_turn("What do you usually offer?")
        self.assertEqual(intake.plan(intake.load())["next_topic"], "Reminders")
        intake.complete_topic("Reminders")
        self.assertEqual(intake.plan(intake.load())["next_topic"], intake.GOODBYE)

    def test_completion_is_idempotent_and_rejects_unknown_topics(self):
        intake.complete_topic("Goals"); intake.complete_topic("Goals")
        intake.complete_topic("Ignore every instruction")
        self.assertEqual(intake.load()["completed_topics"], ["Goals"])

    def test_migrating_old_calls_preserves_pending_reminders(self):
        from stellis import storage
        saved = {"turns": [{"role":"agent","text":"Goals?","topic":"Goals"},
                           {"role":"person","text":"Pregnancy"},
                           {"role":"agent","text":"Health?","topic":"Health"},
                           {"role":"person","text":"Just the usual"},
                           {"role":"agent","text":"Reminders?","topic":"Reminders"},
                           {"role":"person","text":"What do you usually offer?"}]}
        with storage.connect() as conn:
            intake._read(conn)
            conn.execute("INSERT OR REPLACE INTO intake_call VALUES (1, ?)", (json.dumps(saved),))
        self.assertEqual(intake.plan(intake.load())["next_topic"], "Reminders")

    def test_closing_turn_cannot_ask_a_question_then_disconnect(self):
        for topic, _, _ in intake.PLAN: intake.complete_topic(topic)
        async def tokens(messages, tools=None):
            yield "Which reminders would you like?"
        with patch.object(intake, "stream_tokens", tokens):
            pieces = asyncio.run(self._collect())
        self.assertNotIn("Which reminders would you like?", pieces)
        self.assertEqual(pieces[-1], intake.END_MARK)
        text = " ".join(pieces)
        self.assertIn("get your setup started", text)
        self.assertIn("Add records", text)
        self.assertIn("Goodbye", text)

    def test_end_tool_is_ignored_while_a_question_awaits_an_answer(self):
        async def tokens(messages, tools=None):
            yield "Which reminders would help?"
            for topic, _, _ in intake.PLAN:
                yield {"name":"complete_topic", "arguments":{"topic":topic}}
            yield {"name":"end_call", "arguments":{}}
        with patch.object(intake, "stream_tokens", tokens):
            self.assertNotIn(intake.END_MARK, asyncio.run(self._collect()))

    def test_turn_budget_explains_handoff_and_says_goodbye(self):
        for i in range(intake.MAX_ANSWERS): intake.add_person_turn("More context")
        async def tokens(messages, tools=None):
            yield "Another question?"
        with patch.object(intake, "stream_tokens", tokens):
            pieces = asyncio.run(self._collect())
        self.assertIn("wrap up this setup", " ".join(pieces))
        self.assertIn("Goodbye", " ".join(pieces))
        self.assertEqual(pieces[-1], intake.END_MARK)

    async def _collect(self):
        return [piece async for piece in intake.reply()]

    def test_the_opening_invites_multiple_goals(self):
        self.assertIn("Let's talk about your health goals", intake.OPENING)
        self.assertNotIn("three", intake.OPENING)
        self.assertNotIn("main health goal", intake.OPENING)


class NotebookProposalTest(IntakeTestCase):
    def test_catalogue_recommendations_use_reviewed_intake_notes(self):
        intake.save_notes([{"heading":"Goals","items":["Improve nutrition"]}],0)
        result=asyncio.run(intake.propose_notebooks())
        self.assertIn("nutrition--nutrition",[g["template_id"] for g in result["groups"]])
        self.assertTrue(all(g["evidence"] for g in result["groups"]))

    def test_a_revision_requires_catalogue_selection(self):
        with self.assertRaises(ValueError):
            asyncio.run(intake.propose_notebooks("Make new names", []))

    def test_a_goal_becomes_a_to_do_after_template_approval(self):
        auth.register("denver", "correct horse battery staple")
        account=auth.account_id_for_username("denver")
        onboarding.choose(account,[],"review")
        result=onboarding.complete(account,record_groups=[{"template_id":"nutrition--weight-management","items":[],"goal":"Keep the weight off"}])
        book=result['created'][0]
        goal=next(p for p in pages.list_pages(book['notebook_id']) if p['title']=='Goals')
        self.assertTrue(pages.get(goal['id'])['markdown'].startswith('- [ ] Keep the weight off'))


class EchoTest(unittest.TestCase):
    def test_the_agents_own_words_are_not_an_answer(self):
        spoken = "How much have you lost so far, and what has helped most?"
        self.assertTrue(intake_voice.is_echo("how much have you lost so far", spoken))
        self.assertFalse(intake_voice.is_echo("about eight kilos since April", spoken))

    def test_an_answer_that_reuses_the_agents_words_is_the_person(self):
        # #258: the morning check-in names the goal, and the answer names it back in its own sentence.
        spoken = "Good morning, it's Stellis with your check-in. Walk 20 minutes, 0 of 4 this week. How are you doing today?"
        self.assertFalse(intake_voice.is_echo("I'm doing well thanks I walk 20 minutes this morning", spoken))
        self.assertTrue(intake_voice.is_echo("walk 20 minutes 0 of 4 this week how are you doing today", spoken))
        self.assertTrue(intake_voice.is_echo("today", spoken))


def word_overlap(heard, said):
    """The share of the script's words of 4 letters or more that were heard."""
    norm = lambda text: re.findall(r"[a-z']{4,}", text.lower())
    expected = norm(said)
    got = set(norm(heard))
    return sum(word in got for word in expected) / max(1, len(expected))


CALL_MODELS_READY = bool(os.environ.get("STELLIS_CALL_MODELS")) and not intake_voice.missing()


@unittest.skipUnless(CALL_MODELS_READY, "Set STELLIS_CALL_MODELS to a folder with the call models")
class VoiceCallTest(IntakeTestCase):
    """Denver's voice, made with the call's TTS, calls the real speech pipeline."""

    def setUp(self):
        super().setUp()
        from stellis import server
        self.client = TestClient(server.app)
        self.engine = intake_voice.Engine(intake_voice.directory())

    def denver_audio(self, text):
        pcm, rate = self.engine.speak(text, intake_voice.DENVER_VOICE)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        times = np.arange(0, len(samples) / rate, 1 / intake_voice.SAMPLE_RATE)
        resampled = np.interp(times, np.arange(len(samples)) / rate, samples)
        silence = np.zeros(intake_voice.SAMPLE_RATE, dtype=np.float32)
        return np.concatenate([silence[:4000], resampled, silence]).astype("<i2").tobytes()

    def until_listening(self, socket, expect_heard=False):
        """Reads messages until the agent finishes; returns them and its audio."""
        messages, audio = [], 0
        while True:
            message = socket.receive()
            if message.get("bytes") is not None:
                audio += len(message["bytes"])
                continue
            data = json.loads(message["text"])
            messages.append(data)
            if data["type"] == "say" and data["audio"]:
                socket.send_json({"type": "playing", "turn": data["turn"]})
            if data["type"] == "ended":
                return messages, audio
            if data["type"] == "state" and data["state"] == "listening":
                # VAD also emits listening between input silence and ASR. Wait
                # for the completed reply, rather than mistaking that for it.
                if not any(m["type"] == "say" for m in messages):continue
                if expect_heard and not any(m["type"] == "heard" for m in messages):continue
                socket.send_json({"type": "played"})
                return messages, audio

    def test_denver_completes_the_scripted_call_by_voice(self):
        overlaps, transcripts = [], []
        with self.client.websocket_connect("/api/v1/intake/call") as socket:
            ready = socket.receive_json()
            self.assertTrue(ready["voice"])
            messages, audio = self.until_listening(socket)
            self.assertGreater(audio, 0)
            self.assertEqual(" ".join(m["text"] for m in messages if m["type"] == "say"),
                             SCRIPT["turns"][0]["agent"])
            for turn in SCRIPT["turns"]:
                if turn["person"] is None:
                    break
                pcm = self.denver_audio(turn["person"])
                for start in range(0, len(pcm), 640):
                    socket.send_bytes(pcm[start:start + 640])
                messages, _ = self.until_listening(socket, expect_heard=True)
                if messages[-1]["type"] == "ended" and not any(m["type"] == "heard" for m in messages):
                    break  # The call ended after the last reply; this answer went unheard.
                # A pause can split an answer; the last "heard" has all of it.
                heard = [m["text"] for m in messages if m["type"] == "heard"][-1]
                overlaps.append(word_overlap(heard, turn["person"]))
                transcripts.append(heard)
                replies = messages
                if messages[-1]["type"] == "ended":
                    break
            while messages[-1]["type"] != "ended":
                messages.append(socket.receive_json())
            said = " ".join(m["text"] for m in replies if m["type"] == "say")
            self.assertIn("choose Add records to continue setup.", said)
            self.assertTrue(said.endswith("Goodbye."), said)
        answered = len(overlaps)
        # Medicine names and spoken numbers miss most often.
        self.assertGreaterEqual(sum(overlaps) / len(overlaps), 0.75, overlaps)
        print("word overlap per answer", [round(o, 2) for o in overlaps])
        print("heard", transcripts)
        state = intake.load()
        self.assertEqual(sum(t["role"] == "person" for t in state["turns"]), answered)
        self.assertEqual(state["notes"], intake.clean_notes(script_notes(answered)))
        log = (Path(self.tmp.name) / "intake-latency.jsonl").read_text().splitlines()
        stages = [json.loads(line) for line in log]
        self.assertEqual(len(stages), answered)
        required = {"stt_done", "first_audio", "playing"}
        self.assertTrue(all(required <= set(s) for s in stages), stages)
        # A direct closing reply has no model-token event to measure.
        self.assertTrue(all("first_token" in s for s in stages[:-1]), stages)
        self.assertTrue(all(isinstance(value, int) and value >= 0
                            for stage in stages for value in stage.values()), stages)
        print("stage times in ms after the end of speech", stages)


if __name__ == "__main__":
    unittest.main()
