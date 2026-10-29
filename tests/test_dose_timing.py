"""Actual instants, explicit source intervals, DST arithmetic and conflict-safe Undo."""
import datetime as dt
import pytest
from stellis import dose_timing,storage,tracker_log,trackers,home_care,calendar_sources
@pytest.fixture
def course(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))
 return trackers.save('medicine',{'name':'Leuprolide acetate','dose_amount':4,'dose_unit':'mg','schedule_type':'daily','start':'2024-06-11','end':'2024-06-11'})

def rule(course,day='2024-06-11',hours=36):
 with storage.connect() as conn:
  return dose_timing.add({'tracker_id':course['id'],'anchor_day':day,'title':'Retrieval','hours':hours},{'document_id':'original','page':1,'quote':'Leuprolide acetate. Retrieval 36 hours after trigger.'},conn)

def test_no_actual_time_means_no_calculation_then_recompute_undo(course):
 rule(course);tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-06-11');assert dose_timing.events()==[]
 saved=tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-06-11','21:30','America/Toronto')
 event=dose_timing.events()[0];assert event['starts_at']=='2024-06-13T09:30:00-04:00';assert event['status']=='calculated'
 assert event['source']['quote'].endswith('36 hours after trigger.')
 assert any(e['date_status']=='Calculated' for e in calendar_sources.timed_care('2024-06-01','2024-06-30'))
 assert any(e['status']=='calculated' for e in home_care.view('2024-06-11')['upcoming'])
 edited=tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-06-11','22:00','America/Toronto');assert dose_timing.events()[0]['time']=='10:00'
 tracker_log.undo(edited['batch_id']);assert dose_timing.events()[0]['time']=='09:30'
 tracker_log.undo(saved['batch_id']);assert dose_timing.events()==[]

def test_wrong_time_zone_future_and_day_rejected_atomically(course):
 for timestamp,zone in [('2024-06-11T21:30:00',None),('2024-06-12T01:30:00+00:00','America/Toronto'),('2024-06-11T21:30:00+00:00','America/Toronto')]:
  with pytest.raises(tracker_log.EntryErrors):tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken','taken_at':timestamp,'time_zone':zone}],'2024-06-11')
 assert trackers.list_records()[0]['outcomes']=={}

def test_same_status_later_timestamp_is_preserved_by_undo(course):
 first=tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-06-11','21:30','America/Toronto')
 trackers.log(course['id'],'2024-06-11','taken',taken_at='2024-06-11T22:00:00-04:00',time_zone='America/Toronto')
 assert tracker_log.undo(first['batch_id'])=={'restored':0,'kept':1}
 assert trackers.list_records()[0]['outcomes']['2024-06-11']['taken_at'].endswith('22:00:00-04:00')

def test_interval_cannot_come_from_clinical_guess(course):
 with storage.connect() as conn:
  with pytest.raises(ValueError,match='explicitly'):dose_timing.add({'tracker_id':course['id'],'anchor_day':'2024-06-11','title':'Retrieval','hours':36},{'quote':'Leuprolide acetate, retrieval later.'},conn)

def test_elapsed_hours_cross_daylight_saving(tmp_path,monkeypatch):
 monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path));course=trackers.save('medicine',{'name':'Leuprolide acetate','dose_amount':4,'dose_unit':'mg','schedule_type':'daily','start':'2024-03-09'})
 rule(course,'2024-03-09');tracker_log.save([{'item':'tracker:'+course['id'],'value':'taken'}],'2024-03-09','21:30','America/Toronto')
 assert dose_timing.events()[0]['starts_at']=='2024-03-11T10:30:00-04:00'
