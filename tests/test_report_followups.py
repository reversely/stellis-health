"""Visit review saves canonical notes/tasks and preserves proof and dismissal."""
from pathlib import Path
import pytest
from stellis import care_events,documents,notebook_chat,notebooks,pages,report_followups as followups,storage,todos
@pytest.fixture
def source(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Iron concern')['id'];p=next(Path('src/stellis/static/maya/documents').rglob('D002*.pdf'));doc,_=documents.create(p.name,p.read_bytes(),book,'2022-01-21');quote='Ferritin';text=doc['pages'][0]['text'];start=text.index(quote);quote=text[start:start+60]
 passage=notebook_chat.Passage('document:'+doc['id'],doc['title'],text,'/document/'+doc['id'],page=1)
 with storage.connect() as conn:
  validated=followups.validate({'text':'Which causes of low iron should we investigate at my review?','source':1,'quote':quote},[passage],conn);followups.stage(conn,doc['id'],'exchange-fixture',validated)
 return {'doc':doc,'book':book,'question':followups.list_for(doc['id'])[0]}

def test_approve_selects_actual_visit_and_indexes_task_with_proof(source):
 event=care_events.add('Iron review','2022-02-02T14:00:00+00:00','scheduled',source['book'])
 question=source['question'];saved=followups.approve(question['id'],event['id'],time_zone='America/Toronto')
 assert saved['state']=='accepted' and saved['task_id'];note=pages.get(saved['page_id']);assert ' '.join(question['quote'].split()) in note['markdown'];assert '/document/'+source['doc']['id'] in note['markdown']
 group=next(g for g in todos.gather(source['book'])['groups'] if g['source']['id']==saved['page_id']);task=group['items'][0];assert task['due_date']=='2022-02-02' and task['due_time']=='09:00'
 assert followups.approve(question['id'],event['id'])['task_id']==saved['task_id'];assert len(todos.gather(source['book'])['groups'])==1
 todos.set_status(saved['task_id'],'done');assert any(g['items'][0]['status']=='done' for g in todos.gather(source['book'],'done')['groups'])

def test_missing_or_unbooked_visit_never_creates_a_note(source):
 with pytest.raises(ValueError,match='actual booked'):followups.approve(source['question']['id'],'fictional-visit')
 event=care_events.add('Planned','2022-02-02','planned',source['book'],precision='day')
 with pytest.raises(ValueError):followups.approve(source['question']['id'],event['id'])
 assert pages.list_pages()==[]

def test_source_change_blocks_save_and_dismissal_persists(source):
 event=care_events.add('Iron review','2022-02-02T14:00:00+00:00','scheduled',source['book']);doc=source['doc'];documents.save_page(doc['id'],1,'Corrected original text',doc['pages'][0]['revision'])
 with pytest.raises(ValueError,match='source changed'):followups.approve(source['question']['id'],event['id'])
 followups.dismiss(source['question']['id']);assert followups.list_for(doc['id'])[0]['state']=='dismissed';assert pages.list_pages()==[]

def test_exact_question_dedup_never_resurrects_a_dismissal(source):
 item=source['question'];followups.dismiss(item['id'])
 with storage.connect() as conn:followups.stage(conn,source['doc']['id'],'next-exchange',item)
 assert len(followups.list_for(source['doc']['id']))==1;assert followups.list_for(source['doc']['id'])[0]['state']=='dismissed'


def test_report_answer_stages_real_contract_before_any_task(source,monkeypatch):
 import asyncio,json
 from stellis import agent_gateway,report_context
 doc=source['doc'];mapped=report_context.page_map(doc['id'],1);region=next(r for r in mapped['regions'] if r['text'].startswith('Ferritin'))
 async def turn(prompt,session,**options):
  if session.startswith('visit-question-'):
   context=json.loads(prompt);proof=next(p for p in context['literal_proofs'] if p['source']==1 and 'ferritin' in p['quote'].casefold())
   return json.dumps({'follow_up':{'text':'Which additional causes of low iron should we investigate together?','proof_id':proof['id']}}),session
  return json.dumps({'paragraphs':[{'text':'The report records low iron stores.','sources':[1]}]}),session
 monkeypatch.setattr(agent_gateway,'turn',turn)
 exchange=asyncio.run(report_context.ask(doc['id'],mapped['id'],region['id'],'What should I discuss at my review?'))
 assert exchange['follow_up']['state']=='pending';assert pages.list_pages()==[]
 event=care_events.add('Iron review','2022-02-02T14:00:00+00:00','scheduled',source['book'])
 saved=followups.approve(exchange['follow_up']['id'],event['id']);assert todos.gather(source['book'])['groups'][0]['items'][0]['id']==saved['task_id']
