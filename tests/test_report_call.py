"""Source-bound native calls persist to ordinary report history, never coach state."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from stellis import documents, report_call, report_context, server
from test_report_context import report


class ReportCallTests(unittest.TestCase):
    def setUp(self):
        editor=mock.patch("stellis.report_context.review_answer",side_effect=lambda question,answer,passages,public,citations,source_fields:(answer,citations))
        editor.start();self.addCleanup(editor.stop)
        followups=mock.patch("stellis.report_followups.generate",return_value=None)
        followups.start();self.addCleanup(followups.stop)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        env = mock.patch.dict(os.environ, {'STELLIS_DATA_DIR': temp.name, 'STELLIS_AUTH_MODE': 'open'})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(server.app); self.addCleanup(self.client.close)
        self.record, _ = documents.create('lab.pdf', report())
        self.mapped = report_context.page_map(self.record['id'], 1)
        self.region = next(r for r in self.mapped['regions'] if 'Potassium' in r['text'])
        self.path = ('/api/v1/coach/call?kind=report&voice=0&document='+self.record['id']+
                     '&map='+self.mapped['id']+'&region='+self.region['id'])

    def test_call_to_source_citations_history_and_followup(self):
        prompts=[]
        async def turn(prompt, session, **options):
            prompts.append(prompt)
            return 'The report records potassium 5.4 mmol/L. [[source:1]]', {}
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=turn):
            with self.client.websocket_connect(self.path) as socket:
                self.assertEqual(socket.receive_json()['type'], 'ready')
                while socket.receive_json()['type'] != 'state': pass
                for text in ('What does this say?', 'What is its reference interval?'):
                    socket.send_json({'type':'text','text':text})
                    while True:
                        message=socket.receive_json()
                        if message.get('action')=='report_answer':break
                        self.assertNotEqual(message['type'],'error',message)
                    self.assertEqual(message['exchange']['question'],text)
                    self.assertEqual(message['exchange']['citations'][0]['source_id'],'document:'+self.record['id'])
                    while socket.receive_json()['type'] != 'state':pass
        history=report_context.history(self.record['id'])
        self.assertEqual(len(history),2)
        self.assertEqual(history[0]['question'],'What does this say?')
        self.assertIn('What does this say?',prompts[1])
        self.assertEqual(self.client.get('/api/v1/facts').json()['facts'],[])

    def test_cross_document_and_stale_map_rejected_before_call(self):
        other,_=documents.create('other.pdf',report('4.8'))
        for path in (self.path.replace(self.record['id'],other['id']),self.path.replace(self.region['id'],'unknown')):
            with self.assertRaises(WebSocketDisconnect) as error:
                with self.client.websocket_connect(path):pass
            self.assertEqual(error.exception.code,4400)
        documents.save_page(self.record['id'],1,'Corrected source',self.mapped['revision'])
        with self.assertRaises(WebSocketDisconnect) as error:
            with self.client.websocket_connect(self.path):pass
        self.assertEqual(error.exception.code,4400)

    def test_active_source_changes_refuse_stale_reply(self):
        call=report_call.ReportCall(self.record['id'],self.mapped['id'],self.region['id'])
        call.add_person_turn('Explain the row')
        documents.save_page(self.record['id'],1,'Corrected source',self.mapped['revision'])
        async def collect():return [piece async for piece in call.reply()]
        from stellis import intake
        with self.assertRaises(intake.IntakeError):asyncio.run(collect())
        self.assertEqual(report_context.history(self.record['id']),[])

    def test_script_instances_do_not_share_selection_or_question(self):
        first=report_call.ReportCall(self.record['id'],self.mapped['id'],self.region['id'])
        second=report_call.ReportCall(self.record['id'],self.mapped['id'],self.region['id'])
        first.add_person_turn('First question')
        second.add_person_turn('Second question')
        self.assertEqual(first.question,'First question')
        self.assertEqual(first.extend_person_turn('continued'),'First question continued')
        self.assertEqual(second.question,'Second question')

    def test_ordinary_entrypoint_has_no_demo_frontend(self):
        html=self.client.get('/').text
        for artifact in ('maya-guide.js','maya-scenes.js','maya-story.css','id="rail-demo"'):
            self.assertNotIn(artifact,html)
        response=self.client.get('/demo',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.assertEqual(response.headers['location'],'/')
