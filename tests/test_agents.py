"""Stellis: identity, notebook access, memory, and durable thread checks."""

import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from stellis import agent_runtime, agents, notebooks, pages, storage
from stellis.server import app


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "off"})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_document_evidence_includes_table_beyond_search_preview(self):
        from stellis import documents
        book = notebooks.create("Fertility")
        import io
        from PIL import Image
        image = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(image, format="PNG")
        record, _ = documents.create("embryology.png", image.getvalue(), book["id"])
        text = "Cryopreservation report. " + "Administrative context " * 50 + "\nRetrieved 12; mature 10; fertilized 8; blastocysts frozen 4."
        documents.save_page(record["id"], 1, text, 0)
        agent = agents.get(agents.assistant()["id"])
        found = agent_runtime._sources(agent, "Cryopreservation report counts", book["id"])
        self.assertEqual(len(found), 1)
        self.assertIn("blastocysts frozen 4", found[0]["excerpt"])
        self.assertEqual(found[0]["target"], f"/document/{record['id']}?page=1")

    def test_stellis_is_the_one_assistant_in_every_notebook(self):
        stellis = agents.list_agents()[0]
        self.assertEqual((stellis["name"], stellis["is_default"]), ("Stellis", True))
        self.assertIn("Never diagnose", stellis["instructions"])
        book = notebooks.create("Diabetes and blood sugar")
        stellis = agents.get(stellis["id"])
        self.assertEqual([n["notebook_id"] for n in stellis["notebooks"]], [book["id"]])

    def test_an_earlier_chief_of_staff_becomes_stellis(self):
        default = agents.list_agents()[0]
        agents.update(default["id"], name="Chief of Staff", role="Coordinates your notebooks and agents")
        renamed = agents.get(default["id"])
        self.assertEqual((renamed["name"], renamed["role"]), ("Stellis", agents.DEFAULT_ROLE))

    def test_legacy_agent_column_migrates_and_stellis_is_fallback(self):
        agents.assistant()
        book = notebooks.create("Unassigned")
        with sqlite3.connect(storage.data_dir() / "stellis.sqlite3") as conn:
            conn.execute("ALTER TABLE agents RENAME COLUMN slug TO openclaw_id")
            conn.execute("UPDATE agents SET openclaw_id = 'main' WHERE is_default = 1")
        restored = {item["name"]: item for item in agents.list_agents()}
        self.assertEqual(restored["Stellis"]["slug"], "main")  # Preserve existing identifiers.
        self.assertEqual(agents.resolved_agent(book["id"])["agent"]["id"], restored["Stellis"]["id"])

    def test_agents_from_the_earlier_team_move_into_stellis(self):
        stellis = agents.assistant()
        with storage.connect() as conn:
            conn.execute("""INSERT INTO agents (id, name, name_key, role, instructions, slug, is_default,
                created_at, updated_at) VALUES ('mira', 'Mira', 'mira', 'Coordinator', '', 'mira', 0, 'now', 'now')""")
            conn.execute("""INSERT INTO agent_memories (id, agent_id, kind, fact, created_at, updated_at)
                VALUES ('m1', 'mira', 'general', 'Use WT-04 subjects', 'now', 'now')""")
            conn.execute("""INSERT INTO agent_messages (id, agent_id, role, content, created_at)
                VALUES ('t1', 'mira', 'user', 'Review the plan', 'now')""")
        self.assertEqual([a["name"] for a in agents.list_agents()], ["Stellis"])
        self.assertEqual([m["fact"] for m in agents.memories(stellis["id"])], ["Use WT-04 subjects"])
        self.assertEqual([m["content"] for m in agents.messages(stellis["id"])], ["Review the plan"])

    def test_memory_is_correctable_and_not_a_source_citation(self):
        agent = agents.assistant()
        saved = self.client.post(f"/api/v1/agents/{agent['id']}/memories", json={
            "fact": "RFI subjects begin WT-04", "source_id": "note:one"})
        self.assertEqual(saved.status_code, 200)
        memory = saved.json()
        updated = self.client.put(f"/api/v1/agents/{agent['id']}/memories/{memory['id']}", json={
            "fact": "RFI subjects begin WT-04 RFI", "source_id": "note:one"})
        self.assertEqual(updated.json()["fact"], "RFI subjects begin WT-04 RFI")
        self.assertEqual(self.client.get(f"/api/v1/agents/{agent['id']}/memories").json()[0]["id"], memory["id"])
        self.assertEqual(self.client.delete(f"/api/v1/agents/{agent['id']}/memories/{memory['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/agents/{agent['id']}/memories").json(), [])

    def test_one_agent_thread_holds_messages_from_multiple_notebooks(self):
        first = notebooks.create("First")
        second = notebooks.create("Second")
        agent = agents.assistant()
        agents.append_message(agent["id"], "user", "Review the first project", first["id"])
        agents.append_message(agent["id"], "agent", "First project reviewed", first["id"], "job-1")
        agents.append_message(agent["id"], "user", "Review the second project", second["id"])
        rows = self.client.get(f"/api/v1/agents/{agent['id']}/messages").json()
        self.assertEqual([row["notebook_id"] for row in rows], [first["id"], first["id"], second["id"]])
        self.assertEqual(rows[1]["job_id"], "job-1")

    def test_chat_request_runs_selected_agent_and_saves_reply(self):
        book = notebooks.create("Water Treatment")
        other = notebooks.create("Other Project")
        mira = agents.update(agents.assistant()["id"], instructions="Keep the project current")
        agents.save_memory(mira["id"], "Use WT-04 subjects")
        agents.save_memory(mira["id"], "Other project secret", notebook_id=other["id"])
        source = pages.create("Pump plan", book["id"], "Pump calibration target is 17 psi.")
        pages.create("Private plan", other["id"], "Pump calibration target is secret.")
        process = type("Process", (), {"returncode": 0,
                                       "communicate": AsyncMock(return_value=(
                                           b'{"status":"ok","result":{"payloads":[{"text":"Done [[source:1]]"}],"meta":{"agentMeta":{"sessionId":"one"}}}}',
                                           b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-agent"}), \
             patch.object(agent_runtime, "_command", return_value="/opt/nemoclaw/bin/nemoclaw"), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process) as launch:
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask",
                                        json={"question": "What is the pump calibration target?", "notebook_id": book["id"]})
            self.assertEqual(self.client.get("/api/v1/agents/runtime").json(), {"configured": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agent_message"]["content"], f"Done [Pump plan](/page/{source['id']})")
        args = launch.await_args.args
        self.assertEqual(args[:7], ("/opt/nemoclaw/bin/nemoclaw", "stellis-agent", "agent", "--agent", "main",
                                    "--session-id", response.json()["user_message"]["job_id"]))
        self.assertEqual(launch.await_args.kwargs["env"]["PATH"].split(os.pathsep)[0],
                         "/opt/nemoclaw/bin")
        self.assertEqual(args[7], "-m")
        prompt = args[8]
        self.assertIn("Keep the project current", prompt)
        self.assertIn("Use WT-04 subjects", prompt)
        self.assertIn("17 psi", prompt)
        self.assertIn(f"/page/{source['id']}", prompt)
        self.assertNotIn("Other project secret", prompt)
        self.assertNotIn("Private plan", prompt)
        self.assertEqual([(row["role"], row["content"]) for row in agents.messages(mira["id"])],
                         [("user", "What is the pump calibration target?"),
                          ("agent", f"Done [Pump plan](/page/{source['id']})")])
        self.assertEqual(response.json()["user_message"]["job_id"],
                         response.json()["agent_message"]["job_id"])
        self.assertEqual(response.json()["session_id"], "one")
        self.assertEqual(response.json()["cited_sources"][0]["source_id"], source["id"])
        self.assertEqual(self.client.get(f"/api/v1/agents/{mira['id']}/jobs").json()[0]["state"],
                         "succeeded")

    def test_failed_reply_and_restart_keep_job_result_in_thread(self):
        mira = agents.assistant()
        process = type("Process", (), {"returncode": 1,
                                       "communicate": AsyncMock(return_value=(b"", b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-agent"}), \
             patch.object(agent_runtime, "_command", return_value="nemoclaw"), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process):
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask", json={"question": "Any update?"})
        self.assertEqual(response.status_code, 503)
        rows = self.client.get(f"/api/v1/agents/{mira['id']}/messages").json()
        jobs = self.client.get(f"/api/v1/agents/{mira['id']}/jobs").json()
        self.assertEqual([(row["role"], row["content"]) for row in rows], [("user", "Any update?")])
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (rows[0]["job_id"], "failed"))
        agents.start_job(mira["id"])
        agents.interrupt_running_jobs()  # Startup recovery after the web process stops.
        self.assertEqual([job["state"] for job in agents.jobs(mira["id"])],
                         ["failed", "interrupted"])

    def test_general_agent_cites_provisional_source_in_notebook(self):
        book = notebooks.create("Unassigned meeting")
        general = agents.list_agents()[0]
        process = type("Process", (), {"returncode": 0,
                                       "communicate": AsyncMock(return_value=(
                                           b'{"status":"ok","result":{"payloads":[{"text":"Draft sent [[source:1]]"}],"meta":{}}}',
                                           b""))})()
        source = {"source_id": "live-0-500", "title": "Live transcript 0s",
                  "excerpt": "Avery will send the draft.", "target": "/recording?t=500", "start_ms": 500}
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process):
            result = __import__("asyncio").run(agent_runtime.ask(general["id"], "What will Avery send?",
                                                        book["id"], [source]))
        self.assertEqual(result["cited_sources"], [{"source_id": "live-0-500", "target": "/recording?t=500"}])
        self.assertIn("[Live transcript 0s](/recording?t=500)", result["agent_message"]["content"])

    def test_uncited_source_answer_fails_closed(self):
        book = notebooks.create("Water Treatment")
        mira = agents.assistant()
        pages.create("Pump target", book["id"], "The pump calibration target is 17 psi.")
        process = type("Process", (), {"returncode": 0,
                                       "communicate": AsyncMock(return_value=(
                                           b'{"status":"ok","result":{"payloads":[{"text":"The target is 17 psi."}],"meta":{}}}',
                                           b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process):
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask",
                                        json={"question": "What is the pump target?", "notebook_id": book["id"]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(agents.jobs(mira["id"])[0]["state"], "failed")

    def test_uncited_first_reply_is_retried_with_source_marker(self):
        book = notebooks.create("Water Treatment")
        mira = agents.assistant()
        page = pages.create("Pump target", book["id"], "The pump calibration target is 17 psi.")
        def process(answer):
            return type("Process", (), {"returncode": 0,
                                        "communicate": AsyncMock(return_value=(
                                            ('{"status":"ok","result":{"payloads":[{"text":'
                                             + __import__("json").dumps(answer)
                                             + '}],"meta":{}}}').encode(), b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          side_effect=[process("17 psi"), process("17 psi [[source:1]]")]) as launch:
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask",
                                        json={"question": "What is the pump target?", "notebook_id": book["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agent_message"]["content"], f"17 psi [Pump target](/page/{page['id']})")
        self.assertEqual(launch.await_count, 2)
        self.assertEqual(agents.jobs(mira["id"])[0]["state"], "succeeded")

    def test_adjacent_source_markers_are_normalized_and_checked(self):
        sources = [{"title": "First", "target": "/page/one"},
                   {"title": "Second", "target": "/page/two"}]
        answer = agent_runtime._normalize_citations("Facts [[source:1][source:2]]")
        self.assertEqual(agent_runtime._trusted_citations(answer, sources),
                         "Facts [First](/page/one) [Second](/page/two)")
        adjacent = agent_runtime._normalize_citations("Facts [[source:1]][[source:2]]")
        self.assertEqual(agent_runtime._trusted_citations(adjacent, sources),
                         "Facts [First](/page/one) [Second](/page/two)")
        with self.assertRaises(agent_runtime.AgentRunError):
            agent_runtime._trusted_citations("Facts [[source:unknown]]", sources)
        with self.assertRaises(agent_runtime.AgentRunError):
            agent_runtime._trusted_citations("Facts [[source:0]]", sources)

    def test_unavailable_source_marker_does_not_save_agent_reply(self):
        mira = agents.assistant()
        process = type("Process", (), {"returncode": 0,
                                       "communicate": AsyncMock(return_value=(
                                           b'{"status":"ok","result":{"payloads":[{"text":"Claim [[source:1]]"}],"meta":{}}}',
                                           b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
             patch.object(agent_runtime, "_sources", return_value=[]), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          return_value=process):
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask",
                                        json={"question": "What happened?"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(agents.jobs(mira["id"])[0]["state"], "failed")
        self.assertEqual([message["role"] for message in agents.messages(mira["id"])], ["user"])

    def test_unavailable_source_marker_retries_without_citation(self):
        mira = agents.assistant()
        def process(answer):
            return type("Process", (), {"returncode": 0,
                                        "communicate": AsyncMock(return_value=(
                                            ('{"status":"ok","result":{"payloads":[{"text":'
                                             + __import__("json").dumps(answer)
                                             + '}],"meta":{}}}').encode(), b""))})()
        with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
             patch.object(agent_runtime, "_sources", return_value=[]), \
             patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                          side_effect=[process("Hello [[source:1]]"), process("Hello")]) as launch:
            response = self.client.post(f"/api/v1/agents/{mira['id']}/ask",
                                        json={"question": "Say hello"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["agent_message"]["content"], "Hello")
        self.assertEqual(launch.await_count, 2)

    def test_cancellation_terminates_managed_wrapper_and_marks_job_interrupted(self):
        mira = agents.assistant()

        async def check():
            entered = asyncio.Event()

            class HangingProcess:
                returncode = None
                terminated = False

                async def communicate(self):
                    entered.set()
                    await asyncio.Future()

                def terminate(self):
                    self.terminated = True
                    self.returncode = 143

                async def wait(self):
                    return self.returncode

                def kill(self):
                    raise AssertionError("graceful termination should be enough")

            process = HangingProcess()
            with patch.dict(os.environ, {"STELLIS_NEMOCLAW_SANDBOX": "stellis-claw"}), \
                 patch.object(agent_runtime.asyncio, "create_subprocess_exec", new_callable=AsyncMock,
                              return_value=process):
                task = asyncio.create_task(agent_runtime.ask(mira["id"], "Status?"))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(process.terminated)

        asyncio.run(check())
        self.assertEqual(agents.jobs(mira["id"])[0]["state"], "interrupted")


class CitationNormalizationTest(unittest.TestCase):
    def test_model_multi_source_marker(self):
        self.assertEqual(
            agent_runtime._normalize_citations("Unverified. [[source:2], [source:7]]"),
            "Unverified. [[source:2]] [[source:7]]")


if __name__ == "__main__":
    unittest.main()
