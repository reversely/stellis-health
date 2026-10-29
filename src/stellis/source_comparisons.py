"""Reviewable comparisons bound to two current notebook source versions."""
import asyncio
import hashlib
import json
import re
import uuid
from stellis import agents, source_search, storage


def _text(value):
    return ' '.join(re.sub(r'[\[\]]', '', value).split())


def _digest(document):
    return hashlib.sha256((document[3]+'\n'+document[4]).encode()).hexdigest()


def validate(value, sources):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value)!={'question','sources'}:
        raise ValueError('Comparison needs a question and two source quotes')
    question=value['question'];selected=value['sources']
    if not isinstance(question,str) or not 1<=len(question.strip())<=600:
        raise ValueError('Keep the comparison question within 600 characters')
    if not isinstance(selected,list) or len(selected)!=2:
        raise ValueError('Choose two distinct saved sources to compare')
    documents={d[5]:d for d in source_search._documents()}
    result=[];identities=set()
    for item in selected:
        if not isinstance(item,dict) or set(item)!={'source_index','quote'}:
            raise ValueError('Each comparison source needs its index and quote')
        index=item['source_index'];quote=item['quote']
        if type(index) is not int or not 1<=index<=len(sources):raise ValueError('Comparison source is unavailable')
        source=sources[index-1];document=documents.get(source['target'])
        if document is None:raise ValueError('Compare only saved notebook sources')
        identity=document[5].split('?')[0]
        if identity in identities:raise ValueError('Choose two distinct saved sources')
        if not isinstance(quote,str) or not 12<=len(quote.strip())<=500 or _text(quote) not in _text(source['excerpt']) or _text(quote) not in _text(document[4]):
            raise ValueError('Comparison quotes must match their current sources')
        identities.add(identity)
        result.append({'source_id':document[1], 'target':document[5], 'title':document[3],
                       'notebook_id':document[2], 'quote':quote.strip(), 'digest':_digest(document)})
    return {'question':question.strip(),'sources':result}


def create(conn, artifact_id, notebook_id, candidate):
    if any(source['notebook_id']!=notebook_id for source in candidate['sources']):
        raise ValueError('Comparison sources must belong to this notebook')
    signature=hashlib.sha256(json.dumps([notebook_id,sorted((s['target'],s['digest']) for s in candidate['sources'])]).encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO agent_comparisons(id,artifact_id,signature,candidate_json) VALUES(?,?,?,?)',
                 (str(uuid.uuid4()),artifact_id,signature,json.dumps(candidate)))


def list_for(notebook_id):
    with storage.connect() as conn:
        rows=conn.execute('''SELECT c.*,a.agent_id,a.notebook_id,a.created_at,g.name AS author,j.state AS job_state,j.error
            FROM agent_comparisons c JOIN agent_artifacts a ON a.id=c.artifact_id
            JOIN agent_messages m ON m.id=a.message_id JOIN agent_jobs parent ON parent.id=m.job_id
            JOIN agents g ON g.id=a.agent_id LEFT JOIN agent_jobs j ON j.id=c.job_id
            WHERE a.notebook_id=? AND parent.state='succeeded' AND a.state!='discarded'
            ORDER BY a.created_at DESC''',(notebook_id,)).fetchall()
    result=[]
    for row in rows:
        item=dict(row);item['candidate']=json.loads(item.pop('candidate_json'))
        if item['state']=='running' and item['job_state']!='running':
            item['state']='done' if item['job_state']=='succeeded' else 'retry'
        result.append(item)
    return result


def get(notebook_id, identifier):
    item=next((c for c in list_for(notebook_id) if c['id']==identifier),None)
    if item is None:raise LookupError('Comparison not found')
    return item


def dismiss(notebook_id, identifier):
    get(notebook_id,identifier)
    with storage.connect() as conn:
        if not conn.execute("UPDATE agent_comparisons SET state='dismissed' WHERE id=? AND (state='pending' OR (state='running' AND job_id IN (SELECT id FROM agent_jobs WHERE state IN ('failed','interrupted'))))",(identifier,)).rowcount:
            raise ValueError('Only a pending or interrupted comparison can be dismissed')
    return get(notebook_id,identifier)


