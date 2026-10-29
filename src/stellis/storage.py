"""Local persistence for imported conversations."""

from __future__ import annotations

import contextvars
import os
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

INITIAL_CATEGORIES = ("Meetings", "Interviews", "Workshops", "Briefings", "Calls")


def normalized_person_name(value: str) -> str:
    """Return the durable identity key used by every Person write."""
    return " ".join(value.split()).casefold()


_workspace = ContextVar("stellis_workspace", default=None)


def base_dir() -> Path:
    """Return the private local data directory, creating it when needed."""
    configured = os.environ.get("STELLIS_DATA_DIR")
    root = Path(configured).expanduser() if configured else Path.home() / "Library/Application Support/Stellis"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def data_dir() -> Path:
    """Returns the request's isolated demo workspace or the real account directory."""
    return _workspace.get() or base_dir()


def in_demo() -> bool:
    return _workspace.get() is not None


def background(target, *args, name: str | None = None) -> None:
    """Runs ``target(*args)`` on a daemon thread in the caller's workspace and demo clock.

    A plain thread starts with empty context variables, so a job queued from a demo would read
    and write the account's own database instead of the demo's.
    """
    context = contextvars.copy_context()
    threading.Thread(target=context.run, args=(target, *args), name=name, daemon=True).start()


def workspace_override() -> Path | None:
    """The demo workspace this context is inside, if any (for handing to a child process)."""
    return _workspace.get()


