"""Demo mode (#247): one-step seeding from the snapshot, and demo guest accounts."""
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from stellis import auth, demo_patient, documents, storage
from stellis.server import app


def write_snapshot(folder, files):
    """A snapshot ZIP and its manifest in `folder`, holding `files` (name to bytes)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    raw = buffer.getvalue()
    (folder / 'snapshot.zip').write_bytes(raw)
    (folder / 'snapshot.json').write_text(json.dumps({'sha256': hashlib.sha256(raw).hexdigest()}))
    return raw


@pytest.fixture
def snapshot_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path / 'data'))
    folder = tmp_path / 'maya'
    folder.mkdir()
    monkeypatch.setattr(demo_patient, 'STATIC', folder)
    return folder


def test_seed_copies_the_snapshot_into_a_fresh_workspace(snapshot_dir):
    write_snapshot(snapshot_dir, {'demo-history.json': '{"years": {"2022": {"filed": true}}}', 'documents/a.pdf': b'%PDF'})
    status = demo_patient.seed()
    workspace = demo_patient.existing_workspace()
    assert (workspace / 'documents/a.pdf').read_bytes() == b'%PDF'
    assert status['years'] == {'2022': {'filed': True}}


def test_seed_replaces_an_earlier_generation(snapshot_dir):
    write_snapshot(snapshot_dir, {'demo-history.json': '{"years": {}}'})
    demo_patient.seed()
    first = demo_patient.existing_workspace()
    (first / 'stale.txt').write_text('left over')
    demo_patient.seed()
    second = demo_patient.existing_workspace()
    assert second != first and not first.exists()


def test_seed_refuses_a_snapshot_that_does_not_match_its_manifest(snapshot_dir):
    write_snapshot(snapshot_dir, {'demo-history.json': '{}'})
    (snapshot_dir / 'snapshot.json').write_text(json.dumps({'sha256': '0' * 64}))
    with pytest.raises(ValueError, match='does not match'):
        demo_patient.seed()


def test_seed_refuses_paths_outside_the_workspace(snapshot_dir):
    write_snapshot(snapshot_dir, {'../escaped.txt': 'no'})
    with pytest.raises(ValueError, match='outside its workspace'):
        demo_patient.seed()
    assert not (storage.base_dir() / 'demos' / 'escaped.txt').exists()


def test_the_built_snapshot_matches_its_manifest_and_holds_maya_filed():
    manifest = json.loads((demo_patient.STATIC / 'snapshot.json').read_text())
    raw = (demo_patient.STATIC / 'snapshot.zip').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest['sha256']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        history = json.loads(archive.read('demo-history.json'))
    assert history['years'] and all(year['filed'] for year in history['years'].values())
    assert manifest['documents'] > 0 and manifest['notebooks']


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLIS_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('STELLIS_AUTH_MODE', 'required')


def test_a_guest_is_an_account_past_onboarding_that_expires(accounts):
    username, password = auth.register_guest()
    account_id = auth.account_id_for_username(username)
    assert username.startswith('demo-') and len(password) >= 32
    assert auth.is_guest(account_id) and not auth.needs_onboarding(account_id)
    assert auth.expired_guests() == []
    with auth._connect() as conn:
        conn.execute('UPDATE demo_guest SET expires_at = ?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    assert auth.expired_guests() == [account_id]
    auth.delete_account(account_id)
    assert not auth.is_guest(account_id) and auth.expired_guests() == []


def test_the_demo_route_needs_the_account_gateway(accounts):
    with TestClient(app) as client:
        assert client.get('/api/v1/auth/status').json()['demo_available'] is False
        assert client.post('/api/v1/auth/demo').status_code == 404


def test_behind_the_gateway_a_guest_signs_in_and_leaves_without_a_password(accounts, monkeypatch):
    monkeypatch.setitem(sys.modules, 'stellis.account_gateway', sys.modules.get('stellis.account_gateway') or object())
    with TestClient(app) as client:
        signed_in = client.post('/api/v1/auth/demo')
        assert signed_in.status_code == 200 and signed_in.json()['onboarding_required'] is False
        status = client.get('/api/v1/auth/status').json()
        assert status['guest'] is True and status['username'].startswith('demo-')
        left = client.request('DELETE', '/api/v1/account', json={'confirm': 'DELETE'})
        assert left.status_code == 200
        assert client.get('/api/v1/auth/status').json()['username'] is None


def test_a_real_account_still_needs_its_password_to_delete(accounts):
    auth.register('person', 'a password long enough')
    with TestClient(app) as client:
        client.post('/api/v1/auth/login', json={'username': 'person', 'password': 'a password long enough'})
        refused = client.request('DELETE', '/api/v1/account', json={'confirm': 'DELETE'})
        assert refused.status_code in {400, 401, 403, 422}


def test_setup_status_with_the_example_cookie_keeps_the_example_open(snapshot_dir):
    # Behind the gateway, /setup is answered where no example workspace exists; it must not end the demo.
    with TestClient(app) as client:
        client.cookies.set(demo_patient.COOKIE, 'maya')
        response = client.get('/api/v1/setup')
        assert response.status_code != 410
        assert demo_patient.COOKIE not in response.headers.get('set-cookie', '')
