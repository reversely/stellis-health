"""Automatic names follow extraction, with source/identity races and reversible receipts."""
import io
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pymupdf
from PIL import Image
from fastapi.testclient import TestClient

from stellis import ai, document_naming as naming, document_ocr, documents, model_routes, runtime_profile, server, storage

TEXT='Metabolic panel results\nGlucose 92 mg/dL\nCreatinine 1.0 mg/dL'
TITLE='Metabolic panel results'


def pdf_bytes(text=TEXT):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((50,50),text)
        return pdf.tobytes()


def png_bytes():
    out=io.BytesIO();Image.new('RGB',(100,100),'white').save(out,format='PNG');return out.getvalue()


class NamingTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_DB_DIR':self.temp.name,'STELLIS_RUNTIME_PROFILE':'direct','STELLIS_AGENT_BACKEND':'direct','STELLIS_AUTH_MODE':'off'},clear=True)
        env.start();self.addCleanup(env.stop)
        self.key=patch.object(ai,'_key',return_value='unit-test-key');self.key.start();self.addCleanup(self.key.stop)

    def proposal(self,pages,**kwargs):
        return {'title':TITLE,'evidence':[{'page':1,'quote':'Metabolic panel results'}]}, {'model':'reported-nemotron-alias','usage':{'prompt_tokens':120,'completion_tokens':25},'elapsed_ms':50,'attempts':1}

    def image(self):
        return documents.create('7f34ae10.png',png_bytes())[0]

    def recognize(self,document):
        with patch.object(document_ocr.shutil,'which',return_value='tesseract'),patch.object(document_ocr,'_recognize',return_value=TEXT):
            return document_ocr.read_page(document['id'],1)

    def test_embedded_pdf_is_named_in_extraction_and_duplicate_reuses_it(self):
        data=pdf_bytes()
        with patch.object(naming,'infer',side_effect=self.proposal) as infer:
            record,duplicate=documents.create('a9c812.pdf',data)
            self.assertFalse(duplicate);self.assertEqual(record['title'],TITLE)
            self.assertEqual(record['title_source'],'automatic');self.assertTrue(record['naming']['can_undo'])
            self.assertEqual(record['naming']['model'],'reported-nemotron-alias')
            self.assertEqual(documents.list_all()[0]['title'],TITLE)
            self.assertEqual(documents.get(record['id'])['title'],TITLE)
            self.assertEqual(documents.file_path(record['id'])[0].read_bytes(),data)
            self.assertEqual(documents.create('another-random.pdf',data)[0]['title'],TITLE)
            naming.enrich(record['id']);self.assertEqual(infer.call_count,1)

    def test_ocr_names_in_same_stage_after_releasing_ocr_worker_slot(self):
        document=self.image()
        def infer(pages,**kwargs):
            self.assertFalse(document_ocr._LOCK.locked())
            return self.proposal(pages,**kwargs)
        with patch.object(naming,'infer',side_effect=infer):record=self.recognize(document)
        self.assertEqual(record['title'],TITLE);self.assertEqual(record['pages'][0]['text_source'],'ocr')
        self.assertEqual(documents.get(record['id'])['title'],TITLE)

    def test_manual_title_is_preserved_through_ocr_and_source_edits(self):
        document=self.image();documents.update(document['id'],title='My chosen name')
        with patch.object(naming,'infer') as infer:
            record=self.recognize(document);documents.save_page(document['id'],1,TEXT+'\nReviewed',1)
            self.assertEqual(record['title'],'My chosen name');infer.assert_not_called()

    def test_manual_rename_during_inference_wins(self):
        def infer(pages,**kwargs):
            documents.update(self.id,title='Keep my title');return self.proposal(pages,**kwargs)
        document=self.image();self.id=document['id']
        with patch.object(naming,'infer',side_effect=infer):record=self.recognize(document)
        self.assertEqual(record['title'],'Keep my title');self.assertEqual(record['naming']['state'],'conflict')

    def test_changed_source_rejects_stale_title(self):
        document=self.image()
        def infer(pages,**kwargs):
            documents.save_page(document['id'],1,'Different source text',1,source='ocr')
            return self.proposal(pages,**kwargs)
        with patch.object(naming,'infer',side_effect=infer):record=self.recognize(document)
        self.assertEqual(record['title'],document['title']);self.assertEqual(record['naming']['state'],'conflict')

    def test_failure_keeps_text_and_explicit_retry_is_a_new_retained_run(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=naming.NamingError('Naming unavailable')) as infer:
            record=self.recognize(document);naming.enrich(document['id']);self.assertEqual(infer.call_count,1)
        self.assertEqual(record['pages'][0]['text'],TEXT);self.assertEqual(record['title'],document['title']);self.assertTrue(record['naming']['can_retry'])
        with patch.object(naming,'infer',side_effect=self.proposal):record=naming.enrich(document['id'],retry=True)
        self.assertEqual(record['title'],TITLE)
        with storage.connect() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM document_title_runs').fetchone()[0],2)

    def test_undo_and_later_edits_are_protected(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=self.proposal):record=self.recognize(document)
        undone=naming.undo(document['id'],record['naming']['id'])
        self.assertEqual(undone['title'],document['title']);self.assertEqual(undone['title_source'],'manual')
        with self.assertRaises(documents.DocumentConflict):naming.undo(document['id'],record['naming']['id'])
        with patch.object(naming,'infer') as infer:
            documents.save_page(document['id'],1,TEXT+'\nReviewed',1);infer.assert_not_called()

    def test_undo_cannot_clobber_a_manual_rename(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=self.proposal):record=self.recognize(document)
        documents.update(document['id'],title='Manual replacement')
        with self.assertRaises(documents.DocumentConflict):naming.undo(document['id'],record['naming']['id'])
        self.assertEqual(documents.get(document['id'])['title'],'Manual replacement')

    def test_same_title_manual_write_is_also_protected(self):
        document=self.image()
        def infer(pages,**kwargs):
            documents.update(document['id'],title=document['title']);return self.proposal(pages,**kwargs)
        with patch.object(naming,'infer',side_effect=infer):record=self.recognize(document)
        self.assertEqual(record['title_source'],'manual');self.assertEqual(record['naming']['state'],'conflict')

    def test_empty_ocr_abstains_without_request(self):
        document=self.image()
        with patch.object(naming,'infer') as infer,patch.object(document_ocr.shutil,'which',return_value='tesseract'),patch.object(document_ocr,'_recognize',return_value=''):
            record=document_ocr.read_page(document['id'],1);infer.assert_not_called()
        self.assertEqual(record['title'],document['title']);self.assertIsNone(record['naming'])

    def test_concurrent_same_source_uses_one_request(self):
        document=self.image();documents.save_page(document['id'],1,TEXT,0,source='ocr')
        entered=threading.Event();release=threading.Event()
        def infer(pages,**kwargs):
            entered.set();self.assertTrue(release.wait(5));return self.proposal(pages,**kwargs)
        with patch.object(naming,'infer',side_effect=infer) as request,ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(naming.enrich,document['id']);self.assertTrue(entered.wait(5))
            second=pool.submit(naming.enrich,document['id']);self.assertEqual(second.result()['naming']['state'],'pending')
            release.set();self.assertEqual(first.result()['title'],TITLE);self.assertEqual(request.call_count,1)

    def test_legacy_titles_migrate_as_manual(self):
        document=self.image()
        with storage.connect() as conn:
            conn.execute('ALTER TABLE documents DROP COLUMN title_source');conn.execute('ALTER TABLE documents DROP COLUMN title_revision')
        with patch.object(naming,'infer') as infer:
            record=naming.enrich(document['id']);infer.assert_not_called()
        self.assertEqual(record['title_source'],'manual')

    def test_evidence_validation_and_name_date_formatting(self):
        pages=[{'page':1,'text':'Lab report\nPatient: Jane Example, born 1980-01-01\nLaboratory results'}]
        result=naming.validate({'title':'Laboratory results for Jane Example 2026-04-02','evidence':[{'page':1,'quote':'Laboratory results'}]},pages)
        self.assertEqual(result['title'],'Laboratory results')
        with self.assertRaises(naming.NamingError):naming.validate({'title':'Laboratory results','evidence':[{'page':1,'quote':'An invented quote'}]},pages)
        with self.assertRaises(naming.NamingError):naming.infer(pages,model='other/model',key='test')
        with self.assertRaises(ValueError):model_routes.validate({'naming':'other/model'})

    def test_undo_and_retry_endpoints(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=self.proposal):record=self.recognize(document)
        with TestClient(server.app) as client:
            r=client.post(f"/api/v1/documents/{record['id']}/title/undo",json={'run_id':record['naming']['id']});self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['title'],document['title'])
            self.assertEqual(client.post(f"/api/v1/documents/{record['id']}/title/undo",json={'run_id':record['naming']['id']}).status_code,409)
            self.assertEqual(client.post('/api/v1/documents/missing/title/retry').status_code,404)

    def test_delete_removes_title_provenance(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=self.proposal):record=self.recognize(document)
        documents.delete(record['id'])
        with storage.connect() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM document_title_runs').fetchone()[0],0)

    def test_reference_only_output_abstains_and_test_name_is_not_patient_name(self):
        pages=[{'page':1,'text':'Reference: AB-123\nNo contextual content.'}]
        self.assertIsNone(naming.decode({'title':'Reference AB-123','evidence_ids':['1']},pages)['title'])
        pages=[{'page':1,'text':'Test Name: Metabolic panel\nLaboratory results'}]
        self.assertEqual(naming.normalize_title('Metabolic panel results',pages),'Metabolic panel results')
        self.assertEqual(runtime_profile.model_id('naming'),'nvidia/Nemotron-3_5-Lightning')

    def test_old_undo_remains_available_after_later_naming_failure(self):
        document=self.image()
        with patch.object(naming,'infer',side_effect=self.proposal):record=self.recognize(document)
        receipt=record['naming']['undo_id']
        with patch.object(naming,'infer',side_effect=naming.NamingError('Temporary failure')):
            updated=documents.save_page(document['id'],1,TEXT+'\nNew wording',1)
        self.assertTrue(updated['naming']['can_undo']);self.assertTrue(updated['naming']['can_retry'])
        self.assertEqual(updated['naming']['undo_id'],receipt)
        undone=naming.undo(document['id'],receipt)
        self.assertFalse(undone['naming']['can_retry']);self.assertEqual(undone['title'],document['title'])