@contextmanager
def demo_workspace(path: Path):
    from stellis import story_clock
    token = _workspace.set(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    shift = story_clock.offset.set(timedelta(days=story_clock.days(story_clock.stored(path))))
    try:
        yield
    finally:
        story_clock.offset.reset(shift)
        _workspace.reset(token)


class TolerantConnection(sqlite3.Connection):
    """Treat ADD COLUMN on an existing column as done.

    The migrations in connect() check the table, then alter it. Two first connections
    (a request thread and the speech worker process) can both pass the check; the loser
    gets "duplicate column name" although the column it wanted now exists.
    """

    # Set while a demo scrub shadows tables with dated views (#252); the schema exists by then.
    story_shadowed = False

    def execute(self, sql, *args):  # type: ignore[no-untyped-def]
        if self.story_shadowed and re.match(r"\s*CREATE\s+(UNIQUE\s+)?(INDEX|TABLE|TRIGGER)\s+IF\s+NOT\s+EXISTS", sql, re.I):
            return self.cursor()
        try:
            return super().execute(sql, *args)
        except sqlite3.OperationalError as error:
            if sql.lstrip().upper().startswith("ALTER TABLE") and "duplicate column name" in str(error):
                return self.cursor()
            raise


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL, retrying while another first connection holds the lock.

    SQLite does not run the busy timeout for the journal-mode switch on a new database,
    so a racing connection fails at once with "database is locked".
    """
    deadline = time.monotonic() + 30
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) or time.monotonic() > deadline:
                raise
            time.sleep(0.01)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open and close a transaction with the current schema."""
    database = data_dir() / "stellis.sqlite3"
    conn = sqlite3.connect(database, timeout=30, factory=TolerantConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _enable_wal(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_sha256 TEXT NOT NULL UNIQUE,
            audio_filename TEXT NOT NULL,
            audio_media_type TEXT NOT NULL,
            duration_ms INTEGER NOT NULL CHECK (duration_ms > 0),
            created_at TEXT NOT NULL
        )
        """
    )
    conversation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
    if "trashed_at" not in conversation_columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN trashed_at TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS notebooks (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    if "notebook_id" not in conversation_columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL")
    # Collections group notebooks without nesting; deleting one leaves its notebooks ungrouped.
    conn.execute("""CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE,
        color TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    if "collection_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(notebooks)")}:
        conn.execute("ALTER TABLE notebooks ADD COLUMN collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL")
    # Whether agents may suggest to-dos (ADR 0059): a collection is on or off, a notebook also inherits.
    if "todo_list" not in {row["name"] for row in conn.execute("PRAGMA table_info(collections)")}:
        conn.execute("ALTER TABLE collections ADD COLUMN todo_list TEXT NOT NULL DEFAULT 'off'")
    if "todo_list" not in {row["name"] for row in conn.execute("PRAGMA table_info(notebooks)")}:
        conn.execute("ALTER TABLE notebooks ADD COLUMN todo_list TEXT NOT NULL DEFAULT 'inherit'")
    for column, definition in (("template_id", "TEXT"), ("description", "TEXT NOT NULL DEFAULT ''"),
                               ("catalogue_version", "INTEGER"), ("archived_at", "TEXT"),
                               ("capabilities_json", "TEXT"), ("recommendation_evidence_json", "TEXT NOT NULL DEFAULT '[]'")):
        if column not in {row["name"] for row in conn.execute("PRAGMA table_info(notebooks)")}:
            conn.execute(f"ALTER TABLE notebooks ADD COLUMN {column} {definition}")
    if "taxonomy_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(collections)")}:
        conn.execute("ALTER TABLE collections ADD COLUMN taxonomy_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS collection_taxonomy ON collections(taxonomy_id) WHERE taxonomy_id IS NOT NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS notebook_template ON notebooks(template_id) WHERE template_id IS NOT NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS notebook_record_links (
        notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
        source_id TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (notebook_id, source_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL, instructions TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
        is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    agent_columns = {row["name"] for row in conn.execute("PRAGMA table_info(agents)")}
    for column, definition in (
        ("template_id", "TEXT"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
    ):
        if column not in agent_columns:
            conn.execute(f"ALTER TABLE agents ADD COLUMN {column} {definition}")
    if "slug" not in agent_columns and "openclaw_id" in agent_columns:
        try:
            conn.execute("ALTER TABLE agents RENAME COLUMN openclaw_id TO slug")
        except sqlite3.OperationalError as error:
            # Another first connection may have completed this rename after our schema read.
            if "no such column" not in str(error) or "slug" not in {
                    row["name"] for row in conn.execute("PRAGMA table_info(agents)")}:
                raise
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS agents_one_default ON agents(is_default) WHERE is_default = 1""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_comparisons (
        id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE, signature TEXT NOT NULL UNIQUE,
        candidate_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', job_id TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_brief (
        id INTEGER PRIMARY KEY CHECK(id=1), job_id TEXT NOT NULL,
        message_id TEXT, coverage_json TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notebook_filings (
        id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
        items_json TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT, result_json TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS notebook_instructions (
        notebook_id TEXT PRIMARY KEY REFERENCES notebooks(id) ON DELETE CASCADE,
        instructions TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_notebooks (
        agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        notebook_id TEXT NOT NULL REFERENCES notebooks(id) ON DELETE CASCADE,
        is_steward INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(agent_id, notebook_id)
    )""")
    if "enabled" not in {row["name"] for row in conn.execute("PRAGMA table_info(agent_notebooks)")}:
        try:
            conn.execute("ALTER TABLE agent_notebooks ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError as error:
            if "duplicate column" not in str(error):
                raise
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS agent_notebook_one_steward
        ON agent_notebooks(notebook_id) WHERE is_steward = 1""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_memories (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
        kind TEXT NOT NULL, fact TEXT NOT NULL, source_id TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_messages (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, job_id TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_artifacts (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        message_id TEXT UNIQUE REFERENCES agent_messages(id) ON DELETE CASCADE,
        notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
        kind TEXT NOT NULL, title TEXT NOT NULL, recipients_json TEXT NOT NULL DEFAULT '[]',
        body TEXT NOT NULL, sources_json TEXT NOT NULL DEFAULT '[]',
        state TEXT NOT NULL DEFAULT 'draft', revision INTEGER NOT NULL DEFAULT 1,
        gmail_draft_id TEXT, account_email TEXT, error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_followups (
        artifact_id TEXT PRIMARY KEY REFERENCES agent_artifacts(id) ON DELETE CASCADE,
        candidate_json TEXT NOT NULL,
        suggestion_id TEXT UNIQUE REFERENCES suggestions(id) ON DELETE SET NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_handoffs (
        id TEXT PRIMARY KEY REFERENCES agent_jobs(id) ON DELETE CASCADE,
        origin_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        target_id TEXT REFERENCES agents(id) ON DELETE SET NULL, target_name TEXT NOT NULL,
        notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
        reply_id TEXT REFERENCES agent_messages(id) ON DELETE SET NULL,
        target_message_id TEXT REFERENCES agent_messages(id) ON DELETE SET NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS agent_messages_thread ON agent_messages(agent_id, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS inference_traces (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL,
        agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        job_id TEXT, profile TEXT NOT NULL, execution TEXT NOT NULL,
        model TEXT, provider TEXT, model_source TEXT NOT NULL DEFAULT 'configured',
        state TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
        duration_ms INTEGER, events_json TEXT NOT NULL DEFAULT '[]',
        usage_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS inference_trace_time ON inference_traces(created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS runtime_observations (
        id INTEGER PRIMARY KEY CHECK(id = 1), signature TEXT NOT NULL,
        checked_at TEXT NOT NULL, result_json TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_jobs (
        id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
        notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
        state TEXT NOT NULL, error TEXT,
        created_at TEXT NOT NULL, finished_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS agent_jobs_thread ON agent_jobs(agent_id, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_work_steps (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL, agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
        agent_name TEXT NOT NULL, task TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
        child_job_id TEXT REFERENCES agent_jobs(id) ON DELETE SET NULL,
        result TEXT NOT NULL DEFAULT '', sources_json TEXT NOT NULL DEFAULT '[]',
        error TEXT, UNIQUE(job_id, position))""")

    # Mail features were removed; drop their tables so copied mail text does not stay on disk.
    for table in ("mail_style_drafts", "mail_style_examples", "mail_style_groups", "mail_style_accounts",
                  "mail_run_events", "mail_rules", "agent_delegations"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    # When the audio was captured; imports without a known capture time fall back to created_at.
    if "recorded_at" not in conversation_columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN recorded_at TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS categories (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
        name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
    )""")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE name = 'initial_categories'").fetchone() is None:
        stamp = datetime.now(timezone.utc).isoformat()
        seeded = conn.execute("INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES ('initial_categories', ?)",
                              (stamp,)).rowcount
        if seeded:
            for name in INITIAL_CATEGORIES:
                conn.execute("INSERT OR IGNORE INTO categories (id, name, normalized, created_at) VALUES (?, ?, ?, ?)",
                             (str(uuid.uuid4()), name, name.casefold(), stamp))
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_categories (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        category_id TEXT NOT NULL REFERENCES categories(id),
        PRIMARY KEY(conversation_id, category_id)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS category_automation (
        id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL DEFAULT 0
    )""")
    if conn.execute("SELECT 1 FROM category_automation WHERE id = 1").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO category_automation (id, enabled) VALUES (1, 0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS category_decisions (
        conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        manual_override INTEGER NOT NULL DEFAULT 0,
        auto_category_id TEXT,
        state TEXT NOT NULL DEFAULT 'idle',
        model_id TEXT, prompt_version TEXT, transcript_revision INTEGER,
        confidence REAL, error TEXT, updated_at TEXT NOT NULL
    )""")
    if "prompt_version" not in {row["name"] for row in conn.execute("PRAGMA table_info(category_decisions)")}:
        conn.execute("ALTER TABLE category_decisions ADD COLUMN prompt_version TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS persons (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    person_columns = {row["name"] for row in conn.execute("PRAGMA table_info(persons)")}
    if "hidden_at" not in person_columns:
        conn.execute("ALTER TABLE persons ADD COLUMN hidden_at TEXT")
    if "name_key" not in person_columns:
        conn.execute("ALTER TABLE persons ADD COLUMN name_key TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS person_links (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        segment_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES persons(id),
        start_ms INTEGER NOT NULL,
        confirmed_at TEXT NOT NULL,
        PRIMARY KEY(conversation_id, segment_id)
    )""")
    if conn.execute("SELECT 1 FROM persons WHERE name_key IS NULL LIMIT 1").fetchone():
        # Prefer a visible profile, then the oldest ID; move links before deleting
        # duplicate legacy rows so confirmed passages stay attached to one Person.
        rows = conn.execute("""SELECT id, name FROM persons
            ORDER BY hidden_at IS NOT NULL, created_at, id""").fetchall()
        keeper_by_key: dict[str, str] = {}
        for row in rows:
            key = normalized_person_name(str(row["name"]))
            keeper = keeper_by_key.get(key)
            if keeper:
                conn.execute("UPDATE person_links SET person_id = ? WHERE person_id = ?", (keeper, row["id"]))
                conn.execute("DELETE FROM persons WHERE id = ?", (row["id"],))
            else:
                keeper_by_key[key] = str(row["id"])
                conn.execute("UPDATE persons SET name_key = ? WHERE id = ?", (key, row["id"]))
    link_columns = {row["name"] for row in conn.execute("PRAGMA table_info(person_links)")}
    if "origin" not in link_columns:
        try:
            conn.execute("ALTER TABLE person_links ADD COLUMN origin TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
    if conn.execute("SELECT 1 FROM person_links WHERE origin IS NULL LIMIT 1").fetchone():
        # Older links did not record where they came from. A link confirmed alone was
        # made on that passage; links written together came from a speaker-wide choice.
        conn.execute("""UPDATE person_links SET origin = CASE WHEN (SELECT COUNT(*) FROM person_links other
            WHERE other.conversation_id = person_links.conversation_id
              AND other.confirmed_at = person_links.confirmed_at) > 1 THEN 'propagated' ELSE 'manual' END
            WHERE origin IS NULL""")
    conn.execute("""CREATE TABLE IF NOT EXISTS passage_voices (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        segment_id TEXT NOT NULL,
        vector BLOB NOT NULL,
        PRIMARY KEY(conversation_id, segment_id)
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS persons_name_key_unique ON persons(name_key)")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS persons_name_key_required_insert
        BEFORE INSERT ON persons WHEN NEW.name_key IS NULL OR NEW.name_key = ''
        BEGIN SELECT RAISE(ABORT, 'Normalized speaker name is required'); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS persons_name_key_required_update
        BEFORE UPDATE OF name, name_key ON persons WHEN NEW.name_key IS NULL OR NEW.name_key = ''
        BEGIN SELECT RAISE(ABORT, 'Normalized speaker name is required'); END""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS processing_jobs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            pipeline_version TEXT NOT NULL,
            state TEXT NOT NULL,
            stage TEXT NOT NULL,
            progress REAL NOT NULL,
            error TEXT,
            metrics_json TEXT,
            vocabulary_json TEXT NOT NULL DEFAULT '[]',
            kind TEXT NOT NULL DEFAULT 'speech',
            source_generation_version TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(conversation_id, pipeline_version)
        )"""
    )
    job_columns = {row["name"] for row in conn.execute("PRAGMA table_info(processing_jobs)")}
    if "vocabulary_json" not in job_columns:
        conn.execute("ALTER TABLE processing_jobs ADD COLUMN vocabulary_json TEXT NOT NULL DEFAULT '[]'")
    if "kind" not in job_columns:
        conn.execute("ALTER TABLE processing_jobs ADD COLUMN kind TEXT NOT NULL DEFAULT 'speech'")
    if "source_generation_version" not in job_columns:
        conn.execute("ALTER TABLE processing_jobs ADD COLUMN source_generation_version TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcripts (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
            revision INTEGER NOT NULL,
            generation_version TEXT NOT NULL DEFAULT 'legacy',
            language TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            turns_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS import_references (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            transcript_text TEXT NOT NULL,
            summary_text TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )"""
    )
    transcript_columns = {row["name"] for row in conn.execute("PRAGMA table_info(transcripts)")}
    if "generation_version" not in transcript_columns:
        conn.execute("ALTER TABLE transcripts ADD COLUMN generation_version TEXT NOT NULL DEFAULT 'legacy'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcript_generations (
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            pipeline_version TEXT NOT NULL,
            revision INTEGER NOT NULL,
            language TEXT NOT NULL,
            segments_json TEXT NOT NULL,
            turns_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, pipeline_version)
        )"""
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS transcript_reviews (
        conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
        source_version TEXT NOT NULL,
        target_version TEXT NOT NULL,
        source_revision INTEGER NOT NULL,
        items_json TEXT NOT NULL,
        decisions_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('pending', 'accepted')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS transcript_review_history (
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        target_version TEXT NOT NULL,
        source_version TEXT NOT NULL,
        source_revision INTEGER NOT NULL,
        items_json TEXT NOT NULL,
        decisions_json TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'kept_source')),
        decided_at TEXT NOT NULL,
        PRIMARY KEY(conversation_id, target_version)
    )""")
    if conn.execute("SELECT 1 FROM transcript_generations LIMIT 1").fetchone() is None:
        conn.execute(
            """INSERT OR IGNORE INTO transcript_generations
               (conversation_id, pipeline_version, revision, language, segments_json, turns_json, created_at)
               SELECT conversation_id, generation_version, revision, language, segments_json, turns_json, updated_at
               FROM transcripts"""
        )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcript_edits (
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            segment_id TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, segment_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcript_edit_events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            segment_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            old_text TEXT NOT NULL,
            new_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS speaker_labels (
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            scope TEXT NOT NULL CHECK (scope IN ('cluster', 'segment')),
            target_id TEXT NOT NULL,
            name TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(conversation_id, scope, target_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS speaker_label_events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            scope TEXT NOT NULL,
            target_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            old_name TEXT,
            new_name TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS vocabulary (
            id TEXT PRIMARY KEY,
            term TEXT NOT NULL,
            normalized TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL DEFAULT 'other',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    if "kind" not in {row["name"] for row in conn.execute("PRAGMA table_info(vocabulary)")}:
        conn.execute("ALTER TABLE vocabulary ADD COLUMN kind TEXT NOT NULL DEFAULT 'other'")
    conn.execute("CREATE INDEX IF NOT EXISTS vocabulary_kind_normalized ON vocabulary(kind, normalized)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcript_highlights (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            segment_id TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            quote TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS transcript_comments (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        segment_id TEXT NOT NULL,
        start_offset INTEGER NOT NULL,
        end_offset INTEGER NOT NULL,
        quote TEXT NOT NULL,
        start_ms INTEGER NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recording_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('recording', 'finalized')),
            conversation_id TEXT REFERENCES conversations(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    if "notebook_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(recording_sessions)")}:
        conn.execute("ALTER TABLE recording_sessions ADD COLUMN notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_images (
        id TEXT PRIMARY KEY,
        conversation_id TEXT REFERENCES conversations(id),
        recording_id TEXT REFERENCES recording_sessions(id),
        media_position_ms INTEGER NOT NULL CHECK (media_position_ms >= 0),
        segment_id TEXT,
        caption TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK ((conversation_id IS NOT NULL) != (recording_id IS NOT NULL))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS page_images (
        id TEXT PRIMARY KEY,
        page_id TEXT NOT NULL REFERENCES pages(id),
        caption TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS page_images_by_page ON page_images(page_id, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS link_previews (
        url TEXT PRIMARY KEY,
        final_url TEXT,
        title TEXT,
        description TEXT,
        site_name TEXT,
        image_url TEXT,
        icon_url TEXT,
        status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
        error TEXT,
        fetched_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS conversation_images_by_conversation ON conversation_images(conversation_id, media_position_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS conversation_images_by_recording ON conversation_images(recording_id, media_position_ms)")
    image_columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversation_images)")}
    for column, definition in (("start_offset", "INTEGER"), ("end_offset", "INTEGER"), ("quote", "TEXT")):
        if column not in image_columns:
            conn.execute(f"ALTER TABLE conversation_images ADD COLUMN {column} {definition}")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recording_chunks (
            recording_id TEXT NOT NULL REFERENCES recording_sessions(id),
            sequence INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(recording_id, sequence)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recording_events (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recording_sessions(id),
            kind TEXT NOT NULL CHECK (kind IN ('pause', 'resume', 'device_ended')),
            media_position_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            markdown TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 0,
            notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            trashed_at TEXT
        )"""
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS page_categories (
        page_id TEXT NOT NULL REFERENCES pages(id),
        category_id TEXT NOT NULL REFERENCES categories(id),
        PRIMARY KEY(page_id, category_id)
    )""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS todo_items (
            id TEXT PRIMARY KEY,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            position INTEGER NOT NULL,
            due_date TEXT,
            due_time TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            canceled_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS todo_items_source ON todo_items (source_kind, source_id)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS suggestions (
            id TEXT PRIMARY KEY,
            run_id TEXT,
            author TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            todo_id TEXT,
            values_json TEXT NOT NULL,
            original_values_json TEXT,
            previous_json TEXT,
            source_kind TEXT,
            source_id TEXT,
            evidence_json TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            amended_at TEXT,
            decided_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS suggestions_target ON suggestions (target_kind, target_id, state)")
    # The sample workspace was removed; its items stay as ordinary user content.
    conn.execute("DROP TABLE IF EXISTS sample_items")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS meeting_notes (
            id TEXT PRIMARY KEY,
            conversation_id TEXT UNIQUE REFERENCES conversations(id),
            recording_id TEXT UNIQUE REFERENCES recording_sessions(id),
            markdown TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            CHECK (conversation_id IS NOT NULL OR recording_id IS NOT NULL)
        )"""
    )
    # Notes written for a calendar meeting before it is recorded; keyed by the provider's event ID only.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS event_notes (
            provider TEXT NOT NULL,
            event_id TEXT NOT NULL,
            markdown TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(provider, event_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS enhanced_notes_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            input_hash TEXT NOT NULL,
            transcript_revision INTEGER NOT NULL,
            source_identity_hash TEXT NOT NULL,
            notes_revision INTEGER NOT NULL,
            notes_hash TEXT NOT NULL,
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            state TEXT NOT NULL,
            stage TEXT NOT NULL,
            result_json TEXT,
            edited_markdown TEXT,
            edit_revision INTEGER NOT NULL DEFAULT 0,
            source_starts_json TEXT NOT NULL,
            usage_json TEXT NOT NULL DEFAULT '{}',
            provider_request_ids_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS transcript_agent_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            summary_run_id TEXT NOT NULL UNIQUE,
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            state TEXT NOT NULL,
            stage TEXT NOT NULL,
            result_json TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            provider_request_ids_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS recording_ai_runs (
            id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recording_sessions(id),
            agent_id TEXT,
            question TEXT NOT NULL,
            input_segments_json TEXT NOT NULL,
            source_starts_json TEXT NOT NULL,
            model_id TEXT NOT NULL,
            state TEXT NOT NULL,
            stage TEXT NOT NULL,
            result_json TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            provider_request_ids_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    if "agent_id" not in {row["name"] for row in conn.execute("PRAGMA table_info(recording_ai_runs)")}:
        conn.execute("ALTER TABLE recording_ai_runs ADD COLUMN agent_id TEXT")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ai_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            kind TEXT NOT NULL CHECK (kind IN ('summary', 'answer')),
            input_hash TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            source_identity_hash TEXT,
            source_segments_json TEXT NOT NULL DEFAULT '{}',
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            question TEXT,
            state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
            stage TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            result_json TEXT,
            checkpoint_json TEXT NOT NULL DEFAULT '[]',
            usage_json TEXT NOT NULL DEFAULT '{}',
            provider_request_ids_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(conversation_id, kind, input_hash)
        )"""
    )
    ai_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_runs)")}
    if "checkpoint_json" not in ai_columns:
        conn.execute("ALTER TABLE ai_runs ADD COLUMN checkpoint_json TEXT NOT NULL DEFAULT '[]'")
    if "source_identity_hash" not in ai_columns:
        conn.execute("ALTER TABLE ai_runs ADD COLUMN source_identity_hash TEXT")
    if "source_segments_json" not in ai_columns:
        conn.execute("ALTER TABLE ai_runs ADD COLUMN source_segments_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS clean_runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            input_hash TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            model_id TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
            stage TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            result_json TEXT,
            checkpoint_json TEXT NOT NULL DEFAULT '[]',
            usage_json TEXT NOT NULL DEFAULT '{}',
            provider_request_ids_json TEXT NOT NULL DEFAULT '[]',
            attempts INTEGER NOT NULL DEFAULT 0,
            edit_revision INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(conversation_id, input_hash)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS clean_transcript_edits (
            run_id TEXT NOT NULL REFERENCES clean_runs(id),
            segment_id TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id, segment_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS clean_run_reviews (
            run_id TEXT NOT NULL REFERENCES clean_runs(id),
            segment_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY(run_id, segment_id)
        )"""
    )
    conn.commit()
    database.chmod(0o600)
    if in_demo():
        from stellis import story_clock
        earlier = story_clock.looking_back(data_dir())
        if earlier is not None:
            story_clock.shadow_later_rows(conn, earlier)
            conn.story_shadowed = True
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def list_conversations(*, trashed: bool = False) -> list[dict[str, object]]:
    """List local conversations with stable ordering."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, duration_ms, created_at, recorded_at, notebook_id, trashed_at FROM conversations "
            "WHERE (trashed_at IS NOT NULL) = ? ORDER BY COALESCE(recorded_at, created_at) DESC, created_at DESC",
            (int(trashed),)
        ).fetchall()
    return [dict(row) for row in rows]


def get_conversation(conversation_id: str, *, include_trashed: bool = False) -> dict[str, object] | None:
    """Return one conversation or None when it is absent."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?" +
                           ("" if include_trashed else " AND trashed_at IS NULL"), (conversation_id,)).fetchone()
    return dict(row) if row else None


def rename_conversation(conversation_id: str, title: str) -> dict[str, object]:
    """Rename an active conversation while retaining its other fields."""
    value = title.strip()
    if not value or len(value) > 180:
        raise ValueError("Enter a title of at most 180 characters")
    with connect() as conn:
        changed = conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ? AND trashed_at IS NULL",
            (value, conversation_id),
        ).rowcount
        if not changed:
            raise LookupError("Conversation not found")
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    return dict(row)


def find_by_hash(source_sha256: str) -> dict[str, object] | None:
    """Find a previously imported audio file by content digest."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE source_sha256 = ?", (source_sha256,)
        ).fetchone()
    return dict(row) if row else None


def add_conversation(record: dict[str, object], *, record_notebook_filing: bool = False) -> None:
    """Insert a validated import record."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations
                (id, title, source_sha256, audio_filename, audio_media_type, duration_ms, created_at,
                 recorded_at, notebook_id)
            VALUES (:id, :title, :source_sha256, :audio_filename,
                    :audio_media_type, :duration_ms, :created_at, :recorded_at, :notebook_id)
            """,
            {"recorded_at": None, "notebook_id": None} | record,
        )
        if record_notebook_filing and record.get('notebook_id'):
            from stellis import notebook_filing
            notebook_filing.record_conversation(conn, record)



def get_job(job_id: str) -> dict[str, object] | None:
    """Return a persisted processing job."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM processing_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_job_for_conversation(conversation_id: str, pipeline_version: str) -> dict[str, object] | None:
    """Find the idempotent job for a recording and pipeline version."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM processing_jobs WHERE conversation_id = ? AND pipeline_version = ?",
            (conversation_id, pipeline_version),
        ).fetchone()
    return dict(row) if row else None


def get_latest_job_for_conversation(conversation_id: str) -> dict[str, object] | None:
    """Find the newest job, including one with an older vocabulary snapshot."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM processing_jobs WHERE conversation_id = ? AND kind != 'silence' ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def list_pending_jobs() -> list[dict[str, object]]:
    """Find unfinished work after a device-service restart."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM processing_jobs WHERE state IN ('queued', 'running')").fetchall()
    return [dict(row) for row in rows]


def add_job(record: dict[str, object]) -> None:
    """Persist a newly queued job."""
    record = {**record, "kind": record.get("kind", "speech"),
              "source_generation_version": record.get("source_generation_version")}
    with connect() as conn:
        conn.execute(
            """INSERT INTO processing_jobs
               (id, conversation_id, pipeline_version, state, stage, progress, error,
                metrics_json, vocabulary_json, kind, source_generation_version, created_at, updated_at)
               VALUES (:id, :conversation_id, :pipeline_version, :state, :stage, :progress,
                       :error, :metrics_json, :vocabulary_json, :kind, :source_generation_version,
                       :created_at, :updated_at)""",
            record,
        )


def update_job(job_id: str, **fields: object) -> None:
    """Update only known job fields."""
    allowed = {"state", "stage", "progress", "error", "metrics_json", "updated_at"}
    if not fields.keys() <= allowed:
        raise ValueError("Unknown job field")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE processing_jobs SET {assignments} WHERE id = ?",
            (*fields.values(), job_id),
        )


def save_transcript(record: dict[str, object]) -> None:
    """Activate a generation while retaining prior machine output and edits."""
    version = str(record.get("pipeline_version", "legacy"))
    stamp = str(record["updated_at"])
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM transcripts WHERE conversation_id = ?",
                           (record["conversation_id"],)).fetchone()
        if old is not None:
            conn.execute(
                """INSERT OR IGNORE INTO transcript_generations
                   (conversation_id, pipeline_version, revision, language, segments_json, turns_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (old["conversation_id"], old["generation_version"], old["revision"],
                 old["language"], old["segments_json"], old["turns_json"], old["updated_at"]),
            )
            revision = int(old["revision"]) + 1
        else:
            revision = int(record["revision"])
        conn.execute(
            """INSERT OR IGNORE INTO transcript_generations
               (conversation_id, pipeline_version, revision, language, segments_json, turns_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record["conversation_id"], version, revision, record["language"],
             record["segments_json"], record["turns_json"], stamp),
        )
        conn.execute(
            """INSERT INTO transcripts
               (conversation_id, revision, generation_version, language, segments_json, turns_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 revision = excluded.revision, generation_version = excluded.generation_version,
                 language = excluded.language,
                 segments_json = excluded.segments_json, turns_json = excluded.turns_json,
                 updated_at = excluded.updated_at""",
            (record["conversation_id"], revision, version, record["language"],
             record["segments_json"], record["turns_json"], stamp),
        )


def get_transcript(conversation_id: str) -> dict[str, object] | None:
    """Return stored transcript data without exposing the database layout."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return dict(row) if row else None


def save_import_reference(conversation_id: str, transcript_text: str,
                          summary_text: str, source_filename: str) -> None:
    """Keep exported wording beside the independently generated transcript."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO import_references
               (conversation_id, transcript_text, summary_text, source_filename, imported_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 transcript_text=excluded.transcript_text,
                 summary_text=excluded.summary_text,
                 source_filename=excluded.source_filename,
                 imported_at=excluded.imported_at""",
            (conversation_id, transcript_text, summary_text, source_filename,
             datetime.now(timezone.utc).isoformat()),
        )


def get_import_reference(conversation_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM import_references WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
    return dict(row) if row else None


def list_transcript_generations(conversation_id: str) -> list[dict[str, object]]:
    """Expose metadata about retained machine outputs, oldest first."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT pipeline_version, revision, language, created_at
               FROM transcript_generations WHERE conversation_id = ? ORDER BY created_at""",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_transcript_generation(conversation_id: str, version: str) -> dict[str, object] | None:
    """Return one immutable machine-output generation."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM transcript_generations WHERE conversation_id = ? AND pipeline_version = ?",
            (conversation_id, version),
        ).fetchone()
    return dict(row) if row else None


def get_transcript_edits(conversation_id: str) -> dict[str, str]:
    """Return user text overrides keyed by stable segment ID."""
    with connect() as conn:
        rows = conn.execute("SELECT segment_id, text FROM transcript_edits WHERE conversation_id = ?",
                            (conversation_id,)).fetchall()
    return {str(row["segment_id"]): str(row["text"]) for row in rows}


def get_speaker_labels(conversation_id: str) -> list[dict[str, object]]:
    """Return user-confirmed cluster and segment labels."""
    with connect() as conn:
        rows = conn.execute("SELECT scope, target_id, name, updated_at FROM speaker_labels WHERE conversation_id = ?",
                            (conversation_id,)).fetchall()
    return [dict(row) for row in rows]


def list_highlights(conversation_id: str) -> list[dict[str, object]]:
    """Return selected transcript excerpts in creation order."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, segment_id, start_offset, end_offset, quote, created_at
               FROM transcript_highlights WHERE conversation_id = ? ORDER BY created_at, id""",
            (conversation_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_comments(conversation_id: str) -> list[dict[str, object]]:
    """Return private transcript comments in creation order."""
    with connect() as conn:
        rows = conn.execute("""SELECT id, segment_id, start_offset, end_offset, quote,
            start_ms, body, created_at, updated_at FROM transcript_comments
            WHERE conversation_id = ? ORDER BY created_at, id""", (conversation_id,)).fetchall()
    return [dict(row) for row in rows]


def add_comment(conversation_id: str, segment_id: str, start_offset: int,
                end_offset: int, body: str, expected_revision: int) -> dict[str, object]:
    """Save a comment and its exact source excerpt in one transaction."""
    value = body.strip()
    if not value or len(value) > 2000:
        raise ValueError("Enter a comment of at most 2000 characters")
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM conversations WHERE id = ? AND trashed_at IS NULL", (conversation_id,)).fetchone():
            raise LookupError("Conversation not found")
        row = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None:
            raise LookupError("Transcript not found")
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Transcript changed. Reload and try again")
        segment = next((item for item in json.loads(str(row["segments_json"]))
                        if item["id"] == segment_id), None)
        if segment is None:
            raise LookupError("Passage not found")
        edit = conn.execute("SELECT text FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                            (conversation_id, segment_id)).fetchone()
        displayed = str(edit["text"]) if edit else str(segment["text"])
        if start_offset < 0 or end_offset > len(displayed) or start_offset >= end_offset:
            raise ValueError("Select text within one passage")
        record = {"id": str(uuid.uuid4()), "segment_id": segment_id,
                  "start_offset": start_offset, "end_offset": end_offset,
                  "quote": displayed[start_offset:end_offset], "start_ms": int(segment["start_ms"]),
                  "body": value, "created_at": stamp, "updated_at": stamp}
        conn.execute("""INSERT INTO transcript_comments (id, conversation_id, segment_id,
            start_offset, end_offset, quote, start_ms, body, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record["id"], conversation_id, segment_id, start_offset, end_offset,
             record["quote"], record["start_ms"], value, stamp, stamp))
    return record


def update_comment(conversation_id: str, comment_id: str, body: str) -> dict[str, object]:
    value = body.strip()
    if not value or len(value) > 2000:
        raise ValueError("Enter a comment of at most 2000 characters")
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        row = conn.execute("""UPDATE transcript_comments SET body = ?, updated_at = ?
            WHERE conversation_id = ? AND id = ? RETURNING id, segment_id, start_offset,
            end_offset, quote, start_ms, body, created_at, updated_at""",
            (value, stamp, conversation_id, comment_id)).fetchone()
    if row is None:
        raise LookupError("Comment not found")
    return dict(row)


def delete_comment(conversation_id: str, comment_id: str) -> bool:
    with connect() as conn:
        removed = conn.execute("DELETE FROM transcript_comments WHERE conversation_id = ? AND id = ?",
                               (conversation_id, comment_id)).rowcount
    return bool(removed)


def add_highlight(conversation_id: str, segment_id: str, start_offset: int,
                  end_offset: int, expected_revision: int) -> dict[str, object]:
    """Anchor a selected excerpt to the current displayed segment text."""
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None:
            raise LookupError("Transcript not found")
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Transcript changed. Reload and try again.")
        segment = next((item for item in json.loads(str(row["segments_json"]))
                        if item["id"] == segment_id), None)
        if segment is None:
            raise LookupError("Segment not found")
        edit = conn.execute("SELECT text FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                            (conversation_id, segment_id)).fetchone()
        displayed = str(edit["text"]) if edit else str(segment["text"])
        if start_offset < 0 or end_offset > len(displayed) or start_offset >= end_offset:
            raise ValueError("Select text within one passage")
        record = {"id": str(uuid.uuid4()), "segment_id": segment_id,
                  "start_offset": start_offset, "end_offset": end_offset,
                  "quote": displayed[start_offset:end_offset], "created_at": stamp}
        conn.execute(
            """INSERT INTO transcript_highlights
               (id, conversation_id, segment_id, start_offset, end_offset, quote, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record["id"], conversation_id, segment_id, start_offset, end_offset,
             record["quote"], stamp),
        )
    return record


def delete_highlight(conversation_id: str, highlight_id: str) -> bool:
    """Remove one highlight without altering the transcript."""
    with connect() as conn:
        cursor = conn.execute("DELETE FROM transcript_highlights WHERE conversation_id = ? AND id = ?",
                              (conversation_id, highlight_id))
    return cursor.rowcount > 0


def edit_transcript_segment(conversation_id: str, segment_id: str, text: str,
                            expected_revision: int) -> int:
    """Apply an optimistic text edit while preserving model text and edit history."""
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None:
            raise LookupError("Transcript not found")
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Transcript changed. Reload and try again.")
        segments = json.loads(str(row["segments_json"]))
        segment = next((item for item in segments if item["id"] == segment_id), None)
        if segment is None:
            raise LookupError("Segment not found")
        old = conn.execute("SELECT text FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                           (conversation_id, segment_id)).fetchone()
        old_text = str(old["text"]) if old else str(segment["text"])
        if text == old_text:
            return expected_revision
        if text == segment["text"]:
            conn.execute("DELETE FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                         (conversation_id, segment_id))
        else:
            conn.execute(
                """INSERT INTO transcript_edits (conversation_id, segment_id, text, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(conversation_id, segment_id) DO UPDATE SET
                   text = excluded.text, updated_at = excluded.updated_at""",
                (conversation_id, segment_id, text, stamp),
            )
        revision = expected_revision + 1
        conn.execute("UPDATE transcripts SET revision = ?, updated_at = ? WHERE conversation_id = ?",
                     (revision, stamp, conversation_id))
        conn.execute(
            """INSERT INTO transcript_edit_events
               (id, conversation_id, segment_id, revision, old_text, new_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), conversation_id, segment_id, revision, old_text, text, stamp),
        )
    return revision


def edit_transcript_segments(conversation_id: str, edits: list[dict[str, str]], expected_revision: int) -> int:
    """Commit multiple visible passage corrections in one optimistic revision."""
    if len({item["segment_id"] for item in edits}) != len(edits):
        raise ValueError("Each passage may appear only once")
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None:
            raise LookupError("Transcript not found")
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Transcript changed. Your edits are still in the browser; resolve the conflict before retrying.")
        sources = {str(item["id"]): str(item["text"]) for item in json.loads(str(row["segments_json"]))}
        if any(item["segment_id"] not in sources for item in edits):
            raise LookupError("Segment not found")
        existing = {str(item["segment_id"]): str(item["text"]) for item in conn.execute(
            "SELECT segment_id, text FROM transcript_edits WHERE conversation_id = ?", (conversation_id,))}
        changed = [(item["segment_id"], item["text"], existing.get(item["segment_id"], sources[item["segment_id"]]))
                   for item in edits if item["text"] != existing.get(item["segment_id"], sources[item["segment_id"]])]
        if not changed:
            return expected_revision
        revision = expected_revision + 1
        for segment_id, text, old_text in changed:
            if text == sources[segment_id]:
                conn.execute("DELETE FROM transcript_edits WHERE conversation_id = ? AND segment_id = ?",
                             (conversation_id, segment_id))
            else:
                conn.execute(
                    """INSERT INTO transcript_edits (conversation_id, segment_id, text, updated_at)
                       VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id, segment_id) DO UPDATE SET
                       text = excluded.text, updated_at = excluded.updated_at""",
                    (conversation_id, segment_id, text, stamp),
                )
            conn.execute(
                """INSERT INTO transcript_edit_events
                   (id, conversation_id, segment_id, revision, old_text, new_text, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), conversation_id, segment_id, revision, old_text, text, stamp),
            )
        conn.execute("UPDATE transcripts SET revision = ?, updated_at = ? WHERE conversation_id = ?",
                     (revision, stamp, conversation_id))
    return revision


def set_speaker_label(conversation_id: str, scope: str, target_id: str,
                      name: str | None, expected_revision: int) -> int:
    """Set or clear a manual speaker label without touching acoustic clusters."""
    if scope not in {"cluster", "segment"}:
        raise ValueError("Invalid speaker label scope")
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision, segments_json FROM transcripts WHERE conversation_id = ?",
                           (conversation_id,)).fetchone()
        if row is None:
            raise LookupError("Transcript not found")
        if int(row["revision"]) != expected_revision:
            raise RuntimeError("Transcript changed. Reload and try again.")
        segments = json.loads(str(row["segments_json"]))
        targets = {item["id"] if scope == "segment" else item["speaker_cluster_id"] for item in segments}
        if target_id not in targets or target_id is None:
            raise LookupError("Speaker target not found")
        old = conn.execute(
            "SELECT name FROM speaker_labels WHERE conversation_id = ? AND scope = ? AND target_id = ?",
            (conversation_id, scope, target_id),
        ).fetchone()
        old_name = str(old["name"]) if old else None
        if name == old_name:
            return expected_revision
        if name is None:
            conn.execute("DELETE FROM speaker_labels WHERE conversation_id = ? AND scope = ? AND target_id = ?",
                         (conversation_id, scope, target_id))
        else:
            conn.execute(
                """INSERT INTO speaker_labels (conversation_id, scope, target_id, name, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id, scope, target_id) DO UPDATE SET
                   name = excluded.name, updated_at = excluded.updated_at""",
                (conversation_id, scope, target_id, name, stamp),
            )
        revision = expected_revision + 1
        conn.execute("UPDATE transcripts SET revision = ?, updated_at = ? WHERE conversation_id = ?",
                     (revision, stamp, conversation_id))
        conn.execute(
            """INSERT INTO speaker_label_events
               (id, conversation_id, scope, target_id, revision, old_name, new_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), conversation_id, scope, target_id, revision, old_name, name, stamp),
        )
    return revision


def list_vocabulary(kind: str | None = None) -> list[dict[str, object]]:
    """List user-provided ASR hints in stable order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, term, kind, created_at, updated_at FROM vocabulary"
            + (" WHERE kind = ?" if kind else "") + " ORDER BY normalized",
            (kind,) if kind else (),
        ).fetchall()
    return [dict(row) for row in rows]


def add_vocabulary(term: str, kind: str = "other") -> dict[str, object]:
    """Add one unique vocabulary hint."""
    stamp = datetime.now(timezone.utc).isoformat()
    record = {"id": str(uuid.uuid4()), "term": term, "normalized": term.casefold(), "kind": kind,
              "created_at": stamp, "updated_at": stamp}
    with connect() as conn:
        conn.execute("INSERT INTO vocabulary (id, term, normalized, kind, created_at, updated_at) VALUES (:id, :term, :normalized, :kind, :created_at, :updated_at)", record)
    return {key: record[key] for key in ("id", "term", "kind", "created_at", "updated_at")}


def add_vocabulary_batch(terms: list[str], kind: str) -> dict[str, object]:
    """Add distinct terms in one transaction and report existing terms."""
    added: list[dict[str, object]] = []
    skipped: list[str] = []
    with connect() as conn:
        existing = {row[0] for row in conn.execute("SELECT normalized FROM vocabulary")}
        for term in terms:
            normalized = term.casefold()
            if normalized in existing:
                skipped.append(term)
                continue
            stamp = datetime.now(timezone.utc).isoformat()
            record = {"id": str(uuid.uuid4()), "term": term, "normalized": normalized,
                      "kind": kind, "created_at": stamp, "updated_at": stamp}
            conn.execute("INSERT INTO vocabulary (id, term, normalized, kind, created_at, updated_at) VALUES (:id, :term, :normalized, :kind, :created_at, :updated_at)", record)
            added.append({key: record[key] for key in ("id", "term", "kind", "created_at", "updated_at")})
            existing.add(normalized)
    return {"added": added, "skipped": skipped}


def update_vocabulary(term_id: str, term: str) -> dict[str, object] | None:
    """Rename a vocabulary hint without changing its ID."""
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute("UPDATE vocabulary SET term = ?, normalized = ?, updated_at = ? WHERE id = ?",
                              (term, term.casefold(), stamp, term_id))
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT id, term, kind, created_at, updated_at FROM vocabulary WHERE id = ?",
                           (term_id,)).fetchone()
    return dict(row)


def move_vocabulary(ids: list[str], kind: str) -> int:
    """Move a selected set of terms between vocabulary sections atomically."""
    with connect() as conn:
        found = {row[0] for row in conn.execute(
            f"SELECT id FROM vocabulary WHERE id IN ({','.join('?' for _ in ids)})", ids)}
        if found != set(ids):
            raise KeyError("Vocabulary term not found")
        conn.executemany("UPDATE vocabulary SET kind = ?, updated_at = ? WHERE id = ?",
                         [(kind, datetime.now(timezone.utc).isoformat(), term_id) for term_id in ids])
    return len(ids)


def delete_vocabulary_batch(ids: list[str]) -> int:
    """Delete a selected set of terms atomically."""
    with connect() as conn:
        found = {row[0] for row in conn.execute(
            f"SELECT id FROM vocabulary WHERE id IN ({','.join('?' for _ in ids)})", ids)}
        if found != set(ids):
            raise KeyError("Vocabulary term not found")
        conn.executemany("DELETE FROM vocabulary WHERE id = ?", [(term_id,) for term_id in ids])
    return len(ids)


def delete_vocabulary(term_id: str) -> bool:
    """Remove a vocabulary hint; existing transcripts are unaffected."""
    with connect() as conn:
        cursor = conn.execute("DELETE FROM vocabulary WHERE id = ?", (term_id,))
    return cursor.rowcount > 0


def find_ai_run(conversation_id: str, kind: str, input_hash: str) -> dict[str, object] | None:
    """Find an idempotent AI request for an exact transcript revision and input."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM ai_runs WHERE conversation_id = ? AND kind = ? AND input_hash = ?",
            (conversation_id, kind, input_hash),
        ).fetchone()
    return dict(row) if row else None


def add_ai_run(record: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO ai_runs
               (id, conversation_id, kind, input_hash, source_revision, source_identity_hash, source_segments_json, model_id, prompt_version,
                question, state, stage, error_code, error_message, result_json, checkpoint_json, usage_json,
                provider_request_ids_json, attempts, created_at, updated_at)
               VALUES (:id, :conversation_id, :kind, :input_hash, :source_revision, :source_identity_hash, :source_segments_json, :model_id,
                       :prompt_version, :question, :state, :stage, :error_code, :error_message,
                       :result_json, :checkpoint_json, :usage_json, :provider_request_ids_json, :attempts,
                       :created_at, :updated_at)""",
            record,
        )


