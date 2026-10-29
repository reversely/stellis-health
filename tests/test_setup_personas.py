"""Setup answers for people with different conditions propose the right notebooks and measurable goals."""
import os
import tempfile
import unittest
from unittest.mock import patch

from stellis import auth, goals, health_catalogue, onboarding, onboarding_profile as profile

# Each person: their setup answers, then the notebooks they should be offered, no more and no fewer.
PERSONAS = {
    'diabetes and high blood pressure': (
        {'gender': 'Man', 'cycle_tracking': "I don't have a period", 'birth_date': '1961-05-02',
         'conditions': 'Diabetes Type 2\nHigh Blood Pressure', 'medicines': 'metformin\nlisinopril',
         'goals': ['walk', 'blood_pressure']},
        {'Diabetes Management', 'Blood Pressure', 'Fitness'}),
    'asthma and anxiety': (
        {'gender': 'Non-binary', 'cycle_tracking': 'No', 'conditions': 'Asthma\nAnxiety',
         'medicines': 'albuterol\nsertraline', 'goals': ['meditate', 'sleep']},
        {'Asthma Management', 'Mental Health', 'Sleep'}),
    'low iron, trying to conceive': (
        {'gender': 'Woman', 'cycle_tracking': 'Yes', 'conditions': 'Anemia\nTrying to conceive',
         'medicines': 'ferrous sulfate\nfolic acid', 'focus': ['reproductive-health']},
        {'Bloodwork', 'Fertility', 'Menstrual Health'}),
    'hypothyroidism, arthritis and osteoporosis': (
        {'gender': 'Woman', 'birth_date': '1952-08-09', 'conditions': 'Hypothyroidism\nArthritis\nOsteoporosis',
         'medicines': 'levothyroxine\nalendronate', 'goals': ['stretch']},
        {'Thyroid Care', 'Joint Care', 'Bone Health', 'Mobility'}),
    'migraine': (
        {'conditions': 'Migraine', 'medicines': 'sumatriptan', 'goals': [{'id': 'sleep', 'amount': 8, 'times_per_week': 7}]},
        {'Migraine Management', 'Sleep'}),
    'no conditions, building habits': (
        {'conditions': 'None that I know of', 'medicines': 'None', 'goals': ['exercise', 'produce']},
        {'Fitness', 'Nutrition'}),
    'pregnant with gestational diabetes': (
        {'conditions': 'Diabetes and Pregnancy\nPregnant', 'medicines': 'insulin', 'goals': ['walk']},
        {'Pregnancy', 'Diabetes Management', 'Fitness'}),
    'menopause, allergy and depression': (
        {'conditions': 'Menopause\nAllergy\nDepression', 'goals': ['vitals']},
        {'Menopause', 'Allergy Management', 'Mental Health'}),
    'postpartum': ({'conditions': 'Postpartum'}, {'Postpartum Recovery'}),
    # Naming a family that a condition already gives a notebook adds nothing more there.
    'anemia, and blood health as an area': (
        {'conditions': 'Anemia', 'focus': ['blood-health', 'sleep']}, {'Bloodwork', 'Sleep'}),
}


class SetupPersonaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STELLIS_DATA_DIR': self.temp.name, 'STELLIS_AUTH_DB_DIR': self.temp.name}, clear=True)
        env.start(); self.addCleanup(env.stop)
        auth.register('persona', 'persona-test-password')

    def answer(self, answers):
        profile.save(1, 'name', 'Test')
        for field, value in answers.items():
            profile.save(1, field, value)

    def test_each_person_is_offered_their_notebooks_and_no_others(self):
        for name, (answers, expected) in PERSONAS.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as folder, \
                    patch.dict(os.environ, {'STELLIS_DATA_DIR': folder, 'STELLIS_AUTH_DB_DIR': folder}):
                auth.register('persona', 'persona-test-password')
                self.answer(answers)
                self.assertEqual({g['notebook_name'] for g in health_catalogue.recommend()['groups']}, expected)

    def test_a_notebook_goal_is_always_one_of_the_measurable_goals(self):
        self.answer({'cycle_tracking': 'Yes', 'focus': ['blood-health'], 'goals': ['walk']})
        goals_by_book = {g['notebook_name']: g['goal'] for g in health_catalogue.recommend()['groups']}
        self.assertEqual(goals_by_book, {'Menstrual Health': '', 'Bloodwork': '', 'Fitness': 'Walk 20 minutes, 4 days a week'})

    def test_a_general_notebook_kept_beside_a_specific_one_gives_its_own_reason(self):
        self.answer({'conditions': 'Arthritis\nOsteoporosis'})
        groups = {g['notebook_name']: g for g in health_catalogue.recommend()['groups']}
        self.assertEqual(groups['Joint Care']['reason'], 'Arthritis')
        self.assertEqual(groups['Bone Health']['reason'], 'Osteoporosis')

    def test_answers_saved_at_the_same_moment_all_reach_the_notes(self):
        from concurrent.futures import ThreadPoolExecutor
        from stellis import intake
        answers = {'goals': ['walk'], 'focus': ['blood-health'], 'conditions': 'Asthma', 'medicines': 'Iron'}
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda item: profile.save(1, *item), answers.items()))
        notes = {s['heading']: s['items'] for s in intake.load()['notes']}
        self.assertEqual(notes['Conditions'], ['Asthma'])
        self.assertEqual(notes['Medicines'], ['Iron'])
        self.assertIn('Walk 20 minutes, 4 days a week', notes['Goals'])
        self.assertIn('Blood Health', notes['Goals'])

    def test_finishing_setup_tracks_each_goal_with_its_target(self):
        self.answer({'goals': [{'id': 'walk', 'amount': 30, 'times_per_week': 5}, 'sleep']})
        onboarding.choose(1, [], 'review', {}, None)
        groups = health_catalogue.recommend()['groups']
        onboarding.complete(1, record_groups=[{key: g[key] for key in
                                               ('notebook_name', 'template_id', 'collection_id', 'items', 'goal')}
                                              for g in groups])
        tracked = goals.overview('2026-10-28', 0, '12:00')['goals']
        self.assertEqual(sorted(' '.join(g['title_parts']) for g in tracked),
                         ['Sleep at least 7 hours', 'Walk 30 minutes'])
        self.assertEqual(sorted(g['times_per_week'] for g in tracked), [5, 5])


if __name__ == '__main__':
    unittest.main()
