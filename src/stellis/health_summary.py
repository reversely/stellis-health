"""A cited summary of the person's health, built from all their documents.

The model reads the passages that best answer five fixed questions, drawn
from every document with text, and writes a short summary under five
headings. Citations are checked and numbered like notebook chat answers, so
each statement links to the page it came from. The newest summary is stored
and regenerated on request.
"""

from __future__ import annotations

import datetime
import itertools
import json
import uuid

from stellis import agent_gateway
from stellis import documents
from stellis import notebook_chat
from stellis import storage

HEADINGS = ("Overview", "Conditions", "Medications", "Care team",
            "Recent changes")
# Words that rank passages for each heading; recent changes use dates instead.
_QUERIES = {
    "Conditions": (
        "diagnosis assessment history disorder condition problems allergy allergies"),
    "Medications": "medications mg daily tablet stop start continue dose",
    "Care team": "referral dietitian physiotherapist trainer MD DDS clinic",
}
PASSAGES_PER_QUERY = 8
RECENT_DOCUMENTS = 6
MAX_PASSAGES = 36
_INSTRUCTIONS = (
    notebook_chat.ROLE + "\n" + notebook_chat.ANSWER_RULES + "\n"
    "- Write a summary of the person's health for them to read, speaking to them as \"you\": these sections in order, "
    "each a line '## Heading' then 2 to 5 short sentences: " + ", ".join(HEADINGS) + ".\n"
    "- Prefer the newest source when sources disagree, and say what changed.\n"
    "- Put a [[source:N]] marker after each factual sentence. Write no URLs or links.")


def _create_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_summaries (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            citations_json TEXT NOT NULL,
            document_count INTEGER NOT NULL,
            created_at TEXT NOT NULL)""")
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(health_summaries)")}
    for name in ("selected_document_count", "passage_count"):
        if name not in columns:
            conn.execute(f"ALTER TABLE health_summaries ADD COLUMN {name} INTEGER")


def _sources() -> list[dict[str, object]]:
    """Lists every document with text, newest record date first."""
    return [{
        "id": "document:" + str(item["id"]),
        "title": item["title"],
        "date": str(item["record_date"] or item["created_at"])[:10],
    } for item in documents.list_all() if item["status"] == "ready"]


def select_passages(
        sources: list[dict[str, object]]) -> list[notebook_chat.Passage]:
    """Picks the passages the summary is written from.

    Args:
        sources: Documents with text, newest first.

    Returns:
        At most ``MAX_PASSAGES`` unique passages, with reserved capacity
        for recent documents and for each historical topic.
    """
    every = notebook_chat.read_passages(sources)
    recent_ids = {str(source["id"]) for source in sources[:RECENT_DOCUMENTS]}
    recent_by_source: dict[str, list[notebook_chat.Passage]] = {}
    for passage in every:
        if passage.source_id in recent_ids:
            recent_by_source.setdefault(passage.source_id, []).append(passage)
    recent = _interleave(list(recent_by_source.values()))
    ranked = _interleave([
        notebook_chat.rank(every, query, PASSAGES_PER_QUERY)
        for query in _QUERIES.values()
    ])
    # Topic slots remain available even when one recent document is long.
    recent_slots = MAX_PASSAGES - len(_QUERIES) * PASSAGES_PER_QUERY
    candidates = recent[:recent_slots] + ranked + recent[recent_slots:]
    return list(dict.fromkeys(candidates))[:MAX_PASSAGES]


def _interleave(groups: list[list[notebook_chat.Passage]]
                ) -> list[notebook_chat.Passage]:
    """Alternates between sources so long records share the passage budget."""
    return [passage for row in itertools.zip_longest(*groups)
            for passage in row if passage is not None]


def build_prompt(passages: list[notebook_chat.Passage],
                 dates: dict[str, str]) -> str:
    """Builds the summary prompt: instructions, then a JSON context."""
    context = {
        "today":
            datetime.date.today().isoformat(),
        "sources": [{
            "index": index,
            "title": passage.title,
            "date": dates.get(passage.source_id, ""),
            "text": passage.text,
        } for index, passage in enumerate(passages, 1)],
    }
    return _INSTRUCTIONS + "\n\n" + json.dumps(context, ensure_ascii=False)


def ready_count() -> int:
    """Counts the documents with text that a new summary would read."""
    return len(_sources())


def latest() -> dict[str, object] | None:
    """Returns the newest stored summary, or None before the first one."""
    with storage.connect() as conn:
        _create_table(conn)
        row = conn.execute("SELECT * FROM health_summaries "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {
        "content": row["content"],
        "citations": json.loads(row["citations_json"]),
        "document_count": row["document_count"],
        "selected_document_count": row["selected_document_count"],
        "passage_count": row["passage_count"],
        "created_at": row["created_at"],
    }


async def refresh() -> dict[str, object]:
    """Writes a new summary from the person's documents and stores it.

    Returns:
        The stored summary, as ``latest`` returns it.

    Raises:
        ValueError: No document has text yet.
        notebook_chat.ChatError: The model call failed or its citations did
            not check out.
    """
    sources = _sources()
    if not sources:
        raise ValueError("Add a document with text to build a summary")
    passages = select_passages(sources)
    dates = {str(source["id"]): str(source["date"]) for source in sources}
    try:
        reply, _ = await agent_gateway.turn(build_prompt(passages, dates),
                                            "health-summary", model_role="summary")
    except agent_gateway.GatewayError as exc:
        raise notebook_chat.ChatError(str(exc)) from exc
    content, citations = notebook_chat.cite(reply.strip(), passages)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with storage.connect() as conn:
        _create_table(conn)
        selected_documents = len({passage.source_id for passage in passages})
        conn.execute(
            """
            INSERT INTO health_summaries (id, content, citations_json,
                document_count, created_at, selected_document_count, passage_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), content, json.dumps(citations), len(sources),
             stamp, selected_documents, len(passages)))
    return latest()
