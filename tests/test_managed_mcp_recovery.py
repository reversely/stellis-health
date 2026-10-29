"""Agents never get a direct search tool; Stellis runs every search itself."""

import asyncio
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from stellis import agent_runtime

OWNER = {"STELLIS_ACCOUNT_ID": "1", "STELLIS_NEMOCLAW_SANDBOX": "sandbox"}


def bridges(*names: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, json.dumps({"bridges": [{"server": name} for name in names]}), "")


class NoDirectSearchToolTests(unittest.TestCase):
    def run_reconcile(self, responses):
        with patch.dict(os.environ, OWNER), \
             patch.object(agent_runtime, "_command", return_value="/bin/nemoclaw"), \
             patch.object(agent_runtime.subprocess, "run", side_effect=responses) as run:
            agent_runtime._reconcile_managed_mcp()
        return [call.args[0][-3:] for call in run.call_args_list]

    def test_an_existing_tavily_bridge_is_removed_and_checked(self) -> None:
        calls = self.run_reconcile([bridges("tavily", "gmail"),
                                    subprocess.CompletedProcess([], 0, "", ""), bridges("gmail")])
        self.assertEqual(calls, [["mcp", "list", "--json"], ["remove", "tavily", "--force"],
                                 ["mcp", "list", "--json"]])

    def test_a_sandbox_without_search_is_left_alone(self) -> None:
        self.assertEqual(self.run_reconcile([bridges("gmail")]), [["mcp", "list", "--json"]])

    def test_a_bridge_that_will_not_go_stops_the_turn(self) -> None:
        with self.assertRaises(agent_runtime.AgentRunError):
            self.run_reconcile([bridges("tavily"), subprocess.CompletedProcess([], 0, "", ""), bridges("tavily")])

    def test_other_accounts_do_not_touch_owner_sandbox(self) -> None:
        with patch.dict(os.environ, {"STELLIS_ACCOUNT_ID": "2", "STELLIS_NEMOCLAW_SANDBOX": ""}), \
             patch.object(agent_runtime.subprocess, "run") as run:
            agent_runtime._reconcile_managed_mcp()
        run.assert_not_called()

    def test_a_cloud_gateway_with_a_search_tool_refuses_turns(self) -> None:
        with patch.object(agent_runtime.runtime_profile, "remote_agents", return_value=True), \
             patch.dict(os.environ, {"STELLIS_AGENT_GATEWAY_TAVILY_READY": "1"}):
            with self.assertRaisesRegex(agent_runtime.AgentRunError, "search tool"):
                asyncio.run(agent_runtime.ensure_managed_mcp())
        with patch.object(agent_runtime.runtime_profile, "remote_agents", return_value=True), \
             patch.dict(os.environ, {"STELLIS_AGENT_GATEWAY_TAVILY_READY": "0"}):
            asyncio.run(agent_runtime.ensure_managed_mcp())
