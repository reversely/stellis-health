"""Actual workspace snapshots and durable brief projection without provider doubles."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import agents, daily_brief, notebooks, pages, storage


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.tmp.name},clear=True)
        self.env.start();self.addCleanup(self.tmp.cleanup);self.addCleanup(self.env.stop)

    def test_snapshot_excludes_completed_and_trashed_sources(self):
        book=notebooks.create('Review')['id']
        pages.create('Work',book,'- [ ] Check the notes\n- [x] Already done')
        deleted=pages.create('Deleted',book,'- [ ] Do not include this')
        pages.move_to_trash(deleted['id'])
        sources,coverage=asyncio.run(daily_brief.snapshot())
        self.assertEqual(coverage['tasks'],1)
        self.assertFalse(coverage['calendar_connected'])
        payload=json.loads(sources[0]['excerpt'])
        self.assertEqual(payload['items'][0]['text'],'Check the notes')
        self.assertNotIn('Already done',sources[0]['excerpt'])

    def test_empty_and_bounded_coverage_are_explicit(self):
        self.assertEqual(daily_brief.status()['state'],'empty')
        for n in range(22):pages.create(f'Note {n}',markdown=f'- [ ] Action {n}')
        sources,coverage=asyncio.run(daily_brief.snapshot())
        self.assertEqual(coverage['tasks'],22)
        self.assertEqual(len(json.loads(sources[0]['excerpt'])['items']),20)
        self.assertEqual(coverage['notebooks'],0)

    def test_failed_refresh_retains_last_success_and_restart_interrupts(self):
        chief=next(a for a in agents.list_agents() if a['is_default'])
        first=agents.start_job(chief['id'])
        message=agents.append_message(chief['id'],'agent','Prior successful brief',job_id=first)
        agents.finish_job(first,'succeeded')
        second=agents.start_job(chief['id'])
        with storage.connect() as conn:
            conn.execute('INSERT INTO daily_brief(id,job_id,message_id,coverage_json) VALUES(1,?,?,?)',(second,message['id'],json.dumps({'tasks':1})))
        agents.finish_job(second,'failed','Retry')
        current=daily_brief.status()
        self.assertEqual(current['state'],'failed');self.assertEqual(current['brief']['id'],message['id'])
        third=agents.start_job(chief['id'])
        with storage.connect() as conn:conn.execute('UPDATE daily_brief SET job_id=? WHERE id=1',(third,))
        with self.assertRaises(ValueError):daily_brief.start()
        agents.interrupt_running_jobs()
        self.assertEqual(daily_brief.status()['state'],'interrupted')
        self.assertEqual(daily_brief.status()['brief']['id'],message['id'])

    def test_unavailable_calendar_cannot_be_reported_as_empty(self):
        unavailable={'calendar_connected':False,'warnings':[]}
        self.assertIsNotNone(daily_brief.validate_answer('No events are scheduled for the next 24 hours.',unavailable))
        self.assertIsNone(daily_brief.validate_answer('Calendar data is unavailable; the schedule is unknown.',unavailable))
        self.assertIsNone(daily_brief.validate_answer('No events are scheduled.',{'calendar_connected':True,'warnings':[]}))
