"""Tests for sign-in and sign-up limits and their 429 responses."""

import os
import tempfile
import unittest
from unittest import mock

from fastapi import testclient

from stellis import rate_limit
from stellis import server


class SlidingWindowTest(unittest.TestCase):

    def test_window_allows_the_limit_then_reports_the_wait(self):
        now = [100.0]
        window = rate_limit.SlidingWindow(2, 60, clock=lambda: now[0])
        self.assertEqual(window.retry_after("key"), 0)
        window.record("key")
        window.record("key")
        self.assertEqual(window.retry_after("key"), 60)
        self.assertEqual(window.retry_after("other"), 0)
        now[0] = 130.0
        self.assertEqual(window.retry_after("key"), 30)
        now[0] = 160.0
        self.assertEqual(window.retry_after("key"), 0)
        window.record("key")
        window.clear("key")
        self.assertEqual(window.retry_after("key"), 0)


class AuthLimitTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {
                "STELLIS_DATA_DIR": self.temp.name,
                "STELLIS_AUTH_MODE": "required",
                "STELLIS_DEV_ACCOUNT": "0",
                "STELLIS_DEV_PASSWORD": ""
            })
        self.env.start()
        self.client = testclient.TestClient(server.app, follow_redirects=False)
        self.password = "correct horse battery staple"

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    @mock.patch.object(rate_limit.LOGIN_FAILURES_BY_USERNAME, "_clock", lambda: 100.0)
    @mock.patch.object(rate_limit.LOGIN_FAILURES_BY_CLIENT, "_clock", lambda: 100.0)
    def test_failed_sign_ins_pause_the_username_even_for_the_right_password(
            self):
        self.client.post("/api/v1/auth/register",
                         json={
                             "username": "Riley",
                             "password": self.password
                         })
        for _ in range(5):
            self.assertEqual(
                self.client.post("/api/v1/auth/login",
                                 json={
                                     "username": "Riley",
                                     "password": "wrong password"
                                 }).status_code, 401)
        paused = self.client.post("/api/v1/auth/login",
                                  json={
                                      "username": "riley",
                                      "password": self.password
                                  })
        self.assertEqual(paused.status_code, 429)
        self.assertEqual(
            paused.json()["detail"],
            "Sign-in is paused for 15 minutes after too many attempts")
        self.assertEqual(paused.headers["retry-after"], "900")

    def test_failed_sign_ins_across_usernames_pause_the_client(self):
        for index in range(20):
            self.client.post("/api/v1/auth/login",
                             json={
                                 "username": f"guess{index}",
                                 "password": "wrong password"
                             })
        self.assertEqual(
            self.client.post("/api/v1/auth/login",
                             json={
                                 "username": "fresh",
                                 "password": "wrong password"
                             }).status_code, 429)

    def test_successful_sign_in_resets_the_username_count(self):
        self.client.post("/api/v1/auth/register",
                         json={
                             "username": "Riley",
                             "password": self.password
                         })
        for _ in range(4):
            self.client.post("/api/v1/auth/login",
                             json={
                                 "username": "Riley",
                                 "password": "wrong password"
                             })
        self.assertEqual(
            self.client.post("/api/v1/auth/login",
                             json={
                                 "username": "Riley",
                                 "password": self.password
                             }).status_code, 200)
        for _ in range(4):
            self.assertEqual(
                self.client.post("/api/v1/auth/login",
                                 json={
                                     "username": "Riley",
                                     "password": "wrong password"
                                 }).status_code, 401)

    def test_sign_ups_per_client_are_capped(self):
        for index in range(5):
            response = self.client.post("/api/v1/auth/register",
                                        json={
                                            "username": f"person{index}",
                                            "password": self.password
                                        })
            self.assertEqual(response.status_code, 200, response.text)
            self.client.cookies.clear()
        refused = self.client.post("/api/v1/auth/register",
                                   json={
                                       "username": "person6",
                                       "password": self.password
                                   })
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(
            refused.json()["detail"],
            "Sign-up is paused for 60 minutes after too many attempts")


if __name__ == "__main__":
    unittest.main()
