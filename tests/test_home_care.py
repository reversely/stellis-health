"""Dated context, due actions and history remain canonical and patient-independent."""
import datetime as dt
import pytest
from stellis import goals,home_care,notebooks,tracker_log,trackers

@pytest.fixture(autouse=True)
def workspace(tmp_path,monkeypatch):monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))

def test_treatment_day_and_today_commit_undo_share_history():
 day=dt.date.today();book=notebooks.create('Treatment A')['id']
 phase=trackers.save('treatment',{'name':'Treatment A','phase':'Stimulation','start':(day-dt.timedelta(days=5)).isoformat(),'notebook_id':book})
 medicine=trackers.save('medicine',{'name':'Example medicine','dose_amount':150,'dose_unit':'units','schedule_type':'daily','start':(day-dt.timedelta(days=5)).isoformat(),'notebook_id':book})
 goals.create({'activity':'walk','amount':20,'times_per_week':3})
 data=home_care.view();assert data['active']['id']==phase['id'];assert data['active']['number']==6;assert data['active']['total'] is None
 item=next(i for i in data['items'] if i['id']=='tracker:'+medicine['id']);assert item['state']['expected'];assert data['done']==0
 saved=tracker_log.save([{'item':item['id'],'value':'taken'}],day.isoformat())
 data=home_care.view();assert data['done']==1;assert next(h for h in data['history'] if h['id']==item['id'])['cells'][-1]['state']=='taken'
 tracker_log.undo(saved['batch_id']);assert home_care.view()['done']==0

def test_context_selection_and_pregnancy_uses_actual_basis():
 day=dt.date.today();course=trackers.save('treatment',{'name':'Rehab','phase':'Recovery','start':(day-dt.timedelta(days=10)).isoformat()})
 home_care.choose(course['id']);assert home_care.view()['active']['name']=='Rehab'
 with pytest.raises(ValueError):home_care.choose('not-a-tracker')
 data=home_care.view((day-dt.timedelta(days=11)).isoformat());assert data['active'] is None

def test_new_care_context_does_not_leave_an_old_open_phase_primary():
 day=dt.date.today();old=notebooks.create('Earlier treatment')['id'];new=notebooks.create('New concern')['id']
 trackers.save('treatment',{'name':'Earlier phase','phase':'Treatment','start':(day-dt.timedelta(days=100)).isoformat(),'notebook_id':old})
 medicine=trackers.save('medicine',{'name':'New medicine','dose_amount':10,'dose_unit':'mg','schedule_type':'daily','start':(day-dt.timedelta(days=5)).isoformat(),'notebook_id':new})
 assert home_care.view()['active']['id']==medicine['id']

def test_an_open_treatment_phase_stops_counting_after_three_months():
 day=dt.date.today()
 recent=trackers.save('treatment',{'name':'Recent','phase':'Stimulation','start':(day-dt.timedelta(days=89)).isoformat()})
 trackers.save('treatment',{'name':'Old','phase':'Embryo transfer','start':(day-dt.timedelta(days=868)).isoformat()})
 assert [c['id'] for c in home_care.view()['contexts']]==[recent['id']]

def test_only_the_latest_recent_period_is_a_cycle_day_context():
 day=dt.date.today()
 for back in (400,120,60):trackers.save('period',{'start':(day-dt.timedelta(days=back)).isoformat(),'end':(day-dt.timedelta(days=back-4)).isoformat(),'flow':'Moderate'})
 periods=[c for c in home_care.view()['contexts'] if c['kind']=='period']
 # #232: no "Day N of cycle"; outside a period, the next period predicted from the averages of past cycles (gaps 280 and 60 days).
 assert [(c['caption'],c['number'],c['since']) for c in periods]==[('Next period in',110,60)]
 assert periods[0]['detail'].startswith('days, around ')
 assert not [c for c in home_care.view((day-dt.timedelta(days=-40)).isoformat())['contexts'] if c['kind']=='period']


def test_during_a_period_home_says_period_day():
 day=dt.date.today()
 trackers.save('period',{'start':(day-dt.timedelta(days=2)).isoformat(),'flow':'Light'})
 (context,)=[c for c in home_care.view()['contexts'] if c['kind']=='period']
 assert (context['caption'],context['number'])==('Period day',3)
 assert context['detail'].startswith('Started ')
