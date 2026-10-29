"""First-run persistence and reviewed setup, without provider doubles."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stellis import agents, auth, collections, notebooks, onboarding, pages

class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.tmp.name,'STELLIS_AUTH_DB_DIR':self.tmp.name,'STELLIS_AUTH_MODE':'required'},clear=True)
        self.env.start()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def account(self):
        auth.register('learner','correct horse battery staple');return auth.account_id_for_username('learner')
    def test_review_creates_once_and_preserves_changes_on_repeat(self):
        account=self.account()
        self.assertTrue(auth.needs_onboarding(account))
        with self.assertRaises(ValueError):onboarding.complete(account)
        onboarding.choose(account,['learning','meetings'],'review')
        self.assertEqual(notebooks.list_notebooks(),[])
        first=onboarding.complete(account)
        self.assertFalse(auth.needs_onboarding(account))
        self.assertEqual(len(notebooks.list_notebooks()),2)
        self.assertEqual(len(collections.list_collections()),2)
        book=next(b for b in notebooks.list_notebooks() if b['name']=='Learning')
        self.assertEqual(pages.list_pages(book['id']), [])
        self.assertEqual([a['name'] for a in agents.list_agents()],['Stellis'])
        self.assertIn(book['id'],[n['notebook_id'] for n in agents.assistant()['notebooks']])
        self.assertEqual(onboarding.complete(account),first)
    def test_empty_setup_and_invalid_choices(self):
        account=self.account()
        with self.assertRaises(ValueError):onboarding.choose(account,['invented'],'review')
        onboarding.choose(account,[],'review');onboarding.complete(account)
        self.assertEqual(notebooks.list_notebooks(),[])
        self.assertEqual(agents.list_agents()[0]['name'],'Stellis')

    def test_completed_summary_is_readonly_and_reloadable_without_reopening_setup(self):
        from fastapi.testclient import TestClient
        from stellis.server import app
        account = self.account()
        onboarding.choose(account, ['learning'], 'review')
        created = onboarding.complete(account)['created']
        client = TestClient(app, follow_redirects=False)
        self.addCleanup(client.close)
        client.cookies.set(auth.COOKIE_NAME, auth.login('learner','correct horse battery staple'))
        self.assertEqual(client.get('/onboarding').status_code,303)
        response = client.get('/onboarding?finished=1')
        self.assertEqual(response.status_code,200)
        self.assertIn('onboarding-meet.js',response.text)
        self.assertEqual(onboarding.status(account)['created'],created)
        self.assertFalse(onboarding.available(account))
        client.cookies.clear()
        self.assertEqual(client.get('/onboarding?finished=1').headers['location'],'/auth')
    def test_preexisting_accounts_are_not_sent_back_to_setup(self):
        path=Path(self.tmp.name)/'auth.sqlite3'
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE account (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, salt BLOB NOT NULL, verifier BLOB NOT NULL, created_at TEXT NOT NULL)')
            conn.execute("INSERT INTO account VALUES(1,'existing',X'01',X'01','2026-01-01')")
        self.assertFalse(auth.needs_onboarding(1))
        self.assertTrue(auth.needs_onboarding(self.account()))

    def test_reopen_is_additive_and_repeated_completion_is_idempotent(self):
        account=self.account();onboarding.choose(account,['learning'],'review');first=onboarding.complete(account)
        old_id=first['created'][0]['notebook_id']
        state=onboarding.reopen(account)
        self.assertTrue(state['required']);self.assertFalse(auth.needs_onboarding(account))
        self.assertEqual(state['uses'],[])
        onboarding.choose(account,['family','slides'],'review')
        second=onboarding.complete(account)
        self.assertEqual(len(notebooks.list_notebooks()),3)
        self.assertTrue(any(book['id']==old_id for book in notebooks.list_notebooks()))
        self.assertFalse(onboarding.status(account)['required'])
        self.assertEqual(onboarding.complete(account),second)

    def test_task_choices_survive_back_and_preserve_existing_policy(self):
        account=self.account()
        onboarding.choose(account,['projects','learning'],'review',task_choices={'projects':True,'learning':False})
        onboarding.choose(account,['projects','learning'],'uses')
        state=onboarding.choose(account,['projects','learning'],'review')
        self.assertEqual(state['task_choices'],{'projects':True,'learning':False})
        onboarding.complete(account)
        books={b['name']:b for b in notebooks.list_notebooks()}
        self.assertEqual(books['Projects']['todo_list'],'on')
        self.assertEqual(books['Learning']['todo_list'],'off')
        onboarding.reopen(account)
        self.assertEqual(onboarding.status(account)['task_choices'],{})
        self.assertTrue(onboarding.status(account)['task_defaults']['projects'])
        onboarding.choose(account,['projects'],'review')
        onboarding.complete(account)
        self.assertEqual(next(b for b in notebooks.list_notebooks() if b['id']==books['Projects']['id'])['todo_list'],'on')
        onboarding.reopen(account)
        onboarding.choose(account,['projects'],'review',task_choices={'projects':False})
        onboarding.complete(account)
        self.assertEqual(next(b for b in notebooks.list_notebooks() if b['id']==books['Projects']['id'])['todo_list'],'off')

    def test_invalid_task_choice_does_not_change_setup(self):
        account=self.account()
        with self.assertRaises(ValueError):
            onboarding.choose(account,['projects'],'review',task_choices={'projects':'false'})
        self.assertEqual(onboarding.status(account)['step'],'welcome')

    def test_untouched_policy_keeps_collection_inheritance(self):
        account=self.account()
        group=collections.create('Work');collections.update(group['id'],todo_list='on')
        state=onboarding.choose(account,['projects'],'review')
        self.assertTrue(state['collection_task_defaults']['work'])
        onboarding.complete(account)
        book=notebooks.list_notebooks()[0]
        self.assertEqual(book['todo_list'],'inherit')
        self.assertTrue(onboarding.status(account)['task_defaults']['projects'])


class RecordSetupTest(unittest.TestCase):
    """Setup files reviewed record groups into notebooks where Stellis answers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STELLIS_DATA_DIR': self.tmp.name, 'STELLIS_AUTH_DB_DIR': self.tmp.name,
                                           'STELLIS_AUTH_MODE': 'required'}, clear=True)
        self.env.start()
        auth.register('denver', 'correct horse battery staple')
        self.account = auth.account_id_for_username('denver')

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_record_groups_become_notebooks_where_stellis_answers(self):
        onboarding.choose(self.account, [], 'review')
        result = onboarding.complete(self.account, record_groups=[
            {'notebook_name': 'Diabetes care', 'collection_id':'hormonal-metabolic-health', 'items': [], 'suggest_tasks': True},
            {'notebook_name': 'Physiotherapy', 'collection_id':'fitness', 'items': [], 'suggest_tasks': False},
        ])
        self.assertFalse(auth.needs_onboarding(self.account))
        diabetes = next(b for b in result['created'] if b['name'] == 'Diabetes care')
        self.assertIn(diabetes['notebook_id'], [n['notebook_id'] for n in agents.assistant()['notebooks']])
        self.assertIn('Hormonal & Metabolic Health', [c['name'] for c in collections.list_collections()])
        book = next(b for b in notebooks.list_notebooks() if b['name'] == 'Diabetes care')
        self.assertTrue(book['todo_enabled'])

    def test_record_groups_are_checked_before_anything_is_created(self):
        onboarding.choose(self.account, [], 'review')
        with self.assertRaises(ValueError):
            onboarding.complete(self.account, record_groups=[{'notebook_name': str(i)} for i in range(6)])
        self.assertEqual(notebooks.list_notebooks(), [])
        self.assertTrue(auth.needs_onboarding(self.account))