def current_sources(item):
    documents={d[5]:d for d in source_search._documents()}
    result=[]
    for source in item['candidate']['sources']:
        document=documents.get(source['target'])
        if document is None or document[2]!=item['notebook_id'] or _digest(document)!=source['digest']:
            raise ValueError('A source changed or left this notebook. Review sources again before comparing.')
        if len(document[4])>16000:
            raise ValueError('This source is too long for one comparison. Create shorter source notes and review them again.')
        result.append({'source_id':document[1], 'title':document[3], 'target':document[5], 'excerpt':document[4]})
    return result


async def compare(notebook_id, identifier):
    from stellis import agent_runtime
    item=get(notebook_id,identifier)
    agents.require_enabled(item['agent_id'],notebook_id)
    if item['state'] in {'running','done'}:return item
    if item['state'] not in {'pending','retry'}:raise ValueError('This comparison was dismissed')
    sources=current_sources(item)
    job_id=str(uuid.uuid4())
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT c.state,j.state AS job_state FROM agent_comparisons c LEFT JOIN agent_jobs j ON j.id=c.job_id WHERE c.id=?',(identifier,)).fetchone()
        if row['state']=='running' and row['job_state'] in {'running','succeeded'}:return item
        if row['state']=='dismissed':raise ValueError('This comparison was dismissed')
        conn.execute("INSERT INTO agent_jobs(id,agent_id,notebook_id,state,created_at) VALUES(?,?,?,'running',?)",(job_id,item['agent_id'],notebook_id,agents._now()))
        conn.execute("UPDATE agent_comparisons SET state='running',job_id=? WHERE id=?",(job_id,identifier))
    try:
        question=('Prepare a detailed comparison of these two sources for review. '+item['candidate']['question']+
                  ' Compare corresponding points, distinguish missing information from disagreement, and cite both sources. Preserve the source labels and units exactly. If a number is not explicitly identified as a cost, quantity or volume, call its meaning unspecified rather than guessing. A proposal is not an order, approval or completed purchase. Do not send anything or change tasks.')
        def check(answer):
            current_sources(item)
            if not all('[[source:'+str(index)+']]' in answer for index in (1,2)):
                return 'The comparison must cite both selected sources with their original indices.'
            return None
        await agent_runtime.ask(item['agent_id'],question,notebook_id,extra_sources=sources,automatic=False,started_job_id=job_id,only_supplied_sources=True,answer_check=check)
        agents.require_enabled(item['agent_id'],notebook_id)
        current_sources(item)
        agents.finish_job(job_id,'succeeded')
    except asyncio.CancelledError:
        _discard_failed_result(job_id)
        agents.finish_job(job_id,'interrupted','Comparison stopped. You can try again.')
        raise
    except Exception as exc:
        _discard_failed_result(job_id)
        agents.finish_job(job_id,'failed',str(exc) if isinstance(exc,(ValueError,agent_runtime.AgentRunError)) else 'Comparison failed. Try again.')
        raise
    return get(notebook_id,identifier)


def _discard_failed_result(job_id):
    with storage.connect() as conn:
        conn.execute("UPDATE agent_artifacts SET state='discarded' WHERE state='draft' AND message_id IN (SELECT id FROM agent_messages WHERE job_id=?)",(job_id,))


async def review(notebook_id):
    from stellis import agent_runtime
    agent=agents.resolved_agent(notebook_id)['agent']
    documents=[d for d in source_search._documents() if d[2]==notebook_id]
    with storage.connect() as conn:
        stamps={'/page/'+r['id']:r['updated_at'] for r in conn.execute('SELECT id,updated_at FROM pages WHERE notebook_id=?',(notebook_id,))}
        stamps.update({'/conversation/'+r['id']:r['created_at'] for r in conn.execute('SELECT id,created_at FROM conversations WHERE notebook_id=?',(notebook_id,))})
    documents.sort(key=lambda d:stamps.get(d[5].split('?')[0],''),reverse=True)
    # Prefer separate documents to repeated passages from one meeting.
    seen=set();sources=[]
    for document in documents:
        identity=document[5].split('?')[0]
        if identity in seen:continue
        seen.add(identity)
        sources.append({'source_id':document[1],'title':document[3],'target':document[5],'excerpt':document[4][:2500]})
        if len(sources)==12:break
    if len(sources)<2:raise ValueError('Add at least two saved sources to review for a comparison.')
    return await agent_runtime.ask(agent['id'],
        'Prepare a short source review of this notebook. Propose a comparison only when two saved sources describe related alternatives or versions worth comparing. Otherwise explain that no useful pair was found. Do not invent differences or actions.',
        notebook_id,extra_sources=sources,only_supplied_sources=True)
