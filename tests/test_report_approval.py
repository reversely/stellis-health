"""Original candidates, explicit approval and stable DB receipts across three reports."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from stellis import health_facts, readings, server, storage

class ReportApprovalTests(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
  env=patch.dict(os.environ,{'STELLIS_DATA_DIR':temp.name,'STELLIS_AUTH_MODE':'open'});env.start();self.addCleanup(env.stop)
  self.client=TestClient(server.app);self.addCleanup(self.client.close)
 def test_three_source_dates_enter_measurements_only_after_approval(self):
  expected=[]
  for corpus,day,value in [('D002','2027-01-21',11),('D058','2030-10-10',42),('D065','2031-06-15',13)]:
   path=next(Path('src/stellis/static/maya/documents').rglob(corpus+'*.pdf'))
   response=self.client.post('/api/v1/documents',files={'file':(path.name,path.read_bytes(),'application/pdf')});self.assertEqual(response.status_code,200)
   doc=response.json()['document'];base='/api/v1/documents/'+doc['id']
   facts=self.client.get(base+'/facts').json()['facts'];self.assertTrue(facts)
   self.assertTrue(all(f['status']=='suggested' and f['accepted_at'] is None for f in facts))
   points=next(m for m in readings.series() if m['key']=='ferritin')['points'];self.assertEqual(len(points),len(expected))
   with storage.connect() as conn:
    rows=conn.execute('SELECT status,accepted_at FROM health_facts WHERE document_id=?',(doc['id'],)).fetchall()
    self.assertTrue(all(r['status']=='suggested' and r['accepted_at'] is None for r in rows))
   approved=self.client.post(base+'/facts/accept');self.assertEqual(approved.status_code,200)
   saved=self.client.get(base+'/facts').json()['facts'];self.assertTrue(all(f['status']=='accepted' and f['accepted_at'] for f in saved))
   ferritin=next(f for f in saved if f['name']=='Ferritin');timestamp=ferritin['accepted_at']
   with patch.object(health_facts,'_now',return_value='2099-01-01T00:00:00+00:00'):
    again=self.client.post('/api/v1/facts/'+ferritin['id']+'/accept').json();self.assertEqual(again['accepted_at'],timestamp)
    self.assertEqual(self.client.post(base+'/facts/accept').json()['accepted'],0)
   expected.append((day,value,doc['id']))
   actual=next(m for m in readings.series() if m['key']=='ferritin')['points']
   self.assertEqual(sorted((p['time'][:10],p['value']) for p in actual),sorted((d,v) for d,v,_ in expected))
   self.assertTrue(all(any(identity in p['target'] for p in actual) for _,_,identity in expected))
