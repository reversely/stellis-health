"""Account-local execution metadata, without prompts, responses or reasoning."""
from __future__ import annotations

import asyncio
import contextvars
import json
import time
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from stellis import runtime_profile, storage

_GUARD = threading.Lock()
_SWITCHING = False
_ACTIVE_REQUESTS = set()

_ACTIVE = contextvars.ContextVar("inference_trace", default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def active() -> bool:
    """Returns whether this request already has an execution trace."""
    return _ACTIVE.get() is not None


def event(name: str, **details) -> None:
    trace_id = _ACTIVE.get()
    if not trace_id:
        return
    allowed = {"attempt", "source_count", "citation_count", "supplied_count", "cited_count", "error_type", "error_code", "reads", "step_count", "exit_code", "output_limit", "invalid_meta", "timed_out", "abandoned", "provider_error", "page_number", "character_count", "elapsed_ms", "query_count", "blocked_count", "result_count", "gate"}
    safe = {key: value for key, value in details.items() if key in allowed
            and isinstance(value, (int, str))}
    with storage.connect() as conn:
        row = conn.execute("SELECT events_json FROM inference_traces WHERE id = ?", (trace_id,)).fetchone()
        if row:
            events = json.loads(row[0])
            if len(events) < 64:
                events.append({"name": name, "at": _now(), **safe})
                conn.execute("UPDATE inference_traces SET events_json = ? WHERE id = ?",
                             (json.dumps(events), trace_id))


def job(job_id: str) -> None:
    if _ACTIVE.get():
        with storage.connect() as conn:
            conn.execute("UPDATE inference_traces SET job_id = ? WHERE id = ?", (job_id, _ACTIVE.get()))


def report(model=None, provider=None, usage=None) -> None:
    """Record only an explicit model identity and numeric token accounting."""
    if not _ACTIVE.get():
        return
    fields = {}
    for key, value in (("model", model), ("provider", provider)):
        if isinstance(value, str) and value.strip() and len(value) <= 200:
            fields[key] = value
    if "model" in fields:
        fields["model_source"] = "reported"
    cleaned = {}
    if isinstance(usage, dict):
        cleaned = {key: value for key, value in usage.items()
                   if key in {"input", "output", "total", "prompt_tokens", "completion_tokens", "total_tokens"}
                   and type(value) is int and value >= 0}
    if fields or cleaned:
        with storage.connect() as conn:
            if cleaned:
                # One request can make several model calls, such as a research plan and its answer.
                row = conn.execute("SELECT usage_json FROM inference_traces WHERE id = ?", (_ACTIVE.get(),)).fetchone()
                earlier = json.loads(row[0]) if row and row[0] else {}
                fields["usage_json"] = json.dumps({key: cleaned.get(key, 0) + earlier.get(key, 0)
                                                   for key in {*cleaned, *earlier}})
            conn.execute("UPDATE inference_traces SET " + ", ".join(f"{key} = ?" for key in fields)
                         + " WHERE id = ?", (*fields.values(), _ACTIVE.get()))


@contextmanager
def span(kind: str, agent_id=None, model=None, provider=None, execution=None):
    trace_id = str(uuid.uuid4())
    start = time.monotonic()
    if execution is None:
        execution = runtime_profile.agent_execution() if kind in {
            "agent_request", "agent_chat", "agent_plan", "agent_review", "report_explanation",
            "notebook_answer"} else ("GN100 Ollama" if runtime_profile.local_inference() else "Token Factory")
    with _GUARD:
        if _SWITCHING:
            raise RuntimeError("A model change is running. Try again when it finishes.")
        with storage.connect() as conn:
            conn.execute("""INSERT INTO inference_traces
                (id, kind, agent_id, profile, execution, model, provider, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)""",
                (trace_id, kind, agent_id, runtime_profile.selected(), execution, model, provider, _now()))
            conn.execute("""DELETE FROM inference_traces WHERE state != 'running' AND id NOT IN
                (SELECT id FROM inference_traces ORDER BY created_at DESC LIMIT 500)""")
        _ACTIVE_REQUESTS.add(trace_id)
    token = _ACTIVE.set(trace_id)
    state = "succeeded"
    event("request_started")
    try:
        yield trace_id
    except BaseException as exc:
        state = "interrupted" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "failed"
        code = getattr(exc, "code", "")
        if code not in {"timeout", "network", "authentication", "rate_limited", "model_unavailable", "provider", "invalid_json", "output_limit", "not_configured"}:
            code = ""
        event("request_interrupted" if state == "interrupted" else "request_failed",
              error_type=type(exc).__name__, error_code=code)
        raise
    else:
        event("request_completed")
    finally:
        with storage.connect() as conn:
            conn.execute("""UPDATE inference_traces SET state = ?, finished_at = ?, duration_ms = ?
                WHERE id = ?""", (state, _now(), round((time.monotonic()-start)*1000), trace_id))
        _ACTIVE.reset(token)
        with _GUARD:
            _ACTIVE_REQUESTS.discard(trace_id)


def recent():
    with storage.connect() as conn:
        rows = conn.execute("""SELECT t.*, a.name AS agent_name FROM inference_traces t
            LEFT JOIN agents a ON a.id = t.agent_id ORDER BY t.created_at DESC LIMIT 50""").fetchall()
    return [_item(row) for row in rows]


def _item(row):
    item = dict(row)
    item["events"] = json.loads(item.pop("events_json"))
    item["usage"] = json.loads(item.pop("usage_json"))
    return item


def get(trace_id):
    """One trace with its events, or None."""
    with storage.connect() as conn:
        row = conn.execute("""SELECT t.*, a.name AS agent_name FROM inference_traces t
            LEFT JOIN agents a ON a.id = t.agent_id WHERE t.id = ?""", (trace_id,)).fetchone()
    return _item(row) if row else None


def for_job(job_id):
    """The latest trace recorded for an agent job, or None."""
    with storage.connect() as conn:
        row = conn.execute("SELECT id FROM inference_traces WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                           (job_id,)).fetchone()
    return row["id"] if row else None


def clear():
    with storage.connect() as conn:
        conn.execute("DELETE FROM inference_traces WHERE state != 'running'")


def interrupt_running():
    with storage.connect() as conn:
        conn.execute("""UPDATE inference_traces SET state = 'interrupted', finished_at = ?
            WHERE state = 'running'""", (_now(),))


@contextmanager
def model_change():
    global _SWITCHING
    with _GUARD:
        with storage.connect() as conn:
            active = conn.execute("SELECT COUNT(*) FROM inference_traces WHERE state = 'running'").fetchone()[0]
            active += conn.execute("SELECT COUNT(*) FROM agent_jobs WHERE state = 'running'").fetchone()[0]
        if _SWITCHING or active or _ACTIVE_REQUESTS:
            raise ValueError("Wait for active requests to finish before changing models")
        _SWITCHING = True
    try:
        yield
    finally:
        with _GUARD:
            _SWITCHING = False
