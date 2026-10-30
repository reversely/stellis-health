"""Tests that notebook chat reads only checked sources and cites them."""

import datetime
import json
import os
import tempfile
import unittest
import uuid
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import documents
from stellis import notebook_chat
from stellis import notebook_sources
from stellis import notebooks
from stellis import pages
import sample_records
from stellis import server
from stellis import storage

STAMP = datetime.datetime(2026, 10, 21, 12, 0,
                          tzinfo=datetime.timezone.utc).isoformat()
QUESTION = "What did the doctor say about the lisinopril dose?"


def add_conversation(notebook_id: str, title: str,
                     segments: list[tuple[int, str]]) -> str:
    """Stores a conversation whose transcript has the given segments."""
    conversation_id = str(uuid.uuid4())
    storage.add_conversation({
        "id": conversation_id,
        "title": title,
        "source_sha256": conversation_id,
        "audio_filename": "a.mp3",
        "audio_media_type": "audio/mpeg",
        "duration_ms": 600000,
        "created_at": STAMP,
        "notebook_id": notebook_id,
    })
    storage.save_transcript({
        "conversation_id":
            conversation_id,
        "revision":
            1,
        "language":
            "en",
        "turns_json":
            "[]",
        "updated_at":
            STAMP,
        "segments_json":
            json.dumps([{
                "id": f"seg-{start}",
                "start_ms": start,
                "end_ms": start + 4000,
                "text": text,
                "speaker_cluster_id": "speaker-1",
                "speaker_uncertain": False,
                "words": [],
            } for start, text in segments]),
    })
    return conversation_id


class ChunkTest(unittest.TestCase):

    def test_transcript_chunks_open_at_their_first_segment(self):
        segments = [
            {
                "id": "a",
                "start_ms": 0,
                "text": "x" * 500
            },
            {
                "id": "b",
                "start_ms": 9000,
                "text": "y" * 500
            },
            {
                "id": "c",
                "start_ms": 12000,
                "text": "z"
            },
        ]

        passages = notebook_chat.chunk_transcript("conversation:c1", "c1",
                                                  "Visit", segments)

        self.assertEqual([passage.start_ms for passage in passages], [0, 9000])
        self.assertEqual(passages[1].target, "/conversation/c1?at=b&t=9000")
        self.assertTrue(passages[1].text.endswith(" z"))

    def test_long_page_paragraph_splits_at_sentence_ends(self):
        paragraph = " ".join(["The value was stable."] * 60)

        passages = notebook_chat.chunk_page("document:d1", "d1", "Labs", 2,
                                            "Heading\n" + paragraph)

        self.assertGreater(len(passages), 1)
        self.assertTrue(
            all(
                len(passage.text) <= notebook_chat.CHUNK_CHARS
                for passage in passages))
        self.assertEqual({passage.target for passage in passages},
                         {"/document/d1?page=2"})
        self.assertEqual({passage.page for passage in passages}, {2})


class CiteTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.passages = [
            notebook_chat.Passage("conversation:a", "Visit", "Dose text",
                                  "/conversation/a?at=s&t=0", 0),
            notebook_chat.Passage("document:b",
                                  "Labs",
                                  "LDL 85",
                                  "/document/b?page=1",
                                  page=1),
        ]

    def test_markers_are_renumbered_in_order_of_first_use(self):
        text, citations = notebook_chat.cite(
            "LDL fell. [[source:2]] The dose stays. [[source:1]], "
            "[[source:2]]", self.passages)

        self.assertEqual(text, "LDL fell. [[1]] The dose stays. [[2]] [[1]]")
        self.assertEqual([
            (item["n"], item["source_id"], item["page"]) for item in citations
        ], [(1, "document:b", 1), (2, "conversation:a", None)])

    def test_short_markers_name_the_same_numbered_source(self):
        text, citations = notebook_chat.cite("First. [[2]] Second. [[source:1]]", self.passages)
        self.assertEqual(text, "First. [[1]] Second. [[2]]")
        self.assertEqual([c["source_id"] for c in citations],
                         [self.passages[1].source_id, self.passages[0].source_id])

    def test_links_and_bad_markers_are_refused(self):
        for answer in ("See [this](/conversation/x). [[source:1]]",
                       "See https://example.com", "Answer. [[source:9]]",
                       "Answer. [[source 1]]", "Answer. [[9]]"):
            with self.subTest(answer=answer):
                with self.assertRaises(notebook_chat.ChatError):
                    notebook_chat.cite(answer, self.passages)


class NotebookChatTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = notebooks.create("Heart")["id"]
        self.voice = add_conversation(
            self.book, "Voice note",
            [(0, "My blood pressure this morning was 130 over 85.")])
        self.visit = add_conversation(
            self.book, "Clinic visit",
            [(0, "Welcome back."),
             (42000, "Keep the lisinopril dose at 10 milligrams.")])
        pages.create("Lab report", self.book,
                     "LDL cholesterol 160 milligrams per deciliter")
        other = notebooks.create("Skin")["id"]
        self.elsewhere = pages.create("Skin note", other,
                                      "Apply the cream twice daily")["id"]
        self.checked = [
            "conversation:" + self.voice, "conversation:" + self.visit
        ]
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def ask(self, reply: str, source_ids=None, agent_id=None):
        """Asks through the API with a stubbed model; returns the prompt too."""
        turn = mock.AsyncMock(return_value=(reply, "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(
                f"/api/v1/notebooks/{self.book}/ask",
                json={
                    "question": QUESTION,
                    "source_ids": source_ids or self.checked,
                    "agent_id": agent_id,
                })
        prompt = turn.await_args.args[0] if turn.await_args else None
        return response, prompt

    def chat(self) -> list[dict[str, object]]:
        response = self.client.get(f"/api/v1/notebooks/{self.book}/chat")
        return response.json()["messages"]

    def test_direct_answer_links_its_trace(self):
        response, _ = self.ask("The dose stays at 10 mg. [[source:1]]")
        self.assertEqual(response.status_code, 200, response.text)
        trace_id = response.json()["trace_id"]
        self.assertEqual(self.chat()[-1]["trace_id"], trace_id)
        result = self.client.get("/api/v1/traces/" + trace_id).json()
        self.assertEqual([e["name"] for e in result["trace"]["events"]],
                         ["request_started", "sources_selected", "citations_checked",
                          "answer_saved", "request_completed"])
        self.assertEqual(result["answer"]["kind"], "notebook")
        self.assertEqual(result["answer"]["question"], QUESTION)
        self.assertEqual(result["answer"]["citations"], {"found": 1, "total": 1})
        self.assertEqual(result["answer"]["sources"][0]["rank"], 1)

    def test_an_unchecked_answer_is_retried_once_with_the_source_range(self):
        turn = mock.AsyncMock(side_effect=[("See https://example.org [[source:9]]", "s"),
                                           ("The dose stays at 10 mg. [[source:1]]", "s")])
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                        json={"question": QUESTION, "source_ids": self.checked})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(turn.await_count, 2)
        self.assertIn("include no links", turn.await_args_list[1].args[0])
        self.assertEqual(self.chat()[-1]["content"], "The dose stays at 10 mg. [[1]]")

    def test_a_change_question_gets_every_dated_value_and_a_retry_names_skipped_ones(self):
        import pymupdf
        from stellis import health_facts

        def report(value):
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40, 60), f"Ferritin {value} ug/L")
                return pdf.tobytes()

        dated = [("2027-01-21", "11"), ("2029-03-02", "9"), ("2031-12-11", "48")]
        records = [documents.create(f"{day}.pdf", report(value), self.book, day)[0] for day, value in dated]
        accepted = [{"document_id": record["id"], "name": "Ferritin", "value": value, "unit": "ug/L", "date": day}
                    for record, (day, value) in zip(records, dated)]
        sources = ["document:" + record["id"] for record in records]
        turn = mock.AsyncMock(side_effect=[("It rose from 11 [[source:1]] to 48. [[source:3]]", "s"),
                                           ("11 [[source:1]], then 9 [[source:2]], then 48. [[source:3]]", "s")])
        with mock.patch.object(health_facts, "accepted", return_value=accepted), \
                mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                        json={"question": "How has my Ferritin changed since 2027?", "source_ids": sources})
        self.assertEqual(response.status_code, 200, response.text)
        first = json.loads(turn.await_args_list[0].args[0].split("\n\n", 1)[1])
        self.assertEqual([(item["value"], item["date"]) for item in first["result_series"]],
                         [("11", "2027-01-21"), ("9", "2029-03-02"), ("48", "2031-12-11")])
        self.assertEqual(turn.await_count, 2)
        self.assertIn("missing: 9 ug/L on 2029-03-02", turn.await_args_list[1].args[0])
        self.assertEqual(len(self.chat()[-1]["citations"]), 3)

    def test_a_repeat_of_the_same_value_within_a_month_is_not_a_new_point(self):
        from stellis import health_facts
        passages = [notebook_chat.Passage(f"document:{name}", name, "Ferritin", f"/document/{name}") for name in "abc"]
        accepted = [{"document_id": "a", "name": "Ferritin", "value": "11", "unit": "ug/L", "date": "2027-01-21"},
                    {"document_id": "b", "name": "Ferritin", "value": "11", "unit": "ug/L", "date": "2027-02-02"},
                    {"document_id": "c", "name": "Ferritin", "value": "34", "unit": "ug/L", "date": "2027-05-20"}]
        with mock.patch.object(health_facts, "accepted", return_value=accepted):
            series = notebook_chat.result_series("How has my ferritin changed?", passages)
        self.assertEqual([(item["value"], item["source"]) for item in series], [("11", 1), ("34", 3)])

    def test_a_models_own_citation_marks_are_removed(self):
        passages = [notebook_chat.Passage("document:a", "Lab", "Ferritin 48", "/document/a")]
        self.assertEqual(notebook_chat.cite("Ferritin is 48【1†L12-L14】. [[source:1]]", passages)[0], "Ferritin is 48. [[1]]")

    def test_only_a_question_that_searches_public_sources_uses_the_research_model(self):
        for research, role in ((False, "answers"), (True, "research")):
            turn = mock.AsyncMock(return_value=("The dose stays at 10 mg. [[source:1]]", "s"))
            found = mock.Mock(results=[], summary=lambda: None)
            with mock.patch.object(agent_gateway, "turn", turn), \
                    mock.patch("stellis.research.research", mock.AsyncMock(return_value=found)):
                response = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                            json={"question": QUESTION, "source_ids": self.checked, "research": research})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(turn.await_args.kwargs["model_role"], role)

    def test_a_question_not_about_change_gets_no_series(self):
        passages = [notebook_chat.Passage("document:d", "Lab", "Ferritin 11 ug/L", "/document/d")]
        self.assertEqual(notebook_chat.result_series("What is ferritin?", passages), [])

    def test_a_second_unchecked_answer_still_fails(self):
        turn = mock.AsyncMock(return_value=("See https://example.org", "s"))
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                        json={"question": QUESTION, "source_ids": self.checked})
        self.assertEqual(turn.await_count, 2)
        self.assertNotEqual(response.status_code, 200)
        self.assertIn("could not be checked", response.text)

    def test_a_search_that_finds_nothing_answers_without_a_model(self):
        empty = notebooks.create("Empty")["id"]
        turn = mock.AsyncMock()
        found = mock.Mock(results=[], summary=lambda: None)
        with mock.patch.object(agent_gateway, "turn", turn), \
                mock.patch("stellis.research.research", mock.AsyncMock(return_value=found)):
            response = self.client.post(f"/api/v1/notebooks/{empty}/ask",
                                        json={"question": "When is my next appointment?", "source_ids": [], "research": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["content"], notebook_chat.NOTHING_FOUND)
        turn.assert_not_awaited()

    def test_a_question_only_public_references_answer_gets_the_general_brief(self):
        public = [notebook_chat.Passage("public:1", "Cancer Alternative Therapies", "Safety is not known.",
                                        "https://medlineplus.gov/x", provider="MedlinePlus")]
        record = [notebook_chat.Passage("document:a", "Lab", "Ferritin 48", "/document/a")]
        self.assertTrue(notebook_chat.build_prompt("Which clinics treat cancer?", public, []).startswith(
            notebook_chat._GENERAL_INSTRUCTIONS))
        self.assertTrue(notebook_chat.build_prompt("Is my ferritin low?", record + public, []).startswith(
            notebook_chat._INSTRUCTIONS))

    def test_a_reply_naming_sources_by_number_is_asked_again(self):
        turn = mock.AsyncMock(side_effect=[("Source 1 discusses depression. [[source:1]]", "s"),
                                           ("The dose stays at 10 mg. [[source:1]]", "s")])
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                        json={"question": QUESTION, "source_ids": self.checked})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(turn.await_count, 2)
        self.assertIn("talked about the sources", turn.await_args.args[0])
        self.assertNotIn("Source 1", response.json()["content"])

    def test_unchecked_and_other_notebook_sources_stay_out(self):
        source_ids = [*self.checked, "page:" + self.elsewhere]
        for limit in (notebook_chat.FULL_TEXT_CHARS, 10):
            with self.subTest(full_text_limit=limit):
                with mock.patch.object(notebook_chat, "FULL_TEXT_CHARS", limit):
                    response, prompt = self.ask(
                        "The dose stays at 10 mg. [[source:1]]", source_ids)
                    passages = notebook_chat.retrieve(self.book, source_ids,
                                                      "lisinopril dose")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("lisinopril", prompt)
                self.assertNotIn("LDL", prompt)
                self.assertNotIn("cream", prompt)
                self.assertLessEqual(
                    {passage.source_id for passage in passages},
                    set(self.checked))

    def test_citation_opens_the_transcript_at_the_chunk_start(self):
        passages = notebook_chat.retrieve(self.book, self.checked, "dose")
        dose = next(index for index, passage in enumerate(passages, 1)
                    if "lisinopril" in passage.text)

        response, _ = self.ask(f"The dose stays at 10 mg. [[source:{dose}]]")

        citation = response.json()["citations"][0]
        self.assertEqual(citation["source_id"], "conversation:" + self.visit)
        self.assertEqual(citation["target"],
                         f"/conversation/{self.visit}?at=seg-0&t=0")
        self.assertIn("lisinopril", citation["excerpt"])

    def test_ranking_keeps_the_matching_chunk_and_its_time(self):
        filler = "Routine small talk about the weather. " * 30
        visit = add_conversation(
            self.book, "Long visit",
            [(0, filler), (90000, filler),
             (180000, "The cardiologist ordered an echocardiogram.")])

        with mock.patch.object(notebook_chat, "FULL_TEXT_CHARS", 500):
            passages = notebook_chat.retrieve(self.book,
                                              ["conversation:" + visit],
                                              "When is the echocardiogram?")

        self.assertEqual(passages[0].start_ms, 180000)
        self.assertTrue(passages[0].target.endswith("&t=180000"))

    def test_messages_are_stored_per_notebook(self):
        self.ask("The dose stays at 10 mg. [[source:1]]")

        stored = self.chat()
        missing = self.client.get("/api/v1/notebooks/missing/chat")

        self.assertEqual([item["role"] for item in stored],
                         ["user", "assistant"])
        self.assertEqual(stored[1]["citations"][0]["n"], 1)
        self.assertEqual(missing.status_code, 404)

    def test_failures_store_nothing(self):
        bad_marker, _ = self.ask("Answer. [[source:9]]")
        no_text, prompt = self.ask("unused", ["page:" + self.elsewhere])
        failing_turn = mock.AsyncMock(
            side_effect=agent_gateway.GatewayError("Allowance used"))
        with mock.patch.object(agent_gateway, "turn", failing_turn):
            gateway = self.client.post(f"/api/v1/notebooks/{self.book}/ask",
                                       json={
                                           "question": "Dose?",
                                           "source_ids": self.checked
                                       })

        self.assertEqual(bad_marker.status_code, 503)
        self.assertEqual((no_text.status_code, prompt), (400, None))
        self.assertEqual((gateway.status_code, gateway.json()["detail"]),
                         (503, "Allowance used"))
        self.assertEqual(self.chat(), [])

    def test_agent_reads_only_checked_passages_and_joins_history(self):
        from stellis import agent_runtime
        from stellis import agents
        passages = notebook_chat.retrieve(self.book, self.checked, "dose")
        target = next(passage.target
                      for passage in passages
                      if "lisinopril" in passage.text)
        reply = f"The dose stays at 10 mg. [Clinic visit]({target})"
        runtime = mock.AsyncMock(
            return_value={"agent_message": {
                "content": reply
            }})
        agent = {"id": "agent-1", "name": "Heart helper"}

        with mock.patch.object(agents, "require_enabled", return_value=agent):
            with mock.patch.object(agent_runtime, "ask", runtime):
                response, _ = self.ask("unused", agent_id="agent-1")

        body = response.json()
        self.assertEqual((body["author"], body["content"]),
                         ("Heart helper", "The dose stays at 10 mg. [[1]]"))
        self.assertEqual(body["citations"][0]["target"], target)
        supplied = runtime.await_args.args[3]
        self.assertTrue(runtime.await_args.kwargs["only_supplied_sources"])
        self.assertEqual({item["source_id"] for item in supplied},
                         set(self.checked))
        self.assertEqual(self.chat()[1]["agent_id"], "agent-1")

    def test_issue_42_done_when_with_a_lab_pdf_unchecked(self):
        folder = sample_records.FOLDER
        lab, _ = documents.create(
            "lab.pdf", (folder / "2026-04-02-lab-results.pdf").read_bytes(),
            self.book)
        photo, _ = documents.create(
            "photo.png",
            (folder / "2025-03-23-lab-results-photo.png").read_bytes(),
            self.book)
        lab_id, photo_id = "document:" + lab["id"], "document:" + photo["id"]

        listed = {
            item["id"]: item
            for item in notebook_sources.list_sources(self.book)
        }
        with_lab = notebook_chat.retrieve(self.book, [*self.checked, lab_id],
                                          "creatinine")
        response, prompt = self.ask("The dose stays at 10 mg. [[source:1]]")

        self.assertEqual((listed[lab_id]["type"], listed[lab_id]["selected"]),
                         ("pdf", True))
        self.assertEqual(
            (listed[photo_id]["status"], listed[photo_id]["selected"]),
            ("needs_text", False))
        page = next(
            passage for passage in with_lab if passage.source_id == lab_id)
        self.assertEqual((page.page, page.target),
                         (1, f"/document/{lab['id']}?page=1"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Creatinine", prompt)
        self.assertLessEqual(
            {item["source_id"] for item in response.json()["citations"]},
            set(self.checked))



class UnreadNotebookTest(unittest.TestCase):
    """Related sources in other notebooks are named, can be included, and citations are checked."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.blood = notebooks.create("Blood Health")["id"]
        self.pregnancy = notebooks.create("Pregnancy")["id"]
        self.mine = pages.create("Iron note", self.blood, "Ferritin was 13 ug/L in June.")["id"]
        self.theirs = pages.create("Pregnancy bloods", self.pregnancy, "Ferritin was 9 ug/L at 12 weeks.")["id"]
        pages.create("Scan note", self.pregnancy, "The scan was normal.")
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def ask(self, reply, **extra):
        turn = mock.AsyncMock(return_value=(reply, "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post(f"/api/v1/notebooks/{self.blood}/ask", json={
                "question": "How has my ferritin changed?", "source_ids": ["page:" + self.mine], **extra})
        return response, turn.await_args.args[0]

    def test_unread_notebook_is_named_with_its_matching_sources(self):
        response, _ = self.ask("Ferritin was 13 ug/L. [[source:1]]")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["unread"], [{"notebook_id": self.pregnancy, "name": "Pregnancy",
                                                      "source_ids": ["page:" + self.theirs]}])
        self.assertEqual(response.json()["citations"][0]["found"], True)
        self.assertEqual(self.chat_unread(), response.json()["unread"])

    def chat_unread(self):
        return self.client.get(f"/api/v1/notebooks/{self.blood}/chat").json()["messages"][-1]["unread"]

    def test_including_a_notebook_reads_it_for_one_question(self):
        response, prompt = self.ask("Ferritin was 9 at 12 weeks. [[source:2]]",
                                    include=[{"notebook_id": self.pregnancy, "source_ids": ["page:" + self.theirs]}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Ferritin was 9 ug/L at 12 weeks.", prompt)
        self.assertEqual(response.json()["unread"], [])
        self.assertEqual(response.json()["citations"][0]["title"], "Pregnancy bloods")

    def test_a_question_without_distinctive_words_finds_nothing_elsewhere(self):
        self.assertEqual(notebook_chat.unread(self.blood, "How was it?"), [])

    def test_citations_not_on_their_source_are_marked(self):
        citations = [{"source_id": "page:x", "excerpt": "Ferritin was 13"}, {"source_id": "page:x", "excerpt": "Gone"}]
        marked = notebook_chat.mark_found(citations, {"page:x": "Note: ferritin   was 13 ug/L"})
        self.assertEqual([c["found"] for c in marked], [True, False])


if __name__ == "__main__":
    unittest.main()
