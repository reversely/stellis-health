"""Stellis, the one assistant: identity, notebook access, memory and Chat history."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from stellis import storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(value: str, label: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > limit:
        raise ValueError(f"{label} must be 1 to {limit} characters")
    return cleaned


DEFAULT_NAME = "Stellis"
DEFAULT_ROLE = "AI agent for your health records"
# Roles an account was given by default before; one still unchanged takes the current default.
_EARLIER_ROLES = {"Your health assistant"}
DEFAULT_INSTRUCTIONS = (
    "Help the person with their own health. Answer from their records and cite them. Research public "
    "medical sources when they ask, draft letters and questions for appointments when they ask, and "
    "suggest check-ins for them to approve. Never diagnose, and never suggest starting, stopping or "
    "changing a medicine; suggest bringing those questions to their clinician.")
# Earlier releases named the default agent General, then Chief of Staff.
_EARLIER_DEFAULTS = ("general", "chief of staff")


def _default(conn: sqlite3.Connection) -> None:
    """Ensures Stellis, the one assistant, exists and answers in every notebook."""
    row = conn.execute("SELECT id, name_key, instructions FROM agents WHERE is_default = 1").fetchone()
    if row is None:
        now = _now()
        taken = conn.execute("SELECT 1 FROM agents WHERE slug = 'stellis'").fetchone()
        agent_id = str(uuid.uuid4())
        conn.execute("""INSERT INTO agents
            (id, name, name_key, role, instructions, slug, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (agent_id, DEFAULT_NAME, DEFAULT_NAME.casefold(), DEFAULT_ROLE, DEFAULT_INSTRUCTIONS,
             "stellis-assistant" if taken else "stellis", now, now))
    else:
        agent_id = str(row["id"])
        named = conn.execute("SELECT 1 FROM agents WHERE name_key = ? AND id != ?",
                             (DEFAULT_NAME.casefold(), agent_id)).fetchone()
        if row["name_key"] in _EARLIER_DEFAULTS and not named:
            conn.execute("""UPDATE agents SET name = ?, name_key = ?, role = ?, instructions = ?, updated_at = ?
                WHERE id = ?""", (DEFAULT_NAME, DEFAULT_NAME.casefold(), DEFAULT_ROLE,
                                  row["instructions"] or DEFAULT_INSTRUCTIONS, _now(), agent_id))
    conn.execute("UPDATE agents SET role = ?, updated_at = ? WHERE id = ? AND role IN ({})".format(",".join("?" * len(_EARLIER_ROLES))),
                 (DEFAULT_ROLE, _now(), agent_id, *sorted(_EARLIER_ROLES)))
    _consolidate(conn, agent_id)
    conn.execute("""INSERT OR IGNORE INTO agent_notebooks (agent_id, notebook_id)
        SELECT ?, id FROM notebooks""", (agent_id,))


def _consolidate(conn: sqlite3.Connection, agent_id: str) -> None:
    """Moves agents from the earlier agent team into Stellis, then deletes them.

    Their memories, messages, drafts and request history move to Stellis, so
    nothing the person wrote or saved is lost.
    """
    if not conn.execute("SELECT 1 FROM agents WHERE is_default = 0").fetchone():
        return
    for table in ("agent_memories", "agent_messages", "agent_artifacts", "agent_jobs"):
        conn.execute(f"UPDATE {table} SET agent_id = ? WHERE agent_id != ?", (agent_id, agent_id))
    conn.execute("UPDATE agent_work_steps SET agent_id = ? WHERE agent_id IS NOT NULL", (agent_id,))
    conn.execute("DELETE FROM agents WHERE is_default = 0")


def _record(conn: sqlite3.Connection, agent_id: str) -> dict[str, object]:
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise LookupError("Agent not found")
    assignments = [dict(item) for item in conn.execute("""SELECT an.notebook_id, n.name AS notebook_name,
        an.is_steward, an.enabled FROM agent_notebooks an JOIN notebooks n ON n.id = an.notebook_id
        WHERE an.agent_id = ? ORDER BY n.name COLLATE NOCASE""", (agent_id,))]
    record = {**dict(row), "is_default": bool(row["is_default"]), "enabled": bool(row["enabled"]),
              "notebooks": [{**item, "is_steward": bool(item["is_steward"]), "enabled": bool(item["enabled"])} for item in assignments]}
    return {**record, "behavior": {"does": record["role"], "returns": "Reply", "runs": "When you ask",
                                   "asks_first": "Changes outside this chat"}}


