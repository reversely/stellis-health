"""Real report ingestion, review, source proof and persisted measurement projection (#187)."""
import io
import sample_records
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pymupdf
from fastapi.testclient import TestClient
from stellis import demo_patient, documents, health_facts, readings, report_context, server

REPORT = next((demo_patient.STATIC / 'documents').rglob('D002*'))

class ReportMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_MODE':'open'});env.start();self.addCleanup(env.stop)
        self.client=TestClient(server.app);self.addCleanup(self.client.close)
    def upload(self, name, content):
        r=self.client.post('/api/v1/documents',files={'file':(name,content)});self.assertEqual(r.status_code,200,r.text)
        return r.json()['document']
    def test_upload_review_projection_reload_dismiss_and_delete(self):
        record=self.upload(REPORT.name, REPORT.read_bytes());base=f'/api/v1/documents/{record["id"]}'
        facts=self.client.get(base+'/facts').json()['facts'];self.assertEqual(len(facts),6)
        self.assertEqual(len(health_facts.accepted()),0)
        ferritin=next(f for f in facts if f['name']=='Ferritin')
        self.assertEqual((ferritin['value'],ferritin['date'],ferritin['quote']),('11','2027-01-21','Ferritin 11 ug/L 15-150 L'))
        mapped=report_context.page_map(record['id'],1)
        self.assertTrue(any(r['text']==ferritin['quote'] for r in mapped['regions']))
        self.client.delete('/api/v1/facts/'+ferritin['id'])
        for _ in range(2):self.assertEqual(len(self.client.get(base+'/facts').json()['facts']),5)
        self.assertEqual(self.client.post(base+'/facts/accept').json()['accepted'],5)
        measures=self.client.get('/api/v1/readings').json()['measures'];hgb=next(m for m in measures if m['key']=='hemoglobin')
        self.assertEqual(hgb['points'][0]['value'],124)
        self.assertIn(record['id'],hgb['points'][0]['target'])
        self.assertEqual(self.client.post(base+'/facts/accept').json()['accepted'],0)
        self.assertEqual(len(self.client.get(base+'/facts').json()['facts']),5)
        self.client.delete(base)
        self.assertFalse(any(m['points'] for m in self.client.get('/api/v1/readings').json()['measures']))

    def test_only_reviewed_results_feed_the_source_linked_checkin_agenda(self):
        from stellis import coach_call
        record=self.upload(REPORT.name,REPORT.read_bytes())
        before=coach_call.agenda('2027-02-02',{'goals':[]});self.assertEqual(before,[])
        fact=next(f for f in health_facts.list_for(record['id']) if f['name']=='Ferritin')
        health_facts.accept(fact['id'])
        agenda=coach_call.agenda('2027-02-02',{'goals':[]})
        self.assertEqual(agenda[0]['title'],'Review ferritin 11 ug/L')
        self.assertIn(record['id'],agenda[0]['target'])
        self.assertEqual(coach_call.agenda('2027-01-20',{'goals':[]}),[])

    def test_synthea_labels_keep_original_values_and_project_only_after_approval(self):
        source = (sample_records.FOLDER / '2026-04-02-lab-results.pdf')
        record = self.upload(source.name, source.read_bytes())
        base = f'/api/v1/documents/{record["id"]}'
        self.client.patch(base, json={'record_date': '2026-04-02'})
        facts = self.client.get(base+'/facts').json()['facts']
        self.assertEqual(len(facts), 20)
        glucose = next(f for f in facts if f['name']=='Glucose [Mass/volume] in Blood')
        self.assertEqual((glucose['value'], glucose['unit'], glucose['date']), ('83.5','mg/dL','2026-04-02'))
        self.assertEqual(glucose['quote'], 'Glucose [Mass/volume] in Blood: 83.5 mg/dL')
        self.assertTrue(any('1.73 sq M' in f['name'] for f in facts))
        self.assertEqual(health_facts.accepted(), [])
        self.assertFalse(any(m['points'] for m in readings.series()))
        receipt = self.client.post(base+'/facts/accept').json()
        self.assertEqual(receipt['accepted'], 20)
        accepted = self.client.get(base+'/facts').json()['facts']
        self.assertTrue(all(f['status']=='accepted' and f['accepted_at'] for f in accepted))
        series = next(m for m in readings.series() if m['label']==glucose['name'])
        self.assertEqual(series['points'][0]['value'], 83.5)
        self.assertIn(record['id'], series['points'][0]['target'])
        self.assertEqual(self.client.post(base+'/facts/accept').json()['accepted'], 0)
        self.client.delete('/api/v1/facts/'+glucose['id'])
        self.assertFalse(any(m['label']==glucose['name'] and m['points'] for m in readings.series()))

    def test_label_digits_are_not_values_and_known_units_remain_strict(self):
        from stellis.report_measurements import proposals
        text = ('Vitamin B12 [Mass/volume] in Serum: 400 pg/mL\n'
                'Glucose [Mass/volume] in Blood: 83.5 mg/dL\n'
                'Hemoglobin 12.4 g/dL\nVisit 2026-04-02\n'
                'Follow-up in 12 days\nGlucose [Mass/volume] in Blood: 84.5 mg/dL')
        facts = proposals({'pages':[{'number':1,'text':text}]}, '2026-04-02')
        self.assertEqual([(f['name'],f['value']) for f in facts], [('Vitamin B12 [Mass/volume] in Serum','400')])

    def test_parser_upgrade_revisits_an_existing_empty_receipt(self):
        source = (sample_records.FOLDER / '2026-04-02-lab-results.pdf')
        with patch('stellis.report_measurements.VERSION', 1), patch('stellis.report_measurements.proposals', return_value=[]):
            record = self.upload(source.name, source.read_bytes())
            base = f'/api/v1/documents/{record["id"]}'
            self.client.patch(base, json={'record_date':'2026-04-02'})
            self.assertEqual(self.client.get(base+'/facts').json()['facts'], [])
        self.assertEqual(len(self.client.get(base+'/facts').json()['facts']), 20)
        self.assertEqual(health_facts.accepted(), [])
    def test_real_scanned_page_recognition_proposes_values_without_model(self):
        with pymupdf.open(REPORT) as pdf:png=pdf[0].get_pixmap(matrix=pymupdf.Matrix(3,3)).tobytes('png')
        record=self.upload('scanned-lab.png',png);base=f'/api/v1/documents/{record["id"]}'
        self.assertEqual(self.client.get(base+'/facts').json()['facts'],[])
        result=self.client.post(base+'/pages/1/read');self.assertEqual(result.status_code,200,result.text)
        facts=self.client.get(base+'/facts').json()['facts']
        ferritin=next(f for f in facts if f['name']=='Ferritin')
        self.assertEqual(ferritin['value'],'11');self.assertEqual(ferritin['date'],'2027-01-21')
        saved=self.client.post('/api/v1/facts/'+ferritin['id']+'/accept');self.assertEqual(saved.status_code,200)
        m=next(m for m in self.client.get('/api/v1/readings').json()['measures'] if m['key']=='ferritin')
        self.assertEqual(m['points'][0]['value'],11)
    def test_no_implicit_date_or_unsupported_unit_conversion(self):
        from stellis.report_measurements import proposals
        record={'pages':[{'number':1,'text':'Ferritin 11 ug/L 15-150 L\nHemoglobin 12.4 g/dL'}]}
        self.assertEqual([p['name'] for p in proposals(record,'2026-01-21')],['Ferritin'])
        point=dict(kind='lab_result',date='2026-01-21',name='Hemoglobin',value='12.4',unit='g/dL',id='x',target='/document/x')
        self.assertIsNone(readings.fact_point(point))
        with pymupdf.open() as pdf:
            p=pdf.new_page();p.insert_text((40,40),'Ferritin 11 ug/L 15-150 L');content=pdf.tobytes()
        record=self.upload('undated.pdf',content)
        self.assertEqual(self.client.get(f'/api/v1/documents/{record["id"]}/facts').json()['facts'],[])
        from unittest.mock import AsyncMock
        import json
        proposal=dict(kind='lab_result',name='Ferritin',value='11',unit='ug/L',date='',page=1,quote='Ferritin 11 ug/L 15-150 L')
        with patch('stellis.health_facts.agent_gateway.turn',AsyncMock(return_value=(json.dumps({'facts':[proposal]}),'test'))):
            response=self.client.post(f'/api/v1/documents/{record["id"]}/facts/extract')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['facts'],[])
    def test_low_resolution_ocr_can_be_corrected_before_any_database_value_is_accepted(self):
        import re
        with pymupdf.open(REPORT) as pdf:png=pdf[0].get_pixmap(matrix=pymupdf.Matrix(2,2)).tobytes('png')
        record=self.upload('low-resolution-lab.png',png);base=f'/api/v1/documents/{record["id"]}'
        read=self.client.post(base+'/pages/1/read').json();page=read['pages'][0]
        facts=self.client.get(base+'/facts').json()['facts'];ferritin=next(f for f in facts if f['name']=='Ferritin')
        self.assertIn(' '.join(ferritin['quote'].split()),' '.join(page['text'].split()))
        self.assertEqual(health_facts.accepted(),[])
        corrected=re.sub(r'(?im)(Ferritin\s+)\d+(?=\s+ug/L)',r'\g<1>11',page['text'])
        result=self.client.patch(base+'/pages/1',json={'revision':page['revision'],'text':corrected})
        self.assertEqual(result.status_code,200,result.text)
        facts=self.client.get(base+'/facts').json()['facts'];ferritin=next(f for f in facts if f['name']=='Ferritin')
        self.assertEqual(ferritin['value'],'11')
        self.client.post('/api/v1/facts/'+ferritin['id']+'/accept')
        self.assertEqual(next(m for m in readings.series() if m['key']=='ferritin')['points'][0]['value'],11)
