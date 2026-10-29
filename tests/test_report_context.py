"""Real page coordinates, saved conversations, citations and stale-source guards."""
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
import pymupdf

from stellis import documents, report_context, server


def report(value='5.4', rotate=0):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=420, height=500)
        page.insert_text((40, 50), 'Specimen collected: 2026-04-02')
        page.insert_text((40, 90), 'Test          Result          Unit          Reference interval')
        page.insert_text((40, 120), f'Potassium     {value}          mmol/L        3.5 - 5.1')
        page.set_rotation(rotate)
        return pdf.tobytes()


class ReportContextTests(unittest.TestCase):
    def setUp(self):
        editor=mock.patch("stellis.report_context.review_answer",side_effect=lambda question,answer,passages,public,citations,source_fields:(answer,citations))
        editor.start();self.addCleanup(editor.stop)
        followups=mock.patch("stellis.report_followups.generate",return_value=None)
        followups.start();self.addCleanup(followups.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = mock.patch.dict(os.environ, {'STELLIS_DATA_DIR': self.temp.name, 'STELLIS_AUTH_MODE': 'open'})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(server.app); self.addCleanup(self.client.close)
        self.record, _ = documents.create('lab.pdf', report())
        self.base = f'/api/v1/documents/{self.record["id"]}'

    def mapped(self):
        response = self.client.get(self.base+'/pages/1/map')
        self.assertEqual(response.status_code, 200, response.text)
        mapped = response.json()
        region = next(r for r in mapped['regions'] if 'Potassium' in r['text'])
        return mapped, region

    def test_original_coordinates_rotation_cache_and_page_validation(self):
        for rotation in (0, 90, 180, 270):
            record, _ = documents.create(f'lab-{rotation}.pdf', report(rotate=rotation))
            mapped = report_context.page_map(record['id'], 1)
            self.assertEqual(mapped['engine'], 'pdf-text')
            for region in mapped['regions']:
                x, y, w, h = region['box']
                self.assertTrue(0 <= x <= 1 and 0 <= y <= 1)
                self.assertTrue(w > 0 and h > 0 and x+w <= 1.00001 and y+h <= 1.00001)
            self.assertEqual(mapped, report_context.page_map(record['id'], 1))
        response = self.client.get(self.base+'/pages/1/original')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get(self.base+'/pages/99/map').status_code, 404)

    def test_source_anchored_question_followup_history_and_related_records(self):
        prior, _ = documents.create('earlier.pdf', report('4.8'))
        mapped, region = self.mapped()
        prompts = []
        async def turn(prompt, conversation, **options):
            prompts.append(prompt)
            return 'The selected potassium result is 5.4 mmol/L. [[source:1]]', {}
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=turn):
            for question in ('What does this mean?', 'How has this changed?'):
                response = self.client.post(self.base+'/explanations', json={
                    'map_id': mapped['id'], 'region_id': region['id'], 'question': question})
                self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(self.base+'/explanations').json()['exchanges']
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]['selection'], region)
        self.assertIn('map='+mapped['id'], saved[0]['citations'][0]['target'])
        self.assertIn('4.8', prompts[0])
        self.assertIn('What does this mean?', prompts[1])
        self.assertTrue(saved[0]['evidence'])
        self.assertFalse(saved[0]['stale'])
        documents.save_page(self.record['id'], 1, 'Reviewed result', mapped['revision'])
        self.assertTrue(self.client.get(self.base+'/explanations').json()['exchanges'][0]['stale'])
        stale = self.client.post(self.base+'/explanations', json={
            'map_id': mapped['id'], 'region_id': region['id'], 'question': 'Explain'})
        self.assertEqual(stale.status_code, 409)

    def test_whole_page_summary_reads_every_line_and_keeps_its_history(self):
        documents.create('earlier.pdf', report('4.8'))
        mapped, _ = self.mapped()
        prompts = []
        async def turn(prompt, conversation, **options):
            prompts.append(prompt)
            return 'This report lists one potassium result of 5.4 mmol/L. [[source:1]]', {}
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=turn):
            response = self.client.post(self.base+'/explanations', json={
                'map_id': mapped['id'], 'region_id': 'page', 'question': 'What does this report say?'})
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(self.base+'/explanations').json()['exchanges']
        self.assertEqual(saved[0]['region_id'], 'page')
        for line in mapped['regions']:
            self.assertIn(json.dumps(line['text'])[1:-1], prompts[0])
        # The summary reads only this report: the earlier one stays out, so it is short and repeatable.
        self.assertNotIn('4.8', prompts[0])

    def test_each_explanation_links_its_trace_with_sources_and_checks(self):
        prior, _ = documents.create('earlier.pdf', report('4.8'))
        mapped, region = self.mapped()
        async def turn(prompt, conversation, **options):
            return 'Potassium is 5.4 mmol/L [[source:1]], earlier 4.8 [[source:2]].', {}
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=turn):
            saved = self.client.post(self.base+'/explanations', json={
                'map_id': mapped['id'], 'region_id': region['id'], 'question': 'How has this changed?'}).json()
        self.assertTrue(saved['trace_id'])
        result = self.client.get('/api/v1/traces/'+saved['trace_id']).json()
        trace, answer = result['trace'], result['answer']
        self.assertEqual(trace['state'], 'succeeded')
        self.assertEqual([e['name'] for e in trace['events']], [
            'request_started', 'selection_resolved', 'sources_selected', 'citations_checked',
            'sources_checked', 'answer_saved', 'request_completed'])
        self.assertEqual(trace['events'][1]['page_number'], 1)
        self.assertEqual(answer['kind'], 'report')
        self.assertEqual(answer['question'], 'How has this changed?')
        self.assertEqual(answer['sources'][0]['role'], 'selected')
        related = [s for s in answer['sources'] if s['role'] == 'related']
        self.assertEqual(related[0]['rank'], 1)
        self.assertIn('4.8', related[0]['quote'])
        self.assertEqual({c['status'] for c in answer['checks'][:-1]}, {'unchanged'})
        self.assertEqual(answer['checks'][-1]['status'], '2 of 2')
        self.assertEqual(answer['citations'], {'found': 2, 'total': 2})
        # A later edit to a related source shows as changed since the request.
        documents.save_page(prior['id'], 1, 'Reviewed', documents.get(prior['id'])['pages'][0]['revision'])
        checks = self.client.get('/api/v1/traces/'+saved['trace_id']).json()['answer']['checks']
        self.assertIn('changed', {c['status'] for c in checks})
        self.assertEqual(self.client.get('/api/v1/traces/missing').status_code, 404)

    def test_rejects_invalid_citations_and_concurrent_source_edits(self):
        mapped, region = self.mapped()
        payload = {'map_id': mapped['id'], 'region_id': region['id'], 'question': 'Explain'}
        for reply in ('No citations.', 'Made up. [[source:99]]'):
            with mock.patch.object(report_context.agent_gateway, 'turn', return_value=(reply, {})):
                response = self.client.post(self.base+'/explanations', json=payload)
                self.assertEqual(response.status_code, 503, response.text)
        async def edit(prompt, conversation, **options):
            documents.save_page(self.record['id'], 1, 'Changed while explaining', mapped['revision'])
            return 'A result. [[source:1]]', {}
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=edit):
            self.assertEqual(self.client.post(self.base+'/explanations', json=payload).status_code, 409)
        self.assertEqual(self.client.get(self.base+'/explanations').json()['exchanges'], [])

    def test_cross_document_map_refused_and_deletion_cascades(self):
        mapped, region = self.mapped()
        other, _ = documents.create('other.pdf', report('4.0'))
        response = self.client.post(f'/api/v1/documents/{other["id"]}/explanations', json={
            'map_id': mapped['id'], 'region_id': region['id'], 'question': 'Explain'})
        self.assertEqual(response.status_code, 404)
        documents.delete(self.record['id'])
        self.assertEqual(self.client.get(self.base+'/explanations').status_code, 404)

    def test_scan_geometry_is_grounded_in_original_pixels(self):
        from PIL import Image, ImageDraw, ImageFont
        import shutil
        if not shutil.which('tesseract'):
            self.skipTest('Tesseract not installed')
        image = Image.new('RGB', (1400, 600), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('DejaVuSans.ttf', 38)
        draw.text((70, 80), 'Potassium 5.4 mmol/L', font=font, fill='black')
        data = io.BytesIO(); image.save(data, format='PNG')
        record, _ = documents.create('scan.png', data.getvalue())
        mapped = report_context.page_map(record['id'], 1)
        self.assertEqual(mapped['engine'], 'tesseract-tsv')
        region = next(r for r in mapped['regions'] if 'Potassium' in r['text'])
        self.assertIn('5.4', region['text'])
        x, y, w, h = region['box']
        self.assertTrue(.04 <= x <= .07 and .13 <= y <= .19)
        self.assertGreater(w, .2)
        self.assertGreater(h, .03)
        self.assertEqual(documents.get(record['id'])['pages'][0]['text'], '')

    def test_related_source_change_during_inference_is_rejected(self):
        prior, _ = documents.create('prior.pdf', report('4.8'))
        mapped, region = self.mapped()
        async def edit(prompt, session, **options):
            documents.save_page(prior['id'], 1, 'Changed prior result', prior['pages'][0]['revision'])
            return 'The prior result was 4.8. [[source:2]]', session
        with mock.patch.object(report_context.agent_gateway, 'turn', side_effect=edit):
            response = self.client.post(self.base+'/explanations', json={
                'map_id': mapped['id'], 'region_id': region['id'], 'question': 'Compare'})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(report_context.history(self.record['id']), [])

    def test_page_map_is_account_local(self):
        mapped, region = self.mapped()
        with tempfile.TemporaryDirectory() as other:
            with mock.patch.dict(os.environ, {'STELLIS_DATA_DIR': other}):
                response = self.client.get(self.base+'/pages/1/map')
                self.assertEqual(response.status_code, 404)
                response = self.client.get(self.base+'/explanations')
                self.assertEqual(response.status_code, 404)

    def test_login_required_for_original_geometry_and_conversation(self):
        with mock.patch.dict(os.environ, {'STELLIS_AUTH_MODE': 'required'}):
            for path in ('/pages/1/original', '/pages/1/map', '/explanations'):
                self.assertEqual(self.client.get(self.base+path).status_code, 401)
            self.assertEqual(self.client.post(self.base+'/explanations', json={
                'map_id': 'x', 'region_id': '0', 'question': 'Explain'}).status_code, 401)

    def test_related_source_deletion_marks_saved_answer_historical(self):
        prior, _ = documents.create('prior.pdf', report('4.8'))
        mapped, region = self.mapped()
        with mock.patch.object(report_context.agent_gateway, 'turn', return_value=('Result. [[source:1]]', 's')):
            response = self.client.post(self.base+'/explanations', json={
                'map_id': mapped['id'], 'region_id': region['id'], 'question': 'Explain'})
        self.assertEqual(response.status_code, 200, response.text)
        documents.delete(prior['id'])
        saved = report_context.history(self.record['id'])[0]
        self.assertTrue(saved['stale'])
        self.assertTrue(saved['evidence'][0]['text'])

    def test_concurrent_map_and_raster_requests_wait_for_the_same_worker_slot(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            mapped = pool.submit(report_context.page_map, self.record['id'], 1)
            original = pool.submit(report_context.page_image, self.record['id'], 1)
            self.assertEqual(mapped.result(timeout=15)['engine'], 'pdf-text')
            self.assertTrue(original.result(timeout=15).startswith(b'\x89PNG'))
