"""The deployment's AI budget is one pool for every account, and the owner's model choices apply to all."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from stellis import agent_gateway, deployment, model_routes, privacy, runtime_profile, storage, token_quota


class _Replies(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"model": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
                                         "choices": [{"message": {"content": f"reply {self.calls}"}}],
                                         "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}, request=request)


class DeploymentBudgetTest(unittest.TestCase):
    def setUp(self) -> None:
        privacy._served.clear()
        self.temp = tempfile.TemporaryDirectory()
        shared = Path(self.temp.name, "shared"); shared.mkdir()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": str(Path(self.temp.name, "account")),
                                           "STELLIS_AUTH_DB_DIR": str(shared), "STELLIS_AI_BUDGET_USD": "0.10",
                                           "STELLIS_RUNTIME_PROFILE": "direct", "NEBIUS_API_KEY": "test-key"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_the_pool_is_priced_from_the_catalog_and_stops_new_calls_when_spent(self) -> None:
        self.assertEqual(deployment.budget(), {"limit_usd": 0.10, "spent_usd": 0.0, "enabled": True})
        self.assertAlmostEqual(deployment.cost("nvidia/nemotron-3-super-120b-a12b", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}), 1.20)
        token_quota.record({"prompt_tokens": 1_000_000, "completion_tokens": 0}, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
        self.assertIsNone(token_quota.exceeded())
        token_quota.record({"prompt_tokens": 1_000_000, "completion_tokens": 0}, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
        self.assertEqual(token_quota.exceeded(), deployment.MESSAGE)
        # Turning the limit off lets calls through; spending is still counted.
        deployment.set_enabled(False)
        self.assertIsNone(token_quota.exceeded())
        self.assertAlmostEqual(deployment.budget()["spent_usd"], 0.12)

    def test_a_caller_that_skips_the_check_sends_nothing_once_spent(self) -> None:
        from stellis import intake
        inner = _Replies()
        real = privacy.AsyncTransport

        def counted() -> privacy.AsyncTransport:
            transport = real(); transport.inner = inner; return transport

        deployment.charge(1.0)
        with patch.object(privacy, "AsyncTransport", counted), patch.object(intake, "_key", return_value="test-key"):
            with self.assertRaises(intake.IntakeError):
                asyncio.run(intake.complete_json("Reply with JSON.", {}, None, 50))
        self.assertEqual(inner.calls, 0)

    def test_the_owners_first_sign_in_leaves_the_shared_budget_and_stored_replies_in_place(self) -> None:
        from stellis import vault
        root = Path(os.environ["STELLIS_AUTH_DB_DIR"])
        deployment.charge(4.0)
        (root / "demo-model-cache").mkdir()
        (root / "stellis.sqlite3").write_text("owner records")
        vault.adopt(root, 1, Path(self.temp.name, "workspace"))
        self.assertTrue((root / "demo-model-cache").is_dir())
        self.assertFalse((root / "stellis.sqlite3").exists())
        self.assertAlmostEqual(deployment.budget()["spent_usd"], 4.0)

    def test_a_new_account_shares_the_same_spent_pool(self) -> None:
        token_quota.record({"prompt_tokens": 2_000_000}, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
        with patch.dict(os.environ, {"STELLIS_DATA_DIR": str(Path(self.temp.name, "another-guest"))}):
            self.assertEqual(token_quota.exceeded(), deployment.MESSAGE)

    def test_a_stored_demo_reply_is_served_and_listed_once_the_pool_is_spent(self) -> None:
        inner = _Replies()
        real = privacy.AsyncTransport

        def counted() -> privacy.AsyncTransport:
            transport = real(); transport.inner = inner; return transport

        def ask(prompt: str) -> str:
            agent_gateway.QUESTION.set((prompt, "notebook:Anemia Management"))
            return asyncio.run(agent_gateway.turn(prompt, "budget", model_role="summary"))[0]

        with storage.demo_workspace(Path(self.temp.name, "demo")), patch.object(privacy, "AsyncTransport", counted):
            self.assertEqual(ask("How has my ferritin changed?"), "reply 1")
            ask("A second question.")
            self.assertTrue(deployment.exhausted())
            privacy._served.clear()  # Another visitor.
            self.assertEqual(ask("How has my ferritin changed?"), "reply 1")
            with self.assertRaisesRegex(agent_gateway.GatewayError, "AI credits"):
                ask("A question nobody has asked.")
            self.assertEqual(set(privacy.stored_questions("notebook:Anemia Management")), {"A second question.", "How has my ferritin changed?"})
            self.assertEqual(privacy.stored_questions("notebook:Another notebook"), [])
        self.assertEqual(inner.calls, 2)

    def test_a_reply_is_replayed_for_another_visitors_copy_of_the_same_record(self) -> None:
        class Echo(httpx.AsyncBaseTransport):
            calls = 0

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                Echo.calls += 1
                record = privacy._ids(request.content.decode())[0]
                return httpx.Response(200, json={"choices": [{"message": {"content": "about " + record}}]}, request=request)

        real = privacy.AsyncTransport

        def counted() -> privacy.AsyncTransport:
            transport = real(); transport.inner = Echo(); return transport

        first, second = "dd214629-6f5e-414d-a05f-1bf22b5c85d4", "e7cc1969-bcfc-418e-992a-15202b7bac04"
        with storage.demo_workspace(Path(self.temp.name, "demo")), patch.object(privacy, "AsyncTransport", counted):
            ask = lambda record: asyncio.run(agent_gateway.turn('{"id": "%s"}' % record, "ids", model_role="summary"))[0]
            self.assertEqual(ask(first), "about " + first)
            privacy._served.clear()  # Another visitor, whose copy of the record has its own id.
            self.assertEqual(ask(second), "about " + second)
        self.assertEqual(Echo.calls, 1)

    def test_placeholders_are_numbered_the_same_in_every_process(self) -> None:
        import subprocess, sys
        code = ("from stellis import privacy; p = {'messages': [{'role': 'user', 'content': 'Dr Ruth Ada and Dr Ana Bly met Dr Kim Oyl.'}]};"
                "print(privacy.pseudonymize(p, [])[0]['messages'][0]['content'])")
        runs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                               env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in map(str, range(1, 11))}
        self.assertEqual(len(runs), 1, runs)

    def test_the_owners_model_choice_applies_to_every_account_on_shared_keys(self) -> None:
        with patch.dict(os.environ, {"STELLIS_SHARED_PROVIDER_KEYS": "1"}):
            model_routes.save({"summary": "nvidia/Nemotron-3_5-Lightning"})
            with patch.dict(os.environ, {"STELLIS_DATA_DIR": str(Path(self.temp.name, "guest")), "STELLIS_ACCOUNT_ID": "7"}):
                self.assertEqual(runtime_profile.model_id("summary"), "nvidia/Nemotron-3_5-Lightning")


if __name__ == "__main__":
    unittest.main()
