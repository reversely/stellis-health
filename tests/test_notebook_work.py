"""Persisted notebook policies, scoped work records and approval boundaries."""
import os,tempfile,unittest
from unittest.mock import patch
from stellis import notebook_work,notebooks,pages,agents,agent_artifacts,agent_runtime,suggestions

class NotebookWorkTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name},clear=True);self.env.start()
        self.book=notebooks.create('Practice');self.other=notebooks.create('Other')
        notebooks.update(self.book['id'],todo_list='on')
        self.agent=agents.assistant()
        self.page=pages.create('Practice notes',notebook_id=self.book['id'],markdown='Practice a constrained sketch.')
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_policy_revision_and_scoped_prompt(self):
        notebook_work.save_instructions(self.book['id'],'Ask before changing a plan.',0)
        with self.assertRaises(ValueError):notebook_work.save_instructions(self.book['id'],'Stale',0)
        self.assertIn('Ask before changing a plan.',agent_runtime._prompt(self.agent,[],[],'Help',[],self.book['id']))
        self.assertNotIn('Ask before changing a plan.',agent_runtime._prompt(self.agent,[],[],'Help',[],self.other['id']))
        with tempfile.TemporaryDirectory() as other,patch.dict(os.environ,{'STELLIS_DATA_DIR':other}):
            with self.assertRaises(LookupError):notebook_work.instructions(self.book['id'])
    def test_approval_counts_and_draft_job_deduplication(self):
        proposed=suggestions.propose('Tutor','page',self.page['id'],'add_todo',{'text':'Practice a constrained sketch'})
        self.assertEqual(notebook_work.activity(self.book['id'])['counts']['needs_you'],1)
        suggestions.accept([proposed['id']]);suggestions.accept([proposed['id']])
        self.assertEqual(pages.get(self.page['id'])['markdown'].count('Practice a constrained sketch'),2)
        self.assertEqual(notebook_work.activity(self.book['id'])['counts']['needs_you'],0)
        job=agents.start_job(self.agent['id'],self.book['id']);self.assertEqual(notebook_work.activity(self.book['id'])['counts']['working'],1)
        message=agents.append_message(self.agent['id'],'agent','Draft ready',self.book['id'],job)
        draft=agent_artifacts.create(self.agent['id'],message['id'],self.book['id'],'Practice plan','Use constraints',[])
        agents.finish_job(job,'succeeded');work=notebook_work.activity(self.book['id'])
        self.assertEqual(len(work['items']),1);self.assertEqual(work['items'][0]['artifact']['id'],draft['id'])
        self.assertEqual(notebook_work.activity(self.other['id'])['items'],[])
        agent_artifacts.discard(draft['id'],1);self.assertEqual(notebook_work.activity(self.book['id'])['counts']['needs_you'],0)