def get_ai_run(run_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM ai_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_ai_runs(conversation_id: str) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM ai_runs WHERE conversation_id = ? ORDER BY created_at DESC",
                            (conversation_id,)).fetchall()
    return [dict(row) for row in rows]


def update_ai_run(run_id: str, **fields: object) -> None:
    """Update only runtime fields, leaving the source identity immutable."""
    allowed = {"state", "stage", "error_code", "error_message", "result_json", "checkpoint_json", "usage_json",
               "provider_request_ids_json", "attempts", "updated_at"}
    if not fields.keys() <= allowed:
        raise ValueError("Unknown AI run field")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(f"UPDATE ai_runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))


def fail_interrupted_ai_runs() -> None:
    """Make a server-restart interruption visible and explicitly retryable."""
    with connect() as conn:
        conn.execute(
            """UPDATE ai_runs SET state = 'failed', stage = 'interrupted',
               error_code = 'interrupted', error_message = 'The device service stopped during this request',
               updated_at = ? WHERE state IN ('queued', 'running')""",
            (datetime.now(timezone.utc).isoformat(),),
        )


def add_clean_run(record: dict[str, object]) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO clean_runs
               (id, conversation_id, input_hash, source_revision, model_id, prompt_version,
                state, stage, error_code, error_message, result_json, checkpoint_json,
                usage_json, provider_request_ids_json, attempts, edit_revision, created_at, updated_at)
               VALUES (:id, :conversation_id, :input_hash, :source_revision, :model_id, :prompt_version,
                       :state, :stage, :error_code, :error_message, :result_json, :checkpoint_json,
                       :usage_json, :provider_request_ids_json, :attempts, :edit_revision, :created_at, :updated_at)""",
            record,
        )


def get_clean_run(run_id: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clean_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def find_clean_run(conversation_id: str, input_hash: str) -> dict[str, object] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM clean_runs WHERE conversation_id = ? AND input_hash = ?",
                           (conversation_id, input_hash)).fetchone()
    return dict(row) if row else None


def list_clean_runs(conversation_id: str) -> list[dict[str, object]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM clean_runs WHERE conversation_id = ? ORDER BY created_at DESC",
                            (conversation_id,)).fetchall()
    return [dict(row) for row in rows]


def update_clean_run(run_id: str, **fields: object) -> None:
    allowed = {"state", "stage", "error_code", "error_message", "result_json", "checkpoint_json",
               "usage_json", "provider_request_ids_json", "attempts", "updated_at"}
    if not fields.keys() <= allowed:
        raise ValueError("Unknown clean run field")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with connect() as conn:
        conn.execute(f"UPDATE clean_runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))


def fail_interrupted_clean_runs() -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE clean_runs SET state = 'failed', stage = 'interrupted', error_code = 'interrupted',
               error_message = 'The device service stopped during this request', updated_at = ?
               WHERE state IN ('queued', 'running')""",
            (datetime.now(timezone.utc).isoformat(),),
        )


