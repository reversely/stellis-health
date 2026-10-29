"""On-device voice fingerprints, person voice profiles, and speaker suggestions (#210).

Fingerprints are TitaNet vectors of transcript passages. They stay in the local
database, are removed with their conversation, and never leave the device. A
person's profile is the mean fingerprint of passages confirmed as that person in
other conversations; a suggestion is only ever shown, never confirmed for the user.
"""

from __future__ import annotations

import numpy as np

from stellis import storage

# Calibrated on the user's recordings.
MIN_SIMILARITY = 0.6
MIN_MARGIN = 0.1
MIN_PROFILE_PASSAGES = 3


def save(conversation_id: str, segments: list[dict[str, object]], vectors: np.ndarray, valid: np.ndarray) -> int:
    rows = [(conversation_id, str(segment["id"]), vectors[index].astype(np.float32).tobytes())
            for index, segment in enumerate(segments) if valid[index]]
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM passage_voices WHERE conversation_id = ?", (conversation_id,))
        conn.executemany("INSERT INTO passage_voices (conversation_id, segment_id, vector) VALUES (?, ?, ?)", rows)
    return len(rows)


def has_index(conversation_id: str) -> bool:
    with storage.connect() as conn:
        return conn.execute("SELECT 1 FROM passage_voices WHERE conversation_id = ? LIMIT 1",
                            (conversation_id,)).fetchone() is not None


def _vectors(conn, conversation_id: str) -> dict[str, np.ndarray]:
    return {str(row["segment_id"]): np.frombuffer(row["vector"], dtype=np.float32)
            for row in conn.execute("SELECT segment_id, vector FROM passage_voices WHERE conversation_id = ?",
                                    (conversation_id,))}


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / max(float(np.linalg.norm(vector)), 1e-9)


def profiles(exclude_conversation: str | None = None) -> dict[str, dict[str, object]]:
    """Mean voice of each visible person's confirmed passages, outside one conversation."""
    with storage.connect() as conn:
        rows = conn.execute("""SELECT pl.person_id, p.name, pv.vector FROM person_links pl
            JOIN persons p ON p.id = pl.person_id AND p.hidden_at IS NULL
            JOIN passage_voices pv ON pv.conversation_id = pl.conversation_id AND pv.segment_id = pl.segment_id
            WHERE pl.conversation_id IS NOT ?""", (exclude_conversation,)).fetchall()
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        entry = grouped.setdefault(str(row["person_id"]), {"name": str(row["name"]), "vectors": []})
        entry["vectors"].append(np.frombuffer(row["vector"], dtype=np.float32))
    return {person: {"name": entry["name"], "count": len(entry["vectors"]),
                     "vector": _unit(np.mean(entry["vectors"], axis=0))}
            for person, entry in grouped.items() if len(entry["vectors"]) >= MIN_PROFILE_PASSAGES}


def suggestions(conversation_id: str, segments: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Suggested person per unnamed speaker group, when one profile is clearly closest."""
    unnamed: dict[str, list[str]] = {}
    named_people = set()
    for segment in segments:
        if segment.get("person_id"):
            named_people.add(str(segment["person_id"]))
        elif segment.get("speaker_label_source") == "automatic" and segment.get("speaker_cluster_id"):
            unnamed.setdefault(str(segment["speaker_cluster_id"]), []).append(str(segment["id"]))
    if not unnamed:
        return {}
    known = {person: profile for person, profile in profiles(conversation_id).items() if person not in named_people}
    if not known:
        return {}
    with storage.connect() as conn:
        vectors = _vectors(conn, conversation_id)
    people = list(known)
    matrix = np.stack([known[person]["vector"] for person in people])
    result: dict[str, dict[str, object]] = {}
    claimed: dict[str, float] = {}
    for cluster, ids in unnamed.items():
        voices = [vectors[segment_id] for segment_id in ids if segment_id in vectors]
        if len(voices) < MIN_PROFILE_PASSAGES:
            continue
        scores = matrix @ _unit(np.mean(voices, axis=0))
        order = np.argsort(-scores)
        best = float(scores[order[0]])
        margin = best - float(scores[order[1]]) if len(order) > 1 else best
        if best < MIN_SIMILARITY or margin < MIN_MARGIN:
            continue
        person = people[int(order[0])]
        # One person per conversation: the closest speaker group keeps the suggestion.
        if person in claimed and claimed[person] >= best:
            continue
        for other, entry in list(result.items()):
            if entry["person_id"] == person:
                del result[other]
        claimed[person] = best
        result[cluster] = {"person_id": person, "name": known[person]["name"], "similarity": round(best, 3)}
    return result
