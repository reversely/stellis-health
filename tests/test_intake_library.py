"""Offline term search and bounded authenticated API responses."""
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from stellis import intake_library, server


class LibraryTest(unittest.TestCase):
    def test_every_goal_choice_is_measurable(self):
        labels = [item["label"] for item in intake_library.search("goals", "")["items"]]
        self.assertIn("Walk 20 minutes, 4 days a week", labels)
        self.assertIn("Sleep at least 7 hours, 5 nights a week", labels)
        self.assertTrue(all(any(ch.isdigit() for ch in label) for label in labels), labels)

    def test_generic_and_brand_names_have_real_reference_ids(self):
        for query in ("metformin", "Tylenol", "folic acid"):
            with self.subTest(query=query):
                result = intake_library.search("medicines", query)
                self.assertGreater(result["total"], 0)
                self.assertLessEqual(len(result["items"]), 20)
                for item in result["items"]:
                    self.assertTrue(item["id"].isdigit())
                    self.assertNotIn("I take", item["answer"])
                    self.assertIn("discuss", item["answer"])

    def test_search_does_not_recommend_medicines_before_a_query(self):
        self.assertEqual(intake_library.search("medicines", "")["items"], [])
        self.assertEqual(intake_library.search("medicines", "<img src=x>")["items"], [])

    def test_api_is_authenticated_and_bounds_input(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "STELLIS_DATA_DIR": directory, "STELLIS_AUTH_DB_DIR": directory,
            "STELLIS_AUTH_MODE": "required"}):
            client = TestClient(server.app)
            self.assertEqual(client.get("/api/v1/intake/library").status_code, 401)
        with patch.dict(os.environ, {"STELLIS_AUTH_MODE": "off"}):
            client = TestClient(server.app)
            self.assertEqual(client.get("/api/v1/intake/library", params={"q": "a" * 101}).status_code, 422)
            self.assertEqual(client.get("/api/v1/intake/library", params={"category": "invented"}).status_code, 422)

    def test_conditions_are_condition_names_not_goal_or_fitness_templates(self):
        result=intake_library.search('conditions','asthma')
        self.assertIn('Asthma',[item['label'] for item in result['items']])
        self.assertTrue(all(item['url'].startswith('https://medlineplus.gov/') for item in result['items']))
        self.assertEqual(intake_library.search('conditions','strength training')['items'],[])
        self.assertEqual(intake_library.search('conditions','')['items'],[])
        self.assertIn('MedlinePlus',result['attribution'])