def list_agents() -> list[dict[str, object]]:
    with storage.connect() as conn:
        _default(conn)
        ids = [str(row["id"]) for row in conn.execute("SELECT id FROM agents ORDER BY is_default DESC, name COLLATE NOCASE")]
        return [_record(conn, agent_id) for agent_id in ids]


def assistant() -> dict[str, object]:
    """Returns Stellis."""
    return list_agents()[0]


def get(agent_id: str) -> dict[str, object]:
    with storage.connect() as conn:
        _default(conn)
        return _record(conn, agent_id)


def update(agent_id: str, *, name: str | None = None, role: str | None = None,
           instructions: str | None = None, enabled: bool | None = None) -> dict[str, object]:
    with storage.connect() as conn:
        current = _record(conn, agent_id)
        next_name = _value(name, "Name", 80) if name is not None else str(current["name"])
        next_role = _value(role, "Role", 160) if role is not None else str(current["role"])
        next_instructions = instructions.strip() if instructions is not None else str(current["instructions"])
        if len(next_instructions) > 4000:
            raise ValueError("Instructions must be at most 4000 characters")
        try:
            conn.execute("""UPDATE agents SET name = ?, name_key = ?, role = ?, instructions = ?, updated_at = ?
                WHERE id = ?""", (next_name, next_name.casefold(), next_role, next_instructions, _now(), agent_id))
        except sqlite3.IntegrityError as exc:
            raise ValueError("An agent with this name already exists") from exc
        if enabled is not None:
            if current["is_default"] and not enabled:
                raise ValueError("The default agent must remain on")
            conn.execute("UPDATE agents SET enabled = ? WHERE id = ?", (int(enabled), agent_id))
        return _record(conn, agent_id)


def require_enabled(agent_id: str, notebook_id: str | None = None) -> dict[str, object]:
    agent = get(agent_id)
    if not agent["enabled"]:
        raise ValueError("This agent is off. Turn it on in Agent settings.")
    if notebook_id:
        assignment = next((item for item in agent["notebooks"] if item["notebook_id"] == notebook_id), None)
        if assignment and not assignment["enabled"]:
            raise ValueError("This agent is off in this notebook. Turn it on in Agent settings.")
    return agent


def resolved_agent(notebook_id: str | None = None) -> dict[str, object]:
    """Returns Stellis, which answers in every notebook."""
    del notebook_id  # Stellis answers everywhere.
    return {"agent": assistant(), "reason": "assistant"}


def memories(agent_id: str) -> list[dict[str, object]]:
    with storage.connect() as conn:
        _record(conn, agent_id)
        return [dict(row) for row in conn.execute("""SELECT * FROM agent_memories WHERE agent_id = ?
            ORDER BY created_at, id""", (agent_id,))]


def save_memory(agent_id: str, fact: str, notebook_id: str | None = None,
                memory_id: str | None = None, source_id: str | None = None) -> dict[str, object]:
    fact = _value(fact, "Memory", 2000)
    with storage.connect() as conn:
        _record(conn, agent_id)
        if notebook_id and not conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone():
            raise LookupError("Notebook not found")
        if memory_id:
            count = conn.execute("""UPDATE agent_memories SET fact = ?, kind = 'general', notebook_id = ?,
                source_id = ?, updated_at = ? WHERE id = ? AND agent_id = ?""",
                (fact, notebook_id, source_id, _now(), memory_id, agent_id)).rowcount
            if not count:
                raise LookupError("Memory not found")
        else:
            memory_id = str(uuid.uuid4())
            now = _now()
            conn.execute("""INSERT INTO agent_memories
                (id, agent_id, notebook_id, kind, fact, source_id, created_at, updated_at)
                VALUES (?, ?, ?, 'general', ?, ?, ?, ?)""",
                (memory_id, agent_id, notebook_id, fact, source_id, now, now))
        return dict(conn.execute("SELECT * FROM agent_memories WHERE id = ?", (memory_id,)).fetchone())


