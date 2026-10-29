"""The demo story clock (#252): a demo workspace's date, its scrub and the rows it shows."""
import datetime as real
import os
import tempfile
import unittest
from unittest.mock import patch

from stellis import storage, story_clock, trackers


class StoryClockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STELLIS_DATA_DIR': self.temp.name}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.demo = storage.base_dir() / 'demos' / 'story'

    def test_only_a_demo_workspace_moves_today(self):
        with storage.demo_workspace(self.demo):
            story_clock.save(self.demo, real.date(2028, 6, 1), advance=True)
        with storage.demo_workspace(self.demo):
            self.assertEqual(trackers.dt.date.today(), real.date(2028, 6, 1))
            self.assertEqual(storage.datetime.now().date(), real.date(2028, 6, 1))
        self.assertEqual(trackers.dt.date.today(), real.date.today())
        self.assertEqual(storage.datetime.now().date(), real.date.today())

    def test_the_date_stays_inside_the_example_years(self):
        with storage.demo_workspace(self.demo):
            with self.assertRaises(ValueError):
                story_clock.save(self.demo, real.date(2040, 1, 1))

    def test_looking_back_hides_later_rows_until_the_scrub_moves_forward(self):
        def stamps():
            with storage.connect() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS notes_demo (id TEXT, created_at TEXT NOT NULL)')
                return [row['id'] for row in conn.execute('SELECT id FROM notes_demo ORDER BY id')]
        with storage.demo_workspace(self.demo):
            story_clock.save(self.demo, real.date(2029, 12, 31), advance=True)
            stamps()
            with storage.connect() as conn:
                conn.executemany('INSERT INTO notes_demo VALUES (?, ?)',
                                 [('a', '2027-05-01T10:00:00+00:00'), ('b', '2029-05-01T10:00:00+00:00')])
            story_clock.save(self.demo, real.date(2028, 1, 1))
            self.assertIsNotNone(story_clock.looking_back(self.demo))
            self.assertEqual(stamps(), ['a'])
            story_clock.save(self.demo, real.date(2029, 12, 31))
            self.assertIsNone(story_clock.looking_back(self.demo))
            self.assertEqual(stamps(), ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
