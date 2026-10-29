"""Approval is atomic, dated, quoted, reload-safe and reversible."""
import datetime as dt
from pathlib import Path
import pytest
from stellis import care_events,documents,notebooks,routine_review as review,storage,tracker_log,trackers
@pytest.fixture
def source(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Care')['id'];path=next(Path('src/stellis/static/maya/documents').rglob('D022*.pdf'));doc,_=documents.create(path.name,path.read_bytes(),book,'2024-06-07');return review.source('document',doc['id'])

def staged(source):
 course=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','schedule_type':'daily','start':'2024-06-02','notebook_id':source['notebook_id']})
 text=source['parts']['1'];start=text.index('Reduce follitropin');quote=text[start:text.index('Return',start)].strip()
 proposal={'action':'change','kind':'medicine','target_id':course['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'dose_amount':150}}
 return course,review.stage(source,[proposal])

def test_dated_change_preserves_history_and_undo(source):
 course,pending=staged(source)
 assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==225
 result=review.apply(pending['id'],[p['id'] for p in pending['proposals']]);assert result['saved']==1;assert result['selected']==[p['id'] for p in pending['proposals']]
 assert trackers.list_records(today=dt.date(2024,6,6))[0]['dose_amount']==225
 assert trackers.list_records(today=dt.date(2024,6,7))[0]['dose_amount']==150
 assert review.get('document',source['id'])['state']=='applied'
 review.undo(pending['id']);assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==225

def test_numbers_where_text_belongs_are_refused_as_unsupported(source):
 course=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','schedule_type':'daily','start':'2024-06-02','notebook_id':source['notebook_id']})
 text=source['parts']['1'];start=text.index('Reduce follitropin');quote=text[start:text.index('Return',start)].strip()
 with pytest.raises(ValueError,match='explicit source support'):review.stage(source,[{'action':'timing','kind':'medicine','target_id':None,'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'tracker_id':course['id'],'anchor_day':36,'title':'Retrieval','hours':36}}])
 with pytest.raises(ValueError,match='phase'):review.stage(source,[{'action':'add','kind':'treatment','target_id':None,'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'phase':6}}])

def test_all_or_none_and_stale_proof(source):
 course,pending=staged(source)
 with pytest.raises(ValueError):review.apply(pending['id'],[pending['proposals'][0]['id'],'unknown'])
 assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==225
 documents.save_page(source['id'],1,'Changed text',documents.get(source['id'])['pages'][0]['revision'])
 with pytest.raises(ValueError,match='source changed'):review.apply(pending['id'],[pending['proposals'][0]['id']])

def test_undo_will_not_erase_later_dose(source):
 course,pending=staged(source);review.apply(pending['id'],[pending['proposals'][0]['id']]);tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-06-07')
 with pytest.raises(ValueError,match='later changes or logs'):review.undo(pending['id'])

def test_conditional_course_and_date_only_event(source):
 course=trackers.save('medicine',{'name':'Conditional','dose_amount':1,'dose_unit':'mg','schedule_type':'daily','course_state':'planned','start':'2024-06-02'})
 assert not trackers.list_records(today=dt.date(2024,6,7))[0]['summary']['expected_today']
 with pytest.raises(ValueError):trackers.log(course['id'],'2024-06-07','taken')
 event=care_events.add('Return','2024-06-10','planned',source['notebook_id'],precision='day');assert event['starts_at']=='2024-06-10';assert event['precision']=='day'
 with pytest.raises(ValueError):care_events.add('Return','2024-06-10','scheduled',precision='day')

def test_quote_required_and_dismissal_survives(source):
 with pytest.raises(ValueError):review.stage(source,[{'action':'add','kind':'medicine','quote':'invented instruction','part':'1','data':{}}])
 _,pending=staged(source);review.dismiss(pending['id']);assert review.get('document',source['id'])['state']=='dismissed'


def test_completed_course_uses_unique_original_formulation_and_retains_two_sources(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Iron history')['id'];docs={}
 for code,day in [('D003','2022-02-02'),('D007','2022-08-02')]:
  path=next(Path('src/stellis/static/maya/documents').rglob(code+'*.pdf'));doc,_=documents.create(path.name,path.read_bytes(),book,day);docs[code]=doc
 course=trackers.save('medicine',{'name':'ferrous sulfate','dose_amount':300,'dose_unit':'mg','start':'2022-02-02','schedule_type':'every','every_days':2,'notebook_id':book,'source_document_id':docs['D003']['id'],'source_page':1})
 evidence=review.source('document',docs['D007']['id']);text=evidence['parts']['1'];quote=text[text.index('Completed alternate-day'):text.index('Continue dietary iron')].strip()
 proposal={'action':'change','kind':'medicine','target_id':course['id'],'effective_date':'2022-08-02','part':'1','quote':quote,'data':{'end':'2022-08-01'}}
 pending=review.stage(evidence,[proposal]);identity=pending['proposals'][0]['identity_evidence'];assert identity['alias']=='iron';assert identity['document_id']==docs['D003']['id'];assert 'elemental iron' in identity['quote'].lower()
 review.apply(pending['id'],[pending['proposals'][0]['id']]);assert trackers.list_records(today=dt.date(2024,6,7))[0]['end']=='2022-08-01'
 assert trackers.list_records(today=dt.date(2022,7,31))[0]['end']==''
 review.undo(pending['id'])
 # Two saved courses supported by the same ingredient reference are ambiguous.
 other=trackers.save('medicine',{'name':'ferrous sulfate second course','dose_amount':300,'dose_unit':'mg','start':'2022-02-02','schedule_type':'daily','notebook_id':book,'source_document_id':docs['D003']['id'],'source_page':1})
 # A second independently named prescription establishes the same ingredient.
 second,_=documents.create('second.pdf',next(Path('src/stellis/static/maya/documents').rglob('D003*.pdf')).read_bytes(),book,'2022-02-03')
 documents.save_page(second['id'],1,'Ferrous gluconate providing 35 mg elemental iron.',second['pages'][0]['revision'])
 trackers.save('medicine',{**other,'name':'ferrous gluconate','source_document_id':second['id']},other['id'])
 with pytest.raises(ValueError,match='uniquely match'):review.course_identity(course['id'],quote,{r['id']:r for r in trackers.list_records(notebook_id=book)})


def test_a_quoted_dose_identifies_the_only_course_with_that_dose(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Iron')['id']
 prior,_=documents.create('prescription.pdf',next(Path('src/stellis/static/maya/documents').rglob('D003*.pdf')).read_bytes(),book,'2022-02-02')
 course=trackers.save('medicine',{'name':'ferrous sulfate','dose_amount':300,'dose_unit':'mg','start':'2022-02-02','schedule_type':'every','every_days':2,'notebook_id':book,'source_document_id':prior['id'],'source_page':1})
 quote='Continue the same 300 mg every-other-day dose, taken in the evening with food for tolerability.'
 known=lambda:{r['id']:r for r in trackers.list_records(notebook_id=book)}
 assert review.course_identity(course['id'],quote,known()) is None
 with pytest.raises(ValueError,match='uniquely match'):review.course_identity(course['id'],'Continue the same dose.',known())
 # A second course with the same dose makes the instruction ambiguous.
 trackers.save('medicine',{'name':'calcium carbonate','dose_amount':300,'dose_unit':'mg','start':'2022-02-02','schedule_type':'daily','notebook_id':book})
 with pytest.raises(ValueError,match='uniquely match'):review.course_identity(course['id'],quote,known())


def test_formulation_identity_change_invalidates_pending_review(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Care')['id']
 prior,_=documents.create('prescription.pdf',next(Path('src/stellis/static/maya/documents').rglob('D003*.pdf')).read_bytes(),book,'2022-02-02')
 documents.save_page(prior['id'],1,'Start ferrous sulfate 300 mg providing 60 mg elemental iron.',prior['pages'][0]['revision'])
 follow,_=documents.create('review.pdf',next(Path('src/stellis/static/maya/documents').rglob('D007*.pdf')).read_bytes(),book,'2022-08-02')
 documents.save_page(follow['id'],1,'Stop therapeutic iron after the completed course.',follow['pages'][0]['revision'])
 course=trackers.save('medicine',{'name':'ferrous sulfate','dose_amount':300,'dose_unit':'mg','start':'2022-02-02','schedule_type':'daily','notebook_id':book,'source_document_id':prior['id'],'source_page':1})
 pending=review.stage(review.source('document',follow['id']),[{'action':'change','kind':'medicine','target_id':course['id'],'effective_date':'2022-08-02','part':'1','quote':'Stop therapeutic iron after the completed course.','data':{'end':'2022-08-01'}}])
 documents.save_page(prior['id'],1,'Corrected prescription',documents.get(prior['id'])['pages'][0]['revision'])
 with pytest.raises(ValueError,match='prescription source changed'):review.apply(pending['id'],[pending['proposals'][0]['id']])
 assert trackers.list_records()[0]['end']==''


def test_existing_treatment_uses_named_phase_proof_not_medicine_matching(source):
 phase=trackers.save('treatment',{'name':'IVF','phase':'Stimulation','start':'2024-06-02','notebook_id':source['notebook_id']})
 quote='Stimulation day 6 monitoring';assert quote in source['parts']['1']
 pending=review.stage(source,[{'action':'change','kind':'treatment','target_id':phase['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'phase':'Stimulation','source_day':6}}])
 assert pending['proposals'][0]['data']['start']=='2024-06-02';assert 'identity_evidence' not in pending['proposals'][0]


def test_literal_source_name_resolves_a_wrong_model_id_without_touching_oral_medicine(source):
 injection=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','start':'2024-06-02','schedule_type':'daily','notebook_id':source['notebook_id']})
 oral=trackers.save('medicine',{'name':'Folic acid','dose_amount':400,'dose_unit':'mcg','start':'2024-06-01','schedule_type':'daily','notebook_id':source['notebook_id']})
 quote='Reduce follitropin alfa from 225 to 150 IU nightly starting tonight.';assert quote in source['parts']['1']
 pending=review.stage(source,[{'action':'change','kind':'medicine','target_id':oral['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'dose_amount':150,'dose_unit':'IU'}}])
 assert pending['proposals'][0]['target_id']==injection['id']
 review.apply(pending['id'],[pending['proposals'][0]['id']]);now={r['id']:r for r in trackers.list_records(today=dt.date(2024,6,7))}
 assert now[injection['id']]['dose_amount']==150;assert now[oral['id']]['dose_amount']==400;assert now[oral['id']]['dose_unit']=='mcg'


def test_compound_instruction_keeps_an_explicitly_named_matching_target(source):
 first=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','start':'2024-06-02','schedule_type':'daily','notebook_id':source['notebook_id']})
 second=trackers.save('medicine',{'name':'Menotropins','dose_amount':75,'dose_unit':'IU','start':'2024-06-02','schedule_type':'daily','notebook_id':source['notebook_id']})
 text=source['parts']['1'];quote=text[text.index('Reduce follitropin'):text.index('Start cetrorelix')].strip()
 p=review.stage(source,[{'action':'change','kind':'medicine','target_id':first['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':{'dose_amount':150}},{'action':'keep','kind':'medicine','target_id':second['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':{}}])
 assert p['proposals'][0]['target_id']==first['id']


def test_final_dose_instruction_cannot_silently_drop_one_named_medicine(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Fertility')['id'];path=next(Path('src/stellis/static/maya/documents').rglob('D023*.pdf'));doc,_=documents.create(path.name,path.read_bytes(),book,'2024-06-10');evidence=review.source('document',doc['id'])
 courses=[trackers.save('medicine',{'name':name,'dose_amount':amount,'dose_unit':'IU','start':'2024-06-02','schedule_type':'daily','notebook_id':book}) for name,amount in [('Follitropin alfa',150),('Menotropins',75)]]
 quote='Give final follitropin alfa 150 IU and menotropins 75 IU tonight.';assert quote in evidence['parts']['1']
 def item(c):return {'action':'change','kind':'medicine','target_id':c['id'],'effective_date':'2024-06-10','part':'1','quote':quote,'data':{'end':'2024-06-10'}}
 with pytest.raises(ValueError,match='Follitropin'):review.stage(evidence,[item(courses[1])])
 pending=review.stage(evidence,[item(c) for c in courses]);assert len(pending['proposals'])==2


def test_continue_through_requires_the_source_end_instead_of_unchanged_blank(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));book=notebooks.create('Fertility')['id'];path=next(Path('src/stellis/static/maya/documents').rglob('D023*.pdf'));doc,_=documents.create(path.name,path.read_bytes(),book,'2024-06-10');evidence=review.source('document',doc['id'])
 course=trackers.save('medicine',{'name':'Cetrorelix','dose_amount':.25,'dose_unit':'mg','start':'2024-06-07','schedule_type':'daily','notebook_id':book})
 p={'action':'keep','kind':'medicine','target_id':course['id'],'effective_date':'2024-06-10','part':'1','quote':'Continue cetrorelix through June 11.','data':{}}
 with pytest.raises(ValueError,match='through-date'):review.stage(evidence,[p])
 p['action']='change';p['data']={'end':'2024-06-11'};assert review.stage(evidence,[p])['proposals'][0]['data']['end']=='2024-06-11'

def test_prepare_passes_system_instructions_and_short_registry_refs(source,monkeypatch):
 course,_=staged(source)
 with review.storage.connect() as conn:conn.execute('DELETE FROM routine_reviews')
 async def turn(prompt,session,**options):
  import json
  context=json.loads(prompt)
  assert context['existing'][0]['id']=='course_1_follitropin_alfa'
  assert course['id'] not in prompt
  assert 'EVERY explicitly named' in options['instructions']
  text=source['parts']['1'];start=text.index('Reduce follitropin');quote=text[start:text.index('Return',start)].strip()
  return json.dumps({'proposals':[{'action':'change','kind':'medicine','target_id':'course_1_follitropin_alfa','effective_date':'2024-06-07','part':'1','quote':quote,'data':{'dose_amount':150}}]}),{}
 monkeypatch.setattr(review.agent_gateway,'turn',turn)
 import asyncio
 pending=asyncio.run(review._prepare('document',source['id']))
 assert pending['proposals'][0]['target_id']==course['id']


def test_conditional_quote_cannot_activate_a_course(source):
 course=trackers.save('medicine',{'name':'Leuprolide acetate','dose_amount':4,'dose_unit':'mg','start':'2024-06-11','schedule_type':'daily','notebook_id':source['notebook_id']})
 document=documents.get(source['id']);documents.save_page(source['id'],1,'Leuprolide acetate tomorrow if no new concerns.',document['pages'][0]['revision']);e=review.source('document',source['id'])
 p={'action':'keep','kind':'medicine','target_id':course['id'],'part':'1','quote':e['parts']['1'],'effective_date':'2024-06-07','data':{}}
 with pytest.raises(ValueError,match='conditional'):review.stage(e,[p])
 p['action']='change';p['data']={'course_state':'planned'}
 assert review.stage(e,[p])['proposals'][0]['data']['course_state']=='planned'


def test_unzoned_planned_clock_is_retained_as_note_not_booked_instant(source):
 document=documents.get(source['id']);quote='Return June 13 at 09:30.';documents.save_page(source['id'],1,quote,document['pages'][0]['revision']);e=review.source('document',source['id'])
 p={'action':'event','kind':'treatment','part':'1','quote':quote,'effective_date':'2024-06-13','data':{'title':'Return','starts_at':'2024-06-13T09:30:00','status':'planned','precision':'instant'}}
 data=review.stage(e,[p])['proposals'][0]['data'];assert data['starts_at']=='2024-06-13';assert data['precision']=='day';assert data['status']=='planned';assert '09:30' in data['note']


def test_duplicate_overlapping_medicine_is_rejected_but_later_course_is_allowed(source):
 course=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','start':'2024-06-02','end':'2024-06-06','schedule_type':'daily','notebook_id':source['notebook_id']})
 quote='Reduce follitropin alfa from 225 to 150 IU nightly starting tonight.'
 p={'action':'add','kind':'medicine','part':'1','quote':quote,'effective_date':'2024-06-07','data':{'name':'Follitropin alfa','dose_amount':150,'dose_unit':'IU','start':'2024-06-06','schedule_type':'daily'}}
 with pytest.raises(ValueError,match='overlapping'):review.stage(source,[p],require_coverage=False)
 p['data']['start']='2024-06-07';assert review.stage(source,[p],require_coverage=False)['proposals'][0]['action']=='add'


def test_a_course_added_by_a_later_record_is_hidden_from_an_earlier_one(source):
 # Try again can read a record after later ones were applied; it sees the courses of its own date (#230).
 path=next(Path('src/stellis/static/maya/documents').rglob('D023*.pdf'));later,_=documents.create(path.name,path.read_bytes(),source['notebook_id'],'2024-06-20')
 trackers.save('medicine',{'name':'Estradiol valerate','dose_amount':2,'dose_unit':'mg','schedule_type':'daily','start':'2024-06-21','notebook_id':source['notebook_id'],'source_document_id':later['id']})
 planned=trackers.save('medicine',{'name':'Cetrorelix','dose_amount':0.25,'dose_unit':'mg','schedule_type':'daily','start':'2024-06-09','notebook_id':source['notebook_id']})
 assert [r['id'] for r in review.courses_on(source['notebook_id'],source['date'])]==[planned['id']]
 assert len(review.courses_on(source['notebook_id'],'2024-06-20'))==2


def test_two_changes_to_one_course_on_one_day_apply_as_one(source):
 # Found building the #247 demo snapshot: a review held a new dose and an end date for one course, same day.
 course=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','schedule_type':'daily','start':'2024-06-02','notebook_id':source['notebook_id']})
 text=source['parts']['1'];start=text.index('Reduce follitropin');quote=text[start:text.index('Return',start)].strip()
 change=lambda data:{'action':'change','kind':'medicine','target_id':course['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':data}
 pending=review.stage(source,[change({'dose_amount':150}),change({'end':'2024-06-10'})])
 result=review.apply(pending['id'],[p['id'] for p in pending['proposals']])
 assert result['saved']==2
 assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==150
 assert not trackers.list_records(today=dt.date(2024,6,12))[0]['summary']['expected_today']
 review.undo(pending['id']);assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==225


def test_a_change_approved_again_is_already_in_place_and_a_different_one_is_refused(source):
    # Found filming #251: the re-dated snapshot re-proposed a dose change it had already applied, and approving it crashed.
    course=trackers.save('medicine',{'name':'Follitropin alfa','dose_amount':225,'dose_unit':'IU','schedule_type':'daily','start':'2024-06-02','notebook_id':source['notebook_id']})
    text=source['parts']['1'];start=text.index('Reduce follitropin');quote=text[start:text.index('Return',start)].strip()
    change=lambda data:{'action':'change','kind':'medicine','target_id':course['id'],'effective_date':'2024-06-07','part':'1','quote':quote,'data':data}
    first=review.stage(source,[change({'dose_amount':150})])
    review.apply(first['id'],[p['id'] for p in first['proposals']])
    # The source is read again (its revision changed), so a new review proposes the same change.
    with storage.connect() as conn:conn.execute('DELETE FROM routine_reviews')
    again=review.stage(source,[{**change({'dose_amount':150}),'before':{'dose_amount':150}}])
    result=review.apply(again['id'],[p['id'] for p in again['proposals']])
    assert [item['action'] for item in result['items']]==['already']
    assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==150
    review.undo(again['id']);assert trackers.list_records(today=dt.date(2024,6,8))[0]['dose_amount']==150
    with storage.connect() as conn:conn.execute('DELETE FROM routine_reviews')
    other=review.stage(source,[{**change({'dose_amount':175}),'before':{'dose_amount':150}}])
    with pytest.raises(ValueError,match='different change'):
        review.apply(other['id'],[p['id'] for p in other['proposals']])

