"""Quoted follow-ups reuse real suggestion persistence and apply-once task writes."""
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import agents, agent_artifacts, agent_followups, notebooks, pages, storage, suggestions, todos


class FollowupTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name},clear=True);self.env.start()
        self.book=notebooks.create('Project');notebooks.update(self.book['id'],todo_list='on')
        self.agent=agents.assistant()
        self.page=pages.create('Evidence',self.book['id'],'A signed approval record is still missing.')
        self.source={'source_id':self.page['id'],'title':'Evidence','excerpt':self.page['markdown'],'target':'/page/'+self.page['id']}
        self.candidate=agent_followups.validate({'text':'Obtain the signed approval record','source_index':1,'quote':self.page['markdown']},[self.source])
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def artifact(self):
        job=agents.start_job(self.agent['id'],self.book['id'])
        message=agents.append_message(self.agent['id'],'agent','Draft ready',self.book['id'],job)
        artifact=agent_artifacts.create(self.agent['id'],message['id'],self.book['id'],'Review','Approval is missing.',[self.source],self.candidate)
        return job,artifact
    def test_quotes_must_match_real_selected_source(self):
        for value in ({'text':'Obtain approval','source_index':2,'quote':self.page['markdown']},
                      {'text':'Obtain approval','source_index':1,'quote':'Approval has been received.'},
                      {'text':'Create a signed approval record','source_index':1,'quote':self.page['markdown']}):
            with self.assertRaises(ValueError):agent_followups.validate(value,[self.source])
    def test_success_publishes_once_and_accepts_edited_task_once(self):
        job,artifact=self.artifact()
        self.assertIsNone(agent_artifacts.get(artifact['id'])['follow_up'])
        agents.finish_job(job,'succeeded');agents.finish_job(job,'succeeded')
        proposal=agent_artifacts.get(artifact['id'])['follow_up']
        self.assertEqual(len(suggestions.list_pending(notebook_id=self.book['id'])),1)
        suggestions.amend(proposal['id'],{'text':'Request the missing signed approval'})
        suggestions.accept([proposal['id']]);suggestions.accept([proposal['id']])
        self.assertEqual(agent_artifacts.get(artifact['id'])['follow_up']['state'],'accepted')
        with storage.connect() as conn:
            rows=conn.execute('SELECT text FROM todo_items').fetchall()
        self.assertEqual([r['text'] for r in rows],['Request the missing signed approval'])
    def test_failed_and_off_policy_work_publish_no_proposal(self):
        failed,_=self.artifact();agents.finish_job(failed,'failed')
        stopped,_=self.artifact();agents.finish_job(stopped,'interrupted')
        disabled,_=self.artifact();notebooks.update(self.book['id'],todo_list='off');agents.finish_job(disabled,'succeeded')
        self.assertEqual(suggestions.list_pending(notebook_id=self.book['id']),[])
        self.assertEqual(len(pages.list_pages(self.book['id'])),1)
