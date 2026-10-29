"""Real HTTP boundary checks for private account creation and routing."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse
from pathlib import Path

import httpx


@unittest.skipIf(sys.platform == "win32", "Account child Unix sockets need a Windows transport")
class AccountGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(STELLIS_DATA_DIR=self.temp.name, STELLIS_AUTH_MODE="required",
                   STELLIS_ACCOUNT_SOCKET_DIR=str(Path(self.temp.name) / "sockets"),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        for key in ("STELLIS_DEV_ACCOUNT", "STELLIS_DEV_PASSWORD", "STELLIS_NEMOCLAW_SANDBOX",
                    "STELLIS_TAVILY_MCP_READY"):
            env.pop(key, None)
        env["STELLIS_GOOGLE_CALENDAR_CLIENT_ID"] = "test-client"
        self.env = env
        self.command = [sys.executable, "-m", "uvicorn", "stellis.account_gateway:app", "--host", "127.0.0.1",
                        "--port", str(port), "--no-access-log"]
        self.start_gateway()

    def start_gateway(self) -> None:
        self.process = subprocess.Popen(self.command, env=self.env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if self.process.poll() is not None:
                self.fail("Account gateway stopped at startup")
            try:
                if httpx.get(self.url + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            self.fail("Account gateway did not start")

    def tearDown(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.temp.cleanup()

    def test_second_signup_is_private_and_sessions_survive_restart(self) -> None:
        owner = httpx.Client(base_url=self.url, timeout=30)
        second = httpx.Client(base_url=self.url, timeout=30)
        try:
            for client, username in ((owner, "owner"), (second, "second")):
                response = client.post("/api/v1/auth/register", json={
                    "username": username, "password": "correct horse battery staple"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get("/api/v1/account").json()["username"], username)
            self.assertTrue(second.get("/api/v1/auth/status").json()["registration_open"])
            self.assertEqual(second.post("/api/v1/auth/register", json={
                "username": "owner", "password": "another correct horse battery"}).status_code, 409)
            created = owner.post("/api/v1/pages", json={"title": "Private owner note", "markdown": "Only owner"})
            self.assertEqual(created.status_code, 201)
            note_id = created.json()["id"]
            owner.put("/api/v1/onboarding/profile", json={"field":"name", "value":"Owner name"}).raise_for_status()
            self.assertEqual(owner.get("/api/v1/onboarding/profile").json()["answers"]["name"], "Owner name")
            self.assertNotIn("name", second.get("/api/v1/onboarding/profile").json()["answers"])
            self.assertEqual(len(owner.get("/api/v1/pages").json()), 1)
            self.assertEqual(second.get("/api/v1/pages").json(), [])
            self.assertEqual(second.get(f"/api/v1/pages/{note_id}").status_code, 404)
            self.assertFalse(second.get("/api/v1/settings/nebius-key").json()["configured"])
            self.assertEqual(second.get("/api/v1/calendar/connections").json()[0]["connected"], False)
            private_key = "tvly-private-test-credential"
            response = second.put("/api/v1/settings/tavily-key", json={"key": private_key})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(private_key, response.text)
            self.assertEqual(second.get("/api/v1/settings/tavily-key").json()["source"], "saved")
            self.assertNotEqual(owner.get("/api/v1/settings/tavily-key").json()["source"], "saved")
            self.assertEqual(owner.get("/api/v1/pages").json()[0]["id"], note_id)
            owner_cookie = owner.cookies.get("stellis_session")
            second_cookie = second.cookies.get("stellis_session")
            self.assertTrue(owner_cookie and second_cookie and owner_cookie != second_cookie)
            self.process.terminate()
            self.process.wait(timeout=20)
            self.start_gateway()
            self.assertEqual(owner.get("/api/v1/pages").json()[0]["id"], note_id)
            self.assertEqual(second.get("/api/v1/pages").json(), [])
            self.assertEqual(second.get("/api/v1/account").json()["username"], "second")
            self.assertEqual(second.request("DELETE", "/api/v1/account", json={
                "password": "correct horse battery staple", "confirm": "DELETE"}).status_code, 200)
            self.assertEqual(second.get("/api/v1/pages").status_code, 401)
            self.assertEqual(second.post("/api/v1/auth/login", json={
                "username": "second", "password": "correct horse battery staple"}).status_code, 401)
            self.assertEqual(owner.get("/api/v1/pages").json()[0]["id"], note_id)
        finally:
            owner.close()
            second.close()

    def test_oauth_callback_uses_initiating_account_after_restart(self):
        with httpx.Client(base_url=self.url, timeout=30) as owner, httpx.Client(base_url=self.url, timeout=30) as second:
            for client, username in ((owner, "owner"), (second, "second")):
                self.assertEqual(client.post("/api/v1/auth/register", json={"username":username,"password":"correct horse battery staple"}).status_code, 200)
            second.put("/api/v1/onboarding", json={"uses":["learning"],"step":"uses","agent_choices":{}})
            url = second.get("/api/v1/calendar/google/authorize?return_to=/onboarding").json()["url"]
            state = parse_qs(urlparse(url).query)["state"][0]
            self.process.terminate(); self.process.wait(timeout=20); self.start_gateway()
            # Wrong account's cookie on the callback host must not change ownership.
            result = owner.get("/api/v1/calendar/google/callback", params={"state":state,"error":"access_denied"})
            self.assertEqual(result.status_code, 303)
            self.assertTrue(result.headers["location"].startswith(self.url + "/onboarding?calendar=failed&connection_error="))
            setup = second.get("/api/v1/onboarding").json()
            self.assertTrue(setup["required"])
            self.assertEqual(setup["step"], "uses")
            self.assertEqual(setup["uses"], ["learning"])
            replay = owner.get("/api/v1/calendar/google/callback", params={"state":state,"error":"access_denied"})
            self.assertEqual(replay.headers["location"], "/onboarding?calendar=expired")

    def test_dash_component_and_runtime_socket_use_the_account_gateway(self) -> None:
        """The integrated component reaches the same authenticated account worker."""
        import sqlite3
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect
        address=self.url.replace('http','ws')+'/components/measurements/_dash-ws-callback'
        self.assertEqual(httpx.get(self.url+'/components/measurements/_dash-layout').status_code,303)
        with self.assertRaises(InvalidStatus):connect(address,open_timeout=10).close()
        with httpx.Client(base_url=self.url,timeout=30) as client:
            client.post('/api/v1/auth/register',json={'username':'componentowner','password':'correct horse battery staple'}).raise_for_status()
            # Fixture setup completes onboarding without changing the interface under test.
            with sqlite3.connect(Path(self.temp.name)/'auth.sqlite3') as conn:
                conn.execute('UPDATE account SET onboarding_completed=1 WHERE username=?',('componentowner',))
            layout=client.get('/components/measurements/_dash-layout')
            self.assertEqual(layout.status_code,200,layout.text)
            self.assertIn('measure-data',layout.text)
            cookie='stellis_session='+client.cookies.get('stellis_session')
            with self.assertRaises(InvalidStatus):
                connect(address,additional_headers={'Cookie':cookie,'Origin':'https://unrelated.example'},open_timeout=10).close()
            with connect(address,additional_headers={'Cookie':cookie,'Origin':self.url},open_timeout=30) as runtime:
                self.assertTrue(runtime.ping().wait(5),'the component runtime must relay both directions')

    def test_the_intake_call_needs_a_session_from_this_site(self) -> None:
        """The gateway relays the call's WebSocket to the account's process."""
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        address = self.url.replace("http", "ws") + "/api/v1/intake/call"
        with self.assertRaises(InvalidStatus):
            connect(address, open_timeout=10).close()
        with httpx.Client(base_url=self.url, timeout=20) as client:
            self.assertEqual(client.post("/api/v1/auth/register", json={
                "username": "denver", "password": "correct horse battery staple"}).status_code, 200)
            cookie = "stellis_session=" + client.cookies.get("stellis_session")
        with self.assertRaises(InvalidStatus):
            connect(address, additional_headers={"Cookie": cookie, "Origin": "https://example.com"},
                    open_timeout=10).close()
        with connect(address, additional_headers={"Cookie": cookie, "Origin": self.url},
                     open_timeout=60) as call:
            ready = json.loads(call.recv(timeout=60))
            self.assertEqual(ready["type"], "ready")
            self.assertFalse(ready["voice"])
            opening = json.loads(call.recv(timeout=10))
            self.assertEqual(opening["type"], "say")
            self.assertTrue(opening["text"].startswith("Hi, I'm Stellis."))

    def test_coach_and_tour_calls_relay_with_account_and_query(self) -> None:
        """Hosted calls retain parameters and enforce session and origin checks."""
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        with httpx.Client(base_url=self.url, timeout=30) as client:
            client.post("/api/v1/auth/register", json={
                "username": "caller", "password": "correct horse battery staple"}).raise_for_status()
            cookie = "stellis_session=" + client.cookies.get("stellis_session")
            today = date.today().isoformat()
            for path in (
                # Today, so the call opens with a greeting rather than a review of a past day.
                f"/api/v1/coach/call?kind=call&today={today}&now=10:00&utc_offset=0",
                f"/api/v1/coach/call?kind=morning&today={today}&now=10:00&utc_offset=0",
                "/api/v1/tour/call",
            ):
                with self.subTest(path=path):
                    address = self.url.replace("http", "ws") + path
                    with self.assertRaises(InvalidStatus):
                        connect(address, open_timeout=10).close()
                    with self.assertRaises(InvalidStatus):
                        connect(address, additional_headers={
                            "Cookie": cookie, "Origin": "https://example.com"},
                            open_timeout=10).close()
                    with connect(address, additional_headers={
                            "Cookie": cookie, "Origin": self.url}, open_timeout=60) as call:
                        ready = json.loads(call.recv(timeout=60))
                        self.assertEqual(ready["type"], "ready")
                        self.assertFalse(ready["voice"])
                        opening = json.loads(call.recv(timeout=10))
                        if path == "/api/v1/tour/call":
                            self.assertEqual(opening["type"], "action")
                        # Tours and check-ins may send actions (stage, agenda) before speaking.
                        while opening["type"] == "action":
                            opening = json.loads(call.recv(timeout=10))
                        self.assertEqual(opening["type"], "say")
                        if "kind=call" in path:
                            self.assertIn("Good morning", opening["text"])
                        elif "kind=morning" in path:
                            self.assertIn("check-in", opening["text"])
