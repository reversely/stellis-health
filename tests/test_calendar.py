"""OAuth boundaries and read-only calendar integration checks."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from stellis import calendar, notebooks
from stellis.server import app


class CalendarTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "STELLIS_DATA_DIR": self.temp.name,
            "STELLIS_GOOGLE_CALENDAR_CLIENT_ID": "google-test-id",
            "STELLIS_MICROSOFT_CALENDAR_CLIENT_ID": "microsoft-test-id",
            "STELLIS_AUTH_MODE": "off",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_authorization_state_and_loopback_redirect(self):
        for provider in calendar.PROVIDERS:
            url = calendar.authorize(provider, 8765, "http://127.0.0.1:8765")
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertEqual(query["redirect_uri"], [calendar.redirect_uri(provider, 8765)])
            if provider == "google":
                self.assertEqual(set(query["scope"][0].split()), {
                    "https://www.googleapis.com/auth/calendar.events.readonly",
                    "https://www.googleapis.com/auth/calendar.app.created"})
            else:
                self.assertEqual(query["scope"], ["offline_access Calendars.ReadBasic"])
            state = query["state"][0]
            with self.assertRaises(calendar.CalendarError):
                calendar.consume_state("microsoft" if provider == "google" else "google", state)
            calendar.consume_state(provider, state)
            with self.assertRaises(calendar.CalendarError):
                calendar.consume_state(provider, state)
        with TestClient(app, base_url="http://example.com:8765") as client:
            self.assertEqual(client.get("/api/v1/calendar/google/authorize").status_code, 400)

    def test_hosted_app_without_public_origin_reports_calendar_unavailable(self):
        with TestClient(app, base_url="https://health.example") as client:
            rows = client.get("/api/v1/calendar/connections").json()
            self.assertEqual([row["connectable"] for row in rows], [False, False])
            response = client.get("/api/v1/calendar/google/authorize?return_to=/onboarding")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "Calendar connection is not available on the web app yet")
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            self.assertTrue(all(row["connectable"] for row in client.get("/api/v1/calendar/connections").json()))

    def test_public_origin_redirects_to_the_hosted_callback(self):
        with patch.dict(os.environ, {"STELLIS_PUBLIC_ORIGIN": "https://health.example/"}), \
                TestClient(app, base_url="https://health.example") as client:
            self.assertTrue(client.get("/api/v1/calendar/connections").json()[0]["connectable"])
            url = client.get("/api/v1/calendar/google/authorize?return_to=/onboarding").json()["url"]
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["redirect_uri"], ["https://health.example/api/v1/calendar/google/callback"])
            _, uri, return_origin = calendar.consume_state("google", query["state"][0])
            self.assertEqual((uri, return_origin), ("https://health.example/api/v1/calendar/google/callback",
                                                    "https://health.example/onboarding"))
        for invalid in ("http://health.example", "https://health.example/app", "health.example"):
            with self.subTest(origin=invalid), patch.dict(os.environ, {"STELLIS_PUBLIC_ORIGIN": invalid}), \
                    self.assertRaises(calendar.CalendarError):
                calendar.public_origin()

    async def test_google_disconnect_revokes_shared_grant(self):
        calendar._save("google", {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600})
        original = httpx.AsyncClient
        def handler(request):
            self.assertEqual(str(request.url), "https://oauth2.googleapis.com/revoke")
            self.assertIn(b"token=refresh", request.content)
            return httpx.Response(200)
        with patch.object(calendar.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await calendar.disconnect_grant("google")
        self.assertFalse(next(row for row in calendar.connections() if row["provider"] == "google")["connected"])

    def test_existing_google_oauth_configuration_is_reused(self):
        values = {"GOOGLE_OAUTH_CLIENT_ID":"existing-app-id",
                  "GOOGLE_OAUTH_CLIENT_SECRET":"existing-app-secret"}
        with patch.object(calendar, "_setting", side_effect=lambda name: values.get(name)):
            self.assertEqual(calendar.client_id("google"), "existing-app-id")
            self.assertEqual(calendar._google_secret(), "existing-app-secret")
        values["STELLIS_GOOGLE_CALENDAR_CLIENT_ID"] = "desktop-id"
        with patch.object(calendar, "_setting", side_effect=lambda name: values.get(name)):
            self.assertEqual(calendar.client_id("google"), "desktop-id")
            self.assertIsNone(calendar._google_secret())

    def test_provider_fields_and_notebook_assignment(self):
        book = notebooks.create("Water Treatment")["id"]
        google = calendar._google_event({"id":"g1", "recurringEventId":"series-g", "summary":"Water Treatment review",
            "attendees":[{"email":"a@example.com"},{"email":"b@example.com"}],
            "start":{"dateTime":"2026-10-18T10:00:00Z"}, "end":{"dateTime":"2026-10-18T11:00:00Z"}})
        microsoft = calendar._microsoft_event({"id":"m1", "seriesMasterId":"series-m", "subject":"Water Treatment review",
            "attendees":[{"emailAddress":{"address":"a@example.com"}}, {"emailAddress":{"address":"b@example.com"}}],
            "start":{"dateTime":"2026-10-18T10:00:00Z"}, "end":{"dateTime":"2026-10-18T11:00:00Z"}})
        self.assertEqual(google["attendees"], microsoft["attendees"])
        self.assertEqual(google["series_id"], "series-g")
        self.assertEqual(microsoft["series_id"], "series-m")
        self.assertEqual(calendar.assign_notebook(google)["notebook_id"], book)  # title contains notebook name
        calendar.save_notebook_assignment(google, book, "series")
        next_google = {**google, "id":"g2", "title":"Weekly project review"}
        self.assertEqual(calendar.assign_notebook(next_google)["assignment_source"], "series")
        calendar.save_notebook_assignment(next_google, None, "event")
        self.assertIsNone(calendar.assign_notebook(next_google)["notebook_id"])
        self.assertEqual(calendar.assign_notebook(next_google)["assignment_source"], "event")
        calendar.save_notebook_assignment(next_google, book, "series")
        self.assertEqual(calendar.assign_notebook(next_google)["notebook_id"], book)
        self.assertEqual(calendar.assign_notebook(next_google)["assignment_source"], "series")
        calendar.save_notebook_assignment(microsoft, book, "event")
        next_microsoft = {**microsoft, "id":"m2", "series_id":"", "title":"Supplier call"}
        self.assertEqual(calendar.assign_notebook(next_microsoft)["assignment_source"], "attendees")

    async def test_exchange_refresh_events_and_disconnect(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/token"):
                if b"grant_type=refresh_token" in request.content:
                    return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
                return httpx.Response(200, json={"access_token": "access", "refresh_token": "refresh", "expires_in": 60})
            if "googleapis" in request.url.host:
                self.assertEqual(request.headers["Authorization"], "Bearer new-access")
                if "pageToken" not in request.url.params:
                    return httpx.Response(200, json={"items": [{"id":"a","recurringEventId":"series-a","summary":"Planning","attendees":[{"email":"A@example.com"}],"start":{"dateTime":"2026-10-17T10:00:00Z"},"end":{"dateTime":"2026-10-17T11:00:00Z"}}],"nextPageToken":"next"})
                return httpx.Response(200, json={"items": [{"id":"b","summary":"Day off","start":{"date":"2026-10-18"},"end":{"date":"2026-10-19"}}]})
            return httpx.Response(404)

        original = httpx.AsyncClient
        with patch.object(calendar.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            await calendar.exchange("google", "code", "verifier", calendar.redirect_uri("google", 8765))
            with calendar._connect() as conn:
                conn.execute("UPDATE connections SET expires_at = 0 WHERE provider = 'google'")
            events = await calendar.events("google", datetime(2026, 10, 16, tzinfo=timezone.utc))
        self.assertEqual([event["title"] for event in events], ["Planning", "Day off"])
        self.assertEqual(events[0]["series_id"], "series-a")
        self.assertEqual(events[0]["attendees"], ["a@example.com"])
        self.assertEqual(events[1]["start"], "2026-10-18")
        self.assertTrue(events[1]["all_day"])
        self.assertEqual(len([call for call in calls if call.url.path.endswith("/token")]), 2)
        calendar.disconnect("google")
        self.assertFalse(next(row for row in calendar.connections() if row["provider"] == "google")["connected"])

    async def test_app_created_demo_events_appear_with_primary_events(self):
        calendar._save("google", {"access_token": "access", "refresh_token": "refresh",
                                  "expires_in": 3600, "scope": calendar._SCOPE["google"]})
        calendar.save_demo_calendar_id("demo@example.com")
        requested = []
        def handler(request):
            requested.append(request.url.path)
            title = "Sample — Water Treatment RFI 91 triage" if "demo@example.com" in request.url.path else "Primary meeting"
            return httpx.Response(200, json={"items": [{"id": title, "summary": title,
                "start": {"dateTime": "2026-10-20T10:00:00Z"},
                "end": {"dateTime": "2026-10-20T10:30:00Z"}}]})
        original = httpx.AsyncClient
        with patch.object(calendar.httpx, "AsyncClient",
                          side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            found = await calendar.events("google", datetime(2026, 10, 19, tzinfo=timezone.utc))
        self.assertEqual(len(found), 2)
        self.assertTrue(any("demo@example.com" in path for path in requested))
        calendar.disconnect("google")
        self.assertIsNone(calendar.demo_calendar_id())

    async def test_microsoft_pagination_and_untrusted_next_link(self):
        calendar._save("microsoft", {"access_token":"access","refresh_token":"refresh","expires_in":3600})

        def handler(request):
            if "skiptoken" in request.url.params:
                return httpx.Response(200, json={"value":[{"id":"two","subject":"Review","start":{"dateTime":"2026-10-18T12:00:00"},"end":{"dateTime":"2026-10-18T13:00:00"}}]})
            return httpx.Response(200, json={"value":[{"id":"one","seriesMasterId":"series-one","subject":"Meet","attendees":[{"emailAddress":{"address":"B@example.com"}}],"start":{"dateTime":"2026-10-17T12:00:00"},"end":{"dateTime":"2026-10-17T13:00:00"}}],
                "@odata.nextLink":"https://graph.microsoft.com/v1.0/me/calendar/calendarView?skiptoken=next"})

        original = httpx.AsyncClient
        with patch.object(calendar.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            events = await calendar.events("microsoft", datetime(2026, 10, 16, tzinfo=timezone.utc))
        self.assertEqual([item["title"] for item in events], ["Meet", "Review"])
        self.assertEqual(events[0]["series_id"], "series-one")
        self.assertEqual(events[0]["attendees"], ["b@example.com"])
        with patch.object(calendar.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"value":[],"@odata.nextLink":"https://evil.example/steal"})), **kw)):
            with self.assertRaises(calendar.CalendarError):
                await calendar.events("microsoft")


class CalendarRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name,
            "STELLIS_GOOGLE_CALENDAR_CLIENT_ID":"google-test-id", "STELLIS_AUTH_MODE":"off"})
        self.env.start()
        self.client = TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_authorize_callback_and_empty_meetings(self):
        authorization = self.client.get("/api/v1/calendar/google/authorize")
        self.assertEqual(authorization.status_code, 200)
        state = parse_qs(urlparse(authorization.json()["url"]).query)["state"][0]
        response = self.client.get("/api/v1/calendar/google/callback", params={"state":state,"error":"access_denied"})
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("http://127.0.0.1:8765/?calendar=failed&connection_error="))
        self.assertEqual(self.client.get("/api/v1/calendar/meetings").json()["meetings"], [])
        from_settings = self.client.get("/api/v1/calendar/google/authorize", params={"return_to": "/settings"})
        state = parse_qs(urlparse(from_settings.json()["url"]).query)["state"][0]
        response = self.client.get("/api/v1/calendar/google/callback", params={"state":state,"error":"access_denied"})
        self.assertTrue(response.headers["location"].startswith("http://127.0.0.1:8765/settings?calendar=failed&connection_error="))
        self.assertEqual(self.client.get("/api/v1/calendar/google/authorize",
                                         params={"return_to": "https://evil.example"}).status_code, 422)

    def test_calendar_routes_keep_local_login_boundary(self):
        with patch.dict(os.environ, {"STELLIS_AUTH_MODE":"required", "STELLIS_DEV_ACCOUNT":"0"}):
            self.assertEqual(self.client.get("/api/v1/calendar/connections").status_code, 401)
            self.assertEqual(self.client.get("/api/v1/calendar/google/authorize").status_code, 401)
            self.assertEqual(self.client.get("/api/v1/calendar/meetings").status_code, 401)
            self.assertEqual(self.client.post("/api/v1/calendar/google/disconnect").status_code, 401)
            self.assertEqual(self.client.get("/api/v1/calendar/google/callback").status_code, 303)

    def test_assigned_event_flows_into_recording(self):
        book = notebooks.create("Water Treatment")["id"]
        event = {"id":"fixture-event", "provider":"google", "series_id":"fixture-series",
                 "title":"Project review", "attendees":["a@example.com", "b@example.com"],
                 "start":"2026-10-19T10:00:00+00:00", "end":"2026-10-19T11:00:00+00:00", "all_day":False}
        saved = self.client.put("/api/v1/calendar/notebook", json={"provider":"google", "event_id":event["id"],
            "series_id":event["series_id"], "title":event["title"], "attendees":event["attendees"],
            "notebook_id":book, "scope":"series"})
        self.assertEqual(saved.status_code, 200)
        async def events(provider):
            return [event]
        with patch.object(calendar, "connections", return_value=[{"provider":"google", "connected":True}]), \
             patch.object(calendar, "events", side_effect=events):
            meeting = self.client.get("/api/v1/calendar/meetings").json()["meetings"][0]
        self.assertEqual(meeting["notebook_id"], book)
        session = self.client.post("/api/v1/recordings", json={"title":meeting["title"],
            "media_type":"audio/webm", "notebook_id":meeting["notebook_id"],
            "calendar_provider":meeting["provider"], "calendar_event_id":meeting["id"]}).json()
        self.assertEqual(session["notebook_id"], book)


class EventNotesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "off"})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_notes_before_a_meeting_start_its_recording_notes(self):
        event = {"provider": "microsoft", "event_id": "AAMk/abc+="}
        self.assertEqual(self.client.get("/api/v1/calendar/notes", params=event).json()["revision"], 0)
        saved = self.client.put("/api/v1/calendar/notes", json={**event, "markdown": "- Ask about budget", "expected_revision": 0})
        self.assertEqual(saved.json()["revision"], 1)
        stale = self.client.put("/api/v1/calendar/notes", json={**event, "markdown": "older", "expected_revision": 0})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get("/api/v1/calendar/notes", params={"provider": "yahoo", "event_id": "x"}).status_code, 400)

        connected = [{"provider": "microsoft", "connected": True}, {"provider": "google", "connected": False}]
        meeting = {"id": event["event_id"], "provider": "microsoft", "title": "Budget", "start": "2026-10-18T10:00:00+00:00",
                   "end": "2026-10-18T11:00:00+00:00", "all_day": False}
        other = {**meeting, "id": "other"}

        async def events(provider):
            return [meeting, other]
        with patch.object(calendar, "connections", return_value=connected), patch.object(calendar, "events", side_effect=events):
            flags = {item["id"]: item["has_notes"] for item in self.client.get("/api/v1/calendar/meetings").json()["meetings"]}
        self.assertEqual(flags, {event["event_id"]: True, "other": False})

        session = self.client.post("/api/v1/recordings", json={"title": "Budget", "media_type": "audio/webm",
                                                                "calendar_provider": "microsoft",
                                                                "calendar_event_id": event["event_id"]}).json()
        notes = self.client.get(f"/api/v1/recordings/{session['id']}/notes").json()
        self.assertEqual(notes["markdown"], "- Ask about budget")
        # The notes moved: the meeting starts empty again and is no longer marked.
        self.assertEqual(self.client.get("/api/v1/calendar/notes", params=event).json()["markdown"], "")
