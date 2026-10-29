"""Adapters from canonical workspace records into the shared calendar event model."""
from datetime import date
import calendar as month_calendar
from stellis import care_events,dose_timing
from stellis import health_preferences, reproductive, timeline, todos, health_observations, health_milestones, trackers


def tracking_events(first_date, last_date, current_date=None):
    if health_preferences.preferences()['experiences_periods'] is not True:
        return []
    result = []
    for record in reproductive.list_records()['records']:
        if record['kind'] == 'period':
            result.append({'id': 'period:'+record['id'], 'source_id': record['id'], 'source_type': 'reproductive',
                           'kind': 'period', 'track_id': 'periods', 'title': record['flow']+' flow',
                           'start_date': record['start'], 'end_date': record.get('end') or current_date or date.today().isoformat(),
                           'all_day': True})
        elif record['kind'] == 'larc' and not record.get('end'):
            result.append({'id': 'renewal:'+record['id'], 'source_id': record['id'], 'source_type': 'reproductive',
                           'kind': 'renewal', 'track_id': 'renewals', 'title': record['name']+' renewal',
                           'start_date': record['renewal'], 'end_date': record['renewal'], 'all_day': True})
    # Predicted periods, from the averages of past cycles (#232).
    for estimate in reproductive.predicted_periods(current_date or date.today().isoformat()):
        result.append({'id': 'predicted-period:'+estimate['start'], 'source_type': 'reproductive', 'source_id': 'prediction',
                       'kind': 'prediction', 'track_id': 'predicted_periods', 'title': 'Predicted period',
                       'start_date': estimate['start'], 'end_date': estimate['end'], 'all_day': True})
    for observation in health_observations.entries():
        result.append({'id': 'observation:'+observation['day']+':'+observation['track_id'],
                       'source_type': 'reproductive', 'source_id': 'day:'+observation['day'],
                       'kind': 'observation', 'track_id': observation['track_id'],
                       'title': observation['value'] + ('' if observation['value'] in {'No bleeding', 'Had flow'} else ' flow'),
                       'start_date': observation['day'], 'end_date': observation['day'], 'all_day': True})
    return result


def record_events(first_date, last_date, current_date=None):
    return [{'id': 'record:'+record['target'], 'kind': 'record', 'track_id': 'records',
             'title': record['title'], 'start_date': record['date'], 'end_date': record['date'],
             'target': record['target'], 'notebook_id': record['notebook_id'], 'all_day': True}
            for record in timeline.entries(kinds=('document',))]


def plan_events(first_date, last_date, current_date=None):
    result = []
    for group in todos.gather()['groups']:
        source = group['source']
        for task in group['items']:
            if not task.get('due_date'):
                continue
            result.append({'id': 'plan:'+task['id'], 'kind': 'plan', 'track_id': 'plans',
                           'title': task['text'], 'start_date': task['due_date'], 'end_date': task['due_date'],
                           'start': task['due_date']+('T'+task['due_time'] if task.get('due_time') else ''),
                           'target': '/'+source['kind']+'/'+source['id'],
                           'notebook_id': source.get('notebook_id'), 'all_day': not bool(task.get('due_time'))})
    return result


def milestone_events(first_date, last_date, current_date=None):
    result = []
    for milestone in health_milestones.entries():
        event_date = milestone['actual_date'] or milestone['target_date']
        qualifier = 'Confirmed' if milestone['actual_date'] else milestone['date_kind'].capitalize()
        result.append({'id': 'milestone:'+milestone['id'], 'source_id': milestone['id'],
                       'source_type': 'milestone', 'kind': 'milestone', 'track_id': 'milestones',
                       'title': milestone['title'], 'date_status': qualifier,
                       'start_date': event_date, 'end_date': event_date, 'all_day': True})
    return result


def derived_events(first_date, last_date, current_date=None):
    """Calculated and planned dates from registered kinds outside the cycle gate."""
    today = date.fromisoformat(current_date) if current_date else None
    result = []
    for record in trackers.list_records(kinds_filter=('medicine', 'pregnancy'), today=today):
        for item in record['summary']['derived']:
            result.append({'id': f"derived:{record['id']}:{item['key']}", 'source_id': record['id'],
                           'source_type': 'tracker', 'kind': 'derived',
                           'track_id': 'medicines' if record['kind'] == 'medicine' else 'pregnancy',
                           'title': item['title'], 'date_status': item['status'].capitalize(),
                           'start_date': item['date'], 'end_date': item['date'],
                           'notebook_id': record['notebook_id'], 'all_day': True})
    return result


def care_schedule(first_date,last_date,current_date=None):
    result=[]
    for event in care_events.list_all():
        first=event['starts_at'][:10];last=first
        if event.get('precision')=='month':
            value=date.fromisoformat(first);last=value.replace(day=month_calendar.monthrange(value.year,value.month)[1]).isoformat()
        result.append({'id':'care:'+event['id'],'kind':'appointment','track_id':'appointments','title':event['title'],
          'start_date':first,'end_date':last,'start':event['starts_at'],'all_day':event.get('precision') in {'day','month'},
          'date_status':'Planned month' if event.get('precision')=='month' else 'Booked' if event['status']=='scheduled' else 'Planned',
          'target':event['target'],'notebook_id':event['notebook_id']})
    return result


def timed_care(first_date,last_date,current_date=None):
    return [{'id':e['id'],'kind':'derived','track_id':'medicines','title':e['title'],'start':e['starts_at'],
      'start_date':e['date'],'end_date':e['date'],'all_day':False,'date_status':'Calculated',
      'target':e['target'],'notebook_id':e['notebook_id']} for e in dose_timing.events()]


SOURCES = (tracking_events, record_events, plan_events, milestone_events, derived_events, care_schedule, timed_care)
TRACKS = (
    {'id': 'milestones', 'label': 'Milestones', 'tone': 'accent'},
    {'id': 'appointments', 'label': 'Appointments', 'tone': 'default'},
    {'id': 'records', 'label': 'Records', 'tone': 'muted'},
    {'id': 'plans', 'label': 'Plans', 'tone': 'accent'},
    {'id': 'periods', 'label': 'Periods', 'tone': 'cycle'},
    {'id': 'predicted_periods', 'label': 'Predicted periods', 'tone': 'muted'},
    {'id': 'renewals', 'label': 'Device renewals', 'tone': 'accent'},
    {'id': 'medicines', 'label': trackers.KINDS['medicine'].plural, 'tone': 'accent'},
    {'id': 'pregnancy', 'label': trackers.KINDS['pregnancy'].plural, 'tone': 'accent'},
)


def local_events(first_date, last_date, current_date=None):
    events = [event for source in SOURCES for event in source(first_date, last_date, current_date)]
    return [event for event in events if event['start_date'] <= last_date and event['end_date'] >= first_date]
