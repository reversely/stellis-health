"""Calculated care times require an approved literal interval and actual dose instant."""
import datetime as dt
import json,re,uuid
from zoneinfo import ZoneInfo
from stellis import storage,trackers

def _table(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS dose_time_rules (id TEXT PRIMARY KEY, tracker_id TEXT NOT NULL,
      anchor_day TEXT NOT NULL,title TEXT NOT NULL,hours REAL NOT NULL,source TEXT NOT NULL,
      UNIQUE(tracker_id,anchor_day,title))''')


def validate(data,proof,conn):
    identifier=data.get('tracker_id');row=conn.execute('SELECT kind,data FROM reproductive_records WHERE id=?',(identifier,)).fetchone()
    if not row or row['kind']!='medicine':raise ValueError('Choose the medicine named in the source')
    record=json.loads(row['data']);quote=proof['quote']
    if not any(w.lower() in quote.lower() for w in record['name'].split() if len(w)>3):raise ValueError('The interval quote must identify its medicine')
    hours=float(data.get('hours') or 0)
    if not 0<hours<=720:raise ValueError('Enter the explicit interval in hours')
    intervals=[float(m) for m in re.findall(r'(\d+(?:\.\d+)?)\s*hours?\s+after\s+(?:(?:the|last|final)\s+)*(?:trigger|dose|injection|administration|taking|medicine|tablet)\b',quote,re.I)]
    if hours not in intervals:raise ValueError('The source must explicitly state this many hours after the dose')
    day=trackers.date(data.get('anchor_day') or '').isoformat()
    title=str(data.get('title') or '').strip()
    if not title or len(title)>180 or not any(w.lower() in quote.lower() for w in title.split() if len(w)>3):raise ValueError('Use the event named in the source')
    return {'tracker_id':identifier,'anchor_day':day,'title':title,'hours':hours}


def add(data,proof,conn):
    _table(conn);clean=validate(data,proof,conn);identifier=str(uuid.uuid4())
    conn.execute('INSERT INTO dose_time_rules VALUES (?,?,?,?,?,?)',(identifier,clean['tracker_id'],clean['anchor_day'],clean['title'],clean['hours'],json.dumps(proof)))
    return {'id':identifier,**clean,'source':proof}


def events():
    """Recompute from current actual times. No booking rows are written."""
    with storage.connect() as conn:
        trackers._table(conn);_table(conn)
        rows=conn.execute('''SELECT r.*,d.taken_at,d.time_zone,t.notebook_id FROM dose_time_rules r
          JOIN pill_doses d ON d.pill_id=r.tracker_id AND d.day=r.anchor_day
          JOIN reproductive_records t ON t.id=r.tracker_id
          WHERE d.status='taken' AND d.taken_at IS NOT NULL''').fetchall()
    result=[]
    for row in rows:
        actual=dt.datetime.fromisoformat(row['taken_at']);zone=ZoneInfo(row['time_zone']) if row['time_zone'] else actual.tzinfo
        moment=(actual.astimezone(dt.timezone.utc)+dt.timedelta(hours=row['hours'])).astimezone(zone)
        proof=json.loads(row['source']);target='/document/'+proof['document_id']+'?page='+str(proof.get('page') or 1) if proof.get('document_id') else '/conversation/'+proof['source_id']
        result.append({'id':'dose-time:'+row['id'],'rule_id':row['id'],'title':row['title'],'date':moment.date().isoformat(),
         'starts_at':moment.isoformat(),'time':moment.strftime('%H:%M'),'time_zone':row['time_zone'],'status':'calculated',
         'actual_dose_time':row['taken_at'],'hours':row['hours'],'source':proof,'target':target,'notebook_id':row['notebook_id']})
    return result
