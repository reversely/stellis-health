"""Tests for the security headers every response carries."""

import unittest

from stellis import security_headers


class SecurityHeadersTest(unittest.TestCase):

    def test_camera_vitals_run_locally_without_new_hosts(self):
        headers = {}

        security_headers.apply(headers, https=False)

        policy = dict(
            part.split(" ", 1)
            for part in headers["Content-Security-Policy"].split("; "))
        self.assertIn("'wasm-unsafe-eval'", policy["script-src"])
        self.assertNotIn("'unsafe-eval'", policy["script-src"])
        self.assertEqual(policy["connect-src"], "'self' data:")
        self.assertIn("camera=(self)", headers["Permissions-Policy"])
        self.assertNotIn("Strict-Transport-Security", headers)


if __name__ == "__main__":
    unittest.main()
