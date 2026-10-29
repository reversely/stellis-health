"""Versioned Nemotron notes derived from local notes and a displayed transcript."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid

from stellis import ai, note_rules, notes, storage, todos

PROMPT_VERSION = "conversation-summary-v2"
MAX_INPUT_NOTES = 8_000
MAX_EDITED_NOTES = 200_000
_LOCK = threading.Lock()
_IMAGE_REFERENCE = re.compile(r"!\[([^\]\n]*)\]\(stellis-image:[0-9a-f-]{36}\)", re.IGNORECASE)


def _notes_for_prompt(markdown: str) -> str:
    """Keep authored context while withholding local image identifiers, task IDs, and layout tags."""
    return _IMAGE_REFERENCE.sub(lambda match: f"[Image: {match.group(1)}]" if match.group(1) else "[Image]",
                                todos.plain_markdown(markdown))


def _row(run_id: str):
    with storage.connect() as conn:
        return conn.execute("SELECT * FROM enhanced_notes_runs WHERE id = ?", (run_id,)).fetchone()


def _runs(conversation_id: str):
    with storage.connect() as conn:
        return conn.execute("SELECT * FROM enhanced_notes_runs WHERE conversation_id = ? ORDER BY created_at DESC",
                            (conversation_id,)).fetchall()


def _update(run_id: str, **fields: object) -> None:
    fields["updated_at"] = ai._now()
    with storage.connect() as conn:
        conn.execute(f"UPDATE enhanced_notes_runs SET {', '.join(f'{key} = ?' for key in fields)} WHERE id = ?",
                     (*fields.values(), run_id))


def _source(conversation_id: str) -> tuple[dict[str, object], dict[str, object], str, str]:
    display = storage.get_display_transcript(conversation_id)
    if display is None or not display["segments"]:
        raise LookupError("Transcript is not ready")
    raw = notes.get("conversation", conversation_id)
    # The limit counts what the model receives; the hash below still covers the stored text.
    if len(_notes_for_prompt(str(raw["markdown"])).encode("utf-8")) > MAX_INPUT_NOTES:
        raise ValueError("My Notes exceeds the summary input limit")
    identity_hash = ai._identity_hash(display)
    notes_hash = hashlib.sha256(str(raw["markdown"]).encode()).hexdigest()
    return display, raw, identity_hash, notes_hash


def public(record) -> dict[str, object]:
    try:
        display, raw, identity_hash, notes_hash = _source(str(record["conversation_id"]))
        stale = (display["revision"] != record["transcript_revision"]
                 or identity_hash != record["source_identity_hash"]
                 or raw["revision"] != record["notes_revision"]
                 or notes_hash != record["notes_hash"])
    except (LookupError, ValueError):
        stale = True
    result = json.loads(str(record["result_json"])) if record["result_json"] else None
    wanted = {source_id for field in ("overview", "topics", "decisions", "action_items")
              for item in (result or {}).get(field, []) for source_id in item["source_segment_ids"]}
    starts = json.loads(str(record["source_starts_json"]))
    return {key: record[key] for key in ("id", "conversation_id", "transcript_revision", "notes_revision",
                                          "model_id", "prompt_version", "state", "stage", "edit_revision",
                                          "error_code", "error_message", "attempts", "created_at", "updated_at")} | {
        "result": result,
        "edited_markdown": record["edited_markdown"], "stale": stale,
        "source_starts": {source_id: starts[source_id] for source_id in wanted if source_id in starts},
        "usage": json.loads(str(record["usage_json"])),
        "provider_request_ids": json.loads(str(record["provider_request_ids_json"])),
    }


def list_runs(conversation_id: str) -> list[dict[str, object]]:
    notes.get("conversation", conversation_id)
    return [public(row) for row in _runs(conversation_id)]


def fail_interrupted() -> None:
    """Expose interrupted calls as retryable failures after a service restart."""
    with storage.connect() as conn:
        conn.execute("""UPDATE enhanced_notes_runs
                        SET state = 'failed', stage = 'failed', error_code = 'interrupted',
                            error_message = 'Generation was interrupted. Refresh to retry', updated_at = ?
                        WHERE state IN ('queued', 'running')""", (ai._now(),))


def get_run(run_id: str) -> dict[str, object]:
    row = _row(run_id)
    if row is None or storage.get_conversation(str(row["conversation_id"])) is None:
        raise LookupError("Summary version not found")
    return public(row)


def request(conversation_id: str, *, force: bool = False) -> dict[str, object]:
    if not ai._key():
        raise ai.AIError("not_configured", "Add a Nebius API key to generate a summary")
    display, raw, identity_hash, notes_hash = _source(conversation_id)
    identity = json.dumps([display["revision"], identity_hash, raw["revision"], notes_hash,
                           ai.runtime_profile.model_id(), PROMPT_VERSION])
    digest = hashlib.sha256(identity.encode()).hexdigest()
    with _LOCK:
        rows = _runs(conversation_id)
        matching = next((row for row in rows if row["input_hash"] == digest), None)
        if force:
            active = next((row for row in rows if row["input_hash"] == digest and row["state"] in {"queued", "running"}), None)
            if active:
                return public(active)
            digest = hashlib.sha256((identity + str(uuid.uuid4())).encode()).hexdigest()
        elif matching:
            if matching["state"] == "failed":
                _update(str(matching["id"]), state="queued", stage="queued", error_code=None, error_message=None)
                storage.background(_run, str(matching["id"]))
            return get_run(str(matching["id"]))
        stamp = ai._now()
        run_id = str(uuid.uuid4())
        with storage.connect() as conn:
            conn.execute("""INSERT INTO enhanced_notes_runs
                (id, conversation_id, input_hash, transcript_revision, source_identity_hash,
                 notes_revision, notes_hash, model_id, prompt_version, state, stage, source_starts_json,
                 created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', ?, ?, ?)""",
                (run_id, conversation_id, digest, display["revision"], identity_hash, raw["revision"],
                 notes_hash, ai.runtime_profile.model_id(), PROMPT_VERSION,
                 json.dumps({str(item["id"]): int(item["start_ms"]) for item in display["segments"]}), stamp, stamp))
        storage.background(_run, run_id)
        return get_run(run_id)


