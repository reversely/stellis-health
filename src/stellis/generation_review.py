"""Carry user overlays onto a changed ASR generation (ADR 0054)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher

from stellis import storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidates(source: dict[str, object], targets: list[dict[str, object]]) -> list[dict[str, object]]:
    start, end = int(source["start_ms"]), int(source["end_ms"])
    duration = max(1, end - start)
    ranked = []
    for target in targets:
        overlap = max(0, min(end, int(target["end_ms"])) - max(start, int(target["start_ms"])))
        if not overlap and abs(int(target["start_ms"]) - start) > 500:
            continue
        similarity = SequenceMatcher(None, str(source["text"]).casefold(),
                                     str(target["text"]).casefold()).ratio()
        score = round(.6 * overlap / duration + .4 * similarity, 3)
        ranked.append({"segment_id": target["id"], "start_ms": target["start_ms"],
                       "end_ms": target["end_ms"], "text": target["text"],
                       "overlap": round(overlap / duration, 3),
                       "text_similarity": round(similarity, 3), "score": score})
    overlapping = [item for item in ranked if item["overlap"] > 0]
    return sorted(overlapping or ranked, key=lambda item: (-item["score"], item["start_ms"]))[:5]


def _automatic_targets(item: dict[str, object]) -> list[str] | None:
    """Target passages for an overlay whose new home is unambiguous, else None."""
    start, end = int(item["source_start_ms"]), int(item["source_end_ms"])
    duration = max(1, end - start)
    candidates = [candidate for candidate in item["candidates"] if float(candidate["overlap"]) > 0]

    def inside(candidate: dict[str, object]) -> float:
        length = max(1, int(candidate["end_ms"]) - int(candidate["start_ms"]))
        return float(candidate["overlap"]) * duration / length

    if item["kind"] in {"person", "speaker"}:
        # A split or re-cut passage: every new piece lies within the old passage's time.
        pieces = [candidate for candidate in candidates if inside(candidate) >= .8]
        if pieces and sum(float(candidate["overlap"]) for candidate in pieces) >= .8:
            return [str(candidate["segment_id"]) for candidate in pieces]
        # A name never holds an old passage in place: it moves to the new passages that
        # sit mostly inside the old one's time, or else to the one overlapping it most.
        mostly = [candidate for candidate in candidates if inside(candidate) >= .5]
        if mostly:
            return [str(candidate["segment_id"]) for candidate in mostly]
        if candidates:
            return [str(max(candidates, key=lambda candidate: float(candidate["overlap"]))["segment_id"])]
        return []  # No new speech covers this time, so the name has nothing left to label.
    if item["kind"] == "text":
        same = [candidate for candidate in candidates if float(candidate["overlap"]) >= .8 and
                inside(candidate) >= .8 and float(candidate["text_similarity"]) >= .95]
        return [str(same[0]["segment_id"])] if len(same) == 1 and len(candidates) == 1 else None
    quoted = [candidate for candidate in candidates if str(candidate["text"]).count(str(item["value"])) == 1]
    return [str(quoted[0]["segment_id"])] if len(quoted) == 1 else None


def stage(record: dict[str, object]) -> str | None:
    """Activate a changed ASR generation, carrying user overlays over without asking.

    Returns "applied" when the new transcript was activated with overlays carried
    over, or None when nothing depended on the changed passages (the caller saves it)."""
    conversation_id = str(record["conversation_id"])
    current = storage.get_transcript(conversation_id)
    if current is None:
        return None
    source = json.loads(str(current["segments_json"]))
    targets = json.loads(str(record["segments_json"]))
    target_ids = {str(item["id"]) for item in targets}
    removed = {str(item["id"]) for item in source} - target_ids
    target_by_id = {str(item["id"]): item for item in targets}
    changed_clusters = {str(item["id"]) for item in source if str(item["id"]) in target_by_id and
                        item.get("speaker_cluster_id") != target_by_id[str(item["id"])].get("speaker_cluster_id")}
    if not removed and not changed_clusters:
        return None
    display = storage.get_display_transcript(conversation_id)
    if display is None:
        return None
    source_by_id = {str(item["id"]): item for item in display["segments"]}
    machine_by_id = {str(item["id"]): item for item in source}
    overlays: list[dict[str, object]] = []
    for segment_id, text in storage.get_transcript_edits(conversation_id).items():
        overlays.append({"kind": "text", "source_segment_id": segment_id, "value": text})
    for label in storage.get_speaker_labels(conversation_id):
        if label["scope"] == "segment":
            overlays.append({"kind": "speaker", "source_segment_id": label["target_id"], "value": label["name"]})
        else:
            for segment in source:
                if segment.get("speaker_cluster_id") == label["target_id"]:
                    overlays.append({"kind": "speaker", "source_segment_id": segment["id"],
                                     "value": label["name"], "cluster_label": True})
    for highlight in storage.list_highlights(conversation_id):
        overlays.append({"kind": "highlight", "source_segment_id": highlight["segment_id"],
                         "value": highlight["quote"], "source_highlight_id": highlight["id"]})
    for comment in storage.list_comments(conversation_id):
        overlays.append({"kind": "comment", "source_segment_id": comment["segment_id"],
                         "value": comment["quote"], "source_comment_id": comment["id"]})
    with storage.connect() as conn:
        anchored_images = conn.execute("""SELECT id, segment_id, quote FROM conversation_images
            WHERE conversation_id = ? AND quote IS NOT NULL""", (conversation_id,)).fetchall()
        people = conn.execute("SELECT segment_id, person_id, origin FROM person_links WHERE conversation_id = ?",
                              (conversation_id,)).fetchall()
    for image in anchored_images:
        overlays.append({"kind": "image", "source_segment_id": image["segment_id"],
                         "value": image["quote"], "source_image_id": image["id"]})
    for link in people:
        overlays.append({"kind": "person", "source_segment_id": link["segment_id"], "value": link["person_id"],
                         "origin": link["origin"] or "manual"})
    targets = [dict(item) for item in targets]
    transcribed = list(targets)
    kept: set[str] = set()
    while True:
        items, unresolved = _overlay_items(overlays, removed, changed_clusters, source_by_id, machine_by_id, targets,
                                           transcribed)
        newly = {str(item["source_segment_id"]) for item in items if item["auto_targets"] is None} - kept
        if not newly:
            break
        kept |= newly
        targets = _keep_edited_passages(targets, [machine_by_id[segment_id] for segment_id in kept])
    if not items and not kept:
        return None
    expanded, decisions, used = [], {}, set()
    for item in items:
        source_id = str(item["source_segment_id"])
        if source_id in kept:
            # The passage itself is kept with its old ID, so its overlays stay put; only
            # cluster-wide names must become passage names before cluster labels are reset.
            auto = [source_id] if item.get("cluster_label") else []
        else:
            auto = item["auto_targets"]
        for position, target in enumerate(auto):
            key = (item["kind"], target)
            if item["kind"] not in {"comment", "image"} and key in used:
                continue
            used.add(key)
            entry = {key_: value for key_, value in item.items() if key_ != "auto_targets"}
            entry["id"] = f"{item['id']}-{position}"
            expanded.append(entry)
            decisions[entry["id"]] = target
    record = {**record, "segments_json": json.dumps(targets)}
    stamp = _now()
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        live = conn.execute("SELECT revision, generation_version FROM transcripts WHERE conversation_id = ?",
                            (conversation_id,)).fetchone()
        if live is None or live["revision"] != current["revision"] or live["generation_version"] != current["generation_version"]:
            raise RuntimeError("Transcript changed during retranscription; retry the job")
        conn.execute("""INSERT OR REPLACE INTO transcript_generations
            (conversation_id, pipeline_version, revision, language, segments_json, turns_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, record["pipeline_version"], int(current["revision"]) + 1,
             record["language"], record["segments_json"], record["turns_json"], record["updated_at"]))
        conn.execute("""INSERT INTO transcript_reviews
            (conversation_id, source_version, target_version, source_revision, items_json, decisions_json,
             state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET source_version=excluded.source_version,
            target_version=excluded.target_version, source_revision=excluded.source_revision,
            items_json=excluded.items_json, decisions_json=excluded.decisions_json, state='pending',
            updated_at=excluded.updated_at""",
            (conversation_id, current["generation_version"], record["pipeline_version"],
             current["revision"], json.dumps(expanded), json.dumps(decisions), stamp, stamp))
    activate(conversation_id)
    return "applied"


