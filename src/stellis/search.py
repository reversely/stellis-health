"""Device-local search over the current displayed transcript revision."""

from __future__ import annotations

import json
import re

from stellis import documents, library, pages, people, storage, todos


def _excerpt(value: str, query: str, width: int = 120) -> str:
    position = value.casefold().find(query.casefold())
    if position < 0 or len(value) <= width:
        return value
    start = max(0, position - 38)
    end = min(len(value), start + width)
    start = max(0, end - width)
    return ("…" if start else "") + value[start:end] + ("…" if end < len(value) else "")


def _pages(category_id: str | None) -> list[dict[str, object]]:
    """Active pages, narrowed like conversations by a tag ID or `uncategorized`."""
    with storage.connect() as conn:
        rows = conn.execute("SELECT id, title, markdown FROM pages WHERE trashed_at IS NULL "
                            "ORDER BY updated_at DESC").fetchall()
        tags = pages.page_tags(conn)
    if category_id is None:
        return [dict(row) for row in rows]
    return [dict(row) for row in rows
            if (not tags.get(str(row["id"])) if category_id == "uncategorized" else category_id in tags.get(str(row["id"]), []))]


_LINE_MARK = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)*")


def plain_text(markdown: str) -> str:
    """Readable text only: task IDs and layout tags, list and heading marks, emphasis, and link or image targets dropped."""
    lines = (todos.markdown_inline_text(_LINE_MARK.sub("", line)) for line in todos.plain_markdown(markdown).split("\n"))
    return "\n".join(line for line in lines if line)


def note_texts(ids: list[str]) -> dict[str, str]:
    """Plain My Notes text per conversation: no task IDs, layout tags, or image references."""
    if not ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(f"SELECT conversation_id, markdown FROM meeting_notes WHERE conversation_id IN ({','.join('?' * len(ids))})",
                            ids).fetchall()
    return {str(row["conversation_id"]): plain_text(str(row["markdown"])) for row in rows}


def _summary_texts(ids: list[str]) -> dict[str, str]:
    """Plain text of each conversation's newest successful Summary: the edited version, else the generated one."""
    if not ids:
        return {}
    with storage.connect() as conn:
        rows = conn.execute(f"""SELECT conversation_id, result_json, edited_markdown FROM enhanced_notes_runs
            WHERE state = 'succeeded' AND conversation_id IN ({','.join('?' * len(ids))}) ORDER BY created_at""",
                            ids).fetchall()
    texts: dict[str, str] = {}
    for row in rows:  # the newest run overwrites earlier ones
        if row["edited_markdown"]:
            texts[str(row["conversation_id"])] = plain_text(str(row["edited_markdown"]))
        else:
            result = json.loads(row["result_json"] or "{}")
            texts[str(row["conversation_id"])] = "\n".join(
                str(item.get("text", "")) for field in ("overview", "topics", "decisions", "action_items")
                for item in result.get(field, []))
    return texts


def search_library(query: str, limit: int = 150, category_id: str | None = None) -> dict[str, object]:
    """Search effective text and labels afresh, so overlays never need a stale index."""
    needle = query.strip().casefold()
    if len(needle) < 2 or len(needle) > 120:
        raise ValueError("Enter 2 to 120 characters to search")
    results: list[dict[str, object]] = []
    total = 0
    conversations = storage.list_conversations()
    if category_id is not None:
        assigned = library.assignments()
        if category_id == "uncategorized":
            conversations = [item for item in conversations if not assigned.get(str(item["id"]))]
        else:
            conversations = [item for item in conversations
                             if category_id in assigned.get(str(item["id"]), [])]
    ids = [str(item["id"]) for item in conversations]
    notes_by_id, summary_texts = note_texts(ids), _summary_texts(ids)
    for person in people.list_people():
        if needle not in str(person["name"]).casefold():
            continue
        if category_id is not None:
            profile = people.get_person(str(person["id"]))
            if not profile or not any(
                (not assigned.get(str(item["conversation_id"])) if category_id == "uncategorized"
                 else category_id in assigned.get(str(item["conversation_id"]), []))
                for item in profile["conversations"]
            ):
                continue
        total += 1
        if len(results) < limit:
            results.append({"kind": "person", "person_id": person["id"], "conversation_id": None,
                            "conversation_title": person["name"], "revision": None,
                            "segment_id": None, "start_ms": 0, "speaker": person["name"],
                            "speaker_uncertain": False, "excerpt": person["name"]})
    for conversation in conversations:
        conversation_id = str(conversation["id"])
        title = str(conversation["title"])
        display = storage.get_display_transcript(conversation_id)
        segments = display["segments"] if display else []
        if needle in title.casefold():
            total += 1
            if len(results) < limit:
                results.append({"kind": "conversation", "conversation_id": conversation_id,
                                "conversation_title": title, "revision": display["revision"] if display else None,
                                "segment_id": None, "start_ms": 0, "speaker": None,
                                "speaker_uncertain": False, "excerpt": title})
        # One hit per conversation for each text, with an excerpt around the first match.
        for kind, text in (("note", notes_by_id.get(conversation_id, "")), ("summary", summary_texts.get(conversation_id, ""))):
            flat = " ".join(text.split())
            if needle in flat.casefold():
                total += 1
                if len(results) < limit:
                    results.append({"kind": kind, "conversation_id": conversation_id,
                                    "conversation_title": title, "excerpt": _excerpt(flat, query)})
        seen_speakers: set[str] = set()
        for segment in segments:
            speaker = str(segment["speaker_display_name"])
            common = {"conversation_id": conversation_id, "conversation_title": title,
                      "revision": display["revision"], "segment_id": segment["id"],
                      "start_ms": segment["start_ms"], "speaker": speaker,
                      "speaker_uncertain": bool(segment.get("speaker_uncertain", False)),
                      "person_id": segment.get("person_id")}
            if needle in speaker.casefold() and speaker not in seen_speakers:
                seen_speakers.add(speaker)
                total += 1
                if len(results) < limit:
                    results.append({**common, "kind": "speaker", "excerpt": speaker})
            value = str(segment["text"])
            if needle in value.casefold():
                total += 1
                if len(results) < limit:
                    results.append({**common, "kind": "passage", "excerpt": _excerpt(value, query)})
    for page in _pages(category_id):
        title = str(page["title"])
        body = todos.plain_markdown(str(page["markdown"]))
        for value, in_title in ((title, True), (body, False)):
            if needle in value.casefold():
                total += 1
                if len(results) < limit:
                    results.append({"kind": "page", "page_id": page["id"], "title": title,
                                    "excerpt": value if in_title else _excerpt(" ".join(value.split()), query)})
    # Documents have no tags, so a tag filter leaves them out.
    pages = [] if category_id is not None else documents.all_pages()
    titled: set[str] = set()
    for page in pages:
        document_id, title = str(page["document_id"]), str(page["title"])
        hit = None
        if needle in title.casefold() and document_id not in titled:
            titled.add(document_id)
            hit = title
        elif needle in str(page["text"]).casefold():
            hit = _excerpt(str(page["text"]), query)
        if hit is not None:
            total += 1
            if len(results) < limit:
                results.append({"kind": "document", "document_id": document_id, "title": title,
                                "page": page["number"], "excerpt": hit})
    return {"query": query.strip(), "total": total, "limited": total > limit, "results": results}
