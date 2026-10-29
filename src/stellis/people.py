"""User-confirmed people, separate from provisional acoustic speaker groups."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from stellis import storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(value: str) -> str:
    name = " ".join(value.split())
    if not name or len(name) > 100:
        raise ValueError("Enter a person name of at most 100 characters")
    return name


def _matching_person(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT id, name FROM persons WHERE name_key = ?",
                        (storage.normalized_person_name(name),)).fetchone()


def list_people() -> list[dict[str, object]]:
    with storage.connect() as conn:
        rows = conn.execute("""SELECT p.id, p.name, p.created_at, p.updated_at, p.hidden_at,
            COUNT(DISTINCT CASE WHEN c.trashed_at IS NULL THEN pl.conversation_id END) AS conversation_count
            FROM persons p LEFT JOIN person_links pl ON pl.person_id = p.id
            LEFT JOIN conversations c ON c.id = pl.conversation_id
            GROUP BY p.id ORDER BY p.name COLLATE NOCASE, p.created_at""").fetchall()
    return [dict(row) for row in rows]


def create_person(name: str) -> dict[str, object]:
    value = _name(name)
    person = {"id": str(uuid.uuid4()), "name": value, "created_at": _now()}
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = _matching_person(conn, value)
        if existing:
            row = conn.execute("SELECT id, name, created_at FROM persons WHERE id = ?", (existing["id"],)).fetchone()
            return dict(row)
        conn.execute("INSERT INTO persons (id, name, name_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (person["id"], value, storage.normalized_person_name(value),
                      person["created_at"], person["created_at"]))
    return person


def rename_person(person_id: str, name: str) -> dict[str, object]:
    value = _name(name)
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = _matching_person(conn, value)
        if existing and str(existing["id"]) != person_id:
            raise ValueError("A speaker with that name already exists")
        changed = conn.execute("UPDATE persons SET name = ?, name_key = ?, updated_at = ? WHERE id = ?",
                               (value, storage.normalized_person_name(value), _now(), person_id)).rowcount
    if not changed:
        raise LookupError("Person not found")
    return {"id": person_id, "name": value}


def set_person_hidden(person_id: str, hidden: bool) -> dict[str, object]:
    stamp = _now()
    with storage.connect() as conn:
        changed = conn.execute("UPDATE persons SET hidden_at = ?, updated_at = ? WHERE id = ?",
                               (stamp if hidden else None, stamp, person_id)).rowcount
    if not changed:
        raise LookupError("Person not found")
    return {"id": person_id, "hidden": hidden}


def delete_person(person_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        if conn.execute("SELECT 1 FROM person_links WHERE person_id = ? LIMIT 1", (person_id,)).fetchone():
            raise RuntimeError("Remove this person's confirmed passages before deleting the profile")
        removed = conn.execute("DELETE FROM persons WHERE id = ?", (person_id,)).rowcount
    if not removed:
        raise LookupError("Person not found")
    return {"deleted": True}


def get_person(person_id: str) -> dict[str, object] | None:
    with storage.connect() as conn:
        person = conn.execute("SELECT id, name, created_at, updated_at, hidden_at FROM persons WHERE id = ?",
                              (person_id,)).fetchone()
        if person is None:
            return None
        total_links = conn.execute("SELECT COUNT(*) FROM person_links WHERE person_id = ?", (person_id,)).fetchone()[0]
        links = conn.execute("""SELECT c.id AS conversation_id, c.title, c.created_at, pl.segment_id,
            pl.start_ms, pl.confirmed_at FROM person_links pl
            JOIN conversations c ON c.id = pl.conversation_id AND c.trashed_at IS NULL
            WHERE pl.person_id = ? ORDER BY c.created_at DESC, pl.start_ms""", (person_id,)).fetchall()
    by_conversation: dict[str, dict[str, object]] = {}
    current_ids_by_conversation: dict[str, set[str]] = {}
    for link in links:
        cid = str(link["conversation_id"])
        if cid not in current_ids_by_conversation:
            current = storage.get_transcript(cid)
            current_ids_by_conversation[cid] = {item["id"] for item in json.loads(str(current["segments_json"]))} if current else set()
        current_link = link["segment_id"] in current_ids_by_conversation[cid]
        if cid not in by_conversation:
            by_conversation[cid] = {"conversation_id": cid, "title": link["title"],
                                    "created_at": link["created_at"], "segment_id": link["segment_id"],
                                    "start_ms": link["start_ms"], "confirmed_passages": 0,
                                    "current_generation": current_link}
        elif current_link and not by_conversation[cid]["current_generation"]:
            by_conversation[cid].update(segment_id=link["segment_id"], start_ms=link["start_ms"],
                                        current_generation=True)
        by_conversation[cid]["confirmed_passages"] += 1
    return dict(person) | {"conversations": list(by_conversation.values()), "total_link_count": total_links}


def confirm_person(conversation_id: str, segment_id: str, person_id: str | None, scope: str,
                   expected_revision: int, *, name: str | None = None) -> dict[str, object]:
    if scope not in {"segment", "cluster"}:
        raise ValueError("Choose this passage or its acoustic speaker group")
    if bool(person_id) == bool(name):
        raise ValueError("Choose an existing speaker or enter a new name")
    cleaned_name = _name(name) if name is not None else None
    transcript = storage.get_transcript(conversation_id)
    if transcript is None:
        raise LookupError("Transcript not found")
    if int(transcript["revision"]) != expected_revision:
        raise RuntimeError("Transcript changed; reload before confirming a person")
    segments = json.loads(str(transcript["segments_json"]))
    selected = next((item for item in segments if item["id"] == segment_id), None)
    if selected is None:
        raise LookupError("Passage not found")
    cluster = selected.get("speaker_cluster_id")
    matches = [item for item in segments if item.get("speaker_cluster_id") == cluster] if scope == "cluster" and cluster else [selected]
    stamp = _now()
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        live = conn.execute("SELECT revision, generation_version FROM transcripts WHERE conversation_id = ?",
                            (conversation_id,)).fetchone()
        if live is None or live["revision"] != expected_revision or live["generation_version"] != transcript["generation_version"]:
            raise RuntimeError("Transcript changed; reload before confirming a person")
        if not conn.execute("SELECT 1 FROM conversations WHERE id = ? AND trashed_at IS NULL", (conversation_id,)).fetchone():
            raise LookupError("Conversation not found")
        if cleaned_name is not None:
            found = _matching_person(conn, cleaned_name)
            if found:
                person_id = str(found["id"])
            else:
                person_id = str(uuid.uuid4())
                conn.execute("INSERT INTO persons (id, name, name_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                             (person_id, cleaned_name, storage.normalized_person_name(cleaned_name), stamp, stamp))
        else:
            found = conn.execute("SELECT id, name FROM persons WHERE id = ?", (person_id,)).fetchone()
            if not found:
                raise LookupError("Person not found")
            cleaned_name = str(found["name"])
        existing = {row["segment_id"]: row["person_id"] for row in conn.execute(
            "SELECT segment_id, person_id FROM person_links WHERE conversation_id = ?",
            (conversation_id,)).fetchall()}
        selected_person = existing.get(segment_id)
        skipped = 0
        labels = conn.execute(
            "SELECT scope, target_id, name FROM speaker_labels WHERE conversation_id = ?", (conversation_id,)).fetchall()
        normalized_name = storage.normalized_person_name(cleaned_name)
        manual_labels = [row for row in labels
                         if " ".join(str(row["name"]).split()).casefold() == normalized_name]
        passage_labels = {str(row["target_id"]): str(row["name"]) for row in labels if row["scope"] == "segment"}
        cluster_labels = {str(row["target_id"]): str(row["name"]) for row in labels if row["scope"] == "cluster"}
        manual_ids = {str(item["id"]) for item in segments
                      if " ".join((passage_labels.get(str(item["id"])) or
                                   cluster_labels.get(str(item.get("speaker_cluster_id"))) or "").split()).casefold()
                      == normalized_name}
        targets = {str(item["id"]): item for item in matches}
        targets.update({str(item["id"]): item for item in segments if str(item["id"]) in manual_ids})
        confirmed = 0
        for item in targets.values():
            if scope == "cluster" and existing.get(item["id"]) not in (None, person_id, selected_person):
                skipped += 1
                continue
            if item["id"] in manual_ids and existing.get(item["id"]) not in (None, person_id):
                skipped += 1
                continue
            origin = "manual" if item["id"] == segment_id or item["id"] in manual_ids else "propagated"
            conn.execute("""INSERT INTO person_links (conversation_id, segment_id, person_id, start_ms, confirmed_at, origin)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, segment_id) DO UPDATE SET
                person_id = excluded.person_id, start_ms = excluded.start_ms, confirmed_at = excluded.confirmed_at,
                origin = excluded.origin""",
                (conversation_id, item["id"], person_id, item["start_ms"], stamp, origin))
            confirmed += 1
        promoted = 0
        for label in manual_labels:
            if label["scope"] == "segment" and label["target_id"] not in manual_ids:
                continue
            if label["scope"] == "cluster" and not any(item.get("speaker_cluster_id") == label["target_id"] for item in segments):
                continue
            promoted += conn.execute("DELETE FROM speaker_labels WHERE conversation_id = ? AND scope = ? AND target_id = ?",
                                     (conversation_id, label["scope"], label["target_id"])).rowcount
    return {"person_id": person_id, "confirmed_passages": confirmed,
            "preserved_other_people": skipped, "promoted_labels": promoted}


def remove_person(conversation_id: str, segment_id: str, scope: str, expected_revision: int) -> dict[str, object]:
    if scope not in {"segment", "cluster"}:
        raise ValueError("Choose this passage or its acoustic speaker group")
    transcript = storage.get_transcript(conversation_id)
    if transcript is None:
        raise LookupError("Transcript not found")
    if int(transcript["revision"]) != expected_revision:
        raise RuntimeError("Transcript changed; reload before removing a person")
    segments = json.loads(str(transcript["segments_json"]))
    selected = next((item for item in segments if item["id"] == segment_id), None)
    if selected is None:
        raise LookupError("Passage not found")
    cluster = selected.get("speaker_cluster_id")
    ids = [item["id"] for item in segments if item.get("speaker_cluster_id") == cluster] if scope == "cluster" and cluster else [segment_id]
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        live = conn.execute("SELECT revision, generation_version FROM transcripts WHERE conversation_id = ?",
                            (conversation_id,)).fetchone()
        if live is None or live["revision"] != expected_revision or live["generation_version"] != transcript["generation_version"]:
            raise RuntimeError("Transcript changed; reload before removing a person")
        selected_link = conn.execute("SELECT person_id FROM person_links WHERE conversation_id = ? AND segment_id = ?",
                                     (conversation_id, segment_id)).fetchone()
        if selected_link is None:
            raise LookupError("This passage has no confirmed person")
        removed = 0
        for item_id in ids:
            removed += conn.execute("DELETE FROM person_links WHERE conversation_id = ? AND segment_id = ? AND person_id = ?",
                                    (conversation_id, item_id, selected_link["person_id"])).rowcount
    return {"removed": removed}


def merge_people(target_id: str, source_id: str) -> dict[str, object]:
    if target_id == source_id:
        raise ValueError("Choose two different people")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not all(conn.execute("SELECT 1 FROM persons WHERE id = ?", (value,)).fetchone()
                   for value in (target_id, source_id)):
            raise LookupError("Person not found")
        conn.execute("UPDATE person_links SET person_id = ? WHERE person_id = ?", (target_id, source_id))
        conn.execute("DELETE FROM persons WHERE id = ?", (source_id,))
    return {"id": target_id, "merged_id": source_id}


def move_conversation_links(source_id: str, conversation_id: str, target_id: str) -> dict[str, object]:
    if source_id == target_id:
        raise ValueError("Choose a different person")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not all(conn.execute("SELECT 1 FROM persons WHERE id = ?", (value,)).fetchone()
                   for value in (source_id, target_id)):
            raise LookupError("Person not found")
        moved = conn.execute("UPDATE person_links SET person_id = ? WHERE person_id = ? AND conversation_id = ?",
                             (target_id, source_id, conversation_id)).rowcount
    if not moved:
        raise LookupError("No confirmed passages in that conversation")
    return {"moved": moved, "person_id": target_id}


def remove_conversation_links(person_id: str, conversation_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        removed = conn.execute("DELETE FROM person_links WHERE person_id = ? AND conversation_id = ?",
                               (person_id, conversation_id)).rowcount
    if not removed:
        raise LookupError("No confirmed passages in that conversation")
    return {"removed": removed}