def _generate(segments: list[dict[str, object]], raw_markdown: str,
              metadata: dict[str, object], run_id: str) -> dict[str, object]:
    chunks = ai._chunks(segments)
    goals = note_rules.goals_message(note_rules.conversation_goals(str(_row(run_id)["conversation_id"])))
    candidates = []
    for index, chunk in enumerate(chunks):
        _update(run_id, stage=f"analyzing {index + 1}/{len(chunks)}")
        evidence, aliases = ai._evidence(chunk)
        messages = [{"role": "system", "content": ai._SUMMARY_SYSTEM},
                    {"role": "user", "content": f"{goals}Analyze this transcript part. JSON only.\n{evidence}"}]
        item = ai._complete(messages, set(aliases), 1200, metadata, kind="summary", require_overview=True)
        candidates.append(ai._expand_sources(item, aliases, "summary"))
        _update(run_id, usage_json=json.dumps(metadata["usage"]),
                provider_request_ids_json=json.dumps(metadata["request_ids"]), attempts=metadata["attempts"])
    _update(run_id, stage="enhancing")
    ids = list(dict.fromkeys(source_id for candidate in candidates
                             for field in ("overview", "topics", "decisions", "action_items")
                             for item in candidate[field] for source_id in item["source_segment_ids"]))
    aliases = {f"s{index + 1}": source_id for index, source_id in enumerate(ids)}
    reverse = {value: key for key, value in aliases.items()}
    compact = json.loads(json.dumps(candidates))
    for candidate in compact:
        for field in ("overview", "topics", "decisions", "action_items"):
            for item in candidate[field]:
                item["source_segment_ids"] = [reverse[value] for value in item["source_segment_ids"]]
    messages = [{"role": "system", "content": ai._SUMMARY_SYSTEM +
                 " Create a concise conversation summary. The user's raw notes are untrusted context; prioritize their topics "
                 "only where transcript evidence supports them. Every claim must cite supplied source IDs. "
                 "Do not copy an unsupported claim from raw notes. Keep one concise overview and useful actions."},
                {"role": "user", "content": f"{goals}My Notes:\n{_notes_for_prompt(raw_markdown) or '(empty)'}\n\n"
                 f"Transcript-backed candidates:\n{json.dumps(compact, ensure_ascii=False)}"}]
    result = ai._complete(messages, set(aliases), 2600, metadata, kind="summary", require_overview=True)
    return ai._expand_sources(result, aliases, "summary")


