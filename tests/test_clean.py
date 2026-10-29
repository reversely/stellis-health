"""Edited transcript preserves source passages, revisions, and seek positions."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import ai, clean, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class CleanWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        imported = self.client.post('/api/v1/conversations/import', files={'file': ('talk.mp3', mp3_bytes(), 'audio/mpeg')})
        self.conversation_id = imported.json()['conversation']['id']
        self.base = f'/api/v1/conversations/{self.conversation_id}'
        segments = [
            {'id': 'seg-1', 'start_ms': 1000, 'end_ms': 2000, 'text': 'Um, Avery discussed satellites.', 'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []},
            {'id': 'seg-2', 'start_ms': 2000, 'end_ms': 3000, 'text': 'They will send 3 drafts tomorrow.', 'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []},
        ]
        storage.save_transcript({'conversation_id': self.conversation_id, 'revision': 1, 'language': 'en', 'segments_json': json.dumps(segments), 'turns_json': '[]', 'updated_at': datetime.now(timezone.utc).isoformat()})

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _start(self):
        with patch.object(ai, '_key', return_value='test-key'), patch.object(clean, '_launch'):
            response = self.client.post(f'{self.base}/clean-runs')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_clean_run_preserves_source_and_edits_only_clean_version(self):
        run = self._start()
        payload = {'segments': [
            {'source_segment_id': 's1', 'text': 'Avery discussed satellites.', 'needs_review': False},
            {'source_segment_id': 's2', 'text': 'They will send 3 drafts tomorrow.', 'needs_review': False},
        ]}
        with patch.object(ai, '_request', return_value=payload):
            clean._run(run['id'])
        finished = self.client.get(f"/api/v1/clean-runs/{run['id']}").json()
        self.assertEqual(finished['state'], 'succeeded')
        self.assertEqual(finished['result']['segments'][0]['start_ms'], 1000)
        self.assertEqual(finished['result']['segments'][0]['source_text'], 'Um, Avery discussed satellites.')
        edited = self.client.patch(f"/api/v1/clean-runs/{run['id']}/segments/seg-1", json={'text': 'Avery discussed the satellites.', 'expected_revision': 0})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/clean-runs/{run['id']}").json()['result']['segments'][0]['text'], 'Avery discussed the satellites.')
        self.assertEqual(self.client.get(f'{self.base}/transcript').json()['segments'][0]['text'], 'Um, Avery discussed satellites.')
        self.assertEqual(self.client.patch(f"/api/v1/clean-runs/{run['id']}/segments/seg-1", json={'text': 'Old revision', 'expected_revision': 0}).status_code, 409)

    def test_batch_edit_is_atomic_and_review_is_versioned(self):
        run = self._start()
        source = storage.get_display_transcript(self.conversation_id)['segments']
        payload = {'segments': [
            {'source_segment_id': 's1', 'text': source[0]['text'], 'needs_review': True},
            {'source_segment_id': 's2', 'text': source[1]['text'], 'needs_review': False},
        ]}
        with patch.object(ai, '_request', return_value=payload):
            clean._run(run['id'])
        path = f"/api/v1/clean-runs/{run['id']}"
        edits = {'expected_revision': 0, 'edits': [
            {'segment_id': 'seg-1', 'text': 'Avery discussed satellites.'},
            {'segment_id': 'seg-2', 'text': 'They will send three drafts tomorrow.'},
        ]}
        self.assertEqual(self.client.patch(f'{path}/segments', json=edits).status_code, 200)
        version = self.client.get(path).json()
        self.assertEqual(version['edit_revision'], 1)
        self.assertEqual([part['text'] for part in version['result']['segments']], [item['text'] for item in edits['edits']])
        self.assertEqual(self.client.patch(f'{path}/segments', json=edits).status_code, 409)
        invalid = {'expected_revision': 1, 'edits': [
            {'segment_id': 'seg-1', 'text': 'Changed'}, {'segment_id': 'missing', 'text': 'Oops'},
        ]}
        self.assertEqual(self.client.patch(f'{path}/segments', json=invalid).status_code, 404)
        self.assertEqual(self.client.get(path).json()['result']['segments'][0]['text'], 'Avery discussed satellites.')
        review = self.client.put(f'{path}/segments/seg-1/review', json={'expected_revision': 1})
        self.assertEqual(review.status_code, 200)
        self.assertTrue(self.client.get(path).json()['result']['segments'][0]['reviewed'])
        self.assertEqual(self.client.get(f'{self.base}/transcript').json()['segments'][0]['text'], source[0]['text'])

    def test_changed_number_reverts_and_marks_audio_review(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        payload = {'segments': [
            {'source_segment_id': 's1', 'text': 'Avery discussed satellites.', 'needs_review': False},
            {'source_segment_id': 's2', 'text': 'They will send 4 drafts tomorrow.', 'needs_review': False},
        ]}
        result = clean._validate_chunk(payload, source)
        self.assertEqual(result[1]['text'], source[1]['text'])
        self.assertTrue(result[1]['needs_review'])
        self.assertEqual(result[1]['start_ms'], 2000)

    def test_missing_or_reordered_passage_rejected(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        with self.assertRaises(ai.AIError):
            clean._validate_chunk({'segments': [{'source_segment_id': 's2', 'text': 'Wrong', 'needs_review': True}]}, source)

    def test_added_claim_and_lost_name_are_not_published(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        added = {'segments': [
            {'source_segment_id': 's1', 'text': 'Avery discussed satellites and approved the launch.', 'needs_review': False},
            {'source_segment_id': 's2', 'text': 'They will send 3 drafts tomorrow.', 'needs_review': False},
        ]}
        result = clean._validate_chunk(added, source)
        self.assertEqual(result[0]['text'], 'Avery discussed satellites.')
        self.assertTrue(result[0]['needs_review'])
        added['segments'][0]['text'] = 'Discussed satellites.'
        self.assertEqual(clean._validate_chunk(added, source)[0]['text'], 'Avery discussed satellites.')

    def test_omitted_chunk_is_split_and_recovered(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        incomplete = {'segments': [{'source_segment_id': 's1', 'text': source[0]['text'], 'needs_review': False}]}
        first = {'segments': [{'source_segment_id': 's1', 'text': source[0]['text'], 'needs_review': False}]}
        second = {'segments': [{'source_segment_id': 's1', 'text': source[1]['text'], 'needs_review': False}]}
        metadata = {'usage': {}, 'request_ids': [], 'attempts': 0}
        with patch.object(ai, '_request', side_effect=[incomplete, first, second]) as provider:
            result = clean._clean_chunk(source, metadata)
        self.assertEqual(provider.call_count, 3)
        self.assertEqual([item['id'] for item in result], ['seg-1', 'seg-2'])

    def test_safe_filler_fallback_when_model_preserves_words(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        payload = {'segments': [
            {'source_segment_id': 's1', 'text': source[0]['text'], 'needs_review': False},
            {'source_segment_id': 's2', 'text': source[1]['text'], 'needs_review': False},
        ]}
        result = clean._validate_chunk(payload, source)
        self.assertEqual(result[0]['text'], 'Avery discussed satellites.')
        self.assertFalse(result[0]['needs_review'])
        self.assertEqual(result[0]['start_ms'], 1000)

    def test_substantive_deletion_falls_back_and_requires_review(self):
        source = storage.get_display_transcript(self.conversation_id)['segments']
        payload = {'segments': [
            {'source_segment_id': 's1', 'text': 'Avery discussed.', 'needs_review': False},
            {'source_segment_id': 's2', 'text': source[1]['text'], 'needs_review': False},
        ]}
        result = clean._validate_chunk(payload, source)
        self.assertEqual(result[0]['text'], 'Avery discussed satellites.')
        self.assertTrue(result[0]['needs_review'])
        self.assertIsNotNone(result[0]['review_reason'])

    def test_unusual_asr_word_is_flagged_even_when_cleanup_keeps_it(self):
        source = storage.get_display_transcript(self.conversation_id)['segments'][:1]
        source[0]['text'] = 'We discussed q-sets.'
        payload = {'segments': [{'source_segment_id': 's1', 'text': source[0]['text'], 'needs_review': False}]}
        checked = clean._validate_chunk(payload, source)[0]
        self.assertEqual(checked['text'], source[0]['text'])
        self.assertTrue(checked['needs_review'])
        self.assertEqual(checked['review_reason'], 'Unusual word; check audio')
        run = self._start()
        saved = {'segments': [{**checked, 'needs_review': False, 'review_reason': None}]}
        storage.update_clean_run(run['id'], state='succeeded', stage='complete', result_json=json.dumps(saved))
        shown = self.client.get(f"/api/v1/clean-runs/{run['id']}").json()['result']['segments'][0]
        self.assertTrue(shown['needs_review'])
        self.assertEqual(shown['review_reason'], 'Unusual word; check audio')

    def test_older_run_is_rechecked_before_browser_display(self):
        run = self._start()
        source = storage.get_display_transcript(self.conversation_id)['segments']
        result = {'segments': [{**item, 'source_text': item['text'],
                                'text': 'Avery discussed.' if index == 0 else 'They will send 3 drafts today.',
                                'needs_review': False, 'review_reason': None}
                               for index, item in enumerate(source)]}
        storage.update_clean_run(run['id'], state='succeeded', stage='complete', result_json=json.dumps(result))
        with storage.connect() as conn:
            conn.execute('UPDATE clean_runs SET prompt_version = ? WHERE id = ?',
                         ('conservative-clean-v2', run['id']))
        displayed = self.client.get(f"/api/v1/clean-runs/{run['id']}").json()['result']['segments'][0]
        self.assertEqual(displayed['text'], 'Avery discussed satellites.')
        self.assertTrue(displayed['needs_review'])
        second = self.client.get(f"/api/v1/clean-runs/{run['id']}").json()['result']['segments'][1]
        self.assertEqual(second['text'], 'They will send 3 drafts today.')
        self.assertTrue(second['needs_review'])
