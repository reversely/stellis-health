"""Proposal validation and local filing boundaries; no simulated external providers."""
import os
import tempfile
import unittest
from unittest.mock import patch
from stellis import auth, notebooks, onboarding, onboarding_sources, pages

class ProposalTest(unittest.TestCase):
    def test_ids_are_grounded_unique_and_names_bounded(self):
        sources=[{'id':'note:1'},{'id':'note:2'}]
        valid={'groups':[{'name':'Learning','collection':'Personal','source_ids':['note:1']}]}
        self.assertEqual(onboarding_sources.validate(valid,sources)[0]['source_ids'],['note:1'])
        for ids in (['invented'],['note:1','note:1'],[{}]):
            with self.assertRaises(ValueError):onboarding_sources.validate({'groups':[{'name':'Learning','collection':'Personal','source_ids':ids}]},sources)
        with self.assertRaises(ValueError):onboarding_sources.validate({'groups':valid['groups']*2},sources)
        self.assertEqual(onboarding_sources.validate({'groups':[]},sources),[])
        with self.assertRaises(ValueError):onboarding_sources.validate(valid,[{'id':'note:1','excluded':True}])
    def test_review_is_required_and_retry_does_not_duplicate_sources(self):
        with tempfile.TemporaryDirectory() as data,patch.dict(os.environ,{'STELLIS_DATA_DIR':data,'STELLIS_AUTH_DB_DIR':data},clear=True):
            auth.register('reader','long enough password');account=auth.account_id_for_username('reader');onboarding.choose(account,[],'review')
            source={'id':'note:1','kind':'Project note','title':'Learning plan','date':'2026-10-20','excerpt':'Learn sketching','url':''}
            state=onboarding._read();state['scan']={'id':'scan-1','state':'ready','sources':[source],'groups':[{'id':'group-1','name':'Learning','collection':'Personal','source_ids':['note:1']}]};onboarding._save(state)
            with self.assertRaises(ValueError):onboarding.complete(account,'old-scan',['group-1'])
            self.assertEqual(notebooks.list_notebooks(),[])
            self.assertTrue(auth.needs_onboarding(account))
            onboarding_sources.select(account,'scan-1',[])
            self.assertEqual(onboarding_sources.status()['selected_group_ids'],[])
            onboarding.choose(account,[],'review',task_choices={'group-1':True})
            onboarding.complete(account,'scan-1',['group-1']);book=notebooks.list_notebooks()[0]
            self.assertEqual(book['todo_list'],'on')
            onboarding_sources.file_sources(book['id'],[source])
            self.assertEqual(len(pages.list_pages(book['id'])),1)
            self.assertIn('Source reference: note:1',pages.get(pages.list_pages(book['id'])[0]['id'])['markdown'])
    def test_missing_connection_and_interrupted_state_are_honest(self):
        with tempfile.TemporaryDirectory() as data,patch.dict(os.environ,{'STELLIS_DATA_DIR':data,'STELLIS_AUTH_DB_DIR':data},clear=True):
            auth.register('reader','long enough password');account=auth.account_id_for_username('reader')
            with self.assertRaises(ValueError):onboarding_sources.start(account,30)
            state=onboarding._read();state['scan']={'id':'abandoned','state':'reading'};onboarding._save(state)
            self.assertEqual(onboarding_sources.status()['state'],'interrupted')
