"""Instruction roles, model-selected source arrays and public evidence integrity."""
import asyncio,json,os,tempfile,unittest
from unittest.mock import patch
import httpx
from stellis import agent_gateway,clinical_references,notebook_chat,report_context,model_routes,runtime_profile

class ReportOutputContractTests(unittest.TestCase):
 def test_structured_sources_are_model_selected_and_validated(self):
  passages=[notebook_chat.Passage('document:x','Source','Proof','/document/x')]
  text,citations=report_context.cited_reply(json.dumps({'paragraphs':[{'text':'A source-grounded statement.','sources':[1]}]}),passages)
  self.assertEqual(text,'A source-grounded statement. [[1]]');self.assertEqual(citations[0]['source_id'],'document:x')
  for indices in ([],[0],[2],[True],['1']):
   with self.assertRaises(notebook_chat.ChatError):report_context.cited_reply(json.dumps({'paragraphs':[{'text':'Claim','sources':indices}]}),passages)
 def test_provider_receives_system_instruction_and_schema_separately(self):
  captured={}
  async def handle(request):
   captured.update(json.loads(request.content));return httpx.Response(200,json={'model':'test','choices':[{'finish_reason':'stop','message':{'content':'{"paragraphs":[]}'}}],'usage':{}})
  client=httpx.AsyncClient
  with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'STELLIS_DATA_DIR':folder,'STELLIS_RUNTIME_PROFILE':'direct'}),patch('stellis.ai._key',return_value='fixture-key'),patch.object(httpx,'AsyncClient',side_effect=lambda **kwargs:client(transport=httpx.MockTransport(handle))):
   model_routes.save({'report':'nvidia/Nemotron-3-Ultra-550b-a55b','agent':'unrelated-agent'})
   asyncio.run(agent_gateway._direct_turn('Untrusted source payload','fixture',instructions='Source rules',response_format=report_context.response_format(1),model_role='report'))
   self.assertEqual(runtime_profile.model_id('agent'),'unrelated-agent')
  self.assertEqual(captured['model'],'nvidia/Nemotron-3-Ultra-550b-a55b')
  self.assertEqual(captured['messages'],[{'role':'system','content':'Source rules'},{'role':'user','content':'Untrusted source payload'}]);self.assertEqual(captured['response_format']['type'],'json_schema')
 def test_public_reference_is_generic_and_attributed(self):
  refs=clinical_references.matching('Gardner blastocyst grades')
  self.assertEqual(len(refs),2);self.assertTrue(all(r['url'].startswith('https://') and r['reviewed'] for r in refs))
  self.assertEqual(clinical_references.matching('Ferritin hemoglobin'),[])
  self.assertTrue(all('Maya' not in r['summary'] and 'SYN-E01' not in r['summary'] for r in refs))
