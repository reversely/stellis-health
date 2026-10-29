"""Explicit account-local source context; destinations retain their own permissions."""
import datetime as dt,json,uuid
from stellis import documents,report_context,storage

def _table(conn):
 conn.execute('''CREATE TABLE IF NOT EXISTS source_handoffs (id TEXT PRIMARY KEY,document_id TEXT NOT NULL,
  map_id TEXT NOT NULL,region_id TEXT NOT NULL,purpose TEXT NOT NULL,created_at TEXT NOT NULL)''')

def create(document_id,map_id,region_id,purpose):
 if purpose not in {'chat','care'}:raise ValueError('Choose chat or care routines')
 _,_,selection=report_context.resolve_selection(document_id,map_id,region_id);identifier=str(uuid.uuid4())
 with storage.connect() as conn:
  _table(conn);conn.execute('INSERT INTO source_handoffs VALUES (?,?,?,?,?,?)',(identifier,document_id,map_id,selection['id'],purpose,dt.datetime.now(dt.timezone.utc).isoformat()))
 return resolve(identifier,purpose)

def resolve(identifier,purpose=None):
 with storage.connect() as conn:
  _table(conn);row=conn.execute('SELECT * FROM source_handoffs WHERE id=?',(identifier,)).fetchone()
 if not row:raise LookupError('Source context not found in this account')
 if purpose and row['purpose']!=purpose:raise ValueError('Choose an explicit handoff to this destination')
 record,mapped,selection=report_context.resolve_selection(row['document_id'],row['map_id'],row['region_id'])
 return {**dict(row),'title':record['title'],'notebook_id':record.get('notebook_id'),'clinical_date':record.get('record_date'),
   'page':mapped['page'],'revision':mapped['revision'],'quote':selection['text'],
   'target':f"/document/{record['id']}?page={mapped['page']}&map={row['map_id']}&region={selection['id']}"}

def sources(identifier):
 context=resolve(identifier,'chat')
 return [{'source_id':'document:'+context['document_id'],'title':context['title'],
  'excerpt':context['quote'],'target':context['target'],'start_ms':None,'page':context['page']}]

def check(identifier):
 try:resolve(identifier,'chat');return None
 except (LookupError,ValueError,documents.DocumentConflict):return 'The selected report source changed. Return to it and choose current context.'
