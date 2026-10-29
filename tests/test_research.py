"""Outward research keeps personal details out of every query that leaves."""

import json
import os
import tempfile
import unittest
import urllib.parse
from unittest import mock

from fastapi import testclient

from stellis import agent_gateway
from stellis import notebooks
from stellis import pages
from stellis import research
from stellis import server

IDENTITY = frozenset({"maya", "chen", "vale"})
MEDLINE = (b'<nlmSearchResult><list><document url="https://medlineplus.gov/ironde.html">'
           b'<content name="title">Iron Deficiency <span class="qt0">Anemia</span></content>'
           b'<content name="FullSummary">Ferritin shows how much iron the body stores.</content>'
           b'</document></list></nlmSearchResult>')
EMPTY_MEDLINE = b"<nlmSearchResult><list></list></nlmSearchResult>"
PUBMED_SEARCH = json.dumps({"esearchresult": {"idlist": ["123"]}}).encode()
PUBMED_FETCH = (b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>"
                b"<ArticleTitle>Serum ferritin and iron stores</ArticleTitle><Abstract>"
                b"<AbstractText>Low ferritin indicates depleted stores.</AbstractText>"
                b"</Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>")
TAVILY = json.dumps({"results": [{"title": "Ferritin test", "url": "https://example.org/ferritin",
                                  "content": "A ferritin test measures stored iron."}]}).encode()


class Network:
    """Stands in for the internet and records the query each request carried."""

    def __init__(self, medline=MEDLINE):
        self.medline = medline
        self.queries: list[tuple[str, str]] = []

    def __call__(self, url, data=None, headers=None):
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.netloc == "api.tavily.com":
            self.queries.append(("Tavily", json.loads(data)["query"]))
            return TAVILY
        if "term" in params:
            self.queries.append((parsed.netloc, params["term"][0]))
        if parsed.netloc == "wsearch.nlm.nih.gov":
            return self.medline
        return PUBMED_SEARCH if "esearch" in parsed.path else PUBMED_FETCH


class ProviderPolicyTest(unittest.TestCase):

    def test_privacy_page_follows_deployment_route_without_revealing_keys(self):
        with testclient.TestClient(server.app) as client:
            with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": "tavily", "TAVILY_API_KEY": "tvly-private-test"}):
                page = client.get("/privacy").text
                self.assertIn("searches Tavily directly when a key is available", page)
                self.assertIn("otherwise searches MedlinePlus and PubMed", page)
                self.assertNotIn("tvly-private-test", page)
            with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": ""}):
                self.assertIn("searches MedlinePlus and PubMed first", client.get("/privacy").text)


