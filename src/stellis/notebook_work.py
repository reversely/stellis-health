"""Notebook instructions and activity derived from persisted work, never sample events."""
from __future__ import annotations
import json
from stellis import storage, suggestions, agent_artifacts, notebook_filing, source_comparisons
from stellis.todos import markdown_inline_text


def instructions(notebook_id):
    with storage.connect() as conn:
        if not conn.execute('SELECT 1 FROM notebooks WHERE id=?',(notebook_id,)).fetchone():
            raise LookupError('Notebook not found')
        row=conn.execute('SELECT instructions,revision FROM notebook_instructions WHERE notebook_id=?',(notebook_id,)).fetchone()
    return dict(row) if row else {'instructions':'','revision':0}


def save_instructions(notebook_id, text, revision):
    if not isinstance(text,str) or len(text)>4000:raise ValueError('Keep notebook instructions within 4,000 characters')
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM notebooks WHERE id=?',(notebook_id,)).fetchone():raise LookupError('Notebook not found')
        row=conn.execute('SELECT revision FROM notebook_instructions WHERE notebook_id=?',(notebook_id,)).fetchone()
        if (row['revision'] if row else 0)!=revision:raise ValueError('Notebook instructions changed. Reload before saving.')
        conn.execute('INSERT INTO notebook_instructions(notebook_id,instructions,revision) VALUES(?,?,?) ON CONFLICT(notebook_id) DO UPDATE SET instructions=excluded.instructions,revision=excluded.revision',(notebook_id,text.strip(),revision+1))
    return instructions(notebook_id)


def activity(notebook_id):
    policy=instructions(notebook_id)
    items=[]
    comparisons=source_comparisons.list_for(notebook_id)
    comparison_jobs={c['job_id'] for c in comparisons if c['job_id']}
    for comparison in comparisons:
        state='working' if comparison['state']=='running' else 'needs_you' if comparison['state'] in {'pending','retry'} else 'done'
        detail={'pending':'', 'running':'Comparing sources', 'done':'Comparison prepared', 'dismissed':'Comparison dismissed', 'retry':comparison['error'] or 'Comparison interrupted. Try again.'}[comparison['state']]
        items.append({'kind':'comparison','id':comparison['id'],'agent_id':comparison['agent_id'],'author':comparison['author'],
                      'state':state,'text':comparison['candidate']['question'],'detail':detail,'created_at':comparison['created_at'],
                      'target':'/chat/'+comparison['agent_id'],'comparison':comparison})
    for filing in notebook_filing.history(notebook_id):
        count = len(filing['items'])
        result = filing['result']
        meeting = all(item.get('kind') == 'conversation' for item in filing['items'])
        detail = 'Undo moves these notes to No notebook and keeps your edits.'
        if meeting:
            detail = 'Undo moves this meeting to No notebook. Audio and notes are kept.'
        if result is not None:
            detail = f"{len(result['unfiled'])} moved to No notebook."
            if result['unchanged']:
                detail += f" {len(result['unchanged'])} already moved or deleted; left unchanged."
        items.append({'kind':'filing', 'id':filing['id'], 'author':'Recording' if meeting else 'Workspace setup', 'state':'done',
                      'text':'Filed this meeting here.' if meeting else f"Filed {count} source note{'s' if count != 1 else ''} here.",
                      'detail':detail, 'created_at':filing['undone_at'] or filing['created_at'],
                      'target':'/notebook/'+notebook_id, 'filing':filing})

    with storage.connect() as conn:
        followup_sources = {row['suggestion_id']:json.loads(row['candidate_json']) for row in conn.execute(
            'SELECT f.suggestion_id,f.candidate_json FROM agent_followups f JOIN agent_artifacts d ON d.id=f.artifact_id WHERE d.notebook_id=? AND f.suggestion_id IS NOT NULL', (notebook_id,))}
    for suggestion in suggestions.list_pending(notebook_id=notebook_id):
        if suggestion['id'] in followup_sources:
            source = followup_sources[suggestion['id']]
            suggestion.update(source_target=source['source_target'],source_title=source['source_title'])
        title=suggestion['values'].get('text') or 'Change task status to '+suggestion['values'].get('status','')
        items.append({'kind':'suggestion','id':suggestion['id'],'state':'needs_you','author':suggestion['author'],'text':title,'created_at':suggestion['created_at'],'suggestion':suggestion,'target':('/conversation/' if suggestion['target_kind']=='conversation' else '/page/')+suggestion['target_id']})
    with storage.connect() as conn:
        drafts=conn.execute('SELECT d.*,a.name AS author,m.job_id FROM agent_artifacts d JOIN agents a ON a.id=d.agent_id LEFT JOIN agent_messages m ON m.id=d.message_id WHERE d.notebook_id=? ORDER BY d.created_at DESC',(notebook_id,)).fetchall()
        artifact_jobs={row['job_id'] for row in drafts if row['job_id']}
        for row in drafts:
            state='needs_you' if row['state']=='draft' else 'done'
            items.append({'kind':'artifact','artifact':agent_artifacts._record(row, conn),'id':row['id'],'agent_id':row['agent_id'],'author':row['author'],'state':state,'text':row['title'],'detail':{'draft':'Draft ready for review','filed':'Saved as a notebook note','discarded':'Draft discarded'}.get(row['state'],row['state']),'created_at':row['updated_at'],'target':'/chat/'+row['agent_id']+'#message-'+str(row['message_id'] or '')})
        jobs=conn.execute('SELECT j.*,COALESCE(h.target_name,a.name) AS author FROM agent_jobs j JOIN agents a ON a.id=j.agent_id LEFT JOIN agent_handoffs h ON h.id=j.id WHERE j.notebook_id=? ORDER BY j.created_at DESC LIMIT 100',(notebook_id,)).fetchall()
        for row in jobs:
            if row['id'] in artifact_jobs or row['id'] in comparison_jobs:continue
            message=conn.execute("SELECT id,content FROM agent_messages WHERE job_id=? AND role='agent' ORDER BY created_at DESC LIMIT 1",(row['id'],)).fetchone()
            question=conn.execute("SELECT content FROM agent_messages WHERE job_id=? AND role='user' LIMIT 1",(row['id'],)).fetchone()
            state='working' if row['state']=='running' else 'done'
            text=message['content'] if message else row['error'] or (question['content'] if question else 'Agent request')
            items.append({'kind':'job','id':row['id'],'agent_id':row['agent_id'],'author':row['author'],'state':state,'text':text,'detail':{'running':'Working','succeeded':'Reply ready','failed':'Request failed','interrupted':'Stopped or interrupted'}.get(row['state'],row['state']),'created_at':row['finished_at'] or row['created_at'],'target':'/chat/'+row['agent_id']+('#message-'+message['id'] if message else '')})
    items.sort(key=lambda item:item['created_at'],reverse=True)
    counts={state:sum(item['state']==state for item in items) for state in ['needs_you','working','done']}
    return {'items':items[:150],'counts':counts,**policy}


def overview():
    with storage.connect() as conn:books=[dict(row) for row in conn.execute('SELECT id,name,color,collection_id FROM notebooks ORDER BY created_at')]
    for book in books:
        work=activity(book['id']);latest=next((item for item in work['items'] if item['state']!='working'),None)
        book.update(counts=work['counts'],latest={"text":markdown_inline_text(latest['text']),"target":latest['target']} if latest else None)
    return books
