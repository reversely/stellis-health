"""Opt-in Nebius typed decisions for processed conversations."""

from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timezone

import httpx

from stellis import runtime_trace, ai, runtime_profile, storage
from stellis import privacy

MODEL_ID = "zai-org/GLM-5.3-Flash"
PROMPT_VERSION = "six-choice-v1"
LABELS = ("Meetings", "Interviews", "Workshops", "Briefings", "Calls", "Unclear")
_LOCK = threading.Lock()
_RUNNING: set[str] = set()


def _model_id() -> str:
    return runtime_profile.model_id("categories")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def settings() -> dict[str, object]:
    with storage.connect() as conn:
        enabled = bool(conn.execute("SELECT enabled FROM category_automation WHERE id = 1").fetchone()[0])
    return {"enabled": enabled, "configured": bool(ai._key()), "model_id": _model_id()}


def set_enabled(enabled: bool) -> dict[str, object]:
    with storage.connect() as conn:
        conn.execute("UPDATE category_automation SET enabled = ? WHERE id = 1", (int(enabled),))
        if not enabled:
            conn.execute("""UPDATE category_decisions SET state = 'idle', error = NULL, updated_at = ?
                WHERE state IN ('queued', 'running')""", (_now(),))
    return settings()


def status(conversation_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        if not conn.execute("SELECT 1 FROM conversations WHERE id = ? AND trashed_at IS NULL", (conversation_id,)).fetchone():
            raise LookupError("Conversation not found")
        row = conn.execute("""SELECT state, confidence, error, manual_override, model_id, prompt_version,
            transcript_revision FROM category_decisions WHERE conversation_id = ?""",
                           (conversation_id,)).fetchone()
        ready = bool(conn.execute("SELECT 1 FROM transcripts WHERE conversation_id = ?", (conversation_id,)).fetchone())
    return {**(dict(row) if row else {"state": "idle", "confidence": None, "error": None,
                                     "manual_override": False}), "ready": ready}


def fail_interrupted() -> None:
    with storage.connect() as conn:
        conn.execute("""UPDATE category_decisions SET state = 'failed', error = 'Classification interrupted; retry',
            updated_at = ? WHERE state IN ('queued', 'running')""", (_now(),))


def _sample(display: dict[str, object], title: str) -> str:
    segments = [str(item.get("text", "")).strip() for item in display["segments"]]
    segments = [text for text in segments if text]
    if not segments:
        return ""
    # Cover the beginning, middle, and end without uploading a full long transcript.
    count = len(segments)
    if count <= 50:
        chunks = [" ".join(segments)[:4800]]
    else:
        windows = ((0, 25), (count // 2 - 12, count // 2 + 13), (count - 25, count))
        chunks = [" ".join(segments[start:end])[:1600] for start, end in windows]
    return f"Title: {title[:120]}\nTranscript excerpts:\n" + "\n…\n".join(chunks)


def _score_once(source: str, labels: tuple[str, ...], key: str) -> dict[str, float]:
    options = "\n".join(f"{chr(65 + index)}. {name}" for index, name in enumerate(labels))
    prompt = ("Choose the single best conversation type. Meetings are group discussions that coordinate "
              "or decide work; Interviews have sustained questions and answers; Workshops are hands-on "
              "collaborative sessions such as brainstorming, design, or training exercises; Briefings are "
              "one person presenting or explaining to others, such as a talk, lecture, or demo; Calls are "
              "short or informal one-to-one conversations. If the transcript is only greetings, "
              "setup, noise, or too little evidence, choose Unclear. Reply with one letter.\n"
              f"{options}\n\n{source}\nAnswer:")
    body = {"model": _model_id(), "prompt": f"[gMASK]<sop><|user|>\n{prompt}\n<|assistant|>\n</think>",
            "max_tokens": 1, "temperature": 0, "logprobs": 20}
    from stellis import token_quota
    if message := token_quota.exceeded():
        raise RuntimeError(message)
    try:
        with httpx.Client(transport=privacy.Transport(), timeout=30, trust_env=False, follow_redirects=False) as client:
            if runtime_profile.local_inference():
                # Ollama exposes token probabilities through its native API.
                base = runtime_profile.inference_url().removesuffix("/v1")
                response = client.post(f"{base}/api/generate", json={
                    "model": _model_id(), "prompt": prompt, "stream": False, "think": False,
                    "logprobs": True, "top_logprobs": 20,
                    "options": {"num_predict": 1, "temperature": 0}})
                response.raise_for_status()
                data = response.json()
                runtime_trace.report(model=data.get("model"))
                alternatives = data["logprobs"][0]["top_logprobs"]
                top = {item["token"]: item["logprob"] for item in alternatives}
            elif _model_id() == MODEL_ID:
                response = client.post(f"{ai.BASE_URL}/completions", json=body,
                                       headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                data = response.json()
                runtime_trace.report(model=data.get("model"))
                token_quota.record(data.get("usage"))
                choice = data["choices"][0]
                top = choice["logprobs"]["top_logprobs"][0]
            else:
                # Other models use their provider's chat template, never GLM control tokens.
                response = client.post(f"{runtime_profile.inference_url()}/chat/completions", json={
                    "model": _model_id(), "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20},
                    headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                data = response.json()
                runtime_trace.report(model=data.get("model"))
                token_quota.record(data.get("usage"))
                alternatives = data["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
                top = {item["token"]: item["logprob"] for item in alternatives}
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Category provider unavailable; retry") from exc
    weights = {name: 0.0 for name in labels}
    for token, logprob in top.items():
        value = str(token).strip().lower()
        for index, name in enumerate(labels):
            if value in (chr(97 + index), name.lower()):
                weights[name] += math.exp(float(logprob))
                break
    total = sum(weights.values())
    if total < 0.8:
        return {name: float(name == "Unclear") for name in labels}
    return {name: value / total for name, value in weights.items()}


def _classify_impl(source: str, key: str) -> tuple[str, float]:
    scores = {name: 0.0 for name in LABELS}
    rotations = len(LABELS) - 1
    for shift in range(rotations):
        order = LABELS[shift:] + LABELS[:shift]
        for name, value in _score_once(source, order, key).items():
            scores[name] += value / rotations
    ranked = sorted(scores, key=scores.get, reverse=True)
    top, second = ranked[:2]
    confidence = scores[top]
    if top == "Unclear" or confidence < 0.85 or confidence - scores[second] < 0.5:
        return "Unclear", confidence
    return top, confidence


def request(conversation_id: str, *, retry: bool = False) -> dict[str, object]:
    if not settings()["enabled"]:
        return status(conversation_id)
    display = storage.get_display_transcript(conversation_id)
    if display is None:
        raise RuntimeError("Transcript is not ready")
    revision = int(display["revision"])
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT manual_override, state, transcript_revision, prompt_version FROM category_decisions WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None and conn.execute("SELECT 1 FROM conversation_categories WHERE conversation_id = ? LIMIT 1",
                                        (conversation_id,)).fetchone():
            conn.execute("""INSERT INTO category_decisions (conversation_id, manual_override, state, updated_at)
                VALUES (?, 1, 'manual', ?)""", (conversation_id, _now()))
            skip = True
        else:
            skip = bool(row and (row["manual_override"] or (not retry and row["transcript_revision"] == revision
                                                      and row["prompt_version"] == PROMPT_VERSION
                                                      and row["state"] in ("queued", "running", "assigned", "unclear"))))
        if not skip:
            conn.execute("""INSERT INTO category_decisions
            (conversation_id, state, model_id, prompt_version, transcript_revision, updated_at)
            VALUES (?, 'queued', ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET state = 'queued', model_id = excluded.model_id,
            prompt_version = excluded.prompt_version, transcript_revision = excluded.transcript_revision,
            confidence = NULL, error = NULL, updated_at = excluded.updated_at""",
                         (conversation_id, _model_id(), PROMPT_VERSION, revision, _now()))
    if skip:
        return status(conversation_id)
    key = ai._key()
    if not key:
        with storage.connect() as conn:
            conn.execute("""UPDATE category_decisions SET state = 'failed',
                error = 'Nebius API key is unavailable', updated_at = ? WHERE conversation_id = ?""",
                         (_now(), conversation_id))
        return status(conversation_id)
    with _LOCK:
        if conversation_id not in _RUNNING:
            _RUNNING.add(conversation_id)
            storage.background(_run, conversation_id, revision, key)
    return status(conversation_id)


def _run(conversation_id: str, revision: int, key: str) -> None:
    try:
        with storage.connect() as conn:
            conn.execute("UPDATE category_decisions SET state = 'running', updated_at = ? WHERE conversation_id = ?",
                         (_now(), conversation_id))
        display = storage.get_display_transcript(conversation_id)
        conversation = storage.get_conversation(conversation_id)
        if not display or not conversation or int(display["revision"]) != revision:
            raise RuntimeError("Transcript changed; retry classification")
        source = _sample(display, str(conversation["title"]))
        if sum(len(str(segment.get("text", "")).split()) for segment in display["segments"]) < 25:
            label, confidence = "Unclear", 0.0
        else:
            label, confidence = classify(source, key)
        with storage.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            decision = conn.execute("SELECT * FROM category_decisions WHERE conversation_id = ?", (conversation_id,)).fetchone()
            current = conn.execute("SELECT revision FROM transcripts WHERE conversation_id = ?", (conversation_id,)).fetchone()
            enabled = conn.execute("SELECT enabled FROM category_automation WHERE id = 1").fetchone()[0]
            if (not decision or decision["manual_override"] or decision["transcript_revision"] != revision
                    or not current or current["revision"] != revision or not enabled):
                return
            previous = decision["auto_category_id"]
            if previous:
                conn.execute("DELETE FROM conversation_categories WHERE conversation_id = ? AND category_id = ?",
                             (conversation_id, previous))
            category_id = None
            if label != "Unclear":
                category = conn.execute("SELECT id FROM categories WHERE normalized = ?", (label.casefold(),)).fetchone()
                if category:
                    category_id = str(category["id"])
                    conn.execute("INSERT OR IGNORE INTO conversation_categories VALUES (?, ?)", (conversation_id, category_id))
            conn.execute("""UPDATE category_decisions SET state = ?, auto_category_id = ?, confidence = ?,
                error = NULL, updated_at = ? WHERE conversation_id = ?""",
                         ("assigned" if category_id else "unclear", category_id, confidence, _now(), conversation_id))
    except Exception as exc:
        with storage.connect() as conn:
            conn.execute("""UPDATE category_decisions SET state = 'failed', error = ?, updated_at = ?
                WHERE conversation_id = ? AND manual_override = 0""", (str(exc), _now(), conversation_id))
    finally:
        with _LOCK:
            _RUNNING.discard(conversation_id)


def classify(source: str, key: str) -> tuple[str, float]:
    with runtime_trace.span("categorization", model=_model_id(),
                            provider="Ollama" if runtime_profile.local_inference() else "Token Factory"):
        result = _classify_impl(source, key)
        runtime_trace.event("category_scored", reads=len(LABELS)-1)
        return result
