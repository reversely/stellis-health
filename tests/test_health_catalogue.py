"""Catalogue integrity, user authority and shared source projections."""
import asyncio
import sample_records
import os
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from stellis import health_catalogue as catalogue, storage, notebooks, collections, pages, intake, notebook_sources, source_search, auth, onboarding
from stellis.server import app


class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_MODE':'open'})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.env.stop(); self.temp.cleanup()

    def selected(self, tid='fitness--fitness'):
        return catalogue.activate([{'template_id':tid}])[0]

    def test_configuration_covers_every_collection_with_complete_templates(self):
        config = catalogue.configuration()
        self.assertEqual(len(config['collections']),32)
        self.assertEqual(len({c['collection_id'] for c in config['collections']}),32)
        self.assertEqual(len({t['template_id'] for t in config['templates']}),len(config['templates']))
        for c in config['collections']:
            for key in ('collection_id','display_name','group','description','primary_scope','inclusion_criteria','overlap_rules','clinical_concepts'):
                self.assertTrue(c[key],(c,key))
            self.assertTrue(any(t['collection_id']==c['collection_id'] for t in config['templates']))
        for t in config['templates']:
            for key in ('template_id','collection_id','display_name','description','inclusion_rules','exclusion_rules','clinical_concepts','supported_features','catalogue_version'):
                self.assertTrue(t[key],(t,key))
        self.assertEqual(config['source_url'],'https://medlineplus.gov/healthtopics.html')

    def test_families_hold_several_notebooks_inside_one_collection(self):
        config = catalogue.configuration()
        self.assertGreater(len(config['templates']),len(config['collections']))
        for family in config['collections']:
            self.assertTrue(catalogue.family_templates(family['collection_id']))
        self.assertIn('Strength Training',{t['display_name'] for t in config['templates']})
        books = catalogue.activate([{'template_id':'fitness--mobility'}, {'template_id':'fitness--strength-training'}])
        self.assertNotEqual(books[0]['id'],books[1]['id'])
        self.assertEqual({b['name'] for b in notebooks.list_notebooks()},{'Mobility','Strength Training'})
        from stellis import collections
        family = [c for c in collections.list_collections() if c['taxonomy_id'] == 'fitness']
        self.assertEqual(len(family),1)
        self.assertEqual(family[0]['name'],'Fitness')
        self.assertEqual(set(family[0]['notebook_ids']),{b['id'] for b in books})

    def test_existing_retired_templates_keep_identity_title_and_sources(self):
        book = notebooks.create('My heart plan')
        note = pages.create('Existing notes',book['id'],'Retain this source')
        with storage.connect() as conn:
            conn.execute('UPDATE notebooks SET template_id=? WHERE id=?',('heart-circulation--heart-circulation',book['id']))
        before=notebooks.list_notebooks()
        view={t['template_id']:t for t in catalogue.view()['templates']}
        self.assertEqual(view['heart-circulation--heart-care']['notebook_id'],book['id'])
        selected=self.selected('heart-circulation--heart-care')
        self.assertEqual(selected['id'],book['id'])
        self.assertEqual(notebooks.list_notebooks(),before)
        self.assertEqual(pages.get(note['id'])['notebook_id'],book['id'])

    def test_selection_is_atomic_idempotent_and_preserves_user_title(self):
        with self.assertRaises(ValueError):
            catalogue.activate([{'template_id':'fitness--fitness'},{'template_id':'invented'}])
        self.assertEqual(notebooks.list_notebooks(),[])
        self.assertEqual(collections.list_collections(),[])
        book=self.selected(); notebooks.update(book['id'],name='My training',description='My plan')
        self.assertEqual(self.selected()['id'],book['id'])
        saved=notebooks.list_notebooks()[0]
        self.assertEqual((saved['name'],saved['description']),('My training','My plan'))
        notebooks.update(book['id'],archived=True)
        self.assertEqual(notebooks.list_notebooks(),[])
        self.assertEqual(catalogue.view()['archived'][0]['id'],book['id'])
        notebooks.update(book['id'],archived=False)
        self.assertEqual(len(notebooks.list_notebooks()),1)
        collections.assign_notebook(book['id'],'nutrition')
        self.selected()
        self.assertEqual(notebooks.list_notebooks()[0]['collection_id'],'nutrition')
        with self.assertRaises(ValueError): collections.update('fitness',name='Sport')

    def test_selection_adopts_matching_legacy_notebook_and_preserves_records(self):
        book = notebooks.create('Fitness')
        note = pages.create('Training log', book['id'], 'A saved training plan')
        selected = self.selected()
        self.assertEqual(selected['id'], book['id'])
        saved = notebooks.list_notebooks()[0]
        self.assertEqual(saved['template_id'], 'fitness--fitness')
        self.assertEqual(saved['collection_id'], 'fitness')
        self.assertEqual(pages.get(note['id'])['notebook_id'], book['id'])
        self.assertEqual(len(notebooks.list_notebooks()), 1)

    def test_custom_identity_and_source_survive_archive_reassignment_and_deletion(self):
        first=catalogue.activate([{'name':'My <script>plan</script>','description':'Literal text','collection_id':'fitness'}])[0]
        second=self.selected('nutrition--nutrition')
        note=pages.create('Shared note',first['id'],'Ferritin 32 and a nutrition plan')
        notebook_sources.link(second['id'],'page:'+note['id'])
        notebook_sources.link(second['id'],'page:'+note['id'])
        self.assertEqual(len(notebook_sources.list_sources(second['id'])),1)
        self.assertEqual(pages.list_pages(second['id'])[0]['id'],note['id'])
        self.assertEqual(source_search.search_sources('Ferritin',second['id'])[0]['source_id'],note['id'])
        self.assertEqual(pages.get(note['id'])['notebook_id'],first['id'])
        self.assertEqual(len(pages.list_pages()),1)
        notebooks.delete(second['id'])
        self.assertEqual(pages.get(note['id'])['markdown'],'Ferritin 32 and a nutrition plan')
        with self.assertRaises(LookupError): notebook_sources.link(first['id'],'page:unknown')

    def test_recommendations_are_evidence_backed_and_history_does_not_activate(self):
        intake.save_notes([{'heading':'Goals','items':['Manage migraine and prepare for pregnancy']},
                           {'heading':'Conditions','items':['History of asthma','No diabetes','Possible thyroid condition']}],0)
        with patch('stellis.agent_gateway.turn',side_effect=AssertionError('No provider call')):
            result=asyncio.run(intake.propose_notebooks())
        ids={g['template_id'] for g in result['groups']}
        self.assertIn('neurology-cognitive-health--migraine-management',ids)
        self.assertIn('pregnancy-postpartum--pregnancy',ids)
        self.assertFalse(any('asthma' in x or 'diabetes' in x or 'thyroid' in x for x in ids))
        self.assertEqual(notebooks.list_notebooks(),[])
        for g in result['groups']:
            self.assertEqual(g['notebook_name'],catalogue.template(g['template_id'])['display_name'])
            self.assertEqual(g['evidence'][0]['quote'],'Manage migraine and prepare for pregnancy')

    def test_ordered_blood_tests_propose_blood_health(self):
        # The setup call's own words: no provider call, and the blood tests point to Blood Health.
        intake.save_notes([{'heading':'Conditions','items':["I've been tired since December and my GP ordered blood tests"]}],0)
        with patch('stellis.agent_gateway.turn',side_effect=AssertionError('No provider call')):
            result=asyncio.run(intake.propose_notebooks())
        self.assertIn('blood-health--blood-health',{g['template_id'] for g in result['groups']})

    def test_onboarding_creates_only_reviewed_registered_notebooks(self):
        auth.register('catalogue-test','correct horse battery staple')
        account=auth.account_id_for_username('catalogue-test')
        onboarding.choose(account,[],'review')
        result=onboarding.complete(account,record_groups=[{'template_id':'fitness--fitness','notebook_name':'Invented model title','items':[]}])
        self.assertEqual(result['created'][0]['name'],'Fitness')
        self.assertEqual(onboarding.complete(account),result)

    def test_shared_document_retains_fact_identity_chat_and_timeline(self):
        from stellis import documents, health_facts, notebook_chat, timeline
        from pathlib import Path
        first=self.selected(); second=self.selected('nutrition--nutrition')
        photo=(sample_records.FOLDER / '2025-03-23-lab-results-photo.png').read_bytes()
        doc,_=documents.create('Lab photo.png',photo)
        documents.save_page(doc['id'],1,'Ferritin 32 ng/mL',0)
        notebook_sources.link(first['id'],'document:'+doc['id'])
        notebook_sources.link(second['id'],'document:'+doc['id'])
        with storage.connect() as conn:
            health_facts._create_table(conn)
            conn.execute("INSERT INTO health_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",('fact-test',doc['id'],1,'lab','Ferritin','32','ng/mL','2025-03-23','Ferritin 32 ng/mL','accepted','2025-03-23','2025-03-23'))
        self.assertEqual(len(health_facts.accepted()),1)
        for book in [first,second]:
            sources=notebook_sources.list_sources(book['id'])
            passages=notebook_chat.read_passages(sources)
            self.assertTrue(any('Ferritin 32' in p.text for p in passages))
            context=self.client.get('/api/v1/notebooks/'+book['id']+'/health').json()
            self.assertEqual(context['facts'][0]['id'],'fact-test')
            self.assertTrue(any(i['kind']=='fact' for i in timeline.entries(book['id'])))
        self.assertEqual(len(documents.list_all()),1)

    def test_review_choices_survive_reload_and_invalid_capabilities_are_rejected(self):
        auth.register('review-test','correct horse battery staple')
        account=auth.account_id_for_username('review-test')
        choices=[{'template_id':'fitness--fitness','notebook_name':'Fitness','items':[],'include':False}]
        onboarding.choose(account,[],'review',notebook_choices=choices)
        self.assertEqual(onboarding.status(account)['notebook_choices'],choices)
        book=self.selected()
        response=self.client.patch('/api/v1/notebooks/'+book['id'],json={'capabilities':['invented']})
        self.assertEqual(response.status_code,400)
        notebooks.update(book['id'],capabilities=['timeline'])
        self.assertEqual(self.client.get('/api/v1/notebooks/'+book['id']+'/health').json()['requested_features'],['sources','notes','chat','search','timeline'])

    def test_invalid_ids_and_links_are_rejected_at_api_boundary(self):
        self.assertEqual(self.client.post('/api/v1/health-catalogue/activate',json={'selections':[{'template_id':'invented'}]}).status_code,400)
        book=self.selected()
        self.assertEqual(self.client.post('/api/v1/notebooks/'+book['id']+'/links',json={'source_id':'page:unknown'}).status_code,404)
        self.assertEqual(self.client.patch('/api/v1/notebooks/'+book['id'],json={'archived':'yes'}).status_code,422)

    def test_separate_accounts_do_not_share_selections_or_links(self):
        book=self.selected()
        note=pages.create('Private note',book['id'],'Private')
        with tempfile.TemporaryDirectory() as other, patch.dict(os.environ,{'STELLIS_DATA_DIR':other}):
            self.assertEqual(notebooks.list_notebooks(),[])
            fresh=self.selected()
            with self.assertRaises(LookupError): notebook_sources.link(fresh['id'],'page:'+note['id'])


