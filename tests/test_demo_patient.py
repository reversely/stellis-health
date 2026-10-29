"""Yearly synthetic imports with biometrics, Stellis sorting rounds and real/demo isolation."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from unittest import mock

from stellis import agent_gateway, demo_patient, documents, notebooks, storage
from stellis.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('STELLIS_AUTH_MODE','off')
    with TestClient(app) as test:
        yield test


def package(year):
    return (demo_patient.STATIC/'years'/f'maya-{year}.zip').read_bytes()


def upload(client, year):
    return client.post('/api/v1/demo/upload',files={'file':(f'maya-{year}.zip',package(year),'application/zip')})


def confirm(client, year, review):
    return client.post(f'/api/v1/demo/years/{year}/dates',json={'dates':{
        record['id']:record['date_suggestion']['date'] for record in review['documents']}})


def stellis_sorts(prompt, *_):
    """Stands in for the sorting model: files each record by its title, inside a family."""
    context = json.loads(prompt.split('\n\n', 1)[1])
    words = {'pregnan': ('Pregnancy & Postpartum', 'Pregnancy'), 'ivf': ('Reproductive Health', 'IVF cycle'),
             'embryo': ('Reproductive Health', 'IVF cycle'), 'fertil': ('Reproductive Health', 'Fertility'),
             'iron': ('Blood Health', 'Iron deficiency'), 'ferritin': ('Blood Health', 'Iron deficiency')}
    groups = {}
    for record in context['records']:
        title = record['title'].casefold()
        family, name = next((v for k, v in words.items() if k in title), ('Preventive Care', 'Screening'))
        groups.setdefault((family, name), []).append(record['index'])
    reply = {'groups': [{'family': f, 'notebook': n, 'reason': f'{n} records', 'items': i} for (f, n), i in groups.items()][:8]}
    return json.dumps(reply), 'session'


def sort_and_finish(client, year):
    """Asks Stellis to sort a year, approves every proposal, then closes the year."""
    proposal = client.post(f'/api/v1/demo/years/{year}/sort')
    assert proposal.status_code == 200, proposal.text
    for group in proposal.json()['groups']:
        applied = client.post('/api/v1/records/sort/apply', json={
            'notebook_name': group['notebook_name'], 'notebook_id': group['notebook_id'],
            'collection_id': group['collection_id'], 'items': group['items']})
        assert applied.status_code == 200, applied.text
    finished = client.post(f'/api/v1/demo/years/{year}/finish', json={'reasons': {
        g['notebook_name']: g['reason'] for g in proposal.json()['groups']}})
    assert finished.status_code == 200, finished.text
    return finished.json()


def test_years_fill_empty_demo_only_after_approval_and_clear_preserves_real(client):
    image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
    real,_=documents.create('actual-workspace-sentinel.png',image.getvalue())
    real_book=notebooks.create('Actual workspace sentinel')
    original_file=documents.file_path(real['id'])[0].read_bytes()
    client.post('/api/v1/demo/enter').raise_for_status()
    assert client.get('/api/v1/documents').json()==[]
    assert client.get('/api/v1/notebooks').json()['notebooks']==[]
    assert upload(client,2028).status_code==422
    all_ids=[]
    for year in range(2027,2032):
        result=upload(client,year);assert result.status_code==200,result.text
        review=result.json();assert review['documents']
        assert not review['filed']
        assert all(record['record_date'] is None for record in review['documents'])
        assert review['biometrics']['readings'] > 300
        assert client.post(f'/api/v1/demo/years/{year}/sort').status_code==422
        dates=confirm(client,year,review);assert dates.status_code==200,dates.text
        with mock.patch.object(agent_gateway, 'turn', mock.AsyncMock(side_effect=stellis_sorts)):
            round_=sort_and_finish(client, year)
        assert round_['unsorted']==0 and round_['created'] or round_['grown']
        assert client.get(f'/api/v1/demo/years/{year}').json()['filed']
        all_ids += [record['id'] for record in review['documents']]
        count=len(client.get('/api/v1/documents').json())
        assert upload(client,year).status_code==200
        assert len(client.get('/api/v1/documents').json())==count
    assert len(all_ids)==70
    rounds=client.get('/api/v1/demo/history').json()
    assert [r['year'] for r in rounds]==['2027','2028','2029','2030','2031']
    created={(b['family'],b['name']) for r in rounds for b in r['created']}
    assert {('Reproductive Health','Fertility'),('Reproductive Health','IVF cycle')}<=created
    assert all(name!=family for family,name in created)
    weights=next(m for m in client.get('/api/v1/readings').json()['measures'] if m['key']=='weight')['points']
    assert len(weights)>250 and min(p['value'] for p in weights)>55
    assert client.get('/api/v1/documents/'+real['id']).status_code==404
    client.post('/api/v1/demo/leave').raise_for_status()
    assert [d['id'] for d in client.get('/api/v1/documents').json()]==[real['id']]
    assert client.get('/api/v1/documents/'+all_ids[0]).status_code==404
    client.post('/api/v1/demo/enter')
    old_generation=demo_patient.workspace()
    client.delete('/api/v1/demo/history').raise_for_status()
    assert not old_generation.exists()
    assert documents.get(real['id'])['filename']=='actual-workspace-sentinel.png'
    assert documents.file_path(real['id'])[0].read_bytes()==original_file
    assert next(b for b in notebooks.list_notebooks() if b['id']==real_book['id'])['name']=='Actual workspace sentinel'
    client.post('/api/v1/demo/enter')
    assert client.get('/api/v1/documents').json()==[]
    assert demo_patient.workspace()!=old_generation


def test_demo_zip_tampering_and_traversal_do_not_create_records(client):
    client.post('/api/v1/demo/enter')
    for name,body in [('notes.pdf',b'%PDF-1.0'),('bad.zip',b'broken')]:
        assert client.post('/api/v1/demo/upload',files={'file':(name,body)}).status_code==422
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as archive:
        archive.writestr('demo-package.json',json.dumps({'format':'stellis-maya-year-v1','year':2027}))
        archive.writestr('../outside.pdf',b'bad')
    assert client.post('/api/v1/demo/upload',files={'file':('bad.zip',content.getvalue())}).status_code==422
    assert client.get('/api/v1/documents').json()==[]
    assert client.post('/api/v1/documents',files={'file':('notes.pdf',b'%PDF')}).status_code==422


def test_tampered_biometrics_create_nothing(client):
    client.post('/api/v1/demo/enter')
    content=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package(2027))) as original, zipfile.ZipFile(content,'w') as archive:
        for name in original.namelist():
            data=original.read(name)
            if name=='biometrics.json':
                data=data.replace(b'"weight"',b'"pulse"',1)
            archive.writestr(name,data)
    response=client.post('/api/v1/demo/upload',files={'file':('maya-2027.zip',content.getvalue())})
    assert response.status_code==422 and 'biometrics' in response.text
    assert client.get('/api/v1/documents').json()==[]
    assert client.get('/api/v1/readings').json()['measures'] in ([],) or all(not m['points'] for m in client.get('/api/v1/readings').json()['measures'])


def test_context_is_restored_after_error(client):
    real=storage.data_dir()
    client.post('/api/v1/demo/enter')
    assert client.get('/api/v1/documents/no-such-record').status_code==404
    assert storage.data_dir()==real
    assert not storage.in_demo()


def test_tracking_and_settings_stay_in_their_workspace(client):
    from stellis import ai, reproductive
    real_period=reproductive.save('period',{'start':'2026-01-01','end':'2026-01-05','flow':'Light'})
    key_file=ai._key_file()
    key_file.write_text('synthetic-credential-sentinel')
    client.post('/api/v1/demo/enter').raise_for_status()
    assert client.get('/api/reproductive').json()['records']==[]
    saved=client.post('/api/v1/reproductive/period',json={'start':'2022-01-08','end':'2022-01-11','tracking_gap_before':True})
    assert saved.status_code==200,saved.text
    assert client.get('/api/v1/reproductive').json()['records'][0]['tracking_gap_before']
    assert ai._key_file()==key_file
    assert client.post('/api/v1/ai/key',json={'key':'changed'}).status_code==403
    assert client.post('/api/calendar/connect',json={}).status_code==403
    client.delete('/api/v1/demo/history').raise_for_status()
    assert key_file.read_text()=='synthetic-credential-sentinel'
    assert [r['id'] for r in reproductive.list_records()['records']]==[real_period['id']]


def test_demo_inference_retains_configured_provider_without_managed_tools(tmp_path,monkeypatch):
    from stellis import agent_gateway,ai,runtime_profile
    monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('NEBIUS_API_KEY','synthetic-provider-key')
    monkeypatch.setenv('STELLIS_ACCOUNT_ID','1')
    for profile,url,key in [('local','http://127.0.0.1:11434/v1','ollama'),('direct','https://api.tokenfactory.nebius.com/v1','synthetic-provider-key')]:
        monkeypatch.setenv('STELLIS_RUNTIME_PROFILE',profile)
        with storage.demo_workspace(tmp_path/'demo'):
            assert runtime_profile.remote_agents()
            assert agent_gateway.configured()
            assert runtime_profile.inference_url()==url
            assert ai._key()==key


def test_demo_reuses_saved_model_configuration_without_patient_data(tmp_path,monkeypatch):
    from stellis import model_routes,runtime_profile
    monkeypatch.setenv('STELLIS_DATA_DIR',str(tmp_path))
    monkeypatch.setenv('STELLIS_RUNTIME_PROFILE','local')
    (tmp_path/'local-inference-model').write_text('installed-local-model')
    model_routes.save({'agent':'installed-agent-model'})
    original=model_routes.path()
    with storage.demo_workspace(tmp_path/'demo'):
        assert model_routes.path()==original
        assert runtime_profile.model_id('agent')=='installed-agent-model'
        assert runtime_profile.model_id('transcript')=='installed-local-model'


def test_clear_waits_for_read_and_stale_requests_cannot_resurrect_demo(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    entered=threading.Event();release=threading.Event()
    client.post('/api/v1/demo/enter').raise_for_status()
    old_generation=demo_patient.workspace()
    original=documents.list_all
    def delayed_read(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(documents,'list_all',delayed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read=pool.submit(client.get,'/api/v1/documents')
        assert entered.wait(5)
        clear=pool.submit(client.delete,'/api/v1/demo/history')
        try:
            with pytest.raises(TimeoutError):
                clear.result(timeout=0.2)
        finally:
            release.set()
        assert read.result(timeout=5).status_code==200
        assert clear.result(timeout=5).status_code==200
    assert not old_generation.exists()
    stale=client.get('/api/v1/documents',headers={'Cookie':'stellis-demo=maya'})
    assert stale.status_code==410
    assert demo_patient.existing_workspace() is None
    assert not old_generation.exists()
    assert not client.get('/api/v1/demo/status',headers={'Cookie':'stellis-demo=maya'}).json()['active']


def test_clear_interrupts_running_inference_before_waiting_for_response(client,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    import threading
    from stellis import agent_gateway
    entered=threading.Event()
    monkeypatch.setattr(agent_gateway,'configured',lambda:True)
    monkeypatch.setenv('STELLIS_RUNTIME_PROFILE','direct')
    async def blocked_turn(*args,**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(agent_gateway,'turn',blocked_turn)
    client.post('/api/v1/demo/enter').raise_for_status()
    agent=client.get('/api/v1/agents').json()[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ask=pool.submit(client.post,f"/api/v1/agents/{agent['id']}/ask",json={'question':'Synthetic pending question'})
        assert entered.wait(5)
        clear=pool.submit(client.delete,'/api/v1/demo/history')
        assert clear.result(timeout=5).status_code==200
        assert ask.result(timeout=5).status_code==409
    assert demo_patient.existing_workspace() is None


def test_native_call_uses_demo_goals_and_never_writes_ordinary_call_state(client):
    from stellis import coach_call, goals
    real_goal=goals.create({'activity':'meditate','amount':10,'times_per_week':3})
    ordinary=coach_call.load()
    client.post('/api/v1/demo/enter').raise_for_status()
    demo_goal=client.post('/api/v1/goals',json={'activity':'walk','amount':20,'times_per_week':3}).json()
    today=goals._now().date().isoformat()
    # The demo's today is after Maya's last record (#258); the call runs on that day.
    with storage.demo_workspace(demo_patient.existing_workspace()):
        demo_today=goals._now().date().isoformat()
    with client.websocket_connect(f'/api/v1/coach/call?kind=morning&today={demo_today}&now=10:00&utc_offset=0&voice=0') as socket:
        socket.receive_json()
        with storage.demo_workspace(demo_patient.existing_workspace()):
            state=coach_call.load()
            assert [g['goal_id'] for g in state['context']['goals']]==[demo_goal['id']]
            assert 'Walk 20 minutes' in [g['title'] for g in state['context']['goals']]
            assert all(real_goal['id'] not in str(item) for item in state['context']['loggable'])
        socket.send_json({'type':'end'})
    assert coach_call.load()==ordinary
    assert len(goals.overview(today,0,'10:00')['goals'])==1


def test_stale_demo_call_cookie_is_refused_and_clear_drains_active_socket(client):
    from starlette.websockets import WebSocketDisconnect
    client.post('/api/v1/demo/enter').raise_for_status()
    workspace=demo_patient.existing_workspace()
    with client.websocket_connect('/api/v1/coach/call?kind=call&today=2026-10-24&now=10:00&voice=0') as socket:
        socket.receive_json()
        client.delete('/api/v1/demo/history').raise_for_status()
    assert not workspace.exists()
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect('/api/v1/coach/call?kind=call&today=2026-10-24&now=10:00&voice=0',headers={'Cookie':'stellis-demo=maya'}) as socket:
            socket.receive_json()
    assert failure.value.code==4410
    assert not workspace.exists()


def test_native_demo_upload_accepts_only_original_corpus_and_prepares_metrics(client):
    client.post('/api/v1/demo/enter').raise_for_status()
    original=next((demo_patient.STATIC/'documents'/'2027').glob('D002*'))
    response=client.post('/api/v1/documents',files={'file':(original.name,original.read_bytes(),'application/pdf')})
    assert response.status_code==200,response.text
    record=response.json()['document'];assert record['title']=='CBC, ferritin and thyroid results'
    facts=client.get(f'/api/v1/documents/{record["id"]}/facts').json()['facts']
    assert len(facts)==6
    client.post(f'/api/v1/documents/{record["id"]}/facts/accept').raise_for_status()
    series=client.get('/api/v1/readings').json()['measures'];assert next(m for m in series if m['key']=='ferritin')['points'][0]['value']==11
    changed=client.post('/api/v1/documents',files={'file':('changed.pdf',original.read_bytes()+b'changed')})
    assert changed.status_code==422
    client.post('/api/v1/demo/leave').raise_for_status()
    assert client.get('/api/v1/facts').json()['facts']==[]


def test_populate_fills_the_example_workspace_and_one_delete_removes_it(client):
    from stellis import routine_review
    real_book=notebooks.create('Actual workspace sentinel')
    prepared=[]
    async def no_instructions(kind, identifier):
        prepared.append(identifier)
        return {'id': 'r-' + identifier, 'state': 'pending', 'proposals': []}
    with mock.patch.object(agent_gateway, 'turn', mock.AsyncMock(side_effect=stellis_sorts)), \
            mock.patch.object(routine_review, 'prepare', no_instructions):
        populated=client.post('/api/v1/demo/populate')
    populated.raise_for_status()
    assert sorted(populated.json()['years'])==['2027','2028','2029','2030','2031']
    assert all(year['filed'] for year in populated.json()['years'].values())
    # Populating approves each year's care too, record by record.
    assert len(prepared)==70 and all(year['care']['state']=='done' for year in populated.json()['years'].values())
    focus=client.get('/api/v1/home/focus').json()
    assert focus['empty'] is False and focus['results']
    assert {tile['name'] for tile in focus['results']}>={'Ferritin','Hemoglobin'}
    # Populating again changes nothing.
    with mock.patch.object(agent_gateway, 'turn', mock.AsyncMock(side_effect=AssertionError('no new years'))):
        again=client.post('/api/v1/demo/populate').json()
    assert again['documents']==populated.json()['documents']
    client.delete('/api/v1/demo/history').raise_for_status()
    assert client.get('/api/v1/demo/status').json()['active'] is False
    assert [book['name'] for book in notebooks.list_notebooks()]==[real_book['name']]
    empty=client.get('/api/v1/home/focus').json()
    # One Add a record among the first steps, not another for each empty notebook.
    assert empty['empty'] is True and [item['kind'] for item in empty['items']]==['chat']
    assert [idea['action']['label'] for idea in empty['ideas']].count('Add a record')==1


def test_example_workspace_shares_the_installed_speech_models(client, tmp_path, monkeypatch):
    monkeypatch.delenv('STELLIS_CALL_MODELS', raising=False)
    from stellis import intake_voice, models
    for folder in (tmp_path / 'models', tmp_path / 'models' / 'call'):
        folder.mkdir(parents=True, exist_ok=True)
    for name, *_ in models.MODELS:
        (tmp_path / 'models' / name).write_bytes(b'model')
    for name, *_ in intake_voice.MODELS:
        (tmp_path / 'models' / 'call' / name).write_bytes(b'model')
    for name, choices in models.live_asr_models.REQUIRED_FILES.items():
        folder = tmp_path / 'models' / 'live-asr' / name
        folder.mkdir(parents=True)
        for files in choices:
            (folder / files[0]).write_bytes(b'model')
    assert client.get('/api/v1/setup').json()['state'] == 'ready'
    client.post('/api/v1/demo/enter').raise_for_status()
    # Without sharing, every example workspace showed "Speech models are waiting to download".
    assert client.get('/api/v1/setup').json()['state'] == 'ready'
    assert intake_voice.missing() == []


def test_year_care_applies_each_record_in_date_order(client, monkeypatch):
    from stellis import routine_review
    client.post('/api/v1/demo/enter').raise_for_status()
    review = upload(client, '2027').json()
    confirm(client, '2027', review).raise_for_status()
    with mock.patch.object(agent_gateway, 'turn', mock.AsyncMock(side_effect=stellis_sorts)):
        sort_and_finish(client, '2027')
    order, applied, failing = [], [], {'tolerability': True}
    titles = {record['id']: record['title'] for record in review['documents']}
    monkeypatch.setattr(demo_patient, 'CARE_RETRY_WAIT', 0)

    async def prepare(kind, identifier):
        order.append(titles[identifier])
        if 'tolerability' in titles[identifier] and failing['tolerability']:
            raise ValueError('The care review did not match the original instructions. Retry review.')
        proposals = [{'id': 'p1', 'action': 'add', 'kind': 'medicine', 'quote': 'Start ferrous sulfate 300 mg',
                      'effective_date': '2027-02-02', 'data': {'name': 'ferrous sulfate', 'dose_amount': 300.0, 'dose_unit': 'mg'}}] \
            if 'review and cervical' in titles[identifier] else []
        return {'id': 'r-' + identifier, 'state': 'pending', 'proposals': proposals}

    monkeypatch.setattr(routine_review, 'prepare', prepare)
    monkeypatch.setattr(routine_review, 'apply', lambda identifier, selected: applied.append((identifier, selected)))
    def run():
        started = client.post('/api/v1/demo/years/2027/care')
        assert started.status_code == 200, started.text
        assert client.get('/api/v1/demo/years/2027/care').json()['state'] in ('running', 'done')
        for _ in range(200):
            status = client.get('/api/v1/demo/years/2027/care').json()
            if status['state'] == 'done':
                return status
            import time; time.sleep(0.05)
        raise AssertionError('care step did not finish')

    status = run()
    assert status['state'] == 'done' and status['done'] == status['total'] == 8
    dates = [record['date'] for record in status['records']]
    assert dates == sorted(dates)
    iron = next(r for r in status['records'] if 'review and cervical' in r['title'])
    assert iron['changes'] == [{'action': 'add', 'kind': 'medicine', 'name': 'ferrous sulfate', 'dose': '300 mg',
                                'effective_date': '2027-02-02', 'end': '', 'quote': 'Start ferrous sulfate 300 mg'}]
    assert applied == [('r-' + iron['id'], ['p1'])]
    assert 'did not match' in next(r for r in status['records'] if 'tolerability' in r['title'])['error']
    # The failing record was tried three times, the others once.
    assert len(order) == 10 and sum('tolerability' in title for title in order) == 3
    # Running again reads only the record that failed, keeping the others' changes.
    failing['tolerability'] = False
    status = run()
    assert len(order) == 11 and 'tolerability' in order[-1]
    assert not any(r['error'] for r in status['records']) and len(status['records']) == 8
    assert next(r for r in status['records'] if 'review and cervical' in r['title'])['changes'] == iron['changes']
    # A year with nothing left to read is not run again.
    assert client.post('/api/v1/demo/years/2027/care').json()['state'] == 'done' and len(order) == 11


def test_year_care_needs_the_year_filed_first(client):
    client.post('/api/v1/demo/enter').raise_for_status()
    upload(client, '2027')
    assert client.post('/api/v1/demo/years/2027/care').status_code == 422


def test_one_click_adds_a_year_pack_through_every_stage_and_offers_next_steps(client):
    # #258: one click runs read, date, file and extract; no date, notebook or figure needs confirming.
    import time
    from stellis import routine_review
    async def no_instructions(kind, identifier):
        return {'id': 'r-' + identifier, 'state': 'pending', 'proposals': []}
    client.post('/api/v1/demo/enter').raise_for_status()
    with mock.patch.object(agent_gateway, 'turn', mock.AsyncMock(side_effect=stellis_sorts)), \
            mock.patch.object(routine_review, 'prepare', no_instructions):
        started = client.post('/api/v1/demo/years/2027/add')
        started.raise_for_status()
        for _ in range(200):
            pack = client.get('/api/v1/demo/years/2027/add').json()
            if pack['stage'] == 'done' or pack['error']:
                break
            time.sleep(.1)
    assert pack['error'] is None and pack['stage'] == 'done', pack
    counts = pack['counts']
    assert counts['records'] == 8 and counts['first'].startswith('2027') and counts['last'].startswith('2027')
    assert counts['created'] >= 1 and counts['results'] > 0
    # Results are accepted with the pack, so nothing waits for review.
    assert client.get('/api/v1/demo/status').json()['years']['2027']['filed'] is True
    steps = pack['next']
    assert steps['report']['href'].startswith('/document/')
    assert steps['questions'] and all('?ask=' in q['href'] for q in steps['questions'])
    assert steps['notebooks']
    # The next year has not started.
    assert client.get('/api/v1/demo/years/2028/add').json()['stage'] == 'waiting'


def test_without_its_content_the_demo_is_off(client, tmp_path, monkeypatch):
    monkeypatch.setattr(demo_patient, 'STATIC', tmp_path / 'no-demo')
    assert not demo_patient.available()
    assert client.post('/api/v1/demo/enter').status_code == 404
    monkeypatch.setenv('STELLIS_AUTH_MODE', 'required')
    assert client.get('/api/v1/auth/status').json()['demo_available'] is False
