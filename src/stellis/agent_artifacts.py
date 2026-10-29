"""Account-local document drafts that the user reviews, edits and files as notes."""
from __future__ import annotations
import json
import re
import uuid
from datetime import datetime, timezone
from stellis import agents, storage, todos


def _now():
    return datetime.now(timezone.utc).isoformat()


def kind_for(question):
    if not re.search(r"^(?:(?:can|could|would|will) you\s+)?(?:please\s+)?(?:draft|write|compose|prepare|create)\b", question, re.I):
        return None
    return 'document'


def validate(title, body):
    if not isinstance(title,str) or not title.strip() or len(title)>200 or any(c in title for c in '\r\n'):
        raise ValueError('Enter a title up to 200 characters')
    if not isinstance(body,str) or not body.strip() or len(body)>20000:
        raise ValueError('Enter draft text up to 20,000 characters')


def parse(content, question, sources):
    value=content.strip()
    if value.startswith('```'):
        value=re.sub(r'^```(?:json)?\s*|\s*```$', '',value)
    result=json.loads(value)
    if not isinstance(result,dict):raise ValueError('Draft response is not an object')
    validate(result.get('title'),result.get('body'))
    from stellis import agent_followups
    from stellis import source_comparisons
    comparison = source_comparisons.validate(result.get('comparison'), sources) if not question.startswith('Prepare a detailed comparison') else None
    return {'title': result['title'], 'body': result['body'], 'comparison': comparison,
            'follow_up': agent_followups.validate(result.get('follow_up'), sources)}


def _record(row, conn=None):
    if row is None:raise LookupError('Draft not found')
    item=dict(row)
    item['sources']=json.loads(item.pop('sources_json'))
    for column in ('recipients_json','gmail_draft_id','account_email'):item.pop(column,None)
    from stellis import agent_followups
    item['follow_up'] = agent_followups.for_artifact(item['id'], conn)
    return item


def get(identifier):
    with storage.connect() as conn:return _record(conn.execute('SELECT * FROM agent_artifacts WHERE id = ?', (identifier,)).fetchone(), conn)


def list_for(agent_id):
    agents.get(agent_id)
    with storage.connect() as conn:return [_record(row, conn) for row in conn.execute('SELECT * FROM agent_artifacts WHERE agent_id = ? ORDER BY created_at',(agent_id,))]


def create(agent_id, message_id, notebook_id, title, body, sources, follow_up=None, comparison=None):
    validate(title,body);identifier=str(uuid.uuid4());stamp=_now()
    with storage.connect() as conn:
        conn.execute('''INSERT INTO agent_artifacts
            (id,agent_id,message_id,notebook_id,kind,title,body,recipients_json,sources_json,created_at,updated_at)
            VALUES (?,?,?,?,'document',?,?,'[]',?,?,?)''',(identifier,agent_id,message_id,notebook_id,title.strip(),body.strip(),json.dumps(sources),stamp,stamp))
        if comparison:
            from stellis import source_comparisons
            source_comparisons.create(conn, identifier, notebook_id, comparison)
        if follow_up:
            conn.execute('INSERT INTO agent_followups(artifact_id,candidate_json) VALUES(?,?)', (identifier,json.dumps(follow_up)))
    return get(identifier)


def edit(identifier, revision, title, body):
    validate(title,body)
    with storage.connect() as conn:
        changed=conn.execute('''UPDATE agent_artifacts SET title=?,body=?,revision=revision+1,updated_at=?,error=NULL
            WHERE id=? AND revision=? AND state='draft' ''',(title.strip(),body.strip(),_now(),identifier,revision)).rowcount
        if not changed:raise ValueError('This draft changed or is no longer editable. Refresh it first.')
    return get(identifier)


def discard(identifier, revision):
    with storage.connect() as conn:
        if not conn.execute("UPDATE agent_artifacts SET state='discarded',revision=revision+1,updated_at=? WHERE id=? AND revision=? AND state='draft'",(_now(),identifier,revision)).rowcount:
            raise ValueError('Only the current unsaved draft can be discarded')
    return get(identifier)


def save_note(identifier, revision):
    """File the reviewed document and mark its artifact in the same transaction."""
    with storage.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = _record(conn.execute("SELECT * FROM agent_artifacts WHERE id=?", (identifier,)).fetchone(), conn)
        if draft['state'] == 'filed':
            return draft
        if draft['kind'] != 'document' or draft['state'] != 'draft' or draft['revision'] != revision:
            raise ValueError('Refresh this draft before saving')
        stamp = _now()
        markdown = todos.reindex(conn, 'page', identifier, draft['body'])
        conn.execute("INSERT INTO pages(id,title,markdown,notebook_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                     (identifier,draft['title'],markdown,draft['notebook_id'],stamp,stamp))
        conn.execute("UPDATE agent_artifacts SET state='filed',updated_at=? WHERE id=?", (stamp,identifier))
    return get(identifier)
