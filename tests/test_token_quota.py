"""Tests for the daily Token Factory allowance and its refusals."""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

import httpx

from stellis import agent_gateway
from stellis import ai
from stellis import token_quota


class TokenQuotaTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def env(self, **values):
        base = {
            "STELLIS_DATA_DIR": self.temp.name,
            "STELLIS_RUNTIME_PROFILE": "direct",
            "STELLIS_SHARED_PROVIDER_KEYS": "1",
            "STELLIS_ACCOUNT_ID": "2",
            "STELLIS_DAILY_TOKEN_LIMIT": "100",
            "NEBIUS_API_KEY": "nebius-shared"
        }
        return mock.patch.dict(os.environ, {**base, **values}, clear=True)

    def test_only_shared_key_accounts_have_a_limit(self):
        with self.env():
            self.assertEqual(token_quota.limit(), 100)
        for values in ({
                "STELLIS_ACCOUNT_ID": "1"
        }, {
                "STELLIS_SHARED_PROVIDER_KEYS": ""
        }, {
                "STELLIS_DAILY_TOKEN_LIMIT": ""
        }, {
                "STELLIS_DAILY_TOKEN_LIMIT": "none"
        }, {
                "STELLIS_RUNTIME_PROFILE": "local"
        }):
            with self.subTest(values=values), self.env(**values):
                self.assertIsNone(token_quota.limit())

    def test_usage_adds_up_until_the_allowance_is_used(self):
        with self.env():
            token_quota.record({"total_tokens": 60})
            self.assertIsNone(token_quota.exceeded())
            token_quota.record({"prompt_tokens": 30, "completion_tokens": 10})
            token_quota.record({"input": "bad", "output": -4})
            self.assertEqual(token_quota.used_today(), 100)
            self.assertEqual(token_quota.exceeded(), token_quota.MESSAGE)

    def test_direct_turn_records_usage_and_stops_calling_at_the_limit(self):
        reply = httpx.Response(200,
                               json={
                                   "model":
                                       "m",
                                   "usage": {
                                       "total_tokens": 120
                                   },
                                   "choices": [{
                                       "finish_reason": "stop",
                                       "message": {
                                           "content": "Answer"
                                       }
                                   }]
                               })
        post = mock.AsyncMock(return_value=reply)
        with self.env(), mock.patch.object(httpx.AsyncClient, "post", post):
            self.assertEqual(
                asyncio.run(agent_gateway.turn("prompt", "job-1"))[0], "Answer")
            self.assertEqual(token_quota.used_today(), 120)
            with self.assertRaises(agent_gateway.GatewayError) as caught:
                asyncio.run(agent_gateway.turn("prompt", "job-2"))
        self.assertEqual(str(caught.exception), token_quota.MESSAGE)
        self.assertEqual(post.await_count, 1)

    def test_transcript_ai_makes_no_request_over_the_limit(self):
        with self.env(), mock.patch.object(
                httpx.Client, "post",
                side_effect=AssertionError("Provider called")):
            token_quota.record({"total_tokens": 100})
            with self.assertRaises(ai.AIError) as caught:
                ai._request_impl([{
                    "role": "user",
                    "content": "x"
                }], 10, {
                    "attempts": 0,
                    "usage": {},
                    "request_ids": []
                }, "summary")
        self.assertEqual(str(caught.exception), token_quota.MESSAGE)


if __name__ == "__main__":
    unittest.main()
