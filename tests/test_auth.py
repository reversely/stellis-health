"""Local login gate and session lifecycle checks."""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import auth, storage
from stellis import cli
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


class LocalAuthTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name,
                                        "STELLIS_AUTH_MODE": "required",
                                        "STELLIS_DEV_ACCOUNT": "0", "STELLIS_DEV_PASSWORD": ""})
        self.env.start()
        self.client = TestClient(app, follow_redirects=False)
        self.credentials = {"username": "Riley", "password": "correct horse battery staple"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_registration_login_logout_and_protected_routes(self):
        self.assertEqual(self.client.get('/').status_code, 303)
        self.assertEqual(self.client.get('/auth').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/conversations').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/sync-config').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/ai-runs/test').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/conversations/import', files={
            'file': ('talk.mp3', mp3_bytes(), 'audio/mpeg')}).status_code, 401)
        self.assertTrue(self.client.get('/api/v1/auth/status').json()['registration_open'])
        registered = self.client.post('/api/v1/auth/register', json=self.credentials)
        self.assertEqual(registered.status_code, 200)
        self.assertIn('HttpOnly', registered.headers['set-cookie'])
        self.assertIn('samesite=lax', registered.headers['set-cookie'].lower())
        self.assertEqual(self.client.post('/api/v1/auth/register', json=self.credentials).status_code, 409)
        imported = self.client.post('/api/v1/conversations/import', files={
            'file': ('talk.mp3', mp3_bytes(), 'audio/mpeg')})
        self.assertEqual(imported.status_code, 200)
        conversation_id = imported.json()['conversation']['id']
        self.assertEqual(self.client.get(f'/api/v1/conversations/{conversation_id}/audio').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/auth/logout').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{conversation_id}/audio').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/login', json={**self.credentials,
            'password': 'wrong password'}).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/login', json=self.credentials).status_code, 200)
        self.assertEqual(len(self.client.get('/api/v1/conversations').json()), 1)

    def test_sync_origin_is_explicit_loopback_only(self):
        self.client.post('/api/v1/auth/register', json=self.credentials)
        with patch.dict(os.environ, {'STELLIS_SYNC_ORIGIN': ''}):
            self.assertIsNone(self.client.get('/api/v1/sync-config').json()['origin'])
        with patch.dict(os.environ, {'STELLIS_SYNC_ORIGIN': 'http://127.0.0.1:8769'}):
            self.assertEqual(self.client.get('/api/v1/sync-config').json()['origin'],
                             'http://127.0.0.1:8769')
        with patch.dict(os.environ, {'STELLIS_SYNC_ORIGIN': 'https://example.com'}):
            self.assertEqual(self.client.get('/api/v1/sync-config').status_code, 500)

    def test_expiry_forgery_and_origin_rejection(self):
        self.client.post('/api/v1/auth/register', json=self.credentials)
        self.assertEqual(self.client.post('/api/v1/categories', json={'name':'Blocked'},
            headers={'Origin':'https://another.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/conversations',
            headers={'Cookie': f'{auth.COOKIE_NAME}=forged-token'}).status_code, 401)
        token = self.client.cookies.get(auth.COOKIE_NAME)
        with sqlite3.connect(storage.data_dir() / 'auth.sqlite3') as conn:
            conn.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?',
                         ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
                          auth._token_hash(token)))
        self.assertEqual(self.client.get('/api/v1/conversations').status_code, 401)

    def test_dev_account_requires_explicit_setting_and_can_skip_registration(self):
        dev = {'username': 'dev', 'password': 'short'}
        self.assertEqual(self.client.post('/api/v1/auth/login', json=dev).status_code, 401)
        with patch.dict(os.environ, {'STELLIS_DEV_ACCOUNT': '1', 'STELLIS_DEV_PASSWORD': 'short'}):
            self.assertEqual(self.client.post('/api/v1/auth/login', json={**dev, 'password': 'wrong'}).status_code, 401)
            self.assertEqual(self.client.post('/api/v1/auth/login', json=dev).status_code, 200)
            self.assertEqual(self.client.get('/api/v1/conversations').status_code, 200)
            self.assertEqual(self.client.get('/api/v1/auth/status').json()['username'], 'dev')
            self.assertEqual(self.client.post('/api/v1/auth/logout').status_code, 200)
            self.assertEqual(self.client.get('/api/v1/conversations').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/login', json=dev).status_code, 401)

    def test_registration_has_no_recovery_step(self):
        registered = self.client.post('/api/v1/auth/register', json=self.credentials)
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json(), {'username': self.credentials['username'], 'onboarding_required': True})
        self.assertEqual(self.client.post('/api/v1/auth/recover', json={}).status_code, 404)

    def test_cli_serve_requires_login_by_default(self):
        with patch.dict(os.environ):
            os.environ.pop('STELLIS_AUTH_MODE', None)
            with patch('sys.argv', ['stellis', 'serve', '--port', '8899']), patch.object(cli.uvicorn, 'run') as run, \
                 patch.object(cli.models, 'ensure_in_background') as download, \
                 patch.object(cli.intake_voice, 'ensure_in_background') as call_models:
                cli.main()
                self.assertEqual(os.environ['STELLIS_AUTH_MODE'], 'required')
                run.assert_called_once()
                download.assert_called_once()
                call_models.assert_called_once()

    def test_plain_stellis_starts_the_app(self):
        with patch.dict(os.environ):
            os.environ.pop('STELLIS_AUTH_MODE', None)
            with patch('sys.argv', ['stellis']), patch.object(cli.uvicorn, 'run') as run, \
                 patch.object(cli.models, 'ensure_in_background'), \
                 patch.object(cli.intake_voice, 'ensure_in_background'):
                cli.main()
                self.assertEqual(run.call_args.kwargs['port'], 8765)
                self.assertEqual(run.call_args.args[0], 'stellis.account_gateway:app')
