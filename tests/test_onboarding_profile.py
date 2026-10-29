"""Confirmed profile fields, local suggestions and editable speech candidates."""
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

from stellis import auth, intake, onboarding_profile as profile, health_catalogue, health_preferences


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STELLIS_DATA_DIR':self.temp.name,'STELLIS_AUTH_DB_DIR':self.temp.name}, clear=True)
        env.start(); self.addCleanup(env.stop)
        auth.register('profile-user','profile-test-password')

    def test_goals_are_measurable_targets_with_adjustable_amounts(self):
        self.assertEqual(profile.BY_ID['goals']['kind'],'goals')
        profile.save(1,'goals',['sleep',{'id':'walk','amount':30,'times_per_week':5}])
        goals=next(s['items'] for s in intake.load()['notes'] if s['heading']=='Goals')
        self.assertEqual(goals,['Sleep at least 7 hours, 5 nights a week','Walk 30 minutes, 5 days a week'])
        for unmeasured in (['Get more sleep'],[{'id':'walk','amount':17}],[{'id':'sleep','times_per_week':9}]):
            with self.subTest(unmeasured=unmeasured), self.assertRaises(ValueError):
                profile.save(1,'goals',unmeasured)

    def test_a_name_is_saved_to_the_account_and_survives_reload(self):
        profile.save(1,'name','Alexandria')
        self.assertEqual(profile.status()['answers']['name'],'Alexandria')
        self.assertEqual(auth.profile(1)['name'],'Alexandria')

    def test_cycle_tracking_is_one_question_with_all_four_choices(self):
        ids=[f['id'] for f in profile.status()['fields']]
        self.assertNotIn('period',ids)
        self.assertEqual(ids.count('cycle_tracking'),1)
        self.assertEqual(profile.BY_ID['cycle_tracking']['choices'],['Yes','No',"I don't have a period",'Maybe later'])
        for choice in profile.BY_ID['cycle_tracking']['choices']:
            profile.save(1,'cycle_tracking',choice)
            self.assertEqual(profile.status()['step'],'birth_date')
            self.assertEqual(profile.status()['answers']['cycle_tracking'],choice)
        self.assertFalse(health_preferences.preferences()['experiences_periods'])

    def test_period_answer_uses_shared_tracker_preference_without_inventing_records(self):
        profile.save(1, 'gender', 'Woman')
        self.assertIsNone(health_preferences.preferences()['experiences_periods'])
        for answer, expected in [('Yes', True), ('No', False), ('Not currently', None), ('Prefer not to say', None), (None, None)]:
            profile.save(1, 'period', answer)
            self.assertIs(health_preferences.preferences()['experiences_periods'], expected)
        health_preferences.save(False)
        self.assertEqual(profile.status()['answers']['period'], 'No')
        health_preferences.save(None)
        profile.save(1, 'cycle_tracking', 'Yes')
        self.assertTrue(health_preferences.preferences()['experiences_periods'])

    def test_invalid_dates_and_unlisted_interests_are_rejected(self):
        for date in ['2025-02-30',(datetime.date.today()+datetime.timedelta(days=1)).isoformat()]:
            with self.assertRaises(ValueError):profile.save(1,'birth_date',date)
        with self.assertRaises(ValueError):profile.save(1,'interests',['invented'])
        with self.assertRaises(ValueError):profile.save(1,'period','invented')
        with self.assertRaises(ValueError):profile.save(1,'name','x'*101)

    def test_goals_and_wellness_choices_create_reviewable_suggestions(self):
        profile.save(1,'goals',['sleep','produce'])
        profile.save(1,'conditions','Pregnant')
        profile.save(1,'interests',['nutrition','wellness','sleep','nutrition'])
        self.assertEqual(profile.status()['answers']['interests'],['nutrition','wellness','sleep'])
        suggestions=health_catalogue.recommend()['groups']
        self.assertTrue({'nutrition--nutrition','sleep--sleep','pregnancy-postpartum--pregnancy','preventive-care--preventive-care'}
                        .issubset({g['template_id'] for g in suggestions}))
        sleep=next(g for g in suggestions if g['template_id']=='sleep--sleep')
        self.assertEqual(sleep['goal'],'Sleep at least 7 hours, 5 nights a week')
        from stellis import notebooks
        self.assertEqual(notebooks.list_notebooks(),[])

    def test_corrected_profile_notes_replace_the_previous_projection(self):
        profile.save(1,'conditions','Asthma')
        profile.save(1,'conditions','No confirmed conditions')
        text=str(intake.load()['notes'])
        self.assertNotIn('Asthma',text);self.assertIn('No confirmed conditions',text)

    def test_changing_an_earlier_answer_returns_to_the_current_question(self):
        for field,value in (('name','Alex'),('gender','Man'),('cycle_tracking','No')):
            profile.save(1,field,value)
        self.assertEqual(profile.status()['step'],'birth_date')
        profile.save(1,'name','Alexandria')
        self.assertEqual(profile.status()['step'],'birth_date')

    def test_focus_is_limited_to_five_and_projects_goals_not_conditions(self):
        choices=profile.focus_choices()
        with self.assertRaises(ValueError): profile.save(1,'focus',[item['id'] for item in choices[:6]])
        with self.assertRaises(ValueError): profile.save(1,'focus',['invented'])
        selected=[item['id'] for item in choices[:5]]
        profile.save(1,'focus',selected)
        self.assertEqual(profile.status()['answers']['focus'],selected)
        self.assertEqual(profile.status()['step'],'complete')
        notes={item['heading']:item['items'] for item in intake.load()['notes']}
        self.assertIn('Fitness',notes['Goals'])
        self.assertNotIn('Fitness',notes.get('Conditions',[]))
        profile.save(1,'focus',None)
        self.assertNotIn('Fitness',str(intake.load()['notes']))

    def test_focus_choices_are_families_and_template_answers_resolve_to_them(self):
        profile.save(1,'focus',['fitness--fitness','fitness--mobility','reproductive-health--reproductive-health'])
        self.assertEqual(profile.status()['answers']['focus'],['fitness','reproductive-health'])
        self.assertEqual(len(profile.focus_choices()),32)

    def test_legacy_condition_answer_and_step_survive_new_interest_question(self):
        from stellis import onboarding
        profile.save(1,'conditions','Asthma')
        state=onboarding._read();state['profile_step']='conditions';onboarding._save(state)
        self.assertEqual(profile.status()['step'],'conditions')
        self.assertEqual(profile.status()['answers']['conditions'],'Asthma')
        profile.save(1,'focus',['fitness--strength-training'])
        notes={item['heading']:item['items'] for item in intake.load()['notes']}
        self.assertIn('Asthma',notes['Conditions'])
        self.assertIn('Fitness',notes['Goals'])
