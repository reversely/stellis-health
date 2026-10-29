"""Demo workspaces replay model replies by exact request; real accounts never use the cache (#258)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from stellis import privacy, storage

URL = "https://api.example.test/v1/chat/completions"


class _Counting(httpx.BaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": f"reply {self.calls}"}}],
                                         "usage": {"total_tokens": 1000}}, request=request)


class _AsyncCounting(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": f"reply {self.calls}"}}]}, request=request)


def _ask(transport: httpx.BaseTransport, body: dict) -> str:
    with httpx.Client(transport=transport) as client:
        return client.post(URL, json=body).json()["choices"][0]["message"]["content"]


class DemoModelCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        privacy._served.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.demo = Path(self.temp.name, "demo")

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def transport(self) -> tuple[privacy.Transport, _Counting]:
        transport = privacy.Transport()
        inner = _Counting()
        transport.inner = inner
        return transport, inner

    def test_demo_request_is_answered_once_then_replayed(self) -> None:
        transport, inner = self.transport()
        body = {"model": "m", "messages": [{"role": "user", "content": "Date this report"}]}
        with storage.demo_workspace(self.demo):
            first = _ask(transport, body)
            privacy._served.clear()  # Another visitor, after the retry window.
            again = _ask(transport, body)
            other = _ask(transport, {**body, "messages": [{"role": "user", "content": "Something else"}]})
        self.assertEqual((first, again, other, inner.calls), ("reply 1", "reply 1", "reply 2", 2))

    def test_a_quick_repeat_is_a_retry_and_asks_the_model_again(self) -> None:
        transport, inner = self.transport()
        body = {"model": "m", "messages": [{"role": "user", "content": "Sort these records"}]}
        with storage.demo_workspace(self.demo):
            first = _ask(transport, body)
            retry = _ask(transport, body)
            privacy._served.clear()  # A later visitor, after the retry window.
            later = _ask(transport, body)
        self.assertEqual((first, retry, later, inner.calls), ("reply 1", "reply 2", "reply 2", 2))

    def test_a_replayed_reply_counts_no_tokens(self) -> None:
        transport, _ = self.transport()
        body = {"model": "m", "messages": [{"role": "user", "content": "Read this report"}]}
        with storage.demo_workspace(self.demo):
            with httpx.Client(transport=transport) as client:
                first = client.post(URL, json=body).json()
                privacy._served.clear()
                replay = client.post(URL, json=body).json()
        self.assertEqual(first["usage"], {"total_tokens": 1000})
        self.assertNotIn("usage", replay)
        self.assertEqual(replay["choices"], first["choices"])

    def test_real_account_never_reads_or_writes_the_cache(self) -> None:
        transport, inner = self.transport()
        body = {"model": "m", "messages": [{"role": "user", "content": "Date this report"}]}
        with storage.demo_workspace(self.demo):
            _ask(transport, body)
        self.assertEqual(_ask(transport, body), "reply 2")
        self.assertEqual(inner.calls, 2)

    def test_streamed_replies_stay_live(self) -> None:
        transport, inner = self.transport()
        body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "Hello"}]}
        with storage.demo_workspace(self.demo):
            _ask(transport, body), _ask(transport, body)
        self.assertEqual(inner.calls, 2)
        self.assertFalse(Path(self.temp.name, "demo-model-cache").exists())

    def test_async_client_replays_too(self) -> None:
        transport = privacy.AsyncTransport()
        inner = _AsyncCounting()
        transport.inner = inner
        body = {"model": "m", "messages": [{"role": "user", "content": "File this record"}]}

        async def ask() -> str:
            async with httpx.AsyncClient(transport=transport) as client:
                return (await client.post(URL, json=body)).json()["choices"][0]["message"]["content"]

        with storage.demo_workspace(self.demo):
            first = asyncio.run(ask())
            privacy._served.clear()
            replies = [first, asyncio.run(ask())]
        self.assertEqual((replies, inner.calls), (["reply 1", "reply 1"], 1))


if __name__ == "__main__":
    unittest.main()


class DemoAllowanceTest(unittest.TestCase):
    """A demo visitor over the daily allowance still gets replies the demo already holds."""

    def setUp(self) -> None:
        privacy._served.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "NEBIUS_API_KEY": "test-key"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_a_stored_reply_is_served_over_the_allowance_and_a_new_request_is_refused(self) -> None:
        from stellis import agent_gateway, token_quota

        inner = _AsyncCounting()
        real = privacy.AsyncTransport

        def counted() -> privacy.AsyncTransport:
            transport = real()
            transport.inner = inner
            return transport

        def ask(prompt: str) -> str:
            return asyncio.run(agent_gateway.turn(prompt, "allowance", model_role="summary"))[0]

        with storage.demo_workspace(Path(self.temp.name, "demo")), patch.object(privacy, "AsyncTransport", counted):
            self.assertEqual(ask("Summarize the report."), "reply 1")
            privacy._served.clear()  # Another visitor: each account has its own process.
            with patch.object(token_quota, "exceeded", return_value=token_quota.MESSAGE):
                self.assertEqual(ask("Summarize the report."), "reply 1")
                # The same visitor asking again at once is a retry, which would reach the model.
                with self.assertRaisesRegex(agent_gateway.GatewayError, "allowance"):
                    ask("Summarize the report.")
                with self.assertRaisesRegex(agent_gateway.GatewayError, "allowance"):
                    ask("A question the demo has not seen.")
        self.assertEqual(inner.calls, 1)
