"""Dashboard choices stay in the current workspace and preserve source data."""
import tempfile
import unittest
from unittest import mock

from stellis import dashboard, storage, notebooks


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = mock.patch.dict('os.environ', {'STELLIS_DATA_DIR': self.directory.name})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        with storage.connect():
            pass

    def test_selection_survives_reload_and_rejects_unavailable_ids(self):
        book = notebooks.create('Nutrition')
        choices = {'notebooks': [book['id']], 'measurements': ['sleep'], 'care_contexts': [book['id']]}
        self.assertEqual(dashboard.save(choices), choices)
        self.assertEqual(dashboard.preferences(), choices)
        with self.assertRaises(ValueError):
            dashboard.save({**choices, 'notebooks': ['other-account-book']})
        self.assertEqual(dashboard.preferences(), choices)

    def test_default_measurements_and_empty_selection(self):
        self.assertEqual(dashboard.preferences()['measurements'], ['weight','blood_pressure','sleep'])
        empty = {'notebooks': [], 'measurements': [], 'care_contexts': []}
        self.assertEqual(dashboard.save(empty), empty)

    def test_invalid_measurements_and_duplicates_leave_choices_unchanged(self):
        before = dashboard.preferences()
        for values in [['made-up'], ['sleep','sleep'], ['weight','sleep','pulse','hrv']]:
            with self.assertRaises(ValueError):
                dashboard.save({**before,'measurements':values})
            self.assertEqual(dashboard.preferences(), before)

    def test_archived_notebook_disappears_without_removing_its_content(self):
        book = notebooks.create('Care')
        dashboard.save({'notebooks': [book['id']], 'measurements': [], 'care_contexts': [book['id']]})
        notebooks.update(book['id'], archived=True)
        self.assertEqual(dashboard.preferences()['notebooks'], [])
        self.assertEqual(dashboard.preferences()['care_contexts'], [])
        with storage.connect() as conn:
            self.assertIsNotNone(conn.execute('SELECT id FROM notebooks WHERE id = ?', (book['id'],)).fetchone())
