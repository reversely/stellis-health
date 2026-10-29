"""Dynamic report metrics, notebook isolation and exact point/source association."""
import os,tempfile,unittest
from unittest.mock import patch
import fitz
from fastapi.testclient import TestClient
from stellis import server

class NotebookMeasurementsTest(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  env=patch.dict(os.environ,{'STELLIS_DATA_DIR':temp.name,'STELLIS_AUTH_MODE':'open'});env.start();self.addCleanup(env.stop)
  self.client=TestClient(server.app);self.addCleanup(self.client.close)
  self.book=self.client.post('/api/v1/notebooks',json={'name':'Lab history'}).json()['id']
 def report(self,day,value,notebook=None,name='C-reactive protein',unit='mg/L'):
  pdf=fitz.open();page=pdf.new_page();page.insert_text((50,50),f'Laboratory results\nClinical date\n{day}\n{name}\n{value}\n{unit}\n0-5');raw=pdf.tobytes();pdf.close()
  result=self.client.post('/api/v1/documents',data={'notebook_id':notebook or self.book},files={'file':(day+'.pdf',raw,'application/pdf')})
  self.assertEqual(result.status_code,200,result.text);return result.json()['document']['id']
 def data(self,book=None):
  r=self.client.get('/api/v1/readings',params={'notebook_id':book or self.book});self.assertEqual(r.status_code,200,r.text);return r.json()
 def test_unknown_metric_suggestions_approval_scope_reload_and_delete(self):
  first=self.report('2026-01-03',2.4);second=self.report('2026-04-03',7.1)
  data=self.data();self.assertEqual(data['measures'],[])
  self.assertEqual(len(data['suggestions']),1);self.assertEqual(len(data['suggestions'][0]['candidates']),2)
  for identity in [first,second]:self.client.post('/api/v1/documents/'+identity+'/facts/accept')
  data=self.data();self.assertEqual(data['suggestions'],[]);metric=data['measures'][0]
  context=self.client.get('/api/v1/notebooks/'+self.book+'/health');self.assertEqual(context.status_code,200,context.text)
  self.assertEqual(context.json()['measurements'][0]['label'],'C-reactive protein')
  from stellis import dashboard
  saved=dashboard.save({'notebooks':[self.book],'measurements':[metric['key']],'care_contexts':[]})
  self.assertEqual(saved['measurements'],[metric['key']])
  self.assertEqual(metric['label'],'C-reactive protein');self.assertEqual(metric['unit'],'mg/L');self.assertTrue(metric['record_only'])
  self.assertEqual([(p['time'],p['value'],p['document_id']) for p in metric['points']],[('2026-01-03',2.4,first),('2026-04-03',7.1,second)])
  other=self.client.post('/api/v1/notebooks',json={'name':'Other history'}).json()['id'];self.assertEqual(self.data(other)['measures'],[])
  # An explicitly linked original is intentionally available in the other notebook.
  from stellis import notebook_sources
  notebook_sources.link(other,'document:'+first)
  self.assertEqual(len(self.data(other)['measures'][0]['points']),1)
  fact=self.client.get('/api/v1/documents/'+first+'/facts').json()['facts'][0]
  self.client.delete('/api/v1/facts/'+fact['id']);self.assertEqual([p['value'] for p in self.data()['measures'][0]['points']],[7.1])
  self.assertEqual(self.data(other)['measures'],[])
 def test_different_units_are_not_silently_combined(self):
  for day,unit in [('2026-01-03','mg/L'),('2026-02-03','nmol/L')]:
   identity=self.report(day,2.4,unit=unit);self.client.post('/api/v1/documents/'+identity+'/facts/accept')
  self.assertEqual({m['unit'] for m in self.data()['measures']},{'mg/L','nmol/L'})
 def test_missing_notebook_is_rejected(self):
  self.assertEqual(self.client.get('/api/v1/readings?notebook_id=missing').status_code,400)