def clean_run_edits(run_id: str) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT segment_id, text FROM clean_transcript_edits WHERE run_id = ?", (run_id,)).fetchall()
    return {str(row["segment_id"]): str(row["text"]) for row in rows}


def clean_run_reviews(run_id: str) -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT segment_id FROM clean_run_reviews WHERE run_id = ?", (run_id,)).fetchall()
    return {str(row["segment_id"]) for row in rows}


def set_clean_run_review(run_id: str, segment_id: str, expected_revision: int) -> int:
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state, edit_revision, result_json FROM clean_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["state"] != "succeeded":
            raise LookupError("Edited transcript is not ready")
        if int(row["edit_revision"]) != expected_revision:
            raise RuntimeError("Edited transcript changed. Reload and try again")
        if not any(item["id"] == segment_id for item in json.loads(str(row["result_json"]))["segments"]):
            raise LookupError("Segment not found")
        conn.execute("INSERT OR REPLACE INTO clean_run_reviews (run_id, segment_id, reviewed_at) VALUES (?, ?, ?)",
                     (run_id, segment_id, stamp))
        revision = expected_revision + 1
        conn.execute("UPDATE clean_runs SET edit_revision = ?, updated_at = ? WHERE id = ?",
                     (revision, stamp, run_id))
    return revision