def delete_memory(agent_id: str, memory_id: str) -> None:
    with storage.connect() as conn:
        if not conn.execute("DELETE FROM agent_memories WHERE id = ? AND agent_id = ?", (memory_id, agent_id)).rowcount:
            raise LookupError("Memory not found")


def messages(agent_id: str) -> list[dict[str, object]]:
    with storage.connect() as conn:
        _record(conn, agent_id)
        rows = [dict(row) for row in conn.execute("""SELECT m.*,h.target_id AS addressed_agent_id,
            h.target_name AS addressed_agent_name,h.reply_id,h.target_message_id
            FROM agent_messages m LEFT JOIN agent_handoffs h ON h.id=m.job_id
            WHERE m.agent_id=? ORDER BY m.created_at,m.id""", (agent_id,))]
        from stellis import agent_artifacts
        for row in rows:
            if row['id'] == row.get('reply_id') and row.get('target_message_id'):
                artifact = conn.execute('SELECT * FROM agent_artifacts WHERE message_id=?', (row['target_message_id'],)).fetchone()
                if artifact:row['routed_artifact'] = agent_artifacts._record(artifact, conn)
        return rows



def append_message(agent_id: str, role: str, content: str, notebook_id: str | None = None,
                   job_id: str | None = None) -> dict[str, object]:
    if role not in {"user", "agent", "system"}:
        raise ValueError("Unknown message role")
    content = _value(content, "Message", 12000)
    with storage.connect() as conn:
        _record(conn, agent_id)
        if notebook_id and not conn.execute("SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone():
            raise LookupError("Notebook not found")
        message_id = str(uuid.uuid4())
        conn.execute("""INSERT INTO agent_messages(id, agent_id, notebook_id, role, content, job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (message_id, agent_id, notebook_id, role, content, job_id, _now()))
        return dict(conn.execute("SELECT * FROM agent_messages WHERE id = ?", (message_id,)).fetchone())


def start_job(agent_id: str, notebook_id: str | None = None) -> str:
    """Record a reply before calling the sandbox so failed attempts stay visible."""
    job_id = str(uuid.uuid4())
    with storage.connect() as conn:
        _record(conn, agent_id)
        conn.execute("""INSERT INTO agent_jobs(id, agent_id, notebook_id, state, created_at)
            VALUES (?, ?, ?, 'running', ?)""", (job_id, agent_id, notebook_id, _now()))
    return job_id


def finish_job(job_id: str, state: str, error: str | None = None) -> None:
    if state not in {"succeeded", "failed", "interrupted"}:
        raise ValueError("Unknown job state")
    with storage.connect() as conn:
        conn.execute("""UPDATE agent_jobs SET state = ?, error = ?, finished_at = ? WHERE id = ?""",
                     (state, error, _now(), job_id))
        if state == 'succeeded':
            from stellis import agent_followups
            agent_followups.publish(conn, job_id)


def jobs(agent_id: str) -> list[dict[str, object]]:
    with storage.connect() as conn:
        _record(conn, agent_id)
        rows = [dict(row) for row in conn.execute("""SELECT j.* FROM agent_jobs j LEFT JOIN agent_handoffs h ON h.id=j.id WHERE j.agent_id = ? OR h.target_id = ?
            ORDER BY j.created_at,j.id""", (agent_id,agent_id))]
    return rows


def interrupt_running_jobs() -> None:
    """Mark replies lost when the web process stopped during a sandbox call."""
    with storage.connect() as conn:
        conn.execute("""UPDATE agent_jobs SET state = 'interrupted', error = 'Reply interrupted. Send again.',
            finished_at = ? WHERE state = 'running'""", (_now(),))
        conn.execute("UPDATE agent_work_steps SET state='interrupted', error='Work interrupted. Send again.' WHERE state='running'")
        conn.execute("UPDATE agent_work_steps SET state='skipped' WHERE state='queued'")