class SetupFilingTest(unittest.TestCase):
    """Setup files records by what they are, even when the call named none of their areas (#273)."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_MODE':'open'})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def records(self, *titles):
        from stellis import sorting
        items = [sorting.Item('document', str(n), title, '2026-10-01') for n, title in enumerate(titles, 1)]
        return patch.object(sorting, 'unsorted', return_value=items)

    def test_titles_file_records_without_any_answers(self):
        titles = ('Tissue Transglutaminase Ab Panel Results', 'Allergy Testing Results', 'Urinalysis Report',
                  'Lab Result Report', 'Routine Chemistry and Hematology Profile')
        with self.records(*titles):
            result = catalogue.recommend()
        filed = {i['title']: g['notebook_name'] for g in result['groups'] for i in g['items']}
        self.assertEqual(filed, {titles[0]: 'Digestive Health', titles[1]: 'Allergy Management', titles[2]: 'Kidney Care',
                                 titles[3]: 'Bloodwork', titles[4]: 'Bloodwork'})
        self.assertEqual(result['unsorted'], [])

    def test_stellis_files_what_no_title_names(self):
        from stellis import agent_gateway
        reply = '{"groups": [{"family": "Blood Health", "notebook": "Bloodwork", "reason": "Blood results", "items": [1]}]}'
        with self.records('Allergy Testing Results', 'Results from March'), \
                patch.object(agent_gateway, 'turn', return_value=(reply, 's')) as turn:
            result = asyncio.run(intake.propose_notebooks())
        filed = {i['title']: g['notebook_name'] for g in result['groups'] for i in g['items']}
        self.assertEqual(filed, {'Allergy Testing Results': 'Allergy Management', 'Results from March': 'Bloodwork'})
        self.assertIn('Results from March', turn.call_args.args[0])
        self.assertNotIn('Allergy Testing Results', turn.call_args.args[0].split('"records"')[1])

    def test_records_stay_not_filed_when_stellis_is_unavailable(self):
        from stellis import agent_gateway
        with self.records('Results from March'), \
                patch.object(agent_gateway, 'turn', side_effect=agent_gateway.GatewayError('down')):
            result = asyncio.run(intake.propose_notebooks())
        self.assertEqual([i['title'] for i in result['unsorted']], ['Results from March'])
