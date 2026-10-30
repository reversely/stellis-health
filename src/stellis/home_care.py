"""A dated home projection; no parallel clinical or logging state."""
from __future__ import annotations
import datetime as dt
from stellis import care_events, dose_timing, goals, health_preferences, notebooks, storage, tracker_log, trackers


def _table(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS home_context (id INTEGER PRIMARY KEY CHECK(id=1), tracker_id TEXT)')


def choose(identity):
    if identity and identity not in {r['id'] for r in trackers.list_records()}:
        raise ValueError('Choose an available care context')
    with storage.connect() as conn:
        _table(conn)
        conn.execute('INSERT OR REPLACE INTO home_context VALUES(1,?)',(identity or None,))
    return {'saved':True}


def _source(record):
    return {'title':record.get('source_title'),'target':'/document/'+record['source_document_id']+(('?page='+str(record['source_page'])) if record.get('source_page') else '')} if record.get('source_document_id') else None


PERIOD_CONTEXT_DAYS=90


def _context(record, day, books):
    info=record['summary'];start=record.get('start');end=record.get('end');kind=record['kind']
    pregnancy=info.get('pregnancy')
    if pregnancy:
        start=pregnancy['start'];end=pregnancy['due'];number=pregnancy['weeks'];caption='Week';detail=pregnancy['trimester'];total=40
    else:
        if not start:return None
        number=(day-trackers.date(start)).days+1;caption='Day';detail=record.get('phase') or ('Treatment' if kind=='treatment' else info.get('schedule_text',''));total=(trackers.date(end)-trackers.date(start)).days+1 if end else None
    since=None
    if kind=='period':
        # Never "Day N of cycle" (#232): during a period, "Period day N"; otherwise the next
        # period predicted from past cycles, or when the last one started.
        since=number-1;label=lambda d:trackers.date(d).strftime('%b %-d')
        if since<0:return None
        if (end and day.isoformat()<=end) or (not end and since<7):
            caption='Period day';detail='Started '+label(start)
        else:
            from stellis import reproductive
            ahead=reproductive.predicted_periods(day.isoformat(),1)
            if ahead:
                number=(trackers.date(ahead[0]['start'])-day).days
                caption='Next period in';detail=('day' if number==1 else 'days')+', around '+label(ahead[0]['start'])
            else:
                number=label(start);caption='Last period';detail=''
        end=None;total=None
    elif number<0 or (end and end<day.isoformat()):return None
    # A treatment phase nobody closed (an embryo transfer in 2029, seen in 2032 as "Day 869") stops
    # counting after the same three months as a period.
    elif kind=='treatment' and not end and number>PERIOD_CONTEXT_DAYS:return None
    title=books.get(record.get('notebook_id'),record.get('name') or trackers.KINDS[kind].label)
    return {'id':record['id'],'kind':kind,'title':title,'name':record.get('name',title),'number':number,'caption':caption,'detail':detail,'total':total,'start':start,'end':end,'notebook_id':record.get('notebook_id'),'source':_source(record),'since':since,'progress':min(1,max(0,number/total)) if total else None,'derived':info.get('derived',[])}


def view(day=None, utc_offset=0):
    today=trackers.date(day or dt.date.today().isoformat());preferences=health_preferences.preferences()
    records=trackers.list_records(today=today);books={b['id']:b['name'] for b in notebooks.list_notebooks()}
    contexts=[c for r in records if (c:=_context(r,today,books)) is not None and r.get('start',c['start'])<=today.isoformat()]
    # A period is the cycle-day counter: only the latest one counts, and not after three months
    # without a period, when a day count no longer describes a cycle.
    periods=sorted((c for c in contexts if c['kind']=='period'),key=lambda c:c['start'])
    contexts=[c for c in contexts if c['kind']!='period' or (c is periods[-1] and c['since']<=PERIOD_CONTEXT_DAYS)]
    context_starts={c['id']:max(trackers.date(other['start']).toordinal() for other in contexts if (other['notebook_id'] or other['id'])==(c['notebook_id'] or c['id'])) for c in contexts}
    contexts.sort(key=lambda c:(0 if c['kind']=='pregnancy' else 1,-context_starts[c['id']],{'treatment':0,'medicine':1,'pill':2,'period':3}.get(c['kind'],4)))
    with storage.connect() as conn:
        _table(conn);saved=conn.execute('SELECT tracker_id FROM home_context WHERE id=1').fetchone()
    active=next((c for c in contexts if saved and c['id']==saved['tracker_id']),contexts[0] if contexts else None)
    with storage.connect() as conn:
        goals._create_tables(conn)
        goal_starts={r['id']:(dt.datetime.fromisoformat(r['created_at'])+dt.timedelta(minutes=utc_offset)).date().isoformat() for r in conn.execute('SELECT id,created_at FROM coach_goals')}
    current=tracker_log._tracker_items(today,preferences)+[g for g in tracker_log._goal_items(today) if goal_starts.get(g['id'].partition(':')[2],today.isoformat())<=today.isoformat()]
    current=[i for i in current if i['state']['expected'] or i['state']['status'] or (i['control']['type']=='done' and i['state']['loggable'])]
    record_map={r['id']:r for r in records}
    for item in current:
        record=record_map.get(item['id'].partition(':')[2]);item['source']=_source(record) if record else None
    days=[today-dt.timedelta(days=6-i) for i in range(7)]
    goal_history={d.isoformat():{g['id']:g for g in goals.overview(d.isoformat(),utc_offset,'23:59')['goals']} for d in days}
    history=[]
    for item in current:
        cells=[]
        for date in days:
            if item['id'].startswith('tracker:'):
                record=record_map[item['id'].partition(':')[2]];kind=trackers.KINDS[record['kind']]
                outcome=record['outcomes'].get(date.isoformat());expected=kind.expected(trackers.at_date(record,record['id'],date),date)
                state=outcome['status'] if outcome else 'unlogged' if expected else 'not_due'
                if record.get('start') and date.isoformat()<record['start']:state='unavailable'
            else:
                goal_id=item['id'].partition(':')[2]
                goal=goal_history[date.isoformat()].get(goal_id)
                state='done' if goal and goal['done_today'] else 'unavailable' if date.isoformat()<goal_starts.get(goal_id,date.isoformat()) else 'available'
            cells.append({'date':date.isoformat(),'state':state})
        history.append({'id':item['id'],'title':item['title'],'cells':cells})
    upcoming=[]
    for event in care_events.list_all():
        day_text=event['starts_at'][:10]
        if today.isoformat()<=day_text<=(today+dt.timedelta(days=14)).isoformat():
            upcoming.append({'id':event['id'],'title':event['title'],'date':day_text,'time':event['starts_at'][11:16],'status':event['status'],'target':event.get('target')})
    upcoming.extend(e for e in dose_timing.events() if today.isoformat()<=e['date']<=(today+dt.timedelta(days=14)).isoformat())
    if active:
        upcoming.extend({'id':'derived:'+active['id']+':'+d['key'],'title':d['title'],'date':d['date'],'status':d['status'],'target':active['source']['target'] if active['source'] else None} for d in active['derived'] if d['date']>=today.isoformat())
    return {'date':today.isoformat(),'is_today':today==dt.date.today(),'contexts':contexts,'active':active,'items':current,'done':sum(bool(i['state']['status']) for i in current),'history':history,'days':[d.isoformat() for d in days],'upcoming':sorted(upcoming,key=lambda e:e['date']),'notebooks':[{'id':k,'name':v} for k,v in books.items()]}