def _overlay_items(overlays, removed, changed_clusters, source_by_id, machine_by_id, targets, transcribed):
    items = []
    for index, overlay in enumerate(overlays):
        source_id = str(overlay["source_segment_id"])
        if (source_id not in removed and source_id not in changed_clusters and not overlay.get("cluster_label")) \
                or source_id not in source_by_id:
            continue
        segment = source_by_id[source_id]
        item = {"id": f"overlay-{index}", **overlay, "source_text": segment["text"],
                "source_start_ms": segment["start_ms"], "source_end_ms": segment["end_ms"],
                "candidates": _candidates(machine_by_id[source_id], targets)}
        item["auto_targets"] = _automatic_targets(item)
        if item["auto_targets"] == [] and any(
                min(int(segment["end_ms"]), int(item["source_end_ms"])) > max(int(segment["start_ms"]), int(item["source_start_ms"]))
                for segment in transcribed):
            # New speech was here, but a passage kept for the user's edit replaced it: keep this one too.
            item["auto_targets"] = None
        items.append(item)
    return items, [item for item in items if item["auto_targets"] is None]


def _keep_edited_passages(targets: list[dict[str, object]], kept: list[dict[str, object]]) -> list[dict[str, object]]:
    """Put each kept old passage back in place of the new passages centred inside it."""
    result = list(targets)
    for old in kept:
        start, end = int(old["start_ms"]), int(old["end_ms"])
        inside = [item for item in result if str(item["id"]) != str(old["id"]) and
                  start <= (int(item["start_ms"]) + int(item["end_ms"])) / 2 < end]
        if not any(str(item["id"]) == str(old["id"]) for item in result):
            covering = max(inside, key=lambda item: min(end, int(item["end_ms"])) - max(start, int(item["start_ms"])),
                           default=None)
            restored = dict(old)
            if covering is not None:
                # Keep the new speaker grouping so names stay consistent across the conversation.
                for field in ("speaker_cluster_id", "speaker_uncertain", "speaker_match_source", "speaker_margin"):
                    if field in covering:
                        restored[field] = covering[field]
            result = [item for item in result if item not in inside] + [restored]
    return sorted(result, key=lambda item: (int(item["start_ms"]), int(item["end_ms"])))