def set_clean_run_edit(run_id: str, segment_id: str, text: str, expected_revision: int) -> int:
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state, edit_revision, result_json FROM clean_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["state"] != "succeeded":
            raise LookupError("Edited transcript is not ready")
        if int(row["edit_revision"]) != expected_revision:
            raise RuntimeError("Edited transcript changed. Reload and try again")
        result = json.loads(str(row["result_json"]))
        original = next((item["text"] for item in result["segments"] if item["id"] == segment_id), None)
        if original is None:
            raise LookupError("Segment not found")
        if text == original:
            conn.execute("DELETE FROM clean_transcript_edits WHERE run_id = ? AND segment_id = ?",
                         (run_id, segment_id))
        else:
            conn.execute(
                """INSERT INTO clean_transcript_edits (run_id, segment_id, text, updated_at)
                   VALUES (?, ?, ?, ?) ON CONFLICT(run_id, segment_id) DO UPDATE SET
                   text = excluded.text, updated_at = excluded.updated_at""",
                (run_id, segment_id, text, stamp),
            )
        revision = expected_revision + 1
        conn.execute("UPDATE clean_runs SET edit_revision = ?, updated_at = ? WHERE id = ?",
                     (revision, stamp, run_id))
    return revision


def set_clean_run_edits(run_id: str, edits: list[dict[str, str]], expected_revision: int) -> int:
    """Save an edited clean-transcript session without changing the source."""
    if len({item["segment_id"] for item in edits}) != len(edits):
        raise ValueError("Each passage may appear only once")
    stamp = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state, edit_revision, result_json FROM clean_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None or row["state"] != "succeeded":
            raise LookupError("Edited transcript is not ready")
        if int(row["edit_revision"]) != expected_revision:
            raise RuntimeError("Edited transcript changed. Your wording is still in the browser")
        sources = {item["id"]: item["text"] for item in json.loads(str(row["result_json"]))["segments"]}
        if any(item["segment_id"] not in sources for item in edits):
            raise LookupError("Segment not found")
        for item in edits:
            if item["text"] == sources[item["segment_id"]]:
                conn.execute("DELETE FROM clean_transcript_edits WHERE run_id = ? AND segment_id = ?",
                             (run_id, item["segment_id"]))
            else:
                conn.execute(
                    """INSERT INTO clean_transcript_edits (run_id, segment_id, text, updated_at)
                       VALUES (?, ?, ?, ?) ON CONFLICT(run_id, segment_id) DO UPDATE SET
                       text = excluded.text, updated_at = excluded.updated_at""",
                    (run_id, item["segment_id"], item["text"], stamp),
                )
        revision = expected_revision + 1
        conn.execute("UPDATE clean_runs SET edit_revision = ?, updated_at = ? WHERE id = ?",
                     (revision, stamp, run_id))
    return revision


