"""Real local document drafts: persistence, revisions, filing and account isolation."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import agent_artifacts as drafts, agents, agent_runtime, storage

class ArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name},clear=True);self.env.start()
        self.agent=agents.assistant();self.message=agents.append_message(self.agent['id'],'agent','Draft ready')
        self.draft=drafts.create(self.agent['id'],self.message['id'],None,'Plan','First paragraph.\n\nSecond paragraph.',[])
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def test_edits_preserve_paragraphs_reject_stale_and_discard_keeps_record(self):
        changed=drafts.edit(self.draft['id'],1,'Edited','A\n\nB');self.assertEqual(changed['revision'],2);self.assertEqual(drafts.get(changed['id'])['body'],'A\n\nB')
        with self.assertRaises(ValueError):drafts.edit(changed['id'],1,'Stale','Old')
        discarded=drafts.discard(changed['id'],2);self.assertEqual(discarded['state'],'discarded')
        with self.assertRaises(ValueError):drafts.edit(changed['id'],3,'Cannot edit','Body')
    def test_mail_requests_become_document_drafts(self):
        parsed=drafts.parse(json.dumps({'title':'Hello','body':'Draft','recipients':['someone@example.com']}),'Draft a reply to someone@example.com',[])
        self.assertNotIn('recipients',parsed)
        self.assertEqual(drafts.kind_for('Could you please draft a reply?'),'document')
        self.assertNotIn('recipients',drafts.get(self.draft['id']))
        prompt=agent_runtime._prompt(self.agent,[],[],'Draft an email to reader@example.com',[])
        self.assertNotIn('recipients',prompt)
    def test_removed_mail_tables_are_dropped(self):
        with storage.connect() as conn:conn.execute('CREATE TABLE mail_style_examples (cleaned_body TEXT)')
        with storage.connect() as conn:
            tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & {'mail_style_examples','mail_rules','agent_delegations'})
    def test_account_isolation(self):
        with tempfile.TemporaryDirectory() as other,patch.dict(os.environ,{'STELLIS_DATA_DIR':other}):
            with self.assertRaises(LookupError):drafts.get(self.draft['id'])
    def test_artifact_requests_have_complete_output_contract(self):
        prompt=agent_runtime._prompt(self.agent,[],[],'Draft an email to reader@example.com',[])
        self.assertIn('complete draft',prompt);self.assertNotIn('at most 130 words',prompt)
        self.assertEqual(drafts.kind_for('Write questions for the walkthrough'),'document')
        self.assertIsNone(drafts.kind_for('What changed?'))

    def test_document_files_once_and_keeps_paragraphs(self):
        from stellis import notebooks, pages
        book=notebooks.create('Learning')
        draft=drafts.create(self.agent['id'],None,book['id'],'Practice questions','One?\n\nTwo?',[])
        filed=drafts.save_note(draft['id'],1)
        self.assertEqual(filed['state'],'filed')
        self.assertEqual(pages.get(draft['id'])['markdown'],'One?\n\nTwo?')
        self.assertEqual(drafts.save_note(draft['id'],1)['id'],draft['id'])
        self.assertEqual(len(pages.list_pages(book['id'])),1)
