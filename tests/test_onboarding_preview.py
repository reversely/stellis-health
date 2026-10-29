"""Synthetic previews use native data projections without touching account data."""
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import documents, health_facts, notebooks, onboarding_preview


class OnboardingPreviewTest(unittest.TestCase):
    def test_native_synthetic_projection_is_isolated_and_source_linked(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STELLIS_DATA_DIR': folder}):
            actual = notebooks.create('My actual notebook')
            before = notebooks.list_notebooks()
            onboarding_preview.snapshot.cache_clear()
            preview = onboarding_preview.snapshot()
            self.assertTrue(preview['profile']['synthetic'])
            self.assertGreater(len(preview['notebooks']), 1)
            self.assertGreater(len(preview['records']), 1)
            self.assertTrue(any(len(measure['points']) >= 3 for measure in preview['measures']))
            for measure in preview['measures']:
                for point in measure['points']:
                    self.assertEqual(point['source'], 'record')
                    self.assertTrue(point['target'].startswith('/static/maya/documents/'))
                    self.assertIn(point['document_id'], {record['id'] for record in preview['records']})
            self.assertEqual(notebooks.list_notebooks(), before)
            self.assertEqual(documents.list_all(), [])
            self.assertEqual(health_facts.accepted(), [])
            self.assertNotIn(actual['id'], {book['id'] for book in preview['notebooks']})
            self.assertEqual(onboarding_preview.snapshot(), preview)


class PreviewCostTest(unittest.TestCase):
    def test_preview_never_calls_a_model_and_is_built_once(self):
        from stellis import document_naming
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'STELLIS_DATA_DIR': folder}), \
             patch('stellis.ai._key', return_value='key'), \
             patch.object(document_naming, 'infer', side_effect=AssertionError('no model call')) as infer:
            onboarding_preview.snapshot.cache_clear()
            first = onboarding_preview.snapshot()
            infer.assert_not_called()
            cached = [name for name in os.listdir(folder) if name.startswith('onboarding-preview-')]
            self.assertEqual(len(cached), 1)
            onboarding_preview.snapshot.cache_clear()
            with patch.object(onboarding_preview, '_build', side_effect=AssertionError('rebuilt')):
                self.assertEqual(onboarding_preview.snapshot(), first)
            onboarding_preview.snapshot.cache_clear()