def get_display_transcript(conversation_id: str) -> dict[str, object] | None:
    """Compose current model output with user edits and speaker labels."""
    record = get_transcript(conversation_id)
    if record is None:
        return None
    edits = get_transcript_edits(conversation_id)
    labels = get_speaker_labels(conversation_id)
    with connect() as conn:
        person_links = {str(row["segment_id"]): {"id": str(row["person_id"]), "name": str(row["name"]),
                                                  "origin": str(row["origin"] or "manual")}
                        for row in conn.execute("""SELECT pl.segment_id, pl.origin, p.id AS person_id, p.name
                            FROM person_links pl JOIN persons p ON p.id = pl.person_id
                            WHERE pl.conversation_id = ?""", (conversation_id,))}
    cluster_labels = {str(item["target_id"]): str(item["name"]) for item in labels if item["scope"] == "cluster"}
    segment_labels = {str(item["target_id"]): str(item["name"]) for item in labels if item["scope"] == "segment"}
    segments = json.loads(str(record["segments_json"]))
    # Older generations may still hold passages with no acoustic turn.
    from stellis import speaker_fallback
    speaker_fallback.fill_unassigned(segments, json.loads(str(record["turns_json"])))
    cluster_order = list(dict.fromkeys(segment["speaker_cluster_id"] for segment in segments))
    display_names = {cluster: f"Speaker {index + 1}" for index, cluster in enumerate(cluster_order)}
    for segment in segments:
        override = edits.get(segment["id"])
        if override is not None:
            segment["original_text"] = segment["text"]
            segment["text"] = override
            segment["text_provenance"] = "user_edit"
            segment["words"] = []  # ASR offsets cannot be projected onto changed wording.
        else:
            segment["text_provenance"] = "on_device_asr"
        cluster = segment["speaker_cluster_id"]
        if segment["id"] in person_links:
            segment["speaker_display_name"] = person_links[segment["id"]]["name"]
            segment["speaker_label_source"] = "confirmed_person"
            segment["person_id"] = person_links[segment["id"]]["id"]
            segment["speaker_uncertain"] = False
            segment["speaker_label_origin"] = person_links[segment["id"]]["origin"]
        elif segment["id"] in segment_labels:
            segment["speaker_display_name"] = segment_labels[segment["id"]]
            segment["speaker_label_source"] = "manual_segment"
            segment["speaker_label_origin"] = "manual"
        elif cluster in cluster_labels:
            segment["speaker_display_name"] = cluster_labels[cluster]
            segment["speaker_label_source"] = "manual_cluster"
            segment["speaker_label_origin"] = "propagated"
        else:
            segment["speaker_display_name"] = display_names[cluster]
            segment["speaker_label_source"] = "automatic"
            segment["speaker_label_origin"] = "automatic"
    # A speaker-wide confirmation names the whole group. After regrouping, a few of the
    # group's passages can lack the carried link; they show the group's person too.
    group_people: dict[str, dict[str, int]] = {}
    for segment in segments:
        if segment.get("person_id"):
            people = group_people.setdefault(str(segment["speaker_cluster_id"]), {})
            people[segment["person_id"]] = people.get(segment["person_id"], 0) + 1
    group_size: dict[str, int] = {}
    for segment in segments:
        group_size[str(segment["speaker_cluster_id"])] = group_size.get(str(segment["speaker_cluster_id"]), 0) + 1
    named_groups = {cluster: next(iter(people)) for cluster, people in group_people.items()
                    if len(people) == 1 and next(iter(people.values())) * 2 > group_size[cluster]}
    names_by_person = {link["id"]: link["name"] for link in person_links.values()}
    for segment in segments:
        cluster = str(segment["speaker_cluster_id"])
        if segment["speaker_label_source"] == "automatic" and cluster in named_groups:
            segment["speaker_display_name"] = names_by_person[named_groups[cluster]]
            segment["speaker_label_source"] = "confirmed_group"
            segment["speaker_label_origin"] = "propagated"
            segment["speaker_group_person_id"] = named_groups[cluster]
    from stellis import voices
    suggested = voices.suggestions(conversation_id, segments)
    for segment in segments:
        if segment["speaker_label_source"] == "automatic" and segment["speaker_cluster_id"] in suggested:
            segment["speaker_suggestion"] = suggested[segment["speaker_cluster_id"]]
    return {
        "conversation_id": conversation_id, "revision": record["revision"],
        "generation_version": record["generation_version"], "language": record["language"],
        "segments": segments, "turns": json.loads(str(record["turns_json"])),
        "speaker_labels": labels, "highlights": list_highlights(conversation_id),
        "comments": list_comments(conversation_id),
    }