class CleanTest(unittest.TestCase):

    def test_names_numbers_dates_and_filler_are_removed(self):
        query = research.clean({
            "topic": "Maya Chen ferritin 11 ug/L",
            "terms": ["Jan 21 2022", "Dr Vale", "low iron stores", "maya.chen@example.com", "my"],
        }, IDENTITY)
        self.assertEqual(query, "ferritin low iron stores")

    def test_fixed_medical_names_with_digits_or_letters_survive(self):
        self.assertEqual(research.clean({"topic": "vitamin D", "terms": ["B12", "HbA1c", "T4", "11 ug/L"]}, frozenset()),
                         "vitamin D B12 HbA1c T4")
        research.assert_clean("vitamin D B12 HbA1c T4", frozenset())
        with self.assertRaises(research.UnsafeQuery):
            research.assert_clean("B12 448812", frozenset())
        self.assertIsNone(research.clean({"topic": "D", "terms": ["E"]}, frozenset()))

    def test_only_one_demographic_word_stays(self):
        query = research.clean({"topic": "anemia", "terms": ["woman", "pregnant", "postpartum"]}, frozenset())
        self.assertEqual(query, "anemia woman")

    def test_a_query_with_nothing_medical_left_is_dropped(self):
        self.assertIsNone(research.clean({"topic": "Maya Chen", "terms": ["2022", "woman"]}, IDENTITY))

    def test_the_send_check_refuses_identity_and_values(self):
        for query in ("ferritin maya", "ferritin 11", "ferritin a@b"):
            with self.assertRaises(research.UnsafeQuery):
                research.assert_clean(query, IDENTITY)
        research.assert_clean("ferritin low iron stores", IDENTITY)

    def test_markup_in_summaries_becomes_spaces(self):
        self.assertEqual(research._text("What is iron?<p>Iron is a <b>mineral</b>."), "What is iron? Iron is a mineral.")

    def test_the_medlineplus_retry_uses_the_first_specific_word(self):
        self.assertEqual(research._broader("low ferritin levels interpretation"), "ferritin")
        self.assertIsNone(research._broader("ferritin"))
        self.assertIsNone(research._broader("low levels"))
        self.assertEqual(research._broader("relationship between ferritin anemia"), "ferritin")
        self.assertEqual(research._broader("Interpreting low ferritin levels"), "ferritin")

    def test_titled_names_in_records_count_as_identity(self):
        with mock.patch("stellis.auth.profile", side_effect=LookupError), \
             mock.patch("stellis.onboarding_profile.status", return_value={"answers": {"name": "Maya"}}), \
             mock.patch("stellis.people.list_people", return_value=[{"name": "Sam Rivera"}]):
            terms = research.identity_terms(["Ordered by Dr Vale on Jan 18.", "Seen by Nurse Okafor",
                                             "Patient: Lena Ortiz\nNext of kin: Tomas Ortiz"])
        self.assertTrue({"maya", "sam", "rivera", "vale", "okafor", "lena", "ortiz", "tomas"} <= terms)


class ResearchTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.identity = mock.patch.object(research, "identity_terms", return_value=IDENTITY)
        self.identity.start()
        self.addCleanup(self.identity.stop)

    async def run_research(self, planned, network, key="tvly-test"):
        with mock.patch.object(research, "plan", mock.AsyncMock(return_value=planned)), \
             mock.patch.object(research, "_http", network), \
             mock.patch("stellis.tavily.available_key", return_value=key):
            return await research.research("Is my ferritin of 11 low, Dr Vale said?", [])

    async def test_no_identity_term_or_value_reaches_any_search_client(self):
        network = Network(EMPTY_MEDLINE)
        planned = [{"topic": "Maya Chen ferritin 11", "terms": ["Dr Vale", "2022-01-21", "iron stores"]},
                   {"topic": "maya", "terms": ["chen", "42"]}]
        findings = await self.run_research(planned, network)
        self.assertTrue(network.queries)
        for _, query in network.queries:
            self.assertNotRegex(query, r"\d|@")
            self.assertFalse(set(query.casefold().split()) & IDENTITY, query)
        self.assertEqual(findings.blocked, 1)
        # The MedlinePlus retry with one key word is listed as its own sent query.
        self.assertEqual([q["text"] for q in findings.queries], ["ferritin iron stores", "ferritin"])

    async def test_public_sources_answer_first_and_tavily_fills_only_a_thin_result(self):
        network = Network()
        findings = await self.run_research([{"topic": "ferritin", "terms": ["iron stores"]}], network)
        self.assertEqual([name for name, _ in network.queries], ["wsearch.nlm.nih.gov", "eutils.ncbi.nlm.nih.gov"])
        self.assertEqual(findings.queries[0]["providers"], ["MedlinePlus", "PubMed"])
        self.assertEqual([r.provider for r in findings.results], ["MedlinePlus", "PubMed"])
        self.assertEqual(findings.results[0].title, "Iron Deficiency Anemia")

        thin = Network(EMPTY_MEDLINE)
        with mock.patch.object(research, "pubmed", return_value=[]):
            findings = await self.run_research([{"topic": "ferritin"}], thin)
        self.assertEqual(findings.queries[0]["providers"], ["MedlinePlus", "PubMed", "Tavily"])
        self.assertIn(("Tavily", "ferritin"), thin.queries)

    async def test_tavily_only_skips_public_providers_and_broader_retry(self):
        network = Network()
        announced = []
        with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": "tavily"}), \
             mock.patch.object(research, "plan", mock.AsyncMock(return_value=[
                 {"topic": "Maya Chen ferritin 11", "terms": ["iron stores", "Dr Vale", "2022-01-21", "a@b.com"]}])), \
             mock.patch.object(research, "_http", network), \
             mock.patch("stellis.tavily.available_key", return_value="tvly-private-test"), \
             mock.patch.object(research, "assert_clean", wraps=research.assert_clean) as gate:
            findings = await research.research("ferritin", [], lambda q, p: announced.append((q, p)))
        self.assertEqual(network.queries, [("Tavily", "ferritin iron stores")])
        self.assertEqual(announced, [("ferritin iron stores", ["Tavily"])])
        self.assertEqual(findings.queries[0]["providers"], ["Tavily"])
        self.assertTrue(all(r.provider == "Tavily" for r in findings.results))
        self.assertGreaterEqual(gate.call_count, 2)
        self.assertNotIn("tvly-private-test", json.dumps(findings.summary()))

    async def test_tavily_only_failure_does_not_send_to_another_provider(self):
        with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": "tavily"}), \
             mock.patch.object(research, "_http", side_effect=OSError("offline")) as network:
            findings = await self.run_research([{"topic": "ferritin iron"}], network)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(findings.queries[0]["providers"], ["Tavily"])
        self.assertEqual(findings.results, [])

    async def test_tavily_only_without_key_falls_back_and_records_reason(self):
        network = Network()
        with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": "tavily"}), \
             mock.patch("stellis.runtime_trace.event") as event:
            findings = await self.run_research([{"topic": "ferritin"}], network, key=None)
        self.assertEqual(findings.queries[0]["providers"], ["MedlinePlus", "PubMed"])
        self.assertIn("Tavily key unavailable", findings.summary()["fallback"])
        event.assert_any_call("research_provider_fallback", error_code="tavily_key_unavailable")
        self.assertNotIn("Tavily", [p for p, _ in network.queries])

    async def test_tavily_only_still_blocks_an_unsafe_query_at_send_time(self):
        network = Network()
        with mock.patch.dict(os.environ, {"STELLIS_RESEARCH_PROVIDERS": "tavily"}), \
             mock.patch.object(research, "clean", return_value="ferritin maya"):
            findings = await self.run_research([{"topic": "ferritin"}], network)
        self.assertEqual(network.queries, [])
        self.assertEqual(findings.blocked, 1)

    async def test_a_retry_word_already_sent_is_not_sent_again(self):
        network = Network(EMPTY_MEDLINE)
        with mock.patch.object(research, "pubmed", return_value=[]):
            findings = await self.run_research(
                [{"topic": "ferritin", "terms": ["anemia"]}, {"topic": "ferritin", "terms": ["iron"]}], network, key=None)
        self.assertEqual([q["text"] for q in findings.queries], ["ferritin anemia", "ferritin", "ferritin iron"])

    async def test_without_a_tavily_key_only_public_sources_are_asked(self):
        network = Network(EMPTY_MEDLINE)
        with mock.patch.object(research, "pubmed", return_value=[]):
            findings = await self.run_research([{"topic": "ferritin"}], network, key=None)
        self.assertNotIn("Tavily", [name for name, _ in network.queries])
        self.assertEqual(findings.queries[0]["providers"], ["MedlinePlus", "PubMed"])

    async def test_a_failing_provider_does_not_stop_the_others(self):
        network = Network()

        def flaky(url, data=None, headers=None):
            if "wsearch.nlm.nih.gov" in url:
                raise OSError("offline")
            return network(url, data, headers)

        findings = await self.run_research([{"topic": "ferritin"}], flaky)
        # PubMed alone is thin, so Tavily fills in.
        self.assertEqual([r.provider for r in findings.results], ["PubMed", "Tavily"])

    async def test_the_planner_sees_record_excerpts_for_context(self):
        turn = mock.AsyncMock(return_value=('{"queries": []}', "session"))
        with mock.patch.object(agent_gateway, "turn", turn):
            await research.plan("What do 4AA and 3BB mean?", ["Embryology report: embryos graded 4AA and 3BB."])
        self.assertIn("Embryology report", turn.await_args.args[0])
        self.assertIn("What do 4AA and 3BB mean?", turn.await_args.args[0])

    def test_question_words_never_reach_a_query(self):
        self.assertEqual(research.clean({"topic": "when to evaluate menstrual irregularities"}, frozenset()),
                         "menstrual irregularities")

    def test_plan_reads_fenced_json_and_caps_the_number_of_searches(self):
        reply = '```json\n{"queries": [{"topic": "a"}, {"topic": "b"}, {"topic": "c"}]}\n```'
        self.assertEqual(len(research.parse_plan(reply)), research.MAX_QUERIES)
        self.assertEqual(research.parse_plan("not json"), [])


class NotebookResearchTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        environment = mock.patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.book = notebooks.create("Blood Health")["id"]
        self.page = pages.create("Blood test", self.book,
                                 "Ferritin 11 ug/L, range 15 to 150, flagged low. Ordered by Dr Vale.")["id"]
        self.client = testclient.TestClient(server.app)
        self.addCleanup(self.client.close)

    def test_answer_cites_a_record_and_a_public_source_and_lists_its_queries(self):
        network = Network()
        reply = ("Your ferritin is below this lab's range. [[source:1]] Low ferritin "
                 "means the body's iron stores are depleted. [[source:2]]")
        turn = mock.AsyncMock(return_value=(reply, "session"))
        planned = [{"topic": "low ferritin", "terms": ["iron stores", "Dr Vale", "11"]}]
        with mock.patch.object(agent_gateway, "turn", turn), \
             mock.patch.object(research, "plan", mock.AsyncMock(return_value=planned)), \
             mock.patch.object(research, "_http", network), \
             mock.patch("stellis.tavily.available_key", return_value=None):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask", json={
                "question": "Does my low ferritin mean my iron stores are low?",
                "source_ids": ["page:" + self.page],
                "research": True,
                "request_id": "question-0001",
            })
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()
        kinds = [citation.get("kind", "record") for citation in answer["citations"]]
        self.assertEqual(kinds, ["record", "public"])
        self.assertEqual(answer["citations"][1]["target"], "https://medlineplus.gov/ironde.html")
        self.assertEqual(answer["research"]["queries"][0]["text"], "low ferritin iron stores")
        for _, query in network.queries:
            self.assertNotIn("vale", query.casefold())
            self.assertNotRegex(query, r"\d")
        prompt = turn.await_args.args[0]
        self.assertIn('"kind": "public"', prompt)
        saved = self.client.get(f"/api/v1/notebooks/{self.book}/chat").json()["messages"][-1]
        self.assertEqual(saved["research"], answer["research"])
        progress = self.client.get(f"/api/v1/notebooks/{self.book}/ask/question-0001/progress").json()
        self.assertEqual(progress["stage"], "done")
        self.assertEqual([q["text"] for q in progress["queries"]], [q["text"] for q in answer["research"]["queries"]])
        other = self.client.get("/api/v1/notebooks/another/ask/question-0001/progress").json()
        self.assertEqual(other, {"stage": "unknown", "queries": []})
        trace = self.client.get(f"/api/v1/traces/{answer['trace_id']}").json()
        self.assertEqual(trace["answer"]["research"], answer["research"])
        self.assertIn("research_done", [event["name"] for event in trace["trace"]["events"]])
        self.assertEqual(trace["answer"]["sources"][1]["title"], "Iron Deficiency Anemia (MedlinePlus)")

    def test_without_research_nothing_leaves(self):
        network = mock.Mock(side_effect=AssertionError("no network"))
        turn = mock.AsyncMock(return_value=("Your ferritin is low. [[source:1]]", "session"))
        with mock.patch.object(agent_gateway, "turn", turn), mock.patch.object(research, "_http", network):
            response = self.client.post(f"/api/v1/notebooks/{self.book}/ask", json={
                "question": "Is my ferritin low?", "source_ids": ["page:" + self.page]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["research"])
        network.assert_not_called()
