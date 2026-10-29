"""Outward research with personal details kept out of every query.

The model proposes search fields; code builds each query from them, removes
anything that could identify the person, and checks the query again just
before a client sends it. Public medical sources answer first by default; a
deployment may select Tavily alone. Results come back as passages an answer
may cite beside the person's own records, together with the exact queries that left.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree

MAX_QUERIES = 2
MAX_TERMS = 6
MAX_QUERY_CHARS = 100
RESULTS_PER_PROVIDER = 3
# In the default route Tavily fills results below this threshold.
PUBLIC_ENOUGH = 2
SNIPPET_CHARS = 700
TIMEOUT_SECONDS = 8

_TITLES = re.compile(
    r"\b(?:Dr|Doctor|Prof|Professor|Nurse|Midwife|Mr|Mrs|Ms|Miss|Mx)\.?\s+"
    r"((?:[A-Z][\w'’-]+)(?:[ \t]+[A-Z][\w'’-]+)?)")
# Names written as labelled fields, as on most clinic letters and lab reports.
_LABELLED = re.compile(
    r"\b(?:Patient(?: name)?|Name|Partner|Spouse|Next of kin|Contact|Referred by|Ordered by)\s*:\s*"
    r"((?:[A-Z][\w'’-]+)(?:[ \t]+[A-Z][\w'’-]+){0,2})")
_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)
_DIGIT_OR_EMAIL = re.compile(r"\d|@")
_TAGS = re.compile(r"<[^>]+>")
# Words that describe the person rather than the medical topic. One may stay.
_DEMOGRAPHIC = frozenset({
    "woman", "women", "man", "men", "female", "male", "girl", "boy", "pregnant",
    "teen", "teenager", "elderly", "adult", "child", "infant", "postpartum",
    "transgender", "nonbinary", "non-binary", "old", "young", "aged",
})
# Titles and calendar words point at a person or a date, never at the topic.
_NOISE = frozenset({
    "dr", "doctor", "prof", "professor", "nurse", "midwife", "mr", "mrs", "ms", "miss", "mx",
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june",
    "jul", "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov",
    "november", "dec", "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "today", "yesterday", "tomorrow",
})
# Fixed medical names that contain a digit or are very short. Being fixed
# strings, they cannot carry personal details, so they pass the digit rule.
_TERMS = frozenset({
    "b1", "b2", "b3", "b6", "b9", "b12", "d2", "d3", "t3", "t4", "a1c", "hba1c", "brca1",
    "brca2", "her2", "cd4", "covid-19", "h1n1", "ct", "iud", "ivf", "hpv", "ldl", "hdl", "tsh",
})
_VITAMIN_LETTERS = frozenset({"a", "b", "c", "d", "e", "k"})
# Words that qualify a topic but name none, so they make poor single-word searches.
_GENERIC = frozenset({
    "low", "high", "level", "levels", "normal", "range", "result", "results", "test",
    "tests", "testing", "interpretation", "interpreting", "interpret", "understanding",
    "diagnosis", "diagnostic", "treatment",
    "management", "evaluation", "causes", "symptoms", "signs", "risk", "risks",
    "relationship", "between", "implications", "impact", "effect", "effects", "association",
    "link", "role", "meaning", "significance", "studies", "study", "research", "guidelines",
    "common", "chronic", "acute", "early", "stage", "stages", "type", "types", "time",
    "long", "usually", "often", "need", "needs",
})
_FILLER = frozenset({
    "my", "me", "mine", "i", "i'm", "our", "we", "you", "your", "the", "a", "an",
    "and", "or", "of", "for", "in", "on", "to", "is", "are", "was", "be",
    "what", "when", "where", "which", "why", "how", "who", "does", "do", "should", "can",
    "could", "would", "evaluate", "about", "with", "after", "before",
})
_PLAN_INSTRUCTIONS = (
    "Plan web searches that would help answer a health question with general "
    "medical knowledge from public sources. Record excerpts may follow; use "
    "them only to understand the topic. Write at most two searches. Each "
    "search is a short topic and up to five general medical terms: conditions, "
    "tests, medicines, symptoms or treatments. Never include names, ages, "
    "dates, numbers, test values, places, clinics, or anything else about the "
    "person. Reply with JSON only, in the form "
    '{"queries": [{"topic": "...", "terms": ["..."]}]}. Reply with '
    '{"queries": []} when the question is only about the person\'s own records.')


class UnsafeQuery(ValueError):
    """A query still held personal details at the point of sending."""


@dataclasses.dataclass(frozen=True)
class Result:
    """One public search result.

    Attributes:
        provider: The source that returned it, such as ``MedlinePlus``.
        title: The result's title.
        url: The result's https address.
        text: A plain-text summary or excerpt.
    """
    provider: str
    title: str
    url: str
    text: str

    @property
    def source_id(self) -> str:
        return "public:" + hashlib.sha256(self.url.encode()).hexdigest()[:16]


@dataclasses.dataclass
class Findings:
    """What one research step sent and found.

    Attributes:
        queries: Each sent query with ``text``, ``providers`` and ``results``.
        blocked: How many planned queries were dropped as unsafe or empty.
        results: The results, deduplicated by address.
        fallback: A credential-free explanation when the requested route is unavailable.
    """
    queries: list[dict[str, object]] = dataclasses.field(default_factory=list)
    blocked: int = 0
    results: list[Result] = dataclasses.field(default_factory=list)
    fallback: str | None = None

    def summary(self) -> dict[str, object]:
        return {"queries": self.queries, "blocked": self.blocked,
                **({"fallback": self.fallback} if self.fallback else {})}


def identity_terms(texts: list[str] = ()) -> frozenset[str]:
    """Collects words that could identify the person, in lower case.

    Args:
        texts: The question and record passages, scanned for titled names
            such as "Dr Vale" and labelled ones such as "Patient: Maya Chen".

    Returns:
        Words from the account and setup profile, saved people's names and
        titled names in the texts.
    """
    names: list[str] = []
    try:
        from stellis import auth  # Deferred: opens the account database.
        account = auth.profile(int(os.environ.get("STELLIS_ACCOUNT_ID", "1")))
        names += [str(account.get("name") or ""), str(account.get("username") or ""),
                  str(account.get("email") or "").split("@")[0]]
    except (LookupError, OSError, ValueError, ImportError):
        pass
    try:
        from stellis import onboarding_profile
        answers = onboarding_profile.status()["answers"]
        names.append(str(answers.get("name") or ""))
    except (LookupError, OSError, KeyError, ImportError, ValueError):
        pass
    try:
        from stellis import people
        names += [str(person["name"]) for person in people.list_people()]
    except (LookupError, OSError, KeyError, ImportError):
        pass
    for text in texts:
        names += [match.group(1) for match in _TITLES.finditer(text)]
        names += [match.group(1) for match in _LABELLED.finditer(text)]
    return frozenset(word.casefold() for name in names
                     for word in _WORD.findall(name) if len(word) >= 2)


def clean(fields: dict[str, object], identity: frozenset[str]) -> str | None:
    """Builds a query from planned fields, keeping only general medical words.

    Drops any word with a digit or "@", identity words, titles, calendar
    words, words under three letters and all but one demographic word, then
    caps the length.

    Returns:
        The query, or None when nothing useful is left.
    """
    raw = [str(fields.get("topic") or "")]
    terms = fields.get("terms")
    if isinstance(terms, list):
        raw += [str(term) for term in terms]
    kept: list[str] = []
    demographic = False
    for piece in raw:
        for token in piece.split():
            term = token.strip(".,;:!?()").casefold()
            if term in _TERMS:
                if term not in (item.casefold() for item in kept):
                    kept.append(token.strip(".,;:!?()"))
                continue
            if _DIGIT_OR_EMAIL.search(token):
                continue
            for word in _WORD.findall(token):
                folded = word.casefold()
                vitamin = folded in _VITAMIN_LETTERS and kept and kept[-1].casefold() == "vitamin"
                if not vitamin and (folded in identity or folded in _FILLER or folded in _NOISE
                                    or not 3 <= len(word) <= 40):
                    continue
                if folded in _DEMOGRAPHIC:
                    if demographic:
                        continue
                    demographic = True
                if folded not in (item.casefold() for item in kept):
                    kept.append(word)
    kept = kept[:MAX_TERMS]
    if not any(word.casefold() not in _DEMOGRAPHIC for word in kept):
        return None
    return " ".join(kept)[:MAX_QUERY_CHARS].strip()


def assert_clean(query: str, identity: frozenset[str]) -> None:
    """The last check before any search client sends a query.

    Raises:
        UnsafeQuery: The query has a digit outside a fixed medical name, an
            email, an identity word, or is longer than a built query can be.
    """
    words = _WORD.findall(query)
    residue = " ".join(token for token in query.split() if token.casefold() not in _TERMS)
    if (_DIGIT_OR_EMAIL.search(residue) or len(query) > MAX_QUERY_CHARS
            or len(words) > MAX_TERMS
            or any(word.casefold() in identity for word in words)):
        raise UnsafeQuery("A search still held personal details and was not sent")


def _http(url: str, data: bytes | None = None,
          headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def _text(value: str | None) -> str:
    spaced = re.sub(r"\s+", " ", _TAGS.sub(" ", value or ""))
    return re.sub(r"\s+([.,;:!?])", r"\1", spaced).strip()


def medlineplus(query: str) -> list[Result]:
    """Searches MedlinePlus health topics (NLM web service, no key)."""
    url = ("https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&retmax="
           f"{RESULTS_PER_PROVIDER}&term=" + urllib.parse.quote(query))
    root = ElementTree.fromstring(_http(url))
    results = []
    for document in root.iter("document"):
        content = {item.get("name"): _text("".join(item.itertext())) for item in document.iter("content")}
        address = document.get("url", "")
        if address.startswith("https://") and content.get("title"):
            results.append(Result("MedlinePlus", content["title"], address,
                                  (content.get("FullSummary") or content.get("snippet") or "")[:SNIPPET_CHARS]))
    return results


def pubmed(query: str) -> list[Result]:
    """Searches PubMed abstracts through NCBI E-utilities (no key)."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    found = json.loads(_http(base + "esearch.fcgi?db=pubmed&retmode=json&sort=relevance"
                             f"&retmax={RESULTS_PER_PROVIDER}&term=" + urllib.parse.quote(query)))
    ids = [str(item) for item in found.get("esearchresult", {}).get("idlist", []) if str(item).isdigit()]
    if not ids:
        return []
    root = ElementTree.fromstring(_http(base + "efetch.fcgi?db=pubmed&retmode=xml&id=" + ",".join(ids)))
    results = []
    for article in root.iter("PubmedArticle"):
        pmid = article.findtext(".//PMID") or ""
        title = _text("".join(article.find(".//ArticleTitle").itertext())) if article.find(".//ArticleTitle") is not None else ""
        abstract = " ".join(_text("".join(part.itertext())) for part in article.iter("AbstractText"))
        if pmid.isdigit() and title and abstract:
            results.append(Result("PubMed", title, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", abstract[:SNIPPET_CHARS]))
    return results


def tavily(query: str, key: str) -> list[Result]:
    """Searches the web through Tavily with the account's key."""
    body = json.dumps({"query": query, "max_results": RESULTS_PER_PROVIDER,
                       "search_depth": "basic"}).encode()
    payload = json.loads(_http("https://api.tavily.com/search", body, {
        "Content-Type": "application/json", "Authorization": "Bearer " + key}))
    return [Result("Tavily", _text(item.get("title")), str(item.get("url")),
                   _text(item.get("content"))[:SNIPPET_CHARS])
            for item in payload.get("results", [])
            if str(item.get("url", "")).startswith("https://") and item.get("title")]


def _broader(query: str) -> str | None:
    """Returns the query's first specific word, for a topic search that found nothing."""
    words = query.split()
    specific = [word for word in words if word.casefold() not in _GENERIC]
    return specific[0] if len(words) > 1 and specific else None


def _send(provider, query: str, identity: frozenset[str], *args) -> list[Result]:
    assert_clean(query, identity)
    return provider(query, *args)


def parse_plan(reply: str) -> list[dict[str, object]]:
    """Reads the planner's JSON, tolerating a fenced or wrapped reply."""
    match = re.search(r"\{.*\}", reply, re.DOTALL)
    try:
        queries = json.loads(match.group(0))["queries"] if match else []
    except (ValueError, KeyError, TypeError):
        return []
    return [item for item in queries if isinstance(item, dict)][:MAX_QUERIES] if isinstance(queries, list) else []


PLAN_CONTEXT_CHARS = 1500


async def plan(question: str, texts: list[str] = ()) -> list[dict[str, object]]:
    """Asks the model which general searches would help with a question.

    The record excerpts tell the planner what the question is about, so that
    "What do 4AA and 3BB mean?" searches embryo grading. Its fields still pass
    through ``clean`` before anything is sent.
    """
    from stellis import agent_gateway  # Deferred: imports the model client.
    context = "\n\n".join(texts)[:PLAN_CONTEXT_CHARS]
    prompt = f"Record excerpts:\n{context}\n\nQuestion: {question}" if context else question
    try:
        reply, _ = await agent_gateway.turn(prompt, "research-plan", instructions=_PLAN_INSTRUCTIONS)
    except agent_gateway.GatewayError:
        return []
    return parse_plan(reply)


def tavily_only() -> bool:
    """Whether the deployment requests Tavily instead of public-source-first search."""
    return os.environ.get("STELLIS_RESEARCH_PROVIDERS", "").strip().casefold() == "tavily"


async def research(question: str, texts: list[str], on_send=None) -> Findings:
    """Plans, cleans and sends searches through the configured providers.

    Args:
        question: The person's question.
        texts: Record passages the answer will read, scanned for names.
        on_send: Called with a query and its services just before they are
            sent, and again when Tavily is added, so the page can show it.

    Returns:
        The sent queries, the number blocked and the results.
    """
    from stellis import runtime_trace
    from stellis import tavily as tavily_keys
    identity = identity_terms([question, *texts])
    findings = Findings()
    seen: set[str] = set()
    key = tavily_keys.available_key()
    exclusive = tavily_only() and bool(key)
    if tavily_only() and not key:
        findings.fallback = "Tavily key unavailable; using MedlinePlus and PubMed"
        runtime_trace.event("research_provider_fallback", error_code="tavily_key_unavailable")
    announce = on_send or (lambda query, providers: None)
    for fields in await plan(question, texts):
        query = clean(fields, identity)
        if query is None:
            findings.blocked += 1
            continue
        try:
            assert_clean(query, identity)
        except UnsafeQuery:
            findings.blocked += 1
            continue
        attempts = ([("Tavily", tavily, (key,))] if exclusive else
                    [("MedlinePlus", medlineplus, ()), ("PubMed", pubmed, ())])
        announce(query, [name for name, _, _ in attempts])
        providers, found = [], []
        for name, provider, args in attempts:
            providers.append(name)
            try:
                found += await asyncio.to_thread(_send, provider, query, identity, *args)
            except UnsafeQuery:
                findings.blocked += 1
                providers = []
                break
            except (OSError, ValueError, ElementTree.ParseError):
                continue
        if not exclusive and providers and key and len(found) < PUBLIC_ENOUGH:
            providers.append("Tavily")
            announce(query, list(providers))
            try:
                found += await asyncio.to_thread(_send, tavily, query, identity, key)
            except (OSError, ValueError):
                pass
        if not providers:
            continue
        fresh = [item for item in found if item.url not in seen]
        seen.update(item.url for item in fresh)
        findings.results += fresh
        findings.queries.append({"text": query, "providers": providers, "results": len(fresh)})
        broader = _broader(query)
        sent = {item["text"] for item in findings.queries}
        if not exclusive and broader and broader not in sent and not any(item.provider == "MedlinePlus" for item in found):
            # MedlinePlus matches whole topics, so one key word often finds what a phrase misses.
            announce(broader, ["MedlinePlus"])
            try:
                retried = await asyncio.to_thread(_send, medlineplus, broader, identity)
            except (OSError, ValueError, ElementTree.ParseError):
                retried = []
            extra = [item for item in retried if item.url not in seen]
            seen.update(item.url for item in extra)
            findings.results = extra + findings.results
            findings.queries.append({"text": broader, "providers": ["MedlinePlus"], "results": len(extra)})
    runtime_trace.event("research_done", query_count=len(findings.queries),
                        blocked_count=findings.blocked, result_count=len(findings.results))
    return findings
