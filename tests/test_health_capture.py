"""Reviewed device readings and dated care events across shared projections."""
import io
import os
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from stellis import care_events, documents, notebook_health, notebooks, photo_readings, readings, storage, timeline
from stellis.server import app


class CandidateTest(unittest.TestCase):
    def test_labelled_units_pairs_and_multiple_candidates(self):
        for measure,text,expected in [
            ('weight','Scale 180 lb',180),
            ('blood_pressure','SYS 120\nDIA 80',120),
            ('blood_glucose','5.5 mmol/L',5.5),
            ('temperature','98.6 °F',98.6),
        ]:
            with self.subTest(measure=measure):
                result=photo_readings.candidates(text,measure)
                self.assertEqual(result[0]['value'],expected)
        self.assertEqual(len(photo_readings.candidates('70 kg 71 kg 70 kg','weight')),2)
        self.assertEqual(photo_readings.normalize('weight',180,'lb'),81.6)
        self.assertEqual(photo_readings.normalize('blood_glucose',5.5,'mmol/L'),99.1)
        self.assertEqual(photo_readings.normalize('temperature',98.6,'°F'),37)

    def test_missing_units_invalid_pairs_and_partial_numbers_are_ignored(self):
        for measure,text in [('weight','70'),('weight','12345 kg'),('blood_pressure','80/120'),
                             ('temperature','1000 °F'),('blood_glucose','12345 mg/dL')]:
            self.assertEqual(photo_readings.candidates(text,measure),[],text)
        with self.assertRaises(ValueError):photo_readings.normalize('weight',70,'mmHg')


class CaptureApiTest(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        env=patch.dict(os.environ,{'STELLIS_DATA_DIR':folder.name,'STELLIS_AUTH_MODE':'open'});env.start();self.addCleanup(env.stop)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.book=notebooks.create('My readings')
        image=io.BytesIO();Image.new('RGB',(300,100),'white').save(image,format='PNG')
        self.document,_=documents.create('scale.png',image.getvalue(),self.book['id'])
        page=self.document['pages'][0]
        documents.save_page(self.document['id'],1,'180 lb',page['revision'])

    def photo(self,**overrides):
        data={'document_id':self.document['id'],'measure':'weight','value':180,'unit':'lb',
              'taken_at':'2026-01-02T12:00:00+00:00','notebook_id':self.book['id'],'capture_id':'capture-one'}
        data.update(overrides)
        return self.client.post('/api/v1/photo-readings',json=data)

    def test_review_does_not_save_and_confirmation_persists_once(self):
        response=self.client.post('/api/v1/photo-readings/'+self.document['id']+'/extract?measure=weight')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['candidates'][0]['value'],180)
        self.assertEqual(readings.stored_points(),[])
        first=self.photo(value=182);self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['value'],82.6)
        again=self.photo(value=182);self.assertEqual(first.json()['id'],again.json()['id'])
        self.assertEqual(self.photo(value=183).status_code,400)
        self.assertEqual(self.photo(value=182,notebook_id=None).status_code,400)
        context=notebook_health.context(self.book['id'])
        self.assertEqual(context['measurements'][0]['value'],82.6)
        self.assertEqual(context['measurements'][0]['target'],'/document/'+self.document['id'])
        self.assertEqual(len([e for e in timeline.entries(self.book['id']) if e['kind']=='reading']),1)

    def test_candidate_extraction_reuses_ocr_without_saving_a_reading(self):
        document = documents.get(self.document['id'])
        page = documents.save_page(document['id'],1,'',document['pages'][0]['revision'])
        def recognized(*args):
            return documents.save_page(document['id'],1,'180 lb',page['pages'][0]['revision'],source='ocr')
        with patch('stellis.document_ocr.read_page',side_effect=recognized) as read:
            response = self.client.post('/api/v1/photo-readings/'+document['id']+'/extract?measure=weight')
        read.assert_called_once_with(document['id'],1)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['candidates'][0]['value'],180)
        self.assertEqual(readings.stored_points(),[])

    def test_validation_and_manual_entry_context(self):
        for changes in [{'unit':'invalid'},{'notebook_id':'missing'},{'value':-1},{'document_id':'missing'},
                        {'taken_at':'2099-01-01T12:00:00Z'}]:
            self.assertIn(self.photo(**changes).status_code,(400,404),changes)
        response=self.client.post('/api/v1/readings',json={'measure':'temperature','value':37,'notebook_id':self.book['id']})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(readings.stored_points(self.book['id'])[0]['value'],37)
        self.assertEqual(readings.stored_points('missing'),[])

    def test_source_deletion_retains_confirmed_value(self):
        self.assertEqual(self.photo().status_code,200)
        documents.delete(self.document['id'])
        point=readings.stored_points()[0]
        self.assertEqual(point['value'],81.6)
        self.assertIsNone(point['document_id'])

    def test_events_persist_in_schedule_history_and_notebook(self):
        payload={'title':'<script>appointment</script>','starts_at':'2027-01-02T10:00:00+05:00',
                 'status':'planned','notebook_id':self.book['id'],'note':'Bring the readings'}
        response=self.client.post('/api/v1/care-events',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        event=response.json();self.assertEqual(event['starts_at'],'2027-01-02T05:00:00+00:00')
        self.assertEqual(care_events.list_all()[0]['title'],payload['title'])
        context=notebook_health.context(self.book['id'])
        self.assertIn('care_schedule',context['active_features'])
        self.assertEqual(context['care_schedule'][0]['id'],event['id'])
        self.assertTrue(any(e['kind']=='event' for e in timeline.entries(self.book['id'])))
        for changes in [{'status':'booked'},{'starts_at':'2027-01-02T10:00'},{'notebook_id':'missing'}]:
            self.assertEqual(self.client.post('/api/v1/care-events',json={**payload,**changes}).status_code,400)
        self.assertEqual(self.client.delete('/api/v1/care-events/'+event['id']).status_code,200)
        self.assertEqual(care_events.list_all(),[])

    def test_existing_reading_schema_migrates(self):
        with storage.connect() as conn:
            conn.execute('''CREATE TABLE readings (id TEXT PRIMARY KEY, measure TEXT,value REAL,value2 REAL,
              taken_at TEXT,source TEXT,quality REAL,note TEXT,created_at TEXT)''')
            conn.execute("INSERT INTO readings VALUES ('old','weight',70,NULL,'2026-01-01','self',NULL,'','2026-01-01')")
        self.assertEqual(readings.stored_points()[0]['value'],70)
        self.assertIsNone(readings.stored_points()[0]['document_id'])
