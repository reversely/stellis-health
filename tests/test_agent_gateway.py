"""Cloud configuration and response validation without contacting a substitute service."""
import os
import tempfile
import unittest
from unittest.mock import patch

from stellis import agent_gateway, agent_runtime, ai, tavily


class GatewayTests(unittest.TestCase):
    def test_legacy_sandbox_does_not_enable_agent_execution(self):
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "cloud", "STELLIS_NEMOCLAW_SANDBOX": "old-local"}, clear=True):
            self.assertFalse(agent_runtime.configured())

    def test_requires_authenticated_https_and_matching_account(self):
        base = {"STELLIS_RUNTIME_PROFILE": "cloud", "STELLIS_ACCOUNT_ID": "1", "STELLIS_AGENT_GATEWAY_ACCOUNT_ID": "1",
                "STELLIS_AGENT_GATEWAY_URL": "https://agents.example",
                "STELLIS_AGENT_GATEWAY_TOKEN": "private-token"}
        with patch.dict(os.environ, base, clear=True):
            self.assertTrue(agent_runtime.configured())
        for change in [{"STELLIS_ACCOUNT_ID": "2"}, {"STELLIS_AGENT_GATEWAY_TOKEN": ""},
                       {"STELLIS_AGENT_GATEWAY_URL": "http://agents.example"},
                       {"STELLIS_AGENT_GATEWAY_URL": "https://localhost"},
                       {"STELLIS_AGENT_GATEWAY_URL": "https://user:password@agents.example"},
                       {"STELLIS_AGENT_GATEWAY_URL": "https://agents.example?token=secret"}]:
            with self.subTest(change=change), patch.dict(os.environ, {**base, **change}, clear=True):
                self.assertFalse(agent_runtime.configured())

    def test_only_completed_assistant_text_is_accepted(self):
        item = {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "A cited answer"}]}
        self.assertEqual(agent_gateway._reply({"status": "completed", "output": [item]}),
                         "A cited answer")
        for payload in [None, {}, {"status": "failed", "output": [item]},
                        {"status": "incomplete", "output": [item]},
                        {"status": "completed", "output": []},
                        {"status": "completed", "output": [{**item, "role": "user"}]}]:
            with self.subTest(payload=payload), self.assertRaises(agent_gateway.GatewayError):
                agent_gateway._reply(payload)

    def test_key_settings_never_launch_local_provider_commands(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "STELLIS_RUNTIME_PROFILE": "cloud", "STELLIS_DATA_DIR": directory, "STELLIS_ACCOUNT_ID": "1",
                "STELLIS_NEMOCLAW_SANDBOX": "old-local"}, clear=True), \
             patch("subprocess.run", side_effect=AssertionError("Local runtime invoked")), \
             patch.object(ai, "_host_key", return_value=None), \
             patch.object(tavily, "_host_key", return_value=None):
            for provider in (ai, tavily):
                provider.save_key("test-key-1234567890abcdef")
                self.assertEqual(provider._saved_key() if provider is ai else provider.saved_key(),
                                 "test-key-1234567890abcdef")
                self.assertFalse(provider.remove_key()["configured"])


class ProfileTests(unittest.TestCase):
    def test_local_inference_does_not_use_saved_cloud_credential(self):
        from stellis import runtime_profile
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "local", "NEBIUS_API_KEY": "cloud-secret"}, clear=True):
            self.assertEqual(ai._key(), "ollama")
            self.assertEqual(runtime_profile.inference_url(), "http://127.0.0.1:11434/v1")
            self.assertEqual(ai.capabilities()["model_id"], "nemotron-3-nano:latest")
            self.assertIsNone(ai._managed_sandbox())

    def test_local_inference_rejects_external_endpoint(self):
        from stellis import runtime_profile
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "local",
                "STELLIS_LOCAL_INFERENCE_URL": "https://api.tokenfactory.nebius.com/v1"}, clear=True):
            with self.assertRaises(ValueError):
                runtime_profile.inference_url()

    def test_cloud_ignores_local_inference_configuration(self):
        from stellis import runtime_profile
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "cloud",
                "STELLIS_LOCAL_INFERENCE_URL": "http://127.0.0.1:11434/v1",
                "STELLIS_LOCAL_INFERENCE_MODEL": "local-model"}, clear=True):
            self.assertEqual(runtime_profile.inference_url(), "https://api.tokenfactory.nebius.com/v1")
            self.assertEqual(runtime_profile.model_id(), "nvidia/Nemotron-3_5-Lightning")

    def test_invalid_profile_does_not_silently_select_local(self):
        with patch.dict(os.environ, {"STELLIS_RUNTIME_PROFILE": "cluod"}, clear=True):
            with self.assertRaises(ValueError):
                agent_runtime.configured()