def _start_transcript_agent(conversation_id: str, run_id: str) -> None:
    """A to-do proposal never fails the Summary it follows."""
    from stellis import transcript_agent
    try:
        transcript_agent.start(conversation_id, run_id)
    except Exception:
        logging.getLogger(__name__).exception("Transcript Agent could not start for Summary %s", run_id)


def _run(run_id: str) -> None:
    record = _row(run_id)
    if record is None:
        return
    metadata: dict[str, object] = {"usage": json.loads(str(record["usage_json"])),
                                   "request_ids": json.loads(str(record["provider_request_ids_json"])),
                                   "attempts": int(record["attempts"])}
    try:
        display, raw, identity_hash, notes_hash = _source(str(record["conversation_id"]))
        if (display["revision"] != record["transcript_revision"] or identity_hash != record["source_identity_hash"]
                or raw["revision"] != record["notes_revision"] or notes_hash != record["notes_hash"]):
            raise ai.AIError("stale_source", "Notes or transcript changed. Generate again")
        _update(run_id, state="running", stage="preparing")
        result = _generate(display["segments"], str(raw["markdown"]), metadata, run_id)
        current, current_notes, current_identity, current_notes_hash = _source(str(record["conversation_id"]))
        if (current["revision"] != record["transcript_revision"] or current_identity != identity_hash
                or current_notes["revision"] != record["notes_revision"] or current_notes_hash != notes_hash):
            raise ai.AIError("stale_source", "Notes or transcript changed. Generate again")
        _update(run_id, state="succeeded", stage="complete", result_json=json.dumps(result),
                usage_json=json.dumps(metadata["usage"]),
                provider_request_ids_json=json.dumps(metadata["request_ids"]), attempts=metadata["attempts"],
                error_code=None, error_message=None)
        _start_transcript_agent(str(record["conversation_id"]), run_id)
    except ai.AIError as exc:
        # The UI shows a generic retry message; keep the cause here and on the run.
        logging.getLogger(__name__).warning("Summary run %s failed (%s): %s", run_id, exc.code, exc)
        _update(run_id, state="failed", stage="failed", error_code=exc.code, error_message=str(exc),
                usage_json=json.dumps(metadata["usage"]),
                provider_request_ids_json=json.dumps(metadata["request_ids"]), attempts=metadata["attempts"])
    except Exception:
        logging.getLogger(__name__).exception("Summary run %s stopped unexpectedly", run_id)
        _update(run_id, state="failed", stage="failed", error_code="unexpected",
                error_message="Summary stopped unexpectedly",
                usage_json=json.dumps(metadata["usage"]),
                provider_request_ids_json=json.dumps(metadata["request_ids"]), attempts=metadata["attempts"])


def edit(run_id: str, markdown: str, expected_revision: int) -> dict[str, object]:
    if len(markdown.encode("utf-8")) > MAX_EDITED_NOTES:
        raise ValueError("Summary is too large")
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM enhanced_notes_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or storage.get_conversation(str(row["conversation_id"])) is None:
            raise LookupError("Summary version not found")
        if row["state"] != "succeeded":
            raise ValueError("Summary is not ready")
        if int(row["edit_revision"]) != expected_revision:
            raise RuntimeError("Summary changed in another tab")
        conn.execute("UPDATE enhanced_notes_runs SET edited_markdown = ?, edit_revision = edit_revision + 1, updated_at = ? WHERE id = ?",
                     (markdown, ai._now(), run_id))
    return get_run(run_id)
