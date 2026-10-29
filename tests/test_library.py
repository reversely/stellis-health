"""Browser API checks for categories, Trash, and dependent data removal."""

import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import ai, categorization, enhanced_notes, library, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class LibraryWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        self.audio = mp3_bytes()
        self.first = self.client.post('/api/v1/conversations/import',
                                      files={'file': ('first.mp3', self.audio, 'audio/mpeg')}).json()['conversation']['id']
        self.second_audio = mp3_bytes(wav_bytes()[:-2] + b'\x01\x00')
        self.second = self.client.post('/api/v1/conversations/import',
                                       files={'file': ('second.mp3', self.second_audio, 'audio/mpeg')}).json()['conversation']['id']
        now = datetime.now(timezone.utc).isoformat()
        storage.save_transcript({'conversation_id': self.first, 'revision': 1, 'language': 'en',
                                 'segments_json': json.dumps([{'id': 's1', 'start_ms': 0, 'end_ms': 500,
                                     'text': 'Asteroid launch.', 'speaker_cluster_id': 'speaker-1',
                                     'speaker_uncertain': False, 'words': []}]),
                                 'turns_json': '[]', 'updated_at': now})

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_rename_conversation_updates_detail_list_and_search(self):
        path = f'/api/v1/conversations/{self.first}'
        renamed = self.client.patch(path, json={'title': '  Launch interview  '})
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()['title'], 'Launch interview')
        self.assertEqual(self.client.get(path).json()['title'], 'Launch interview')
        listed = self.client.get('/api/v1/conversations').json()
        self.assertEqual(next(item for item in listed if item['id'] == self.first)['title'], 'Launch interview')
        self.assertEqual(self.client.get('/api/v1/search', params={'q': 'Launch interview'}).json()['total'], 1)
        self.assertEqual(self.client.patch(path, json={'title': '   '}).status_code, 400)
        self.assertEqual(self.client.get(path).json()['title'], 'Launch interview')
        self.assertEqual(self.client.patch('/api/v1/conversations/missing', json={'title': 'Any'}).status_code, 404)
        self.client.post(f'{path}/trash')
        self.assertEqual(self.client.patch(path, json={'title': 'Hidden'}).status_code, 404)

    def test_search_finds_my_notes_and_summary_text(self):
        base = f'/api/v1/conversations/{self.first}'
        self.client.put(f'{base}/notes', json={'markdown': '- [ ] Ping the **zebra** team ^t000001\n![shot](stellis-image:'
                        + str(uuid.uuid4()) + ')', 'expected_revision': 0})
        payload = {'overview': [{'text': 'Quokka plans were agreed.', 'source_segment_ids': ['s1']}],
                   'topics': [], 'decisions': [], 'action_items': []}
        with patch.object(ai, '_key', return_value='test-key'), patch.object(enhanced_notes.storage, "background"):
            run = self.client.post(f'{base}/enhanced-notes', json={}).json()
        with patch.object(ai, '_key', return_value='test-key'), patch.object(ai, '_request', return_value=payload):
            enhanced_notes._run(run['id'])
        search = lambda q: self.client.get('/api/v1/search', params={'q': q}).json()['results']
        note = search('zebra team')
        self.assertEqual([(item['kind'], item['conversation_id']) for item in note], [('note', self.first)])
        self.assertEqual(note[0]['excerpt'], 'Ping the zebra team shot')
        self.assertEqual([item['kind'] for item in search('quokka')], ['summary'])
        self.assertEqual(search('t000001') + search('stellis-image'), [])
        self.client.put(f'/api/v1/enhanced-notes/{run["id"]}', json={'markdown': '## Overview\n- Wombat edit', 'expected_revision': 0})
        self.assertEqual(search('quokka'), [])
        self.assertEqual([item['kind'] for item in search('wombat')], ['summary'])

    def test_categories_trash_restore_and_permanent_delete(self):
        categories = [self.client.post('/api/v1/categories', json={'name': name}).json()
                      for name in ('Research', 'Field notes')]
        for category in categories:
            response = self.client.put(f'/api/v1/conversations/{self.first}/categories/{category["id"]}')
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids']), 2)
        duplicate = self.client.post('/api/v1/conversations/import',
                                     files={'file': ('same.mp3', self.audio, 'audio/mpeg')}).json()
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(duplicate['conversation']['category_ids']), 2)
        self.assertEqual(self.client.get('/api/v1/search', params={'q':'Asteroid',
            'category_id': categories[0]['id']}).json()['total'], 1)
        self.assertEqual(self.client.get('/api/v1/search', params={'q':'Asteroid',
            'category_id': 'uncategorized'}).json()['total'], 0)
        self.client.patch(f'/api/v1/categories/{categories[0]["id"]}', json={'name':'Science'})
        self.assertIn('Science', [item['name'] for item in self.client.get('/api/v1/categories').json()])
        self.client.patch(f'/api/v1/conversations/{self.first}/transcript/segments/s1',
                          json={'text':'Asteroid corrected.', 'expected_revision':1})
        media = storage.data_dir() / 'media' / storage.get_conversation(self.first)['audio_filename']
        self.assertTrue(media.exists())
        self.assertEqual(self.client.post(f'/api/v1/conversations/{self.first}/trash').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}').status_code, 404)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}/audio').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/search', params={'q':'Asteroid'}).json()['total'], 0)
        self.assertEqual(len(self.client.get('/api/v1/conversations').json()), 1)
        self.assertEqual(len(self.client.get('/api/v1/trash').json()), 1)
        self.assertEqual(self.client.post('/api/v1/conversations/import',
            files={'file':('same.mp3', self.audio, 'audio/mpeg')}).status_code, 400)
        self.assertEqual(self.client.post(f'/api/v1/trash/{self.first}/restore').status_code, 200)
        restored = self.client.get(f'/api/v1/conversations/{self.first}/transcript').json()
        self.assertEqual(restored['segments'][0]['text'], 'Asteroid corrected.')
        self.assertEqual(len(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids']), 2)
        self.client.delete(f'/api/v1/categories/{categories[1]["id"]}')
        self.assertEqual(len(self.client.get('/api/v1/conversations').json()), 2)
        self.assertEqual(len(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids']), 1)
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        self.assertEqual(self.client.delete(f'/api/v1/trash/{self.first}').status_code, 200)
        self.assertFalse(media.exists())
        self.assertIsNone(storage.get_conversation(self.first, include_trashed=True))
        self.assertEqual(self.client.get('/api/v1/search', params={'q':'Asteroid'}).json()['total'], 0)
        self.assertEqual(len(self.client.get('/api/v1/categories').json()), len(storage.INITIAL_CATEGORIES) + 1)
        self.assertEqual(len(self.client.get('/api/v1/conversations').json()), 1)
        with storage.connect() as conn:
            for table in ('transcripts', 'transcript_generations', 'transcript_edits',
                          'transcript_edit_events', 'conversation_categories'):
                self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table} WHERE conversation_id = ?',
                                              (self.first,)).fetchone()[0], 0)

    def test_automatic_category_requires_opt_in_and_respects_manual_change(self):
        self.client.patch(f'/api/v1/conversations/{self.first}/transcript/segments/s1',
            json={'text': 'The interviewer asked about prior projects and the candidate described their work, '
                          'then answered questions about collaboration, deadlines, and the role on the team, '
                          'including several specific examples from their experience.',
                  'expected_revision': 1})
        setting = self.client.get('/api/v1/categories/automation').json()
        self.assertFalse(setting['enabled'])
        self.assertEqual(self.client.put('/api/v1/categories/automation', json={'enabled': True}).status_code, 200)
        revision = storage.get_transcript(self.first)['revision']
        with storage.connect() as conn:
            conn.execute("""INSERT INTO category_decisions
                (conversation_id, state, model_id, transcript_revision, updated_at)
                VALUES (?, 'queued', ?, ?, ?)""", (self.first, categorization.MODEL_ID, revision, datetime.now(timezone.utc).isoformat()))
        with patch.object(categorization, 'classify', return_value=('Interviews', 0.98)):
            categorization._run(self.first, revision, 'test-key')
        category = next(item for item in self.client.get('/api/v1/categories').json() if item['name'] == 'Interviews')
        self.assertIn(category['id'], self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids'])
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}/categorization').json()['state'], 'assigned')
        self.client.delete(f'/api/v1/conversations/{self.first}/categories/{category["id"]}')
        with patch.object(categorization, 'classify', return_value=('Meetings', 0.99)):
            categorization._run(self.first, revision, 'test-key')
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids'], [])
        self.assertTrue(self.client.get(f'/api/v1/conversations/{self.first}/categorization').json()['manual_override'])

    def test_missing_provider_key_is_retryable_without_changing_transcript(self):
        self.client.put('/api/v1/categories/automation', json={'enabled': True})
        with patch('stellis.ai._key', return_value=None):
            result = self.client.post(f'/api/v1/conversations/{self.first}/categorization').json()
        self.assertEqual(result['state'], 'failed')
        self.assertIn('Nebius API key', result['error'])
        self.assertIsNotNone(storage.get_transcript(self.first))
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids'], [])

    def test_short_transcript_abstains_without_provider_call(self):
        self.client.put('/api/v1/categories/automation', json={'enabled': True})
        with storage.connect() as conn:
            conn.execute("""INSERT INTO category_decisions
                (conversation_id, state, model_id, prompt_version, transcript_revision, updated_at)
                VALUES (?, 'queued', ?, ?, 1, ?)""",
                (self.first, categorization.MODEL_ID, categorization.PROMPT_VERSION,
                 datetime.now(timezone.utc).isoformat()))
        with patch.object(categorization, 'classify') as classify:
            categorization._run(self.first, 1, 'test-key')
            classify.assert_not_called()
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}/categorization').json()['state'], 'unclear')

    def test_initial_categories_are_seeded_once_and_remain_editable(self):
        endpoint = '/api/v1/categories'
        initial = self.client.get(endpoint).json()
        self.assertEqual([item['name'] for item in initial], list(storage.INITIAL_CATEGORIES))
        meeting = initial[0]
        self.assertEqual(self.client.patch(f"{endpoint}/{meeting['id']}", json={'name':'Standups'}).status_code, 200)
        self.assertEqual(self.client.delete(f"{endpoint}/{initial[1]['id']}").status_code, 200)
        with storage.connect():
            pass
        current = self.client.get(endpoint).json()
        self.assertEqual({item['name'] for item in current}, {'Standups', 'Workshops', 'Briefings', 'Calls'})
        self.assertEqual(next(item['id'] for item in current if item['name'] == 'Standups'), meeting['id'])
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}').json()['category_ids'], [])

    def test_existing_categories_survive_one_time_seed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'STELLIS_DATA_DIR': directory}):
            database = Path(directory) / 'stellis.sqlite3'
            with sqlite3.connect(database) as conn:
                conn.execute('CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)')
                conn.execute('INSERT INTO categories VALUES (?, ?, ?, ?)',
                             ('existing-meeting', 'MEETINGS', 'meetings', '2026-01-01'))
                conn.execute('INSERT INTO categories VALUES (?, ?, ?, ?)',
                             ('custom', 'Research', 'research', '2026-01-01'))
            seeded = library.categories()
            self.assertEqual([item['name'] for item in seeded[:5]],
                             ['MEETINGS', 'Interviews', 'Workshops', 'Briefings', 'Calls'])
            self.assertEqual(seeded[0]['id'], 'existing-meeting')
            self.assertEqual(len(seeded), 6)
            self.assertEqual(library.categories(), seeded)

    def test_category_groups_list_the_most_used_categories_with_their_conversations(self):
        categories = {item['name']: item['id'] for item in self.client.get('/api/v1/categories').json()}
        for conversation, name in ((self.first, 'Meetings'), (self.second, 'Meetings'), (self.second, 'Calls')):
            self.assertEqual(self.client.put(f'/api/v1/conversations/{conversation}/categories/{categories[name]}').status_code, 200)
        groups = self.client.get('/api/v1/category-groups').json()
        self.assertEqual([group['name'] for group in groups], ['Meetings', 'Calls'])
        self.assertEqual({item['id'] for item in groups[0]['conversations']}, {self.first, self.second})
        self.assertTrue(all(group['color'].startswith('#') for group in groups))
        self.assertTrue(all('<' not in group['name'] for group in groups))

    def test_library_lists_each_conversations_unfinished_processing(self):
        now = datetime.now(timezone.utc).isoformat()
        job_id = str(uuid.uuid4())
        storage.add_job({'id': job_id, 'conversation_id': self.first, 'pipeline_version': 'listed-test',
                         'kind': 'speech', 'state': 'running', 'stage': 'diarizing', 'progress': 0.55,
                         'error': None, 'metrics_json': None, 'vocabulary_json': '[]',
                         'created_at': now, 'updated_at': now})
        listed = {item['id']: item for item in self.client.get('/api/v1/conversations').json()}
        self.assertEqual(listed[self.first]['processing_job']['state'], 'running')
        self.assertEqual(listed[self.first]['processing_job']['stage'], 'diarizing')
        storage.update_job(job_id, state='succeeded', stage='complete', progress=1.0, updated_at=now)
        listed = {item['id']: item for item in self.client.get('/api/v1/conversations').json()}
        self.assertIsNone(listed[self.first]['processing_job'])

    def test_pending_job_blocks_permanent_delete_until_settled(self):
        now = datetime.now(timezone.utc).isoformat()
        job_id = str(uuid.uuid4())
        storage.add_job({'id':job_id, 'conversation_id':self.first, 'pipeline_version':'queued-test',
                         'kind':'speech', 'state':'queued', 'stage':'queued', 'progress':0,
                         'error':None, 'metrics_json':None, 'vocabulary_json':'[]',
                         'created_at':now, 'updated_at':now})
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        self.assertEqual(self.client.delete(f'/api/v1/trash/{self.first}').status_code, 409)
        self.assertEqual(self.client.get('/api/v1/search', params={'q':'Asteroid'}).json()['total'], 0)
        storage.update_job(job_id, state='failed', stage='failed', error='cancelled', updated_at=now)
        self.assertEqual(self.client.delete(f'/api/v1/trash/{self.first}').status_code, 200)
        self.assertIsNone(storage.get_job(job_id))

    def test_recently_deleted_auto_expires_after_30_days_and_retries_busy_items(self):
        now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
        old = (now - timedelta(days=30, seconds=1)).isoformat()
        fresh = (now - timedelta(days=29)).isoformat()
        person = self.client.post('/api/v1/persons', json={'name':'Retention QA'}).json()
        self.client.put(f'/api/v1/conversations/{self.first}/person-links', json={
            'person_id':person['id'], 'segment_id':'s1', 'scope':'segment', 'expected_revision':1})
        media = storage.data_dir() / 'media' / storage.get_conversation(self.first)['audio_filename']
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        with storage.connect() as conn:
            conn.execute('UPDATE conversations SET trashed_at = ? WHERE id = ?', (old, self.first))
        record = self.client.get('/api/v1/trash').json()[0]
        self.assertEqual(record['expires_at'], (datetime.fromisoformat(old) + timedelta(days=30)).isoformat())
        job_id = str(uuid.uuid4())
        storage.add_job({'id':job_id, 'conversation_id':self.first, 'pipeline_version':'queued-test',
                         'kind':'speech', 'state':'queued', 'stage':'queued', 'progress':0,
                         'error':None, 'metrics_json':None, 'vocabulary_json':'[]',
                         'created_at':old, 'updated_at':old})
        self.assertEqual(library.delete_expired(now=now), {'deleted': [], 'deferred': [self.first]})
        self.assertTrue(media.exists())
        storage.update_job(job_id, state='failed', stage='failed', error='cancelled', updated_at=old)
        self.client.post(f'/api/v1/trash/{self.first}/restore')
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        with storage.connect() as conn:
            conn.execute('UPDATE conversations SET trashed_at = ? WHERE id = ?', (fresh, self.first))
        self.assertEqual(library.delete_expired(now=now)['deleted'], [])
        self.assertTrue(media.exists())
        with storage.connect() as conn:
            conn.execute('UPDATE conversations SET trashed_at = ? WHERE id = ?', (old, self.first))
        self.assertEqual(library.delete_expired(now=now)['deleted'], [self.first])
        self.assertFalse(media.exists())
        self.assertIsNone(storage.get_conversation(self.first, include_trashed=True))
        self.assertEqual(self.client.get(f'/api/v1/persons/{person["id"]}').json()['total_link_count'], 0)
        self.assertIsNotNone(storage.get_conversation(self.second))

    def test_interrupted_media_staging_recovers_or_cleans_up(self):
        original = storage.data_dir() / 'media' / storage.get_conversation(self.first)['audio_filename']
        staged = original.with_name(f'.deleting-{self.first}-{uuid.uuid4().hex}')
        original.rename(staged)
        library.recover_staged_media()
        self.assertTrue(original.exists())
        self.assertFalse(staged.exists())
        orphan = original.with_name(f'.deleting-{uuid.uuid4()}-{uuid.uuid4().hex}')
        orphan.write_bytes(b'orphan')
        library.recover_staged_media()
        self.assertFalse(orphan.exists())

    def test_confirmed_person_history_survives_rematch_and_reprocessing(self):
        stamp = datetime.now(timezone.utc).isoformat()
        storage.save_transcript({'conversation_id': self.second, 'revision': 1, 'language': 'en',
                                 'segments_json': json.dumps([{'id': 't1', 'start_ms': 100,
                                     'end_ms': 600, 'text': 'Hello again.', 'speaker_cluster_id': 'speaker-9',
                                     'speaker_uncertain': False, 'words': []},
                                     {'id': 't2', 'start_ms': 700, 'end_ms': 900, 'text': 'Another voice.',
                                      'speaker_cluster_id': 'speaker-10', 'speaker_uncertain': True, 'words': []}]),
                                 'turns_json': '[]', 'updated_at': stamp})
        morgan = self.client.post('/api/v1/persons', json={'name':'Morgan'}).json()
        jordan = self.client.post('/api/v1/persons', json={'name':'Jordan'}).json()
        for cid, sid in ((self.first, 's1'), (self.second, 't1')):
            linked = self.client.put(f'/api/v1/conversations/{cid}/person-links', json={
                'person_id':morgan['id'], 'segment_id':sid, 'scope':'cluster', 'expected_revision':1})
            self.assertEqual(linked.status_code, 200)
        profile = self.client.get(f'/api/v1/persons/{morgan["id"]}').json()
        self.assertEqual({item['conversation_id'] for item in profile['conversations']}, {self.first, self.second})
        self.assertEqual(self.client.delete(f'/api/v1/persons/{morgan["id"]}').status_code, 409)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.second}/transcript').json()['segments'][1].get('person_id'), None)
        self.assertTrue(any(item['kind']=='person' for item in self.client.get('/api/v1/search', params={'q':'Morgan'}).json()['results']))
        self.client.patch(f'/api/v1/persons/{morgan["id"]}', json={'name':'Avery'})
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}/transcript').json()['segments'][0]['speaker_display_name'], 'Avery')
        original = storage.get_transcript(self.first)
        rematched = [{**item, 'speaker_cluster_id':'speaker-new'} for item in json.loads(original['segments_json'])]
        storage.save_transcript({'conversation_id':self.first, 'pipeline_version':'rematch-test',
                                 'language':'en', 'segments_json':json.dumps(rematched),
                                 'turns_json':'[]', 'updated_at':stamp})
        self.assertEqual(self.client.get(f'/api/v1/conversations/{self.first}/transcript').json()['segments'][0]['person_id'], morgan['id'])
        reprocessed = [{**rematched[0], 'id':'replacement-s1'}]
        storage.save_transcript({'conversation_id':self.first, 'pipeline_version':'reprocess-test',
                                 'language':'en', 'segments_json':json.dumps(reprocessed),
                                 'turns_json':'[]', 'updated_at':stamp})
        self.assertNotIn('person_id', self.client.get(f'/api/v1/conversations/{self.first}/transcript').json()['segments'][0])
        historical = self.client.get(f'/api/v1/persons/{morgan["id"]}').json()['conversations']
        self.assertFalse(next(item for item in historical if item['conversation_id']==self.first)['current_generation'])
        self.client.put(f'/api/v1/conversations/{self.second}/person-links', json={
            'person_id':jordan['id'], 'segment_id':'t2', 'scope':'segment', 'expected_revision':1})
        self.assertEqual(self.client.post(f'/api/v1/persons/{morgan["id"]}/merge',
            json={'source_id':jordan['id']}).status_code, 200)
        separated = self.client.post('/api/v1/persons', json={'name':'Casey'}).json()
        self.assertEqual(self.client.post(f'/api/v1/persons/{morgan["id"]}/move-conversation',
            json={'conversation_id':self.second,'target_id':separated['id']}).json()['moved'], 2)
        self.assertEqual(len(self.client.get(f'/api/v1/persons/{morgan["id"]}').json()['conversations']), 1)
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        self.assertEqual(self.client.get(f'/api/v1/persons/{morgan["id"]}').json()['conversations'], [])
        self.client.post(f'/api/v1/trash/{self.first}/restore')
        self.assertEqual(len(self.client.get(f'/api/v1/persons/{morgan["id"]}').json()['conversations']), 1)
        self.client.post(f'/api/v1/conversations/{self.first}/trash')
        self.assertEqual(self.client.delete(f'/api/v1/trash/{self.first}').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/persons/{morgan["id"]}').json()['conversations'], [])
        self.assertEqual(len(self.client.get(f'/api/v1/persons/{separated["id"]}').json()['conversations']), 1)
        self.assertEqual(self.client.delete(f'/api/v1/persons/{morgan["id"]}').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/v1/persons/{separated["id"]}/conversations/{self.second}').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/v1/persons/{separated["id"]}').status_code, 200)
        self.assertFalse(any(item['kind']=='person' for item in self.client.get('/api/v1/search',
                                                                                 params={'q':'Casey'}).json()['results']))


if __name__ == '__main__':
    unittest.main()
