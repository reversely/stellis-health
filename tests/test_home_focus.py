"""For you now ranks the account's own items and always ends with an offer to chat."""
import datetime
import sample_records
import os
import tempfile
import unittest
from unittest.mock import patch

from stellis import auth, care_events, health_facts, home_focus, notebooks, onboarding_profile

TODAY = datetime.date(2026, 10, 26)


class HomeFocusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STELLIS_DATA_DIR': self.temp.name, 'STELLIS_AUTH_DB_DIR': self.temp.name}, clear=True)
        env.start(); self.addCleanup(env.stop)
        auth.register('focus-user', 'focus-test-password')

    def test_a_new_account_shows_its_empty_notebook_and_a_chat_offer(self):
        # Setup goals are tracked goals from the start, so Home never offers to make one.
        onboarding_profile.save(1, 'goals', ['walk'])
        notebooks.create('Blood Health')
        items = home_focus.items(TODAY)
        self.assertEqual([item['kind'] for item in items], ['empty', 'chat'])
        self.assertEqual(items[0]['title'], 'Add a first record to Blood Health')

    def test_pressing_items_lead_and_reorder_as_data_changes(self):
        book = notebooks.create('Blood Health')['id']
        care_events.add('GP review', (TODAY + datetime.timedelta(days=1)).isoformat(), 'planned', book, precision='day')
        flagged = [{'name': 'Ferritin', 'value': '11', 'unit': 'ug/L', 'date': '2022-01-21', 'document_title': 'CBC results',
                    'notebook_id': book, 'target': '/document/d?page=1', 'range_status': {'label': 'Below range', 'tone': 'alert'}, 'reference': {'low': 15.0, 'high': 150.0, 'flag': None}},
                   {'name': 'Hemoglobin', 'value': '124', 'unit': 'g/L', 'date': '2022-01-21', 'document_title': 'CBC results',
                    'notebook_id': book, 'target': '/document/d?page=1', 'range_status': {'label': 'In range', 'tone': 'ok'}}]
        with patch.object(health_facts, 'accepted', return_value=flagged):
            items = home_focus.items(TODAY)
        self.assertEqual([item['kind'] for item in items][:2], ['upcoming', 'flagged'])
        self.assertTrue(items[0]['detail'].startswith('Tomorrow, planned.'))
        self.assertEqual(items[1]['title'], 'I can explain your Ferritin result')
        self.assertEqual(items[1]['detail'], '11 ug/L on Jan 21, 2022, below the lab’s range of 15–150.')
        self.assertIn('?ask=', items[1]['action']['href'])
        self.assertFalse(any('Hemoglobin' in item['title'] for item in items))
        # Once the event is past, it leaves the list.
        later = home_focus.items(TODAY + datetime.timedelta(days=3))
        self.assertNotIn('upcoming', [item['kind'] for item in later])
        self.assertEqual(later[-1]['kind'], 'chat')

    def test_what_waits_for_confirmation_is_one_summary_leading_to_the_newest_record(self):
        from stellis import documents
        import pymupdf
        def pdf(value):
            with pymupdf.open() as doc:
                page = doc.new_page(width=420, height=500)
                page.insert_text((40, 50), 'Specimen collected: 2026-04-02')
                page.insert_text((40, 90), 'Test          Result          Unit          Reference interval')
                page.insert_text((40, 120), f'Potassium     {value}          mmol/L        3.5 - 5.1')
                return doc.tobytes()
        first, _ = documents.create('first.pdf', pdf('5.4'))
        second, _ = documents.create('second.pdf', pdf('4.8'))
        for record in (first, second):
            health_facts.prepare(record['id'])
        waiting = sum(1 for record in (first, second) for fact in health_facts.list_for(record['id']) if fact['status'] == 'suggested')
        self.assertGreater(waiting, 0)
        items = [item for item in home_focus.items(TODAY) if item['kind'] == 'confirm']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title'], f"I found {waiting} {'result' if waiting == 1 else 'results'} and 2 dates to confirm")
        self.assertTrue(items[0]['detail'].startswith('In 2 records: '))
        newest = documents.list_all()[0]['id']
        self.assertEqual(items[0]['action']['href'], '/document/' + newest)
        # Once everything is confirmed, the summary leaves the list.
        for record in (first, second):
            health_facts.accept_all(record['id'])
            documents.update(record['id'], record_date='2026-04-02')
        self.assertNotIn('confirm', [item['kind'] for item in home_focus.items(TODAY)])

    def test_a_record_filed_by_link_counts_as_filed_and_is_offered_for_chat(self):
        from pathlib import Path
        from stellis import documents, notebook_sources
        book = notebooks.create('Blood Health')['id']
        content = (sample_records.FOLDER / '2016-01-28-annual-exam.pdf').read_bytes()
        record, _ = documents.create('annual-exam.pdf', content, None, '2016-01-28')
        notebook_sources.link(book, 'document:' + record['id'])
        self.assertEqual(documents.list_all()[0]['notebook_ids'], [book])
        items = home_focus.items(TODAY)
        self.assertNotIn('empty', [item['kind'] for item in items])
        self.assertTrue(items[-1]['title'].startswith('Ask me about '))
        self.assertIn(f'/notebook/{book}?ask=', items[-1]['action']['href'])

    def test_latest_results_keep_one_tile_per_test_flagged_first_and_skip_doses(self):
        book = notebooks.create('Blood Health')['id']
        def fact(name, value, date, tone=None, measurement=None, unit='ug/L'):
            return {'kind': 'lab_result', 'name': name, 'value': value, 'unit': unit, 'date': date,
                    'measurement': measurement or name.casefold(), 'document_title': 'Labs ' + date,
                    'notebook_id': book, 'target': '/document/d?page=1',
                    'range_status': {'label': 'Low' if tone == 'alert' else 'In range', 'tone': tone} if tone else None}
        accepted = [fact('Hemoglobin', '131', '2026-06-15', 'ok', unit='g/L'), fact('Ferritin', '13', '2026-06-15', 'alert'),
                    fact('Ferrous sulfate', '300', '2026-06-19', measurement='report_abc', unit='mg'),
                    fact('Ferritin', '42', '2025-10-10', 'ok'), fact('Hemoglobin', '125', '2025-10-10', 'ok', unit='g/L')]
        with patch.object(health_facts, 'accepted', return_value=accepted):
            tiles = home_focus.latest_results()
        self.assertEqual([tile['name'] for tile in tiles], ['Ferritin', 'Hemoglobin'])
        self.assertEqual((tiles[0]['value'], tiles[0]['status'], tiles[0]['tone']), ('13', 'Low', 'alert'))
        self.assertEqual(tiles[0]['previous'], {'value': '42', 'date': '2025-10-10'})
        self.assertEqual([point['value'] for point in tiles[0]['points']], [42.0, 13.0])
        self.assertEqual(tiles[0]['href'], '/trends?measure=ferritin')

    def test_an_empty_account_gets_first_steps_from_what_is_missing(self):
        ideas = home_focus.starting_ideas()
        self.assertFalse(home_focus.has_history())
        self.assertEqual([idea['action'].get('command') or idea['action']['href'] for idea in ideas],
                         ['upload', '/onboarding', 'camera', 'example'])
        notebooks.create('Blood Health')
        self.assertNotIn('/onboarding', [idea['action'].get('href') for idea in home_focus.starting_ideas()])
        # Inside the example workspace there is nothing more to try.
        self.assertNotIn('example', [idea['action'].get('command') for idea in home_focus.starting_ideas(in_example=True)])

    def test_a_topic_name_saved_as_a_goal_is_not_offered_as_a_to_do(self):
        from stellis import pages, todos
        book = notebooks.create('Pregnancy')['id']
        pages.create('Goals', book, '- [ ] Pregnancy\n- [ ] General wellness and preventive care\n- [ ] Walk after lunch\n')
        todos.gather()  # Index the note's tasks.
        titles = [item['title'] for item in home_focus.items(TODAY) if item['kind'] == 'todo']
        self.assertEqual(titles, ['Walk after lunch'])

    def test_a_failing_source_never_blanks_the_list(self):
        with patch.object(care_events, 'list_all', side_effect=RuntimeError('unavailable')):
            items = home_focus.items(TODAY)
        self.assertEqual(items[-1]['kind'], 'chat')


if __name__ == '__main__':
    unittest.main()