def resolve_pending() -> int:
    """Finish reviews staged by the old flow by re-running the automatic carry-over."""
    with storage.connect() as conn:
        pending = conn.execute("SELECT conversation_id, target_version FROM transcript_reviews "
                               "WHERE state = 'pending'").fetchall()
    resolved = 0
    for row in pending:
        target = storage.get_transcript_generation(str(row["conversation_id"]), str(row["target_version"]))
        if target is None:
            continue
        with storage.connect() as conn:
            conn.execute("DELETE FROM transcript_reviews WHERE conversation_id = ? AND state = 'pending'",
                         (row["conversation_id"],))
        record = {"conversation_id": row["conversation_id"], "pipeline_version": target["pipeline_version"],
                  "language": target["language"], "segments_json": target["segments_json"],
                  "turns_json": target["turns_json"], "updated_at": _now()}
        try:
            resolved += stage(record) == "applied"
        except (LookupError, RuntimeError):
            continue
    return resolved


def get_review(conversation_id: str) -> dict[str, object] | None:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM transcript_reviews WHERE conversation_id = ?", (conversation_id,)).fetchone()
    if row is None:
        return None
    if row["state"] == "accepted":
        current = storage.get_transcript(conversation_id)
        if current is None or current["generation_version"] != row["target_version"]:
            return None
    return {"conversation_id": conversation_id, "source_version": row["source_version"],
            "target_version": row["target_version"], "source_revision": row["source_revision"],
            "state": row["state"], "items": json.loads(str(row["items_json"])),
            "decisions": json.loads(str(row["decisions_json"])), "updated_at": row["updated_at"]}


