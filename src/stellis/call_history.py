"""Account-scoped call history and receipts from canonical confirmed log batches."""
import datetime as dt,json
from stellis import coach_call,storage,tracker_log

def list_all():
 with storage.connect() as conn:
  coach_call._create_tables(conn)
  records=[json.loads(r['state_json']) for r in conn.execute('SELECT state_json FROM coach_call_sessions ORDER BY updated_at DESC LIMIT 100')]
 return [{'id':s['session_id'],'kind':s['kind'],'started_at':s['started_at'],'ended_at':s['ended_at'],
  'review_day':s['context'].get('today'),'source':s['context'].get('source_context'),
  'messages':len(s['turns']),'saved_actions':len(s.get('saved_actions',[]))} for s in records]

def get(identifier):
 with storage.connect() as conn:
  coach_call._create_tables(conn);tracker_log._table(conn)
  row=conn.execute('SELECT state_json FROM coach_call_sessions WHERE id=?',(identifier,)).fetchone()
  if not row:raise LookupError('Call not found')
  state=json.loads(row['state_json'])
  for action in state.get('saved_actions',[]):
   batch=conn.execute('SELECT undone_at FROM log_batches WHERE id=?',(action['batch_id'],)).fetchone()
   action['undo_applied']=bool(batch and batch['undone_at'])
  return state

def receipt(identifier,batch_id):
 with storage.connect() as conn:
  coach_call._create_tables(conn);tracker_log._table(conn);conn.commit();conn.execute('BEGIN IMMEDIATE')
  row=conn.execute('SELECT state_json FROM coach_call_sessions WHERE id=?',(identifier,)).fetchone()
  batch=conn.execute('SELECT entries,undone_at,created_at FROM log_batches WHERE id=?',(batch_id,)).fetchone()
  if not row or not batch:raise LookupError('Call or confirmed log not found')
  if batch['undone_at']:raise ValueError('That log has already been undone')
  state=json.loads(row['state_json']);actions=state.setdefault('saved_actions',[])
  if not any(a['batch_id']==batch_id for a in actions):actions.append({'batch_id':batch_id,'entries':json.loads(batch['entries']),'saved_at':batch['created_at']})
  payload=json.dumps(state);conn.execute('UPDATE coach_call_sessions SET state_json=?,updated_at=? WHERE id=?',(payload,dt.datetime.now(dt.timezone.utc).isoformat(),identifier))
  latest=conn.execute('SELECT state_json FROM coach_call WHERE id=1').fetchone()
  if latest and json.loads(latest[0]).get('session_id')==identifier:conn.execute('UPDATE coach_call SET state_json=? WHERE id=1',(payload,))
 return {'saved':True}
