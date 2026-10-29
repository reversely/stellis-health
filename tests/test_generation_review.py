"""Review boundaries between an edited source and a new ASR generation."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import generation_review, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes
from test_images import png_bytes


class GenerationReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        self.id = self.client.post('/api/v1/conversations/import',
                                   files={'file':('review.mp3',mp3_bytes(),'audio/mpeg')}).json()['conversation']['id']
        stamp = datetime.now(timezone.utc).isoformat()
        self.source = [
            {'id':'s1','start_ms':0,'end_ms':600,'text':'Hello orbit.',
             'speaker_cluster_id':'speaker-1','speaker_uncertain':False,'words':[]},
            {'id':'s2','start_ms':600,'end_ms':1000,'text':'Second voice.',
             'speaker_cluster_id':'speaker-2','speaker_uncertain':False,'words':[]},
            {'id':'s3','start_ms':1000,'end_ms':1400,'text':'Closing words.',
             'speaker_cluster_id':'speaker-2','speaker_uncertain':False,'words':[]},
        ]
        storage.save_transcript({'conversation_id':self.id,'revision':1,'pipeline_version':'source-v1',
                                 'language':'en','segments_json':json.dumps(self.source),
                                 'turns_json':'[]','updated_at':stamp})
        self.client.patch(f'/api/v1/conversations/{self.id}/transcript/segments/s1',
                          json={'text':'Hello world orbit.','expected_revision':1})
        self.client.patch(f'/api/v1/conversations/{self.id}/transcript/segments/s3',
                          json={'text':'Closing worlds.','expected_revision':2})
        self.client.put(f'/api/v1/conversations/{self.id}/speakers/label', json={
            'scope':'segment','target_id':'s2','name':'Interviewer','expected_revision':3})
        self.client.post(f'/api/v1/conversations/{self.id}/highlights', json={
            'segment_id':'s1','start_offset':0,'end_offset':5,'expected_revision':4})
        self.person = self.client.post('/api/v1/persons',json={'name':'Morgan'}).json()
        self.client.put(f'/api/v1/conversations/{self.id}/person-links',json={
            'person_id':self.person['id'],'segment_id':'s1','scope':'segment','expected_revision':4})
        self.target = [
            {'id':'t1','start_ms':0,'end_ms':260,'text':'Hello',
             'speaker_cluster_id':'speaker-9','speaker_uncertain':True,'words':[]},
            {'id':'t2','start_ms':260,'end_ms':600,'text':'orbit.',
             'speaker_cluster_id':'speaker-9','speaker_uncertain':True,'words':[]},
            {'id':'t3','start_ms':600,'end_ms':1400,'text':'Second voice. Closing words.',
             'speaker_cluster_id':'speaker-10','speaker_uncertain':True,'words':[]},
        ]
        self.record = {'conversation_id':self.id,'pipeline_version':'target-v2','language':'en',
                       'segments_json':json.dumps(self.target),'turns_json':'[]','updated_at':stamp}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def transcript(self):
        return self.client.get(f'/api/v1/conversations/{self.id}/transcript').json()

    def test_edited_passages_are_kept_as_written_with_their_names_and_highlights(self):
        self.assertEqual(generation_review.stage(self.record), 'applied')
        after = self.transcript()
        self.assertEqual(after['generation_version'], 'target-v2')
        self.assertEqual([item['id'] for item in after['segments']], ['s1', 's2', 's3'])
        self.assertEqual(after['segments'][0]['text'], 'Hello world orbit.')
        self.assertEqual(after['segments'][0]['person_id'], self.person['id'])
        self.assertEqual(after['segments'][1]['speaker_display_name'], 'Interviewer')
        self.assertEqual(after['segments'][2]['text'], 'Closing worlds.')
        self.assertEqual(after['highlights'][0]['segment_id'], 's1')
        # Kept passages take the new speaker grouping.
        self.assertEqual(after['segments'][0]['speaker_cluster_id'], 'speaker-9')
        self.assertIsNone(generation_review.get_review(self.id) if generation_review.get_review(self.id) and
                          generation_review.get_review(self.id)['state'] == 'pending' else None)
        self.assertEqual(self.client.get(f'/api/v1/persons/{self.person["id"]}').json()['total_link_count'], 1)

    def test_same_passages_with_new_ids_carry_every_overlay(self):
        self.target = [{**item, 'id': f'new-{item["id"]}'} for item in self.source]
        self.record['segments_json'] = json.dumps(self.target)
        self.assertEqual(generation_review.stage(self.record), 'applied')
        after = self.transcript()
        self.assertEqual([item['id'] for item in after['segments']], ['new-s1', 'new-s2', 'new-s3'])
        self.assertEqual(after['segments'][0]['text'], 'Hello world orbit.')
        self.assertEqual(after['segments'][0]['person_id'], self.person['id'])
        self.assertEqual(after['segments'][1]['speaker_display_name'], 'Interviewer')
        self.assertEqual(after['segments'][2]['text'], 'Closing worlds.')
        self.assertEqual(after['highlights'][-1]['segment_id'], 'new-s1')
        self.assertEqual(self.client.get(f'/api/v1/persons/{self.person["id"]}').json()['total_link_count'], 1)

    def test_ambiguous_comment_quote_keeps_its_passage(self):
        created = self.client.post(f'/api/v1/conversations/{self.id}/comments', json={
            'segment_id': 's1', 'start_offset': 0, 'end_offset': 5,
            'body': 'Two Hellos', 'expected_revision': 4})
        self.assertEqual(created.status_code, 200)
        self.target[0]['text'] = 'Hello Hello'
        self.record['segments_json'] = json.dumps(self.target)
        self.assertEqual(generation_review.stage(self.record), 'applied')
        self.assertEqual(storage.list_comments(self.id)[0]['segment_id'], 's1')
        self.assertEqual(self.transcript()['generation_version'], 'target-v2')

    def test_selected_image_keeps_its_exact_quote(self):
        endpoint = f'/api/v1/conversations/{self.id}/images'
        created = self.client.post(endpoint, files={'file': ('slide.png', png_bytes(), 'image/png')},
                                   data={'media_position_ms': '0', 'segment_id': 's1', 'start_offset': '0',
                                         'end_offset': '5', 'expected_revision': '4'})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(generation_review.stage(self.record), 'applied')
        image = self.client.get(endpoint).json()[0]
        self.assertEqual((image['segment_id'], image['quote'], image['start_offset']), ('s1', 'Hello', 0))

    def test_review_left_by_the_old_flow_is_resolved_on_startup(self):
        stamp = datetime.now(timezone.utc).isoformat()
        with storage.connect() as conn:
            conn.execute("""INSERT INTO transcript_generations (conversation_id, pipeline_version, revision, language,
                segments_json, turns_json, created_at) VALUES (?, 'target-v2', 5, 'en', ?, '[]', ?)""",
                (self.id, self.record['segments_json'], stamp))
            source = storage.get_transcript(self.id)
            conn.execute("""INSERT INTO transcript_reviews (conversation_id, source_version, target_version,
                source_revision, items_json, decisions_json, state, created_at, updated_at)
                VALUES (?, ?, 'target-v2', ?, '[]', '{}', 'pending', ?, ?)""",
                (self.id, source['generation_version'], source['revision'], stamp, stamp))
        self.assertEqual(generation_review.resolve_pending(), 1)
        after = self.transcript()
        self.assertEqual(after['generation_version'], 'target-v2')
        self.assertEqual(after['segments'][0]['person_id'], self.person['id'])


class SplitCarryOverTest(unittest.TestCase):
    """The "How it works" case: one confirmed passage re-cut into two sentences."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        self.id = self.client.post('/api/v1/conversations/import',
                                   files={'file': ('split.mp3', mp3_bytes(), 'audio/mpeg')}).json()['conversation']['id']
        stamp = datetime.now(timezone.utc).isoformat()
        source = [{'id': 's1', 'start_ms': 0, 'end_ms': 1000, 'text': 'So here is how it works. Every time an agent commits.',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []}]
        storage.save_transcript({'conversation_id': self.id, 'revision': 1, 'pipeline_version': 'source-v1',
                                 'language': 'en', 'segments_json': json.dumps(source), 'turns_json': '[]',
                                 'updated_at': stamp})
        self.person = self.client.post('/api/v1/persons', json={'name': 'Morgan'}).json()
        self.client.put(f'/api/v1/conversations/{self.id}/person-links', json={
            'person_id': self.person['id'], 'segment_id': 's1', 'scope': 'segment', 'expected_revision': 1})
        self.client.post(f'/api/v1/conversations/{self.id}/highlights', json={
            'segment_id': 's1', 'start_offset': 25, 'end_offset': 35, 'expected_revision': 1})
        target = [{'id': 't1', 'start_ms': 0, 'end_ms': 420, 'text': 'So here is how it works.',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []},
                  {'id': 't2', 'start_ms': 420, 'end_ms': 1000, 'text': 'Every time an agent commits.',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []}]
        self.record = {'conversation_id': self.id, 'pipeline_version': 'target-v2', 'language': 'en',
                       'segments_json': json.dumps(target), 'turns_json': '[]', 'updated_at': stamp}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_a_name_on_a_recut_passage_moves_instead_of_keeping_the_old_text(self):
        # New passages overrun the old one's edges, so neither lies 80% inside it.
        target = [{'id': 't1', 'start_ms': 0, 'end_ms': 700, 'text': 'So here is how it works. Every time',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []},
                  {'id': 't2', 'start_ms': 700, 'end_ms': 1300, 'text': 'an agent commits. And then more.',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []}]
        self.client.delete(f'/api/v1/conversations/{self.id}/highlights/{self.client.get(f"/api/v1/conversations/{self.id}/transcript").json()["highlights"][0]["id"]}')
        self.record['segments_json'] = json.dumps(target)
        self.assertEqual(generation_review.stage(self.record), 'applied')
        after = self.client.get(f'/api/v1/conversations/{self.id}/transcript').json()
        self.assertEqual([item['id'] for item in after['segments']], ['t1', 't2'])
        self.assertEqual({item['person_id'] for item in after['segments']}, {self.person['id']})

    def test_a_name_over_audio_with_no_new_speech_is_dropped_not_kept(self):
        # The old passage was a decoding loop over silence; the new transcript has nothing there.
        target = [{'id': 't9', 'start_ms': 2000, 'end_ms': 2600, 'text': 'Later words.',
                   'speaker_cluster_id': 'speaker-1', 'speaker_uncertain': False, 'words': []}]
        self.client.delete(f'/api/v1/conversations/{self.id}/highlights/{self.client.get(f"/api/v1/conversations/{self.id}/transcript").json()["highlights"][0]["id"]}')
        self.record['segments_json'] = json.dumps(target)
        self.assertEqual(generation_review.stage(self.record), 'applied')
        after = self.client.get(f'/api/v1/conversations/{self.id}/transcript').json()
        self.assertEqual([item['id'] for item in after['segments']], ['t9'])
        self.assertEqual(self.client.get(f'/api/v1/persons/{self.person["id"]}').json()['total_link_count'], 0)

    def test_confirmed_person_and_highlight_follow_the_split_without_asking(self):
        self.assertEqual(generation_review.stage(self.record), 'applied')
        after = self.client.get(f'/api/v1/conversations/{self.id}/transcript').json()
        self.assertEqual([item['id'] for item in after['segments']], ['t1', 't2'])
        self.assertEqual({item['person_id'] for item in after['segments']}, {self.person['id']})
        self.assertEqual(after['highlights'][-1]['segment_id'], 't2')
        self.assertEqual(after['highlights'][-1]['quote'], 'Every time')


if __name__ == '__main__':
    unittest.main()
