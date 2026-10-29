"""Separate identity origin isolation and session checks."""

import os
import base64
import importlib
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import sync_identity
from stellis import sync_server
from stellis.sync_server import app


class SyncIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_SYNC_DATA_DIR": self.temp.name})
        self.env.start()
        self.alice = TestClient(app)
        self.bob = TestClient(app)
        self.a = {"username": "alice", "password": "correct horse battery staple"}
        self.b = {"username": "bob", "password": "another secure test password"}

    def tearDown(self):
        self.alice.close()
        self.bob.close()
        self.env.stop()
        self.temp.cleanup()

    def test_two_accounts_and_device_isolation(self):
        self.assertEqual(self.alice.get('/sync/v1/me').status_code, 401)
        self.assertEqual(self.alice.get('/shared/sync-crypto.js').status_code, 200)
        self.assertEqual(self.alice.get('/shared/snapshot-format.js').status_code, 200)
        self.assertEqual(self.alice.post('/sync/v1/auth/register', json=self.a).status_code, 200)
        self.assertEqual(self.bob.post('/sync/v1/auth/register', json=self.b).status_code, 200)
        self.assertEqual(self.bob.post('/sync/v1/auth/register', json=self.a).status_code, 409)
        device = self.alice.post('/sync/v1/devices', json={'name':'Alice Mac'}).json()
        self.assertEqual([x['id'] for x in self.alice.get('/sync/v1/devices').json()], [device['id']])
        self.assertEqual(self.bob.get('/sync/v1/devices').json(), [])
        self.assertEqual(self.bob.delete(f"/sync/v1/devices/{device['id']}").status_code, 404)
        self.assertEqual(len(self.alice.get('/sync/v1/devices').json()), 1)
        self.assertEqual(self.alice.get('/api/v1/conversations').status_code, 404)
        self.assertEqual(self.alice.get('/api/v1/conversations/anything/audio').status_code, 404)
        self.assertEqual(self.alice.post('/sync/v1/auth/logout').status_code, 200)
        self.assertEqual(self.alice.get('/sync/v1/devices').status_code, 401)
        self.assertEqual(self.alice.post('/sync/v1/auth/login', json={**self.a,'password':self.b['password']}).status_code, 401)
        self.assertEqual(self.alice.post('/sync/v1/auth/login', json=self.a).status_code, 200)
        self.assertEqual(self.alice.delete(f"/sync/v1/devices/{device['id']}").status_code, 200)
        self.assertEqual(self.alice.get('/sync/v1/devices').json(), [])

    def test_cookie_expiry_origin_and_session_revocation(self):
        self.alice.post('/sync/v1/auth/register', json=self.a)
        with sqlite3.connect(sync_identity.root()/'identity.sqlite3') as conn:
            stored = conn.execute('SELECT verifier FROM users WHERE username = ?', (self.a['username'],)).fetchone()[0]
        self.assertNotIn(self.a['password'].encode(), stored)
        token = self.alice.cookies.get(sync_identity.COOKIE)
        self.assertEqual(self.alice.post('/sync/v1/devices', json={'name':'Denied'},
            headers={'Origin':'https://other.example'}).status_code, 403)
        self.assertEqual(self.alice.get('/sync/v1/me', headers={'Cookie':'stellis_sync_session=forged'}).status_code, 401)
        with sqlite3.connect(sync_identity.root()/'identity.sqlite3') as conn:
            conn.execute('UPDATE sessions SET expires_at = ? WHERE token_hash = ?',
                         ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
                          sync_identity._digest(token)))
        self.assertEqual(self.alice.get('/sync/v1/me').status_code, 401)
        self.alice.post('/sync/v1/auth/login', json=self.a)
        self.assertEqual(self.alice.post('/sync/v1/sessions/revoke-all').status_code, 200)
        self.assertEqual(self.alice.get('/sync/v1/me').status_code, 401)

    def test_ciphertext_journal_is_isolated_bounded_and_idempotent(self):
        self.alice.post('/sync/v1/auth/register', json=self.a)
        self.bob.post('/sync/v1/auth/register', json=self.b)
        encode = lambda value: base64.b64encode(value).decode()
        setup = {'salt': encode(os.urandom(16)), 'check_iv': encode(os.urandom(12)),
                 'check_ciphertext': encode(os.urandom(32))}
        self.assertEqual(self.alice.post('/sync/v1/vault', json=setup).status_code, 200)
        self.assertEqual(self.alice.post('/sync/v1/vault', json=setup).status_code, 409)
        self.assertEqual(self.bob.get('/sync/v1/vault').status_code, 404)
        entry = {'id': str(uuid.uuid4()), 'item_id': str(uuid.uuid4()),
                 'iv': encode(os.urandom(12)), 'ciphertext': encode(os.urandom(36))}
        first = self.alice.post('/sync/v1/entries', json=entry)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.alice.post('/sync/v1/entries', json=entry).json()['seq'], first.json()['seq'])
        self.assertEqual(self.alice.post('/sync/v1/entries', json={**entry,
            'ciphertext': encode(os.urandom(36))}).status_code, 409)
        self.assertEqual(self.alice.post('/sync/v1/entries', json={**entry,
            'id': str(uuid.uuid4()), 'ciphertext': encode(os.urandom(65537))}).status_code, 400)
        self.assertEqual(self.alice.post('/sync/v1/entries', json={**entry,
            'id': str(uuid.uuid4()), 'iv': encode(os.urandom(8))}).status_code, 422)
        self.assertEqual(len(self.alice.get('/sync/v1/entries').json()['entries']), 1)
        self.assertEqual(self.alice.get('/sync/v1/entries', params={'after':first.json()['seq']}).json()['entries'], [])
        self.assertEqual(self.bob.get('/sync/v1/entries').json()['entries'], [])
        with sqlite3.connect(sync_identity.root()/'identity.sqlite3') as conn:
            columns = [row[1] for row in conn.execute('PRAGMA table_info(ciphertext_entries)')]
        self.assertNotIn('text', columns)
        self.assertNotIn('audio', columns)

    def test_encrypted_media_chunks_are_account_scoped_and_idempotent(self):
        self.alice.post('/sync/v1/auth/register', json=self.a)
        self.bob.post('/sync/v1/auth/register', json=self.b)
        media_id = str(uuid.uuid4())
        route = f'/sync/v1/media/{media_id}/chunks/0'
        encode = lambda value: base64.b64encode(value).decode()
        chunk = {'iv': encode(os.urandom(12)), 'ciphertext': encode(os.urandom(64))}
        self.assertEqual(self.alice.post(route, json=chunk).status_code, 409)
        self.alice.post('/sync/v1/vault', json={'salt': encode(os.urandom(16)),
            'check_iv': encode(os.urandom(12)), 'check_ciphertext': encode(os.urandom(32))})
        self.assertEqual(self.alice.post(route, json=chunk).status_code, 200)
        self.assertEqual(self.alice.post(route, json=chunk).status_code, 200)
        self.assertEqual(self.alice.post(route, json={**chunk,
            'ciphertext': encode(os.urandom(64))}).status_code, 409)
        self.assertEqual(self.alice.get(route).json(), chunk)
        self.assertEqual(self.bob.get(route).status_code, 404)
        self.assertEqual(self.bob.post(route, json=chunk).status_code, 409)
        self.assertEqual(self.alice.post(f'/sync/v1/media/{media_id}/chunks/512', json=chunk).status_code, 400)
        self.assertEqual(self.alice.post(f'/sync/v1/media/{media_id}/chunks/1', json={**chunk,
            'ciphertext': encode(os.urandom(256 * 1024 + 17))}).status_code, 400)
        with sqlite3.connect(sync_identity.root()/'identity.sqlite3') as conn:
            rows = conn.execute('SELECT user_id, media_id, chunk_index, iv, ciphertext FROM media_chunks').fetchall()
        self.assertEqual(len(rows), 1)

    def test_configured_device_origin_has_credentialed_cors_without_wildcard(self):
        with patch.dict(os.environ, {'STELLIS_DEVICE_ORIGIN':'http://127.0.0.1:8765'}):
            module = importlib.reload(sync_server)
            with TestClient(module.app) as client:
                allowed = client.options('/sync/v1/devices', headers={
                    'Origin':'http://127.0.0.1:8765',
                    'Access-Control-Request-Method':'POST',
                    'Access-Control-Request-Headers':'content-type'})
                self.assertEqual(allowed.status_code, 200)
                self.assertEqual(allowed.headers['access-control-allow-origin'], 'http://127.0.0.1:8765')
                self.assertEqual(allowed.headers['access-control-allow-credentials'], 'true')
                denied = client.options('/sync/v1/devices', headers={
                    'Origin':'http://127.0.0.1:8770',
                    'Access-Control-Request-Method':'POST'})
                self.assertEqual(denied.status_code, 400)
                self.assertNotIn('access-control-allow-origin', denied.headers)
                client.post('/sync/v1/auth/register', json=self.a)
                self.assertEqual(client.post('/sync/v1/devices', json={'name':'Blocked'},
                    headers={'Origin':'http://127.0.0.1:8770'}).status_code, 403)
                accepted = client.post('/sync/v1/devices', json={'name':'Allowed'},
                    headers={'Origin':'http://127.0.0.1:8765'})
                self.assertEqual(accepted.status_code, 200)
                self.assertEqual(accepted.headers['access-control-allow-origin'], 'http://127.0.0.1:8765')
        importlib.reload(sync_server)
        with patch.dict(os.environ, {'STELLIS_DEVICE_ORIGIN':'https://example.com'}):
            with self.assertRaises(RuntimeError):
                sync_server._device_origin()