def activate(conversation_id: str) -> dict[str, object]:
    """Switch generations and apply only mappings the user explicitly selected."""
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute("SELECT * FROM transcript_reviews WHERE conversation_id = ? AND state = 'pending'",
                              (conversation_id,)).fetchone()
        if review is None:
            raise LookupError("No pending transcript review")
        current = conn.execute("SELECT * FROM transcripts WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if current is None or current["revision"] != review["source_revision"] or current["generation_version"] != review["source_version"]:
            raise RuntimeError("The source transcript changed. Keep the current version and reprocess again")
        target = conn.execute("""SELECT * FROM transcript_generations WHERE conversation_id = ? AND pipeline_version = ?""",
                              (conversation_id, review["target_version"])).fetchone()
        if target is None:
            raise LookupError("Candidate transcript missing")
        target_segments = {str(item["id"]): item for item in json.loads(str(target["segments_json"]))}
        items = json.loads(str(review["items_json"]))
        decisions = json.loads(str(review["decisions_json"]))
        if any(item["id"] not in decisions for item in items):
            raise RuntimeError("Choose a target passage or keep unresolved for every review item")
        mappings = [(item, decisions[item["id"]]) for item in items if decisions[item["id"]] is not None]
        seen: set[tuple[str, str]] = set()
        for item, target_id in mappings:
            key = (item["kind"], target_id)
            if target_id not in target_segments or (item["kind"] not in {"comment", "image"} and key in seen):
                raise RuntimeError("Two source changes cannot map to the same target passage and kind")
            seen.add(key)
            if item["kind"] in {"highlight", "comment", "image"} and str(item["value"]) not in str(target_segments[target_id]["text"]):
                raise RuntimeError("The selected quote is absent from the chosen target passage")
            if item["kind"] in {"comment", "image"} and str(target_segments[target_id]["text"]).count(str(item["value"])) != 1:
                raise RuntimeError("The selected quote appears more than once in the chosen passage")
        stamp = _now()
        # Cluster IDs are provisional across a fresh ASR/diarization pass.
        conn.execute("DELETE FROM speaker_labels WHERE conversation_id = ? AND scope = 'cluster'",
                     (conversation_id,))
        for item, target_id in mappings:
            if item["kind"] == "text":
                conn.execute("""INSERT INTO transcript_edits (conversation_id, segment_id, text, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id, segment_id) DO UPDATE SET
                    text=excluded.text, updated_at=excluded.updated_at""", (conversation_id, target_id, item["value"], stamp))
            elif item["kind"] == "speaker":
                conn.execute("""INSERT INTO speaker_labels (conversation_id, scope, target_id, name, updated_at)
                    VALUES (?, 'segment', ?, ?, ?) ON CONFLICT(conversation_id, scope, target_id) DO UPDATE SET
                    name=excluded.name, updated_at=excluded.updated_at""", (conversation_id, target_id, item["value"], stamp))
            elif item["kind"] == "person":
                conn.execute("""INSERT INTO person_links (conversation_id, segment_id, person_id, start_ms, confirmed_at, origin)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, segment_id) DO UPDATE SET
                    person_id=excluded.person_id, start_ms=excluded.start_ms, confirmed_at=excluded.confirmed_at,
                    origin=excluded.origin""",
                    (conversation_id, target_id, item["value"], target_segments[target_id]["start_ms"], stamp,
                     item.get("origin", "manual")))
            elif item["kind"] == "highlight":
                quote = str(item["value"])
                start = str(target_segments[target_id]["text"]).index(quote)
                conn.execute("""INSERT INTO transcript_highlights
                    (id, conversation_id, segment_id, start_offset, end_offset, quote, created_at)
                    VALUES (lower(hex(randomblob(16))), ?, ?, ?, ?, ?, ?)""",
                    (conversation_id, target_id, start, start + len(quote), quote, stamp))
            elif item["kind"] == "comment":
                quote = str(item["value"])
                start = str(target_segments[target_id]["text"]).index(quote)
                conn.execute("""UPDATE transcript_comments SET segment_id = ?, start_offset = ?,
                    end_offset = ?, start_ms = ?, updated_at = ? WHERE id = ? AND conversation_id = ?""",
                    (target_id, start, start + len(quote), target_segments[target_id]["start_ms"],
                     stamp, item["source_comment_id"], conversation_id))
            elif item["kind"] == "image":
                quote = str(item["value"])
                start = str(target_segments[target_id]["text"]).index(quote)
                conn.execute("""UPDATE conversation_images SET segment_id = ?, start_offset = ?,
                    end_offset = ?, media_position_ms = ?, updated_at = ? WHERE id = ? AND conversation_id = ?""",
                    (target_id, start, start + len(quote), target_segments[target_id]["start_ms"],
                     stamp, item["source_image_id"], conversation_id))
        # Rows on passages that no longer exist were carried over above or are not
        # carryable; leaving them would double-count names and highlights.
        live_ids = list(target_segments)
        marks = ",".join("?" * len(live_ids)) or "NULL"
        for table, column in (("transcript_edits", "segment_id"), ("person_links", "segment_id"),
                              ("transcript_highlights", "segment_id")):
            conn.execute(f"DELETE FROM {table} WHERE conversation_id = ? AND {column} NOT IN ({marks})",
                         (conversation_id, *live_ids))
        conn.execute(f"DELETE FROM speaker_labels WHERE conversation_id = ? AND scope = 'segment' AND target_id NOT IN ({marks})",
                     (conversation_id, *live_ids))
        conn.execute("""UPDATE transcripts SET revision = ?, generation_version = ?, language = ?,
            segments_json = ?, turns_json = ?, updated_at = ? WHERE conversation_id = ?""",
            (int(current["revision"]) + 1, target["pipeline_version"], target["language"],
             target["segments_json"], target["turns_json"], stamp, conversation_id))
        conn.execute("UPDATE transcript_reviews SET state = 'accepted', updated_at = ? WHERE conversation_id = ?",
                     (stamp, conversation_id))
        conn.execute("""INSERT OR REPLACE INTO transcript_review_history
            (conversation_id, target_version, source_version, source_revision, items_json,
             decisions_json, outcome, decided_at) VALUES (?, ?, ?, ?, ?, ?, 'accepted', ?)""",
            (conversation_id, review["target_version"], review["source_version"], review["source_revision"],
             review["items_json"], review["decisions_json"], stamp))
    return {"generation_version": target["pipeline_version"], "migrated": len(mappings),
            "unresolved": len(items) - len(mappings)}
