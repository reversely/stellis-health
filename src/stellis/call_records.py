"""Answers a question asked on a call from the person's own reports (#270).

The call model has no record text, so a question about results comes here: the newest reports
are read, the notebook answer rules write a cited answer, and its citations are checked before
the call speaks it.
"""
from __future__ import annotations

import re

from stellis import agent_gateway, documents, notebook_chat

# The newest reports read for one question; "my most recent results" is the common ask.
RECENT_REPORTS = 6
NO_REPORTS = "No reports have been added yet. Add a report with Add, and I can go through it with you."
_CITATION = re.compile(r"\s*\[\[\d+\]\]")
# Questions plainly about the person's results, read before the call model replies.
_ABOUT_RECORDS = re.compile(r"\b(?:results?|reports?|records?|labs?|blood ?work|blood tests?|test results?|bloods)\b", re.I)

ASK_RECORDS = {
    "type": "function",
    "function": {
        "name": "ask_records",
        "description": ("Read the person's own reports and test results to answer a question about them, "
                        "such as what their latest blood results mean. Call it for any question about their "
                        "results, reports or records, then say its answer."),
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "The person's question, in their words."}},
            "required": ["question"],
        },
    },
}


def about_records(text: str) -> bool:
    """Whether a call turn asks about the person's results or reports."""
    return bool(_ABOUT_RECORDS.search(text))


def _sources() -> list[dict[str, object]]:
    reports = [item for item in documents.list_all() if item["status"] == "ready"][:RECENT_REPORTS]
    return [{"id": "document:" + str(item["id"]),
             "title": f"{item['title']}, {item['record_date'] or 'date not on the report'}"} for item in reports]


async def answer(question: str) -> str:
    """Returns a checked answer from the newest reports, without citation markers, for speech."""
    question = " ".join(str(question).split())[:notebook_chat.MAX_QUESTION]
    if not question:
        return "Ask what you would like to know about your results."
    sources = _sources()
    if not sources:
        return NO_REPORTS
    passages = notebook_chat.read_passages(sources)
    if sum(len(item.text) for item in passages) > notebook_chat.FULL_TEXT_CHARS:
        passages = notebook_chat.rank(passages, question)
    prompt = notebook_chat.build_prompt(question, passages, [])
    reply, _ = await agent_gateway.turn(prompt, "call-records", model_role="answers")
    try:
        text, _ = notebook_chat.cite(reply.strip(), passages)
    except notebook_chat.ChatError:
        reply, _ = await agent_gateway.turn(
            prompt + notebook_chat._CITATION_RETRY.format(count=len(passages)), "call-records", model_role="answers")
        text, _ = notebook_chat.cite(reply.strip(), passages)
    return " ".join(_CITATION.sub("", text).split())
