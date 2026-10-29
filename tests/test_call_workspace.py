"""Multiple source regions, explicit boundaries, independent sessions and real receipts."""
import asyncio,datetime as dt
from pathlib import Path
import pytest
from functools import wraps
def run_async(fn):
 @wraps(fn)
 def run(*args,**kwargs):return asyncio.run(fn(*args,**kwargs))
 return run
from stellis import call_history,coach_call,documents,goals,notebooks,report_context,source_handoffs,storage,tracker_log,trackers
@pytest.fixture(autouse=True)
def workspace(tmp_path,monkeypatch):monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))

def original():
 p=next(Path('src/stellis/static/maya/documents').rglob('D027*.pdf'));doc,_=documents.create(p.name,p.read_bytes(),record_date='2024-06-19');mapped=report_context.page_map(doc['id'],1);chosen=[r for r in mapped['regions'] if r['text'].startswith(('SYN-E01 ','SYN-E04 '))];assert len(chosen)==2;return doc,mapped,chosen

def test_multiple_regions_are_canonical_original_and_purpose_bound(tmp_path):
 doc,mapped,chosen=original();ids=','.join(r['id'] for r in reversed(chosen));record,_,selected=report_context.resolve_selection(doc['id'],mapped['id'],ids)
 assert selected['id']==','.join(r['id'] for r in chosen);assert selected['text']=='\n'.join(r['text'] for r in chosen)
 handoff=source_handoffs.create(doc['id'],mapped['id'],ids,'chat');assert source_handoffs.sources(handoff['id'])[0]['excerpt']==selected['text']
 with pytest.raises(ValueError):source_handoffs.resolve(handoff['id'],'care')
 with storage.demo_workspace(tmp_path/'separate-account'):
  with pytest.raises(LookupError):source_handoffs.resolve(handoff['id'])
 documents.save_page(doc['id'],1,'Corrected source',doc['pages'][0]['revision'])
 with pytest.raises(documents.DocumentConflict):source_handoffs.resolve(handoff['id'])

def test_invalid_source_selection_never_becomes_context():
 doc,mapped,chosen=original()
 for ids in [chosen[0]['id']+','+chosen[0]['id'],'not-present',','.join(str(n) for n in range(21))]:
  with pytest.raises(ValueError):source_handoffs.create(doc['id'],mapped['id'],ids,'chat')

@run_async
async def test_old_socket_ending_does_not_end_or_overwrite_new_call():
 ready=asyncio.Event();new=asyncio.Event();ids={}
 async def first():
  state=await coach_call.begin('call','2024-06-07','19:00',-240);ids['old']=state['session_id'];coach_call.opening();coach_call.add_person_turn('Review my earlier routine');ready.set();await new.wait();coach_call.end()
 async def second():
  await ready.wait();state=await coach_call.begin('call','2024-06-08','09:00',-240);ids['new']=state['session_id'];coach_call.opening();coach_call.add_person_turn('A separate new call');new.set()
 await asyncio.gather(first(),second())
 assert call_history.get(ids['old'])['ended_at'];assert call_history.get(ids['new'])['ended_at'] is None
 assert coach_call.load()['session_id']==ids['new'];assert len(call_history.list_all())==2
 assert call_history.get(ids['new'])['turns'][-1]['text']=='A separate new call'

@run_async
async def test_four_named_items_yield_one_pending_batch_and_receipt_after_approval(monkeypatch):
 day='2024-06-07';items=[trackers.save('medicine',{'name':name,'dose_amount':amount,'dose_unit':'mg','schedule_type':'daily','start':day}) for name,amount in [('Injection A',1),('Injection B',2),('Injection C',3),('Oral D',4)]]
 state=await coach_call.begin('call',day,'21:30',0);coach_call.opening();words='I took Injection A, Injection B, Injection C and Oral D at 21:30';coach_call.add_person_turn(words)
 rounds=iter([items[:1],items[1:],[]])
 async def stream(messages,tools,timings):
  chosen=next(rounds)
  for item in chosen:yield {'id':item['id'],'name':'log_entry','arguments':{'item':'tracker:'+item['id'],'when':'today','value':'taken','time':'21:30','evidence':words}}
  if not chosen:yield 'Check all four entries before saving.'
 monkeypatch.setattr(coach_call,'_stream',stream)
 pieces=[p async for p in coach_call.reply()];pending=[p for p in pieces if isinstance(p,dict) and p.get('action')=='propose_log'];assert len(pending)==1 and len(pending[0]['proposals'])==4
 assert all(not r['outcomes'] for r in trackers.list_records());assert call_history.get(state['session_id'])['saved_actions']==[]
 saved=tracker_log.save(pending[0]['proposals'],day,time_zone='UTC');call_history.receipt(state['session_id'],saved['batch_id']);assert len(call_history.get(state['session_id'])['saved_actions'])==1
 call_history.receipt(state['session_id'],saved['batch_id']);assert len(call_history.get(state['session_id'])['saved_actions'])==1
 tracker_log.undo(saved['batch_id']);assert call_history.get(state['session_id'])['saved_actions'][0]['undo_applied']

@run_async
async def test_recent_ended_course_can_be_proposed_for_yesterday():
 trackers.save('medicine',{'name':'Recently ended','dose_amount':1,'dose_unit':'mg','schedule_type':'daily','start':'2024-06-01','end':'2024-06-10'})
 assert any(p['title']=='Recently ended' for p in coach_call.loggable('2024-06-11'))

def test_medication_goal_cannot_bypass_care_review():
 result,action=coach_call.run_tool({'name':'fill_goal','arguments':{'activity':'medicine','medicine':'Unverified change','dose_amount':200,'dose_unit':'mg','times_per_week':7}}, {})
 assert action is None;assert 'source-backed care review' in result


def test_reported_actual_amount_keeps_unit_and_is_not_a_routine_change():
 course=trackers.save('medicine',{'name':'Example','dose_amount':150,'dose_unit':'IU','schedule_type':'daily','start':'2024-06-07'})
 context={'today':'2024-06-07','loggable':[{'item':'tracker:'+course['id'],'title':'Example','type':'status','dose_unit':'IU'}],'person_words':'I took 200 IU of Example.'}
 tool={'name':'log_entry','arguments':{'item':'tracker:'+course['id'],'when':'today','value':'taken','amount':200,'amount_unit':'IU','evidence':'took 200 IU'}}
 _,action=coach_call.run_tool(tool,context);assert action['proposals'][0]['amount']==200
 tracker_log.save(action['proposals'],'2024-06-07');record=trackers.list_records(today=dt.date(2024,6,7))[0]
 assert record['dose_amount']==150 and record['outcomes']['2024-06-07']['dose_amount']==200
 tool['arguments']['amount_unit']='tablets';assert coach_call.run_tool(tool,context)[1] is None


def test_split_consult_paragraph_keeps_original_result_proof():
 import json
 fields=[{'name':'Hemoglobin','value':124,'date':'2022-01-21','source_index':1}, {'name':'Ferritin','value':11,'date':'2022-01-21','source_index':1}]
 reply=json.dumps({'paragraphs':[{'text':'Hemoglobin is normal. The earlier consultation ordered tests.','sources':[2]}]})
 linked=json.loads(report_context.result_source_links(reply,fields))
 assert linked['paragraphs'][0]['sources']==[2,1]
 assert linked['paragraphs'][0]['text']=='Hemoglobin is normal. The earlier consultation ordered tests.'
 for invalid in ['[]','null','{"paragraphs":null}']:
  assert report_context.result_source_links(invalid,fields)==invalid
