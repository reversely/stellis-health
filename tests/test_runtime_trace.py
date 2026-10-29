"""Trace persistence, privacy and routing guards without provider stand-ins."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from stellis import agents, runtime_profile, runtime_settings, runtime_trace, storage


class RuntimeTraceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name,
            "STELLIS_RUNTIME_PROFILE": "local", "STELLIS_ACCOUNT_ID": "1"}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_gateway_creates_timed_trace_and_reuses_existing_request(self):
        from stellis import agent_gateway
        reply = AsyncMock(return_value=("private reply", "session"))
        with patch.object(agent_gateway, "_turn", reply):
            asyncio.run(agent_gateway.turn("private prompt", "session"))
            with runtime_trace.span("agent_chat"):
                asyncio.run(agent_gateway.turn("private prompt", "session"))
        traces = runtime_trace.recent()
        self.assertEqual(len(traces), 2)
        self.assertEqual({trace["kind"] for trace in traces},
                         {"agent_request", "agent_chat"})
        for trace in traces:
            self.assertEqual(trace["state"], "succeeded")
            self.assertIsNotNone(trace["duration_ms"])
            self.assertNotIn("private prompt", json.dumps(trace))
            self.assertNotIn("private reply", json.dumps(trace))

    def test_gateway_failure_still_records_finished_time(self):
        from stellis import agent_gateway
        with patch.object(agent_gateway, "_turn", AsyncMock(
                side_effect=agent_gateway.GatewayError("Private provider body"))):
            with self.assertRaises(agent_gateway.GatewayError):
                asyncio.run(agent_gateway.turn("private prompt", "session"))
        trace = runtime_trace.recent()[0]
        self.assertEqual(trace["state"], "failed")
        self.assertTrue(trace["finished_at"])
        self.assertNotIn("Private provider body", json.dumps(trace))

    def test_trace_records_steps_usage_and_outcome_without_content(self):
        agent = agents.assistant()
        with runtime_trace.span("agent_chat", agent_id=agent["id"]):
            runtime_trace.job("job-one")
            runtime_trace.event("sources_selected", source_count=2, prompt="private text", token="secret")
            runtime_trace.report(model="actual-model", provider="ollama", usage={"input": 3, "output": 4, "secret": 99})
        row = runtime_trace.recent()[0]
        self.assertEqual((row["state"], row["model"], row["model_source"]), ("succeeded", "actual-model", "reported"))
        self.assertEqual(row["agent_name"], "Stellis")
        self.assertEqual(row["job_id"], "job-one")
        self.assertEqual(row["usage"], {"input": 3, "output": 4})
        self.assertGreaterEqual(row["duration_ms"], 0)
        self.assertEqual([e["name"] for e in row["events"]], ["request_started", "sources_selected", "request_completed"])
        self.assertNotIn("private text", json.dumps(row))
        self.assertNotIn("secret", json.dumps(row))

    def test_usage_from_several_model_calls_adds_up(self):
        with runtime_trace.span("notebook_answer"):
            runtime_trace.report(model="planner", usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
            runtime_trace.report(model="answerer", usage={"prompt_tokens": 900, "completion_tokens": 80, "total_tokens": 980})
        row = runtime_trace.recent()[0]
        self.assertEqual(row["usage"], {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100})
        self.assertEqual(row["model"], "answerer")

    def test_failure_and_cancellation_keep_safe_outcomes(self):
        for exception, state in [(ValueError("private prompt"), "failed"), (asyncio.CancelledError(), "interrupted")]:
            with self.assertRaises(type(exception)):
                with runtime_trace.span("answer"):
                    raise exception
            row = runtime_trace.recent()[0]
            self.assertEqual(row["state"], state)
            self.assertNotIn("private prompt", json.dumps(row))

    def test_clear_preserves_active_request_and_switching_waits(self):
        with runtime_trace.span("answer"):
            runtime_trace.clear()
            self.assertEqual(len(runtime_trace.recent()), 1)
            with self.assertRaisesRegex(ValueError, "active requests"):
                with runtime_trace.model_change():
                    pass
        runtime_trace.clear()
        self.assertEqual(runtime_trace.recent(), [])
        with runtime_trace.model_change():
            with self.assertRaisesRegex(RuntimeError, "model change"):
                with runtime_trace.span("answer"):
                    pass

    def test_restart_and_account_roots_are_isolated(self):
        with storage.connect() as conn:
            conn.execute("""INSERT INTO inference_traces
                (id, kind, profile, execution, state, created_at)
                VALUES ('old', 'answer', 'local', 'GN100', 'running', '2026-01-01')""")
        runtime_trace.interrupt_running()
        self.assertEqual(runtime_trace.recent()[0]["state"], "interrupted")
        with tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {"STELLIS_DATA_DIR": other}):
            self.assertEqual(runtime_trace.recent(), [])

    def test_cloud_and_non_owner_cannot_switch_local_model(self):
        for env in [{"STELLIS_RUNTIME_PROFILE": "cloud"}, {"STELLIS_ACCOUNT_ID": "2"}]:
            with patch.dict(os.environ, env), self.assertRaisesRegex(ValueError, "owner"):
                runtime_settings.switch_model("installed-model")

    def test_status_does_not_invent_observed_route(self):
        state = runtime_settings.status()
        self.assertIsNone(state["observed"])
        self.assertEqual(state["inference_provider"], "Ollama")
        self.assertEqual(state["traces"], [])
        path = storage.data_dir() / "local-inference-model"
        path.write_text("saved-local-model")
        self.assertEqual(runtime_profile.model_id(), "saved-local-model")
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "cloud"}):
            self.assertEqual(runtime_profile.model_id(), "nvidia/Nemotron-3_5-Lightning")
