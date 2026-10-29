"""Persisted role isolation and inference guards without simulated providers."""
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import categorization, model_routes, runtime_profile, runtime_settings, runtime_trace, storage

class ModelRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STELLIS_DATA_DIR': self.temp.name,
            'STELLIS_RUNTIME_PROFILE': 'local', 'STELLIS_ACCOUNT_ID': '1'}, clear=True)
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def test_summaries_and_record_answers_are_light_and_research_is_largest(self):
        with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE': 'direct'}):
            self.assertEqual(runtime_profile.model_id('summary'), 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B')
            self.assertEqual(runtime_profile.model_id('answers'), 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B')
            self.assertEqual(runtime_profile.model_id('care'), 'nvidia/nemotron-3-super-120b-a12b')
            self.assertEqual(runtime_profile.model_id('review'), 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B')
            self.assertEqual(runtime_profile.model_id('research'), 'nvidia/nemotron-3-super-120b-a12b')
            # A choice saved in Settings still wins.
            model_routes.save({'summary': 'nvidia/Nemotron-3_5-Lightning'})
            self.assertEqual(runtime_profile.model_id('summary'), 'nvidia/Nemotron-3_5-Lightning')

    def test_independent_roles_profiles_and_accounts(self):
        model_routes.save({'transcript':'text:one','categories':'classify:two','planning':'plan:three'})
        self.assertEqual(runtime_profile.model_id(), 'text:one')
        self.assertEqual(categorization._model_id(), 'classify:two')
        self.assertEqual(runtime_profile.model_id('planning'), 'plan:three')
        self.assertEqual(runtime_profile.model_id('agent'), 'nemotron-3-nano:latest')
        self.assertEqual(model_routes.path().stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE':'cloud'}):
            self.assertEqual(model_routes.choices(), {})
            model_routes.save({'agent':'provider/cloud-model'})
            self.assertEqual(runtime_profile.model_id('agent'), 'provider/cloud-model')
            self.assertEqual(runtime_profile.model_id(), 'nvidia/Nemotron-3_5-Lightning')
        self.assertEqual(runtime_profile.model_id(), 'text:one')
        with tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {'STELLIS_DATA_DIR':other}):
            self.assertEqual(model_routes.choices(), {})
    def test_cloud_defaults_use_super_not_ultra_and_offer_the_nemotron_models(self):
        with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE':'cloud'}):
            for role in ('agent', 'report', 'research', 'care'):
                self.assertEqual(runtime_profile.model_id(role), 'nvidia/nemotron-3-super-120b-a12b')
            for role in ('review', 'summary', 'answers'):
                self.assertEqual(runtime_profile.model_id(role), 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B')
            self.assertEqual(runtime_profile.model_id('naming'), 'nvidia/Nemotron-3_5-Lightning')
            offered = [model for model, _ in runtime_profile.RECOMMENDED]
            self.assertEqual(offered[0], 'nvidia/nemotron-3-super-120b-a12b')
            self.assertIn('nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B', offered)
            self.assertIn('nvidia/Nemotron-3_5-Lightning', offered)
            model_routes.save({'agent': 'nvidia/Nemotron-3-Ultra-550b-a55b'})
            self.assertEqual(runtime_profile.model_id('agent'), 'nvidia/Nemotron-3-Ultra-550b-a55b')
    def test_invalid_routes_do_not_replace_saved_settings(self):
        model_routes.save({'transcript':'installed:model'})
        for invalid in [{'unknown':'model'}, {'planning':'../bad model'}, {'agent':None}, []]:
            with self.assertRaises(ValueError): model_routes.save(invalid)
            self.assertEqual(model_routes.choices(), {'transcript':'installed:model'})
    def test_active_request_prevents_save_before_provider_access(self):
        with runtime_trace.span('answer'):
            with self.assertRaisesRegex(ValueError, 'active requests'):
                runtime_settings.save_routes({'transcript':'text:new'})
        self.assertEqual(model_routes.choices(), {})
    def test_saved_route_for_removed_role_is_ignored(self):
        model_routes.path().write_text('{"transcript": "text:one", "vision": "eyes:four"}')
        self.assertEqual(model_routes.choices(), {'transcript': 'text:one'})
    def test_legacy_default_is_preserved_only_until_role_override(self):
        (storage.data_dir()/'local-inference-model').write_text('previous:model')
        model_routes.save({'transcript':'selected:model'})
        self.assertEqual(runtime_profile.model_id(), 'selected:model')
        self.assertEqual(runtime_profile.model_id('agent'), 'previous:model')

    def test_runtime_guard_also_covers_another_account(self):
        with runtime_trace.span('answer'):
            with tempfile.TemporaryDirectory() as other, patch.dict(os.environ, {'STELLIS_DATA_DIR':other}):
                with self.assertRaisesRegex(ValueError, 'active requests'):
                    with runtime_trace.model_change(): pass
