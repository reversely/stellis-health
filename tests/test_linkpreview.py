"""Link preview parsing, address refusal, and the cache."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from stellis import storage
from stellis.linkpreview import parse_preview
from stellis.server import app


class ParsePreviewTest(unittest.TestCase):
    def test_open_graph_wins_and_relative_urls_resolve(self):
        html = """<html><head><title>Plain</title>
        <meta name="description" content="Plain description">
        <meta name="twitter:title" content="Twitter title">
        <meta property="og:title" content="OG title">
        <meta property="og:description" content="OG description">
        <meta property="og:image" content="/img/card.png">
        <meta property="og:site_name" content="Example">
        <link rel="shortcut icon" href="icons/site.png"></head></html>"""
        card = parse_preview(html, "https://example.com/a/b")
        self.assertEqual(card["title"], "OG title")
        self.assertEqual(card["description"], "OG description")
        self.assertEqual(card["image_url"], "https://example.com/img/card.png")
        self.assertEqual(card["site_name"], "Example")
        self.assertEqual(card["icon_url"], "https://example.com/a/icons/site.png")

    def test_fallbacks_and_caps(self):
        html = ('<title>' + "t" * 400 + '</title><meta name="twitter:description" content="x">'
                '<meta name="description" content="y"><meta name="twitter:image" content="https://cdn.test/i.jpg">')
        card = parse_preview(html, "https://example.com/")
        self.assertEqual(len(card["title"]), 300)
        self.assertEqual(card["description"], "x")
        self.assertEqual(card["image_url"], "https://cdn.test/i.jpg")
        self.assertEqual(card["icon_url"], "https://example.com/favicon.ico")
        self.assertEqual(len(parse_preview(f'<meta name="description" content="{"d" * 700}">', "https://e.com/")["description"]), 600)


class LinkPreviewRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name, "STELLIS_AUTH_MODE": "open"})
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_private_addresses_are_refused(self):
        for url in ("http://127.0.0.1/", "http://10.0.0.1/", "http://localhost/"):
            response = self.client.post("/api/v1/link-previews", json={"url": url})
            self.assertEqual(response.status_code, 400, url)
            self.assertEqual(response.json()["detail"], "This address can't be previewed")

    def test_malformed_and_non_http_urls_are_422(self):
        for url in ("not a url", "ftp://example.com/", "file:///etc/passwd", "https://"):
            self.assertEqual(self.client.post("/api/v1/link-previews", json={"url": url}).status_code, 422, url)

    def test_fresh_cached_row_is_served_without_fetching(self):
        fetched = datetime.now(timezone.utc) - timedelta(days=2)
        with storage.connect() as conn:
            conn.execute("INSERT INTO link_previews (url, final_url, title, status, fetched_at) VALUES (?, ?, ?, 'ok', ?)",
                         ("https://example.com/page", "https://example.com/final", "Cached", fetched.isoformat()))
        with patch("stellis.linkpreview._fetch", side_effect=AssertionError("fetched")):
            response = self.client.post("/api/v1/link-previews", json={"url": "https://example.com/page#section"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Cached")
        self.assertEqual(response.json()["url"], "https://example.com/page")
        self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
