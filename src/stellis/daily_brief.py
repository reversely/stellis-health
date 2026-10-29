"""Persisted, cancellable Chief of Staff synthesis of a bounded workspace snapshot."""
import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from stellis import agents, agent_runtime, calendar, notebook_work, storage, todos

_RUNNING = {}


def local_snapshot():
    tasks = []
    for group in todos.gather()['groups']:
        for item in group['items']:
            tasks.append({'text':item['text'][:500], 'due_date':item['due_date'], 'due_time':item['due_time'],
                          'status':'open accepted task', 'source':group['source']['title'], 'notebook_id':group['source']['notebook_id']})
    tasks.sort(key=lambda item: (item['due_date'] or '9999', item['due_time'] or '', item['text']))
    work = notebook_work.overview()
    work.sort(key=lambda item: (-item['counts']['needs_you'], -item['counts']['working'], item['name']))
    return tasks, work


async def snapshot():
    tasks, work = local_snapshot()
    now = datetime.now(timezone.utc)
    providers = [item['provider'] for item in calendar.connections() if item['connected']]
    results = await asyncio.gather(*(calendar.events(p, now=now, until=now+timedelta(days=1)) for p in providers), return_exceptions=True)
    events, warnings = [], []
    for provider, result in zip(providers, results):
        if isinstance(result, Exception):
            warnings.append(f'{provider} events could not be read.')
        else:
            events.extend({'title':e['title'], 'start':e['start'], 'end':e['end']} for e in result)
    events.sort(key=lambda event:event['start'])
    coverage = {'tasks':len(tasks), 'notebooks':len(work), 'events':len(events), 'limit_per_group':20,
                'calendar_connected':bool(providers), 'warnings':warnings, 'as_of':now.isoformat()}
    groups = [
        ('Open tasks', '/todos', {'total':len(tasks), 'items':tasks[:20]}),
        ('Notebook activity', '/#notebook-groups', {'total':len(work), 'items':[{'name':w['name'], 'counts':w['counts'], 'latest':{**w['latest'], 'text':w['latest']['text'][:600]} if w['latest'] else None} for w in work[:20]]}),
        ('Upcoming events', '/#calendar-card' if providers else '/settings', {'next_hours':24, 'connected':bool(providers), 'warnings':warnings, 'total':len(events) if providers and not warnings else None, 'items':events[:20]})]
    sources = [{'source_id':'daily:'+str(i), 'title':title, 'target':target,
                'excerpt':json.dumps(value, ensure_ascii=False)} for i,(title,target,value) in enumerate(groups)]
    return sources, coverage


def status():
    with storage.connect() as conn:
        row = conn.execute('SELECT * FROM daily_brief WHERE id=1').fetchone()
        if not row:
            return {'state':'empty', 'brief':None, 'coverage':None}
        job = conn.execute('SELECT * FROM agent_jobs WHERE id=?', (row['job_id'],)).fetchone()
        message = conn.execute('SELECT * FROM agent_messages WHERE id=?', (row['message_id'],)).fetchone() if row['message_id'] else None
        return {'state':job['state'], 'job_id':job['id'], 'agent_id':job['agent_id'], 'error':job['error'],
                'brief':dict(message) if message else None, 'coverage':json.loads(row['coverage_json']) if row['coverage_json'] else None}


def validate_answer(answer, coverage):
    if not coverage['calendar_connected'] or coverage['warnings']:
        pattern = r'\b(?:no|zero|0)\s+(?:(?:upcoming|scheduled|calendar)\s+)?(?:events|meetings|appointments)\b|\b(?:nothing|none)\s+(?:is\s+)?scheduled\b|\b(?:calendar|schedule)\s+is\s+(?:clear|empty|free)\b'
        if re.search(pattern, answer, re.I):
            return 'Calendar coverage is unavailable or incomplete. State that the schedule is unknown, not that there are no events or free time.'
    return None


async def _run(agent_id, job_id):
    try:
        sources, coverage = await snapshot()
        question = ('Give my daily brief as of '+coverage['as_of']+'. Prioritize open tasks by actual due dates, pending review and upcoming events in the next 24 hours. '
                    'Synthesize the supplied workspace snapshot across notebooks. Distinguish pending proposals from accepted tasks. '
                    'Use the supplied totals and at most 20 items per group; do not imply complete coverage when a total exceeds 20. '
                    'Do not invent deadlines, priorities, meetings, completion or actions you performed. When calendar data is unavailable or incomplete, the schedule is UNKNOWN; never claim there are no events or that time is free. Open tasks are already accepted, not pending suggestions. '
                    'No tool calls or external actions; this is a read-only overview. Cite each factual sentence.')
        result = await agent_runtime.ask(agent_id, question, extra_sources=sources, automatic=True, started_job_id=job_id, review_only=True, answer_check=lambda answer: validate_answer(answer, coverage))
        with storage.connect() as conn:
            conn.execute('UPDATE daily_brief SET message_id=?,coverage_json=? WHERE id=1 AND job_id=?',
                         (result['agent_message']['id'], json.dumps(coverage), job_id))
        agents.finish_job(job_id, 'succeeded')
    except asyncio.CancelledError:
        agents.finish_job(job_id, 'interrupted', 'Brief stopped. Refresh when you are ready.')
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, agent_runtime.AgentRunError)) else 'Could not prepare the daily brief. Try again.'
        agents.finish_job(job_id, 'failed', message)
    finally:
        _RUNNING.pop(job_id, None)


def start():
    chief = next(a for a in agents.list_agents() if a['is_default'])
    agents.require_enabled(chief['id'])
    with storage.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT j.state FROM daily_brief b JOIN agent_jobs j ON j.id=b.job_id WHERE b.id=1').fetchone()
        if existing and existing['state']=='running':
            raise ValueError('A daily brief is already being prepared.')
        job_id = str(uuid.uuid4())
        conn.execute("INSERT INTO agent_jobs(id,agent_id,state,created_at) VALUES (?,?,'running',?)", (job_id, chief['id'], agents._now()))
        conn.execute('INSERT INTO daily_brief(id,job_id) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id', (job_id,))
    _RUNNING[job_id] = asyncio.create_task(_run(chief['id'], job_id))
    return status()


def stop():
    current = status()
    task = _RUNNING.get(current.get('job_id'))
    if task:
        task.cancel()
    return {'stopping':bool(task)}


async def shutdown():
    tasks = list(_RUNNING.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
