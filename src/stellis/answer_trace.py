"""How one answer was produced: its trace joined with the answer's own evidence.

Traces hold only timing, model identity, token counts and step events (see
``runtime_trace``). Quotes and source checks come from the saved answer, so
health content stays with the record it belongs to.
"""
from __future__ import annotations

import hashlib
import json
import re

from stellis import runtime_trace, storage

QUOTE_CHARS = 160
_WORD = re.compile(r'[^\W\d_]{3,}')


def _quote(text, terms):
    """The first line sharing a word with the selection, else the opening text."""
    for line in text.splitlines():
        if terms & {word.casefold() for word in _WORD.findall(line)}:
            return ' '.join(line.split())[:QUOTE_CHARS]
    return ' '.join(text.split())[:QUOTE_CHARS]


def _title(conn, document_id):
    # The open connection, not documents.get: a second connection would wait on this one.
    row = conn.execute('SELECT title FROM documents WHERE id = ?', (document_id,)).fetchone()
    return row['title'] if row else None


def _report(conn, trace_id):
    row = conn.execute('''SELECT e.*, m.page, m.revision, m.payload, p.revision AS current_revision, p.text
        FROM report_exchanges e JOIN report_maps m ON m.id = e.map_id
        LEFT JOIN document_pages p ON p.document_id = e.document_id AND p.page_number = m.page
        WHERE e.trace_id = ?''', (trace_id,)).fetchone()
    if row is None:
        return None
    title = _title(conn, row['document_id']) or 'Deleted record'
    # A whole-page question has no line; it is about the page itself.
    selection = next((region for region in json.loads(row['payload'])['regions'] if region['id'] == row['region_id']),
                     {'text': title})
    terms = {word.casefold() for word in _WORD.findall(selection['text'])}
    sources = [{'role': 'selected', 'title': title, 'page': row['page'], 'quote': selection['text'][:QUOTE_CHARS],
                'target': f"/document/{row['document_id']}?page={row['page']}&map={row['map_id']}&region={row['region_id']}"}]
    checks = [{'label': f'{title}, page revision',
               'status': 'unchanged' if row['current_revision'] == row['revision'] else 'changed'}]
    for rank, item in enumerate(json.loads(row['evidence']), 1):
        if item.get('public_id'):
            from stellis import clinical_references
            ref=clinical_references.get(item['public_id'])
            current=bool(ref and clinical_references.digest(ref)==item['digest'])
            sources.append({'role':'public reference','rank':rank,'title':item['title'],'quote':item['text'],'target':item['url']})
            checks.append({'label':item['title']+', reference hash','status':'unchanged' if current else 'changed'})
            continue
        related = _title(conn, item['document_id'])
        saved = conn.execute('SELECT text FROM document_pages WHERE document_id = ? AND page_number = ?',
                             (item['document_id'], item['page'])).fetchone()
        current = saved is not None and hashlib.sha256(saved['text'].encode()).hexdigest() == item['digest']
        sources.append({'role': 'related', 'rank': rank, 'title': related or 'Deleted record', 'page': item['page'],
                        'quote': _quote(item['text'], terms),
                        'target': f"/document/{item['document_id']}?page={item['page']}" if related else None})
        checks.append({'label': f"{related or 'Deleted record'}, text hash", 'status': 'unchanged' if current else 'changed'})
    cited = len(json.loads(row['citations']))
    checks.append({'label': 'Each citation points to a supplied source', 'status': f'{cited} of {cited}'})
    whole_page = selection['text'] == title
    return {'kind': 'report', 'label': 'Report explanation' if whole_page else 'Report line explanation',
            'title': title if whole_page else f"{title}, {selection['text'][:60]}",
            'question': row['question'], 'created_at': row['created_at'], 'sources': sources, 'checks': checks,
            'citations': {'found': cited, 'total': cited}}


def _notebook(conn, trace_id):
    row = conn.execute('''SELECT m.*, n.name AS notebook_name FROM notebook_messages m
        JOIN notebooks n ON n.id = m.notebook_id WHERE m.trace_id = ? AND m.role = 'assistant' ''',
                       (trace_id,)).fetchone()
    if row is None:
        return None
    question = conn.execute('''SELECT content FROM notebook_messages WHERE notebook_id = ? AND role = 'user'
        AND created_at <= ? ORDER BY created_at DESC, rowid DESC LIMIT 1''',
                            (row['notebook_id'], row['created_at'])).fetchone()
    citations = json.loads(row['citations_json'])
    sources = [{'role': 'cited', 'rank': item['n'],
                'title': f"{item['title']} ({item['provider']})" if item.get('provider') else item['title'],
                'page': item.get('page'),
                'quote': ' '.join(str(item.get('excerpt') or '').split())[:QUOTE_CHARS], 'target': item['target']}
               for item in citations]
    return {'kind': 'notebook', 'label': 'Chat answer', 'title': f"{row['notebook_name']}, chat answer",
            'question': question['content'] if question else '', 'created_at': row['created_at'],
            'sources': sources,
            'checks': [{'label': 'Each citation points to a supplied source',
                        'status': f'{len(citations)} of {len(citations)}'}],
            'citations': {'found': len(citations), 'total': len(citations)},
            'research': json.loads(row['research_json']) if row['research_json'] else None}


def _agent(conn, trace):
    if not trace.get('job_id'):
        return None
    row = conn.execute('''SELECT m.created_at, a.name FROM agent_messages m JOIN agents a ON a.id = m.agent_id
        WHERE m.job_id = ? AND m.role = 'agent' ORDER BY m.created_at DESC LIMIT 1''', (trace['job_id'],)).fetchone()
    if row is None:
        return None
    return {'kind': 'agent', 'label': 'Chat answer', 'title': f"{row['name']}, chat answer", 'question': '',
            'created_at': row['created_at'], 'sources': [], 'checks': [], 'citations': None}


def for_trace(trace_id) -> dict:
    """The trace and its answer.

    Raises:
        LookupError: No trace has this ID.
    """
    trace = runtime_trace.get(trace_id)
    if trace is None:
        raise LookupError('That request was not found')
    from stellis import notebook_chat, report_context  # Deferred: they import the inference stack.
    with storage.connect() as conn:
        report_context._tables(conn)
        notebook_chat._create_table(conn)
        answer = _report(conn, trace_id) or _notebook(conn, trace_id) or _agent(conn, trace)
    return {'trace': trace, 'answer': answer}


def for_job(job_id) -> dict:
    """The trace and answer of an agent chat job."""
    trace_id = runtime_trace.for_job(job_id)
    if trace_id is None:
        raise LookupError('That request was not found')
    return for_trace(trace_id)
