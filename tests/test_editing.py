"""Browser API checks for reversible text, speaker, and vocabulary corrections."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import processing, storage
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class EditingWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        imported = self.client.post("/api/v1/conversations/import", files={"file": ("talk.mp3", mp3_bytes(), "audio/mpeg")})
        self.conversation_id = imported.json()["conversation"]["id"]
        self.base = f"/api/v1/conversations/{self.conversation_id}"
        stamp = datetime.now(timezone.utc).isoformat()
        segments = [
            {"id": "segment-1", "start_ms": 0, "end_ms": 500, "text": "Asteriod launch.",
             "speaker_cluster_id": "speaker-1", "speaker_uncertain": False, "provenance": "on_device_asr",
             "words": [{"text":"Asteriod", "start_offset":0, "end_offset":8, "start_ms":0, "end_ms":300,
                        "confidence":.8, "provenance":"whisper_cpp_token_v1"}]},
            {"id": "segment-2", "start_ms": 500, "end_ms": 1000, "text": "It was ready.",
             "speaker_cluster_id": "speaker-1", "speaker_uncertain": False, "provenance": "on_device_asr", "words": []},
        ]
        storage.save_transcript({"conversation_id": self.conversation_id, "revision": 1,
                                 "language": "en", "segments_json": json.dumps(segments),
                                 "turns_json": json.dumps([]), "updated_at": stamp})

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_word_edit_history_conflict_and_restore(self) -> None:
        endpoint = f"{self.base}/transcript/segments/segment-1"
        changed = self.client.patch(endpoint, json={"text": "Asteroid launch.", "expected_revision": 1})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["revision"], 2)
        result = self.client.get(f"{self.base}/transcript").json()
        self.assertEqual(result["segments"][0]["text"], "Asteroid launch.")
        self.assertEqual(result["segments"][0]["original_text"], "Asteriod launch.")
        self.assertEqual(result["segments"][0]["text_provenance"], "user_edit")
        self.assertEqual(result["segments"][0]["words"], [])
        conflict = self.client.patch(endpoint, json={"text": "wrong", "expected_revision": 1})
        self.assertEqual(conflict.status_code, 409)
        restored = self.client.patch(endpoint, json={"text": "Asteriod launch.", "expected_revision": 2})
        self.assertEqual(restored.json()["revision"], 3)
        self.assertNotIn("original_text", self.client.get(f"{self.base}/transcript").json()["segments"][0])
        self.assertEqual(len(self.client.get(f"{self.base}/transcript").json()["segments"][0]["words"]), 1)
        with storage.connect() as conn:
            events = conn.execute("SELECT old_text, new_text FROM transcript_edit_events ORDER BY revision").fetchall()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["old_text"], "Asteriod launch.")
        self.assertEqual(events[1]["new_text"], "Asteriod launch.")

    def test_selected_comment_persists_and_marks_changed_source(self) -> None:
        endpoint = f'{self.base}/comments'
        invalid = self.client.post(endpoint, json={'segment_id':'segment-1','start_offset':0,
            'end_offset':8,'body':'Check this', 'expected_revision':2})
        self.assertEqual(invalid.status_code,409)
        self.assertEqual(storage.list_comments(self.conversation_id), [])
        saved = self.client.post(endpoint, json={'segment_id':'segment-1','start_offset':0,
            'end_offset':8,'body':' Check this ', 'expected_revision':1})
        self.assertEqual(saved.status_code,200,saved.text)
        comment = saved.json()
        self.assertEqual((comment['quote'],comment['body'],comment['start_ms']),('Asteriod','Check this',0))
        with TestClient(app) as reloaded:
            self.assertEqual(reloaded.get(f'{self.base}/transcript').json()['comments'][0]['id'],comment['id'])
        self.client.patch(f'{self.base}/transcript/segments/segment-1',json={
            'text':'Asteroid launch.','expected_revision':1})
        projected = self.client.get(f'{self.base}/transcript').json()
        self.assertEqual(projected['comments'][0]['quote'],'Asteriod')
        self.assertNotEqual(projected['segments'][0]['text'][:8], projected['comments'][0]['quote'])
        changed = self.client.patch(f"{endpoint}/{comment['id']}",json={'body':'Reviewed against audio'})
        self.assertEqual(changed.json()['body'],'Reviewed against audio')
        with storage.connect() as conn:
            row = conn.execute('SELECT quote,body FROM transcript_comments WHERE id=?',(comment['id'],)).fetchone()
        self.assertEqual(tuple(row),('Asteriod','Reviewed against audio'))
        self.assertEqual(self.client.delete(f"{endpoint}/{comment['id']}").status_code,200)
        self.assertEqual(self.client.get(f'{self.base}/transcript').json()['comments'],[])

    def test_batch_word_edits_are_atomic_and_keep_source_text(self) -> None:
        endpoint = f"{self.base}/transcript/segments"
        changed = self.client.patch(endpoint, json={"expected_revision": 1, "edits": [
            {"segment_id": "segment-1", "text": "Asteroid launch."},
            {"segment_id": "segment-2", "text": "It was ready to go."},
        ]})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["revision"], 2)
        result = self.client.get(f"{self.base}/transcript").json()
        self.assertEqual([item["text"] for item in result["segments"]], ["Asteroid launch.", "It was ready to go."])
        self.assertEqual([item["original_text"] for item in result["segments"]], ["Asteriod launch.", "It was ready."])
        with storage.connect() as conn:
            events = conn.execute("SELECT revision FROM transcript_edit_events").fetchall()
        self.assertEqual([item["revision"] for item in events], [2, 2])
        conflict = self.client.patch(endpoint, json={"expected_revision": 1, "edits": [
            {"segment_id": "segment-1", "text": "Uncommitted"}]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.client.get(f"{self.base}/transcript").json()["segments"][0]["text"], "Asteroid launch.")
        invalid = self.client.patch(endpoint, json={"expected_revision": 2, "edits": [
            {"segment_id": "segment-1", "text": "Another edit"},
            {"segment_id": "missing", "text": "No source"}]})
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(self.client.get(f"{self.base}/transcript").json()["revision"], 2)

    def test_group_tag_preserves_a_different_confirmed_person(self) -> None:
        first = self.client.post('/api/v1/persons', json={'name': 'Riley'}).json()
        second = self.client.post('/api/v1/persons', json={'name': 'Devon'}).json()
        endpoint = f'{self.base}/person-links'
        self.assertEqual(self.client.put(endpoint, json={
            'person_id': first['id'], 'segment_id': 'segment-2',
            'scope': 'segment', 'expected_revision': 1}).status_code, 200)
        tagged = self.client.put(endpoint, json={
            'person_id': second['id'], 'segment_id': 'segment-1',
            'scope': 'cluster', 'expected_revision': 1})
        self.assertEqual(tagged.status_code, 200)
        self.assertEqual(tagged.json()['preserved_other_people'], 1)
        segments = self.client.get(f'{self.base}/transcript').json()['segments']
        self.assertEqual([item['speaker_display_name'] for item in segments], ['Devon', 'Riley'])
        with storage.connect() as conn:
            links = conn.execute('SELECT segment_id, person_id FROM person_links WHERE conversation_id = ? ORDER BY segment_id',
                                 (self.conversation_id,)).fetchall()
        self.assertEqual([tuple(row) for row in links], [('segment-1', second['id']), ('segment-2', first['id'])])
        with TestClient(app) as reloaded:
            names = [item['speaker_display_name'] for item in
                     reloaded.get(f'{self.base}/transcript').json()['segments']]
        self.assertEqual(names, ['Devon', 'Riley'])

    def test_new_speaker_tag_is_atomic_on_revision_conflict(self) -> None:
        endpoint = f'{self.base}/person-links'
        failed = self.client.put(endpoint, json={
            'name': 'Devon', 'segment_id': 'segment-1', 'scope': 'cluster', 'expected_revision': 2})
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(self.client.get('/api/v1/persons').json(), [])
        tagged = self.client.put(endpoint, json={
            'name': 'Devon', 'segment_id': 'segment-1', 'scope': 'cluster', 'expected_revision': 1})
        self.assertEqual(tagged.status_code, 200)
        self.assertEqual(tagged.json()['confirmed_passages'], 2)
        self.assertEqual([item['speaker_display_name'] for item in
                          self.client.get(f'{self.base}/transcript').json()['segments']], ['Devon', 'Devon'])

    def test_repeated_legacy_labels_become_one_saved_person(self) -> None:
        segments = [{"id": f"devon-{i}", "start_ms": i * 1000, "end_ms": (i + 1) * 1000,
                     "text": "Hello.", "speaker_cluster_id": f"cluster-{i}", "words": []}
                    for i in range(74)]
        segments.append({"id": "other", "start_ms": 74000, "end_ms": 75000,
                         "text": "Other.", "speaker_cluster_id": "other", "words": []})
        storage.save_transcript({"conversation_id": self.conversation_id, "revision": 2,
                                 "language": "en", "segments_json": json.dumps(segments),
                                 "turns_json": "[]", "updated_at": datetime.now(timezone.utc).isoformat()})
        riley = self.client.post('/api/v1/persons', json={'name': 'Riley'}).json()
        self.assertEqual(self.client.put(f'{self.base}/person-links', json={
            'person_id': riley['id'], 'segment_id': 'other', 'scope': 'segment',
            'expected_revision': 2}).status_code, 200)
        with storage.connect() as conn:
            conn.executemany('''INSERT INTO speaker_labels (conversation_id, scope, target_id, name, updated_at)
                VALUES (?, 'segment', ?, 'Devon Hart', ?)''',
                [(self.conversation_id, f'devon-{i}', datetime.now(timezone.utc).isoformat()) for i in range(74)])
        response = self.client.put(f'{self.base}/person-links', json={
            'name': '  Devon   Hart ', 'segment_id': 'devon-0', 'scope': 'segment',
            'expected_revision': 2})
        self.assertEqual(response.status_code, 200, response.text)
        person_id = response.json()['person_id']
        self.assertEqual(response.json()['promoted_labels'], 74)
        again = self.client.put(f'{self.base}/person-links', json={
            'name': 'devon hart', 'segment_id': 'devon-1', 'scope': 'segment',
            'expected_revision': 2})
        self.assertEqual(again.json()['person_id'], person_id)
        self.assertEqual(self.client.post('/api/v1/persons', json={'name': 'DEVON HART'}).json()['id'], person_id)
        with storage.connect() as conn:
            people = conn.execute('SELECT id, name, name_key FROM persons ORDER BY name').fetchall()
            links = conn.execute('SELECT person_id, COUNT(*) AS n FROM person_links WHERE conversation_id = ? GROUP BY person_id',
                                 (self.conversation_id,)).fetchall()
            labels = conn.execute('SELECT * FROM speaker_labels WHERE conversation_id = ?',
                                  (self.conversation_id,)).fetchall()
        self.assertEqual(len(people), 2)
        self.assertEqual({row['name_key'] for row in people}, {'devon hart', 'riley'})
        with self.assertRaises(sqlite3.IntegrityError):
            with storage.connect() as conn:
                conn.execute('''INSERT INTO persons (id, name, name_key, created_at, updated_at)
                    VALUES ('duplicate-devon', 'DEVON HART', 'devon hart', 'now', 'now')''')
        with self.assertRaises(sqlite3.IntegrityError):
            with storage.connect() as conn:
                conn.execute('''INSERT INTO persons (id, name, created_at, updated_at)
                    VALUES ('missing-key', 'Devon Hart', 'now', 'now')''')
        self.assertEqual({row['person_id']: row['n'] for row in links}, {person_id: 74, riley['id']: 1})
        self.assertEqual(labels, [])
        with TestClient(app) as reloaded:
            transcript = reloaded.get(f'{self.base}/transcript').json()
            self.assertEqual({item['person_id'] for item in transcript['segments'][:74]}, {person_id})
            self.assertEqual({item['speaker_display_name'] for item in transcript['segments'][:74]}, {'Devon Hart'})
            self.assertEqual(transcript['segments'][-1]['person_id'], riley['id'])
        conflict = self.client.patch(f'/api/v1/persons/{riley["id"]}', json={'name': ' Devon Hart '})
        self.assertEqual(conflict.status_code, 400)

    def test_promoting_cluster_name_respects_passage_override(self) -> None:
        labels = f'{self.base}/speakers/label'
        self.client.put(labels, json={'scope':'cluster', 'target_id':'speaker-1',
                                      'name':'Devon', 'expected_revision':1})
        self.client.put(labels, json={'scope':'segment', 'target_id':'segment-2',
                                      'name':'Sam', 'expected_revision':2})
        result = self.client.put(f'{self.base}/person-links', json={
            'name':'Devon', 'segment_id':'segment-1', 'scope':'segment', 'expected_revision':3})
        self.assertEqual(result.status_code, 200, result.text)
        display = self.client.get(f'{self.base}/transcript').json()['segments']
        self.assertEqual([item['speaker_display_name'] for item in display], ['Devon', 'Sam'])
        self.assertIsNotNone(display[0]['person_id'])
        self.assertIsNone(display[1].get('person_id'))

    def test_hiding_person_only_changes_tag_suggestions(self) -> None:
        person = self.client.post('/api/v1/persons', json={'name':'Devon'}).json()
        self.client.put(f'{self.base}/person-links', json={
            'person_id':person['id'], 'segment_id':'segment-1',
            'scope':'segment', 'expected_revision':1})
        hidden = self.client.patch(f'/api/v1/persons/{person["id"]}/visibility', json={'hidden':True})
        self.assertEqual(hidden.status_code, 200)
        self.assertTrue(next(item for item in self.client.get('/api/v1/persons').json()
                             if item['id'] == person['id'])['hidden_at'])
        self.assertEqual(self.client.get(f'{self.base}/transcript').json()['segments'][0]['speaker_display_name'], 'Devon')
        self.assertEqual(self.client.get(f'/api/v1/persons/{person["id"]}').json()['total_link_count'], 1)
        self.client.patch(f'/api/v1/persons/{person["id"]}/visibility', json={'hidden':False})
        self.assertIsNone(next(item for item in self.client.get('/api/v1/persons').json()
                               if item['id'] == person['id'])['hidden_at'])

    def test_cluster_label_and_segment_override_are_reversible(self) -> None:
        endpoint = f"{self.base}/speakers/label"
        cluster = self.client.put(endpoint, json={"scope":"cluster","target_id":"speaker-1","name":"Ari","expected_revision":1})
        self.assertEqual(cluster.json()["revision"], 2)
        labels = [item["speaker_display_name"] for item in self.client.get(f"{self.base}/transcript").json()["segments"]]
        self.assertEqual(labels, ["Ari", "Ari"])
        single = self.client.put(endpoint, json={"scope":"segment","target_id":"segment-2","name":"Sam","expected_revision":2})
        self.assertEqual(single.json()["revision"], 3)
        result = self.client.get(f"{self.base}/transcript").json()
        self.assertEqual([item["speaker_display_name"] for item in result["segments"]], ["Ari", "Sam"])
        self.assertEqual(result["segments"][1]["speaker_cluster_id"], "speaker-1")
        with storage.connect() as conn:
            persisted = conn.execute("SELECT scope, target_id, name FROM speaker_labels WHERE conversation_id = ? ORDER BY scope, target_id",
                                     (self.conversation_id,)).fetchall()
        self.assertEqual({tuple(row) for row in persisted}, {
            ("cluster", "speaker-1", "Ari"), ("segment", "segment-2", "Sam")})
        with TestClient(app) as reloaded:
            names = [item["speaker_display_name"] for item in
                     reloaded.get(f"{self.base}/transcript").json()["segments"]]
        self.assertEqual(names, ["Ari", "Sam"])
        cleared = self.client.put(endpoint, json={"scope":"segment","target_id":"segment-2","name":None,"expected_revision":3})
        self.assertEqual(cleared.json()["revision"], 4)
        labels = [item["speaker_display_name"] for item in self.client.get(f"{self.base}/transcript").json()["segments"]]
        self.assertEqual(labels, ["Ari", "Ari"])
        with storage.connect() as conn:
            self.assertIsNone(conn.execute("SELECT name FROM speaker_labels WHERE conversation_id = ? AND scope = 'segment' AND target_id = 'segment-2'",
                                           (self.conversation_id,)).fetchone())

    def test_vocabulary_changes_only_future_job_snapshot(self) -> None:
        existing = processing.request_processing(self.conversation_id)
        first = self.client.post("/api/v1/vocabulary", json={"term":"ORBIT"})
        self.assertEqual(first.status_code, 200)
        term = first.json()
        self.assertEqual(self.client.get("/api/v1/vocabulary").json()[0]["term"], "ORBIT")
        self.assertEqual(self.client.get(f"{self.base}/transcript").json()["revision"], 1)
        later = processing.request_processing(self.conversation_id)
        self.assertNotEqual(later["id"], existing["id"])
        self.assertEqual(json.loads(later["vocabulary_json"]), ["ORBIT"])
        self.assertEqual(self.client.get(f"{self.base}/processing-job").json()["id"], later["id"])
        renamed = self.client.patch(f"/api/v1/vocabulary/{term['id']}", json={"term":"ORBIT Alliance"})
        self.assertEqual(renamed.json()["term"], "ORBIT Alliance")
        self.assertEqual(self.client.delete(f"/api/v1/vocabulary/{term['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/vocabulary").json(), [])

    def test_reprocessing_retains_prior_machine_generation(self) -> None:
        original = self.client.get(f"{self.base}/transcript").json()
        version = original["generation_version"]
        new_segments = [{**original["segments"][0], "text": "Asteroid launch.",
                         "id": "new-segment"}]
        storage.save_transcript({"conversation_id": self.conversation_id, "revision": 1,
                                 "pipeline_version": "new-model-with-vocabulary", "language": "en",
                                 "segments_json": json.dumps(new_segments), "turns_json": json.dumps([]),
                                 "updated_at": datetime.now(timezone.utc).isoformat()})
        active = self.client.get(f"{self.base}/transcript").json()
        self.assertEqual(active["revision"], 2)
        self.assertEqual(active["generation_version"], "new-model-with-vocabulary")
        generations = self.client.get(f"{self.base}/transcript/generations").json()
        self.assertEqual(len(generations), 2)
        prior = self.client.get(f"{self.base}/transcript/generations/{version}").json()
        self.assertEqual(prior["segments"][0]["text"], "Asteriod launch.")

    def test_selected_highlight_persists_and_can_be_removed(self) -> None:
        endpoint = f"{self.base}/highlights"
        created = self.client.post(endpoint, json={"segment_id":"segment-1", "start_offset":0,
                                                   "end_offset":8, "expected_revision":1})
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["quote"], "Asteriod")
        self.assertEqual(len(self.client.get(f"{self.base}/transcript").json()["highlights"]), 1)
        invalid = self.client.post(endpoint, json={"segment_id":"segment-1", "start_offset":0,
                                                   "end_offset":500, "expected_revision":1})
        self.assertEqual(invalid.status_code, 400)
        changed = self.client.patch(f"{self.base}/transcript/segments/segment-1",
                                    json={"text":"Asteroid launch.", "expected_revision":1})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/transcript").json()["highlights"][0]["quote"], "Asteriod")
        removed = self.client.delete(f"{endpoint}/{created.json()['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get(f"{self.base}/transcript").json()["highlights"], [])

    def test_search_uses_current_edits_labels_and_multiple_conversations(self) -> None:
        first = self.client.get('/api/v1/search', params={'q': 'Asteriod'}).json()
        self.assertEqual(first['total'], 1)
        self.assertEqual(first['results'][0]['segment_id'], 'segment-1')
        self.client.patch(f'{self.base}/transcript/segments/segment-1',
                          json={'text': 'Asteroid launch.', 'expected_revision': 1})
        self.assertEqual(self.client.get('/api/v1/search', params={'q': 'Asteriod'}).json()['total'], 0)
        self.assertEqual(self.client.get('/api/v1/search', params={'q': 'Asteroid'}).json()['results'][0]['revision'], 2)
        self.client.put(f'{self.base}/speakers/label', json={
            'scope': 'cluster', 'target_id': 'speaker-1', 'name': 'Ari', 'expected_revision': 2})
        speaker = self.client.get('/api/v1/search', params={'q': 'Ari'}).json()
        self.assertEqual([(item['kind'], item['speaker']) for item in speaker['results']], [('speaker', 'Ari')])
        self.assertEqual(speaker['results'][0]['revision'], 3)
        second_audio = mp3_bytes(wav_bytes()[:-2] + b'\x01\x00')
        imported = self.client.post('/api/v1/conversations/import',
                                    files={'file': ('second.mp3', second_audio, 'audio/mpeg')})
        second_id = imported.json()['conversation']['id']
        stamp = datetime.now(timezone.utc).isoformat()
        storage.save_transcript({'conversation_id': second_id, 'revision': 1, 'language': 'en',
                                 'segments_json': json.dumps([{'id': 'second-segment', 'start_ms': 0,
                                     'end_ms': 500, 'text': 'Asteroid ready.', 'speaker_cluster_id': None,
                                     'speaker_uncertain': True, 'words': []}]),
                                 'turns_json': '[]', 'updated_at': stamp})
        results = self.client.get('/api/v1/search', params={'q': 'Asteroid'}).json()['results']
        self.assertEqual({item['conversation_id'] for item in results}, {self.conversation_id, second_id})
        self.assertTrue(next(item for item in results if item['conversation_id'] == second_id)['speaker_uncertain'])
        replacement = [{'id': 'second-new', 'start_ms': 0, 'end_ms': 500,
                        'text': 'Orbit ready.', 'speaker_cluster_id': None,
                        'speaker_uncertain': True, 'words': []}]
        storage.save_transcript({'conversation_id': second_id, 'revision': 1,
                                 'pipeline_version': 'replacement-generation', 'language': 'en',
                                 'segments_json': json.dumps(replacement), 'turns_json': '[]',
                                 'updated_at': stamp})
        refreshed = self.client.get('/api/v1/search', params={'q': 'Asteroid'}).json()['results']
        self.assertEqual({item['conversation_id'] for item in refreshed}, {self.conversation_id})
        self.assertEqual(self.client.get('/api/v1/search', params={'q': 'Orbit'}).json()['results'][0]['segment_id'],
                         'second-new')


if __name__ == "__main__":
    unittest.main()
