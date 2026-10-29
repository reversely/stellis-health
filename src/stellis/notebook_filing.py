"""Durable local source filing and non-destructive reversal of notebook membership."""
import json
import uuid
from stellis import pages, storage


def file_sources(notebook_id, sources):
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        pages._check_notebook(conn, notebook_id)
        conn.execute('CREATE TABLE IF NOT EXISTS onboarding_imports (source_id TEXT PRIMARY KEY, page_id TEXT NOT NULL)')
        items = []
        for source in sources:
            if conn.execute('SELECT 1 FROM onboarding_imports WHERE source_id=?', (source['id'],)).fetchone():
                continue
            marker = 'Source reference: ' + source['id']
            existing = conn.execute('SELECT id FROM pages WHERE notebook_id=? AND trashed_at IS NULL AND instr(char(10)||markdown||char(10),char(10)||?||char(10))>0', (notebook_id, marker)).fetchone()
            if existing:
                page_id = existing['id']
            else:
                attribution = f"{source['kind']}, {source['date']}"
                link = f"\n\n[Open original]({source['url']})" if source['url'] else ''
                page = pages.create_in_transaction(conn, source['title'], notebook_id, f"{attribution}\n\n{source['excerpt']}{link}\n\n{marker}")
                page_id = page['id']
                items.append({'page_id':page_id, 'title':page['title'], 'source_id':source['id']})
            conn.execute('INSERT INTO onboarding_imports VALUES (?,?)', (source['id'], page_id))
        if not items:
            return None
        identifier = str(uuid.uuid4())
        conn.execute('INSERT INTO notebook_filings(id,notebook_id,items_json,created_at) VALUES (?,?,?,?)',
                     (identifier, notebook_id, json.dumps(items), pages._stamp()))
        return identifier


def record_conversation(conn, conversation):
    """Record a newly inserted meeting in the same transaction as its import."""
    item = {'kind':'conversation', 'conversation_id':conversation['id'], 'title':conversation['title']}
    conn.execute('INSERT INTO notebook_filings(id,notebook_id,items_json,created_at) VALUES (?,?,?,?)',
                 (str(uuid.uuid4()), conversation['notebook_id'], json.dumps([item]), pages._stamp()))


def _item_location(item):
    if item.get('kind') == 'conversation':
        return 'conversations', item['conversation_id'], '/conversation/' + item['conversation_id']
    return 'pages', item['page_id'], '/page/' + item['page_id']


def history(notebook_id):
    with storage.connect() as conn:
        rows = conn.execute('SELECT * FROM notebook_filings WHERE notebook_id=? ORDER BY created_at DESC', (notebook_id,)).fetchall()
        result = []
        for row in rows:
            items = json.loads(row['items_json'])
            for item in items:
                table, identifier, target = _item_location(item)
                item['target'] = target
                page = conn.execute(f'SELECT title,notebook_id,trashed_at FROM {table} WHERE id=?', (identifier,)).fetchone()
                item['available'] = bool(page and not page['trashed_at'])
                item['location'] = 'deleted' if not item['available'] else 'here' if page['notebook_id']==notebook_id else 'elsewhere'
                if page:
                    item['title'] = page['title']
            result.append({**dict(row), 'items':items, 'result':json.loads(row['result_json']) if row['result_json'] else None})
        return result


def undo(notebook_id, filing_id):
    """Detach only still-filed notes; preserve content, later moves and deletions."""
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM notebook_filings WHERE id=? AND notebook_id=?', (filing_id, notebook_id)).fetchone()
        if row is None:
            raise LookupError('Filing not found')
        if row['undone_at']:
            return json.loads(row['result_json'])
        result = {'unfiled':[], 'unchanged':[]}
        for item in json.loads(row['items_json']):
            table, identifier, _ = _item_location(item)
            changed = conn.execute(f'UPDATE {table} SET notebook_id=NULL WHERE id=? AND notebook_id=? AND trashed_at IS NULL',
                                   (identifier, notebook_id)).rowcount
            result['unfiled' if changed else 'unchanged'].append(identifier)
        conn.execute('UPDATE notebook_filings SET undone_at=?,result_json=? WHERE id=?', (pages._stamp(), json.dumps(result), filing_id))
        return result
