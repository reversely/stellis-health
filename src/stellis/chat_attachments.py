"""Retained chat files with explicit notebook filing and message associations."""

import uuid

from stellis import agents, documents, notebook_chat, notebook_sources, notebooks, storage

MAX_ATTACHMENTS = 5


def _table(conn):
    documents._create_tables(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS chat_attachments (
        id TEXT PRIMARY KEY, thread_kind TEXT NOT NULL, thread_id TEXT NOT NULL,
        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        message_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS chat_attachments_thread
        ON chat_attachments(thread_kind, thread_id)""")


def check_thread(kind, identifier):
    if kind == 'agent':
        agents.get(identifier)
    elif kind == 'notebook':
        if not any(book['id'] == identifier for book in notebooks.list_notebooks()):
            raise LookupError('Notebook not found')
    else:
        raise ValueError('Choose an agent or notebook chat')


def list_all(kind, identifier):
    check_thread(kind, identifier)
    with storage.connect() as conn:
        _table(conn)
        return [dict(row) for row in conn.execute("""SELECT a.*, d.title, d.filename,
            d.notebook_id, d.status, d.media_type FROM chat_attachments a
            JOIN documents d ON d.id=a.document_id
            WHERE a.thread_kind=? AND a.thread_id=? ORDER BY a.created_at,a.rowid""",
            (kind, identifier))]


def add(kind, identifier, filename, content):
    pending = [item for item in list_all(kind, identifier) if not item['message_id']]
    if len(pending) >= MAX_ATTACHMENTS:
        raise ValueError('Send or remove the current attachments first')
    record, _ = documents.create(filename, content)
    attachment_id = str(uuid.uuid4())
    with storage.connect() as conn:
        _table(conn)
        # Serialize the limit check with insertion across concurrent uploads.
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        count = conn.execute("""SELECT count(*) FROM chat_attachments
            WHERE thread_kind=? AND thread_id=? AND message_id IS NULL""", (kind, identifier)).fetchone()[0]
        if count >= MAX_ATTACHMENTS:
            raise ValueError('Send or remove the current attachments first')
        conn.execute("""INSERT INTO chat_attachments(id,thread_kind,thread_id,document_id)
            VALUES (?,?,?,?)""", (attachment_id, kind, identifier, record['id']))
    return next(item for item in list_all(kind, identifier) if item['id'] == attachment_id)


def selected(kind, identifier, attachment_ids):
    if len(attachment_ids) > MAX_ATTACHMENTS or len(set(attachment_ids)) != len(attachment_ids):
        raise ValueError('Choose up to five different attachments')
    available = {item['id']: item for item in list_all(kind, identifier)}
    result = []
    for attachment_id in attachment_ids:
        item = available.get(attachment_id)
        if not item or item['message_id']:
            raise ValueError('Attachment is unavailable in this chat')
        result.append(item)
    return result


def passages(kind, identifier, attachment_ids, question):
    if not attachment_ids:
        return []
    items = selected(kind, identifier, attachment_ids)
    sources = [{'id': 'document:' + item['document_id'], 'title': item['title']} for item in items]
    return notebook_chat.rank(notebook_chat.read_passages(sources), question, 12)


def bind(kind, identifier, attachment_ids, message_id, conn=None):
    if not attachment_ids:
        return
    if conn is None:
        with storage.connect() as connection:
            bind(kind, identifier, attachment_ids, message_id, connection)
        return
    _table(conn)
    for attachment_id in attachment_ids:
        changed = conn.execute("""UPDATE chat_attachments SET message_id=?
            WHERE id=? AND thread_kind=? AND thread_id=? AND message_id IS NULL""",
            (message_id, attachment_id, kind, identifier)).rowcount
        if not changed:
            raise ValueError('Attachment has already been sent or removed')


def save(kind, identifier, attachment_id, notebook_id):
    item = next((item for item in list_all(kind, identifier) if item['id'] == attachment_id), None)
    if not item:
        raise LookupError('Attachment not found')
    if item['notebook_id'] and item['notebook_id'] != notebook_id:
        raise ValueError('This file is already saved in another notebook')
    notebook_sources.require_room(notebook_id, 'document:' + item['document_id'])
    documents.update(item['document_id'], notebook_id=notebook_id, move=True)
    return next(item for item in list_all(kind, identifier) if item['id'] == attachment_id)


def remove(kind, identifier, attachment_id):
    check_thread(kind, identifier)
    with storage.connect() as conn:
        _table(conn)
        if not conn.execute("""DELETE FROM chat_attachments WHERE id=? AND thread_kind=?
            AND thread_id=? AND message_id IS NULL""", (attachment_id, kind, identifier)).rowcount:
            raise LookupError('Unsent attachment not found')
