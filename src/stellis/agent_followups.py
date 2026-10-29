"""Source-quoted follow-up proposals, published atomically with successful work."""
from __future__ import annotations
import json
import re
import uuid
from stellis import storage, suggestions


def validate(value, sources):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'text', 'source_index', 'quote'}:
        raise ValueError('Follow-up needs text, source_index and quote')
    values = suggestions.validate_values('add_todo', {'text': value['text']})
    index = value['source_index']
    quote = value['quote']
    if type(index) is not int or not 1 <= index <= len(sources):
        raise ValueError('Follow-up source is unavailable')
    if not isinstance(quote, str) or not 12 <= len(quote.strip()) <= 500:
        raise ValueError('Follow-up needs a short source quote')
    source = sources[index - 1]
    if ' '.join(quote.split()) not in ' '.join(str(source.get('excerpt', '')).split()):
        raise ValueError('Follow-up quote does not match its source')
    missing_authorization = re.search(r'\b(?:no|missing|not received|unconfirmed)\b', quote, re.I) and re.search(r'\b(?:approval|authorization|sign.off)\b', quote, re.I)
    invent_authorization = re.search(r'\b(?:create|fabricate|invent|generate)\b.{0,80}\b(?:approval|authorization|sign.off)\b', values['text'], re.I)
    if missing_authorization and invent_authorization:
        raise ValueError('Request the missing authorization from its owner; do not propose creating a substitute approval record')
    return {'text': values['text'], 'quote': quote.strip(), 'source_id': source.get('source_id'),
            'source_title': source['title'], 'source_target': source['target']}


def publish(conn, job_id):
    """Called inside job completion; no model calls or task writes occur here."""
    rows = conn.execute('''SELECT f.*,d.notebook_id,d.agent_id,a.name AS author
        FROM agent_followups f JOIN agent_artifacts d ON d.id=f.artifact_id
        JOIN agent_messages m ON m.id=d.message_id JOIN agents a ON a.id=d.agent_id
        JOIN agent_jobs j ON j.id=m.job_id
        WHERE j.id=? AND j.state='succeeded' AND f.suggestion_id IS NULL AND d.state='draft'
        AND a.enabled=1 AND (a.is_default=1 OR EXISTS (SELECT 1 FROM agent_notebooks n
            WHERE n.agent_id=a.id AND n.notebook_id=d.notebook_id AND n.enabled=1)) ''', (job_id,)).fetchall()
    for row in rows:
        book = row['notebook_id']
        if not suggestions._todo_enabled(conn, book):
            continue
        candidate = json.loads(row['candidate_json'])
        target = conn.execute("SELECT id FROM pages WHERE notebook_id=? AND lower(trim(title))='to-dos' AND trashed_at IS NULL ORDER BY created_at LIMIT 1", (book,)).fetchone()
        stamp = suggestions._stamp()
        target_id = target['id'] if target else str(uuid.uuid4())
        if not target:
            conn.execute('INSERT INTO pages(id,title,markdown,revision,notebook_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                         (target_id, 'To-dos', '', 0, book, stamp, stamp))
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stellis-followup:' + row['artifact_id']))
        source_kind = source_id = None
        for kind, prefix in [('page', '/page/'), ('conversation', '/conversation/')]:
            if candidate['source_target'].startswith(prefix):
                source_kind, source_id = kind, candidate['source_target'][len(prefix):].split('#')[0].split('?')[0]
        conn.execute('''INSERT OR IGNORE INTO suggestions
            (id,run_id,author,target_kind,target_id,operation,values_json,source_kind,source_id,evidence_json,state,created_at)
            VALUES(?,?,?,'page',?,'add_todo',?,?,?,?,'pending',?)''',
            (identifier, job_id, row['author'][:80], target_id, json.dumps({'text': candidate['text']}),
             source_kind, source_id, json.dumps([{'segment_id': '', 'quote': candidate['quote']}]), stamp))
        conn.execute('UPDATE agent_followups SET suggestion_id=? WHERE artifact_id=?', (identifier, row['artifact_id']))


def for_artifact(identifier, conn=None):
    if conn is None:
        with storage.connect() as connection:
            return for_artifact(identifier, connection)
    row = conn.execute('SELECT s.*,f.candidate_json FROM agent_followups f JOIN suggestions s ON s.id=f.suggestion_id WHERE f.artifact_id=?', (identifier,)).fetchone()
    if not row:
        return None
    result = suggestions.public(row)
    candidate = json.loads(row['candidate_json'])
    result['source_target'] = candidate['source_target']
    result['source_title'] = candidate['source_title']
    return result
