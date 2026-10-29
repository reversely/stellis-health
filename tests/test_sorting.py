"""Tests for proposing notebooks for unsorted records and applying them."""

import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import documents
from stellis import notebook_sources
from stellis import notebooks
import sample_records
from stellis import server
from stellis import sorting


def _item(index: int) -> sorting.Item:
    return sorting.Item("document", f"d{index}", f"Record {index}",
                        "2024-01-01")


class ParseGroupsTest(unittest.TestCase):

    def test_maps_names_drops_bad_indexes_and_repeats(self):
        items = [_item(index) for index in range(1, 5)]
        reply = json.dumps({
            "groups": [
                {
                    "notebook": "  Nutrition ",
                    "reason": "Dietitian visits",
                    "items": [1, 2, 9, "3"]
                },
                {
                    "notebook": "Heart care",
                    "items": [2, 3]
                },
                {
                    "notebook": "Empty",
                    "items": [9]
                },
                {
                    "notebook": "",
                    "items": [4]
                },
                "not a group",
            ]
        })

        groups = sorting.parse_groups(reply, items, {"heart care": "nb-1"})

        self.assertEqual([(group["notebook_name"], group["notebook_id"],
                           [item["id"]
                            for item in group["items"]])
                          for group in groups],
                         [("Nutrition", None, ["d1", "d2"]),
                          ("Heart care", "nb-1", ["d3"])])
        self.assertEqual(groups[0]["reason"], "Dietitian visits")

    def test_groups_for_the_same_notebook_merge_before_the_cap(self):
        items = [_item(index) for index in range(1, 13)]
        # One group per record, as a model sometimes replies: twelve groups, two notebooks.
        reply = json.dumps({"groups": [
            {"notebook": "IVF" if index % 2 else "Fertility", "reason": f"r{index}", "items": [index]}
            for index in range(1, 13)]})

        groups = sorting.parse_groups(reply, items, {})

        self.assertEqual([(g["notebook_name"], len(g["items"])) for g in groups], [("IVF", 6), ("Fertility", 6)])
        self.assertEqual(groups[0]["reason"], "r1")

    def test_items_written_as_index_objects_are_read(self):
        items = [_item(index) for index in range(1, 4)]
        # Token Factory's sorting model replied in this shape during the #226 film capture.
        reply = json.dumps({"groups": [
            {"family": "Blood Health", "notebook": "Anemia Management", "reason": "iron",
             "items": [{"index": 1}, {"index": 3}]}]})

        groups = sorting.parse_groups(reply, items, {})

        self.assertEqual([item["id"] for item in groups[0]["items"]], ["d1", "d3"])

    def test_items_written_as_record_labels_are_read(self):
        # #258: on a 25-record year the sorting model wrote items the way the prompt labels them.
        items = [_item(index) for index in range(1, 4)]
        reply = json.dumps({"groups": [{"notebook": "IVF", "reason": "cycle", "items": ["record [3]", "Record [1]", "record 9", "2"]}]})

        groups = sorting.parse_groups(reply, items, {})

        self.assertEqual([item["id"] for item in groups[0]["items"]], ["d3", "d1"])

    def test_unreadable_reply_is_an_error(self):
        for reply in ("no", "[]", '{"groups": {}}'):
            with self.subTest(reply=reply):
                with self.assertRaises(sorting.SortingError):
                    sorting.parse_groups(reply, [_item(1)], {})


class SortingApiTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ,
                                      {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        sample_records.load()
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def unsorted(self) -> list[dict[str, str]]:
        return self.client.get("/api/v1/records/unsorted").json()["items"]

    def test_suggest_sends_titles_and_opening_text(self):
        reply = json.dumps(
            {"groups": [{
                "notebook": "Nutrition",
                "items": [1, 2]
            }]})
        turn = mock.AsyncMock(return_value=(reply, "session"))

        with mock.patch.object(agent_gateway, "turn", turn):
            response = self.client.post("/api/v1/records/sort/suggest")

        body = response.json()
        prompt = turn.await_args.args[0]
        context = json.loads(prompt.split("\n\n", 1)[1])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(context["records"]), 50)
        self.assertEqual(context["records"][0]["date"], "1997-12-04")
        self.assertIn("Hospital admission", context["records"][0]["title"])
        self.assertLessEqual(
            max(len(record["opening_text"]) for record in context["records"]),
            sorting.EXCERPT_CHARS)
        self.assertEqual(len(body["groups"][0]["items"]), 2)
        self.assertEqual(len(body["unsorted"]), 48)

    def test_an_unreadable_reply_is_retried_once(self):
        good = json.dumps({"groups": [{"family": "Nutrition", "notebook": "Nutrition", "items": [1]}]})
        turn = mock.AsyncMock(side_effect=[("not json", "s"), (good, "s")])
        with mock.patch.object(agent_gateway, "turn", turn):
            body = self.client.post("/api/v1/records/sort/suggest")
        self.assertEqual(body.status_code, 200, body.text)
        self.assertEqual(turn.await_count, 2)
        turn = mock.AsyncMock(return_value=("not json", "s"))
        with mock.patch.object(agent_gateway, "turn", turn):
            self.assertEqual(self.client.post("/api/v1/records/sort/suggest").status_code, 503)
        self.assertEqual(turn.await_count, 2)

    def test_new_notebooks_are_placed_in_the_named_family(self):
        reply = json.dumps({"groups": [
            {"family": "Reproductive Health", "notebook": "IVF cycle", "reason": "Monitoring scans", "items": [1]},
            {"family": "Reproductive Health", "notebook": "Fertility", "items": [2]},
            {"family": "Blood Health", "notebook": "Anemia", "items": [3]},
            {"family": "Reproductive Health", "notebook": "Reproductive Health", "items": [4]},
            {"family": "Not a family", "notebook": "Elsewhere", "items": [5]},
            {"family": "Reproductive Health", "notebook": "Pregnancy", "items": [6]},
        ]})
        turn = mock.AsyncMock(return_value=(reply, "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            body = self.client.post("/api/v1/records/sort/suggest").json()
        context = json.loads(turn.await_args.args[0].split("\n\n", 1)[1])
        self.assertIn({"family": "Reproductive Health", "usual_notebooks": ["Menstrual Health", "Fertility", "IVF",
                       "Egg Freezing", "Menopause", "Contraception"]}, context["families"])
        placed = {g["notebook_name"]: g for g in body["groups"]}
        self.assertEqual(set(placed), {"IVF cycle", "Fertility", "Bloodwork", "Pregnancy"})
        # A diagnosis is never a notebook: "Anemia" becomes the catalogue's Bloodwork notebook.
        self.assertEqual(placed["Bloodwork"]["template_id"], "blood-health--blood-health")
        # The catalogue's Pregnancy notebook stays in its own family, whatever family the model named.
        self.assertEqual(placed["Pregnancy"]["collection_name"], "Pregnancy & Postpartum")
        self.assertEqual(placed["IVF cycle"]["collection_id"], "reproductive-health")
        self.assertIsNone(placed["IVF cycle"]["template_id"])
        self.assertEqual(placed["Fertility"]["template_id"], "reproductive-health--fertility")
        created = self.client.post("/api/v1/records/sort/apply", json={
            "notebook_name": "IVF cycle", "collection_id": "reproductive-health",
            "items": placed["IVF cycle"]["items"]}).json()
        from stellis import collections
        family = next(c for c in collections.list_collections() if c["taxonomy_id"] == "reproductive-health")
        self.assertEqual(family["name"], "Reproductive Health")
        self.assertIn(created["notebook_id"], family["notebook_ids"])
        refused = self.client.post("/api/v1/records/sort/apply", json={"notebook_name": "Loose idea", "items": []})
        self.assertEqual(refused.status_code, 400)

    def test_apply_creates_the_notebook_and_moves_the_group(self):
        dietitian = [
            item for item in self.unsorted() if "Dietitian" in item["title"]
        ]

        response = self.client.post("/api/v1/records/sort/apply",
                                    json={
                                        "notebook_name": "Nutrition",
                                        "items": dietitian
                                    })

        body = response.json()
        self.assertEqual((body["moved"], body["failed"]), (len(dietitian), []))
        self.assertEqual(len(documents.list_all(body["notebook_id"])),
                         len(dietitian))
        self.assertEqual(len(self.unsorted()), 50 - len(dietitian))
        again = self.client.post("/api/v1/records/sort/apply",
                                 json={
                                     "notebook_name": "nutrition",
                                     "items": []
                                 }).json()
        self.assertEqual(again["notebook_id"], body["notebook_id"])

    def test_full_notebook_reports_the_records_it_could_not_take(self):
        items = self.unsorted()[:4]

        with mock.patch.object(notebook_sources, "LIMIT", 3):
            body = self.client.post("/api/v1/records/sort/apply",
                                    json={
                                        "notebook_name": "Heart Care",
                                        "items": items
                                    }).json()

        self.assertEqual(body["moved"], 3)
        self.assertEqual(body["failed"][0]["error"],
                         "This notebook is full at 3 sources")

    def test_move_one_record_and_back(self):
        book = notebooks.create("Dental")["id"]
        dental = next(
            item for item in self.unsorted() if "Dental" in item["title"])

        moved = self.client.put(
            f"/api/v1/records/document/{dental['id']}/notebook",
            json={"notebook_id": book})
        back = self.client.put(
            f"/api/v1/records/document/{dental['id']}/notebook",
            json={"notebook_id": None})
        unknown = self.client.put("/api/v1/records/surgery/x/notebook",
                                  json={"notebook_id": book})

        self.assertEqual((moved.status_code, back.status_code), (200, 200))
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(len(self.unsorted()), 50)

    def test_model_failure_is_a_503(self):
        failing = mock.AsyncMock(
            side_effect=agent_gateway.GatewayError("Allowance used"))
        with mock.patch.object(agent_gateway, "turn", failing):
            response = self.client.post("/api/v1/records/sort/suggest")

        self.assertEqual((response.status_code, response.json()["detail"]),
                         (503, "Allowance used"))


if __name__ == "__main__":
    unittest.main()
