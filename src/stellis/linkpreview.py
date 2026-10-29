"""Device-side link previews for bookmark cards (ADR 0058)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from stellis import storage

OK_TTL = timedelta(days=7)
FAILED_TTL = timedelta(days=1)
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 8
TITLE_LIMIT = 300
DESCRIPTION_LIMIT = 600
REFUSED = "This address can't be previewed"
FIELDS = ("url", "final_url", "title", "description", "site_name", "image_url", "icon_url", "status")


class AddressRefused(Exception):
    """The host resolves to an address that must not be contacted."""


def normalize_url(raw: str) -> str:
    """Return the URL without its fragment, or raise ValueError when it is not http(s)."""
    url = urldefrag(raw.strip())[0]
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError as exc:
        raise ValueError("Enter a valid http or https address") from exc
    if parts.scheme not in ("http", "https") or not hostname:
        raise ValueError("Enter a valid http or https address")
    return url


def _http_url(base_url: str, value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    resolved = urljoin(base_url, value.strip())
    return resolved if urlsplit(resolved).scheme in ("http", "https") else None


class _PreviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.icon: str | None = None
        self.title_parts: list[str] = []
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "title" and not self._title_done:
            self._in_title = True
        elif tag == "meta" and attributes.get("content"):
            key = (attributes.get("property") or attributes.get("name") or "").strip().lower()
            if key:
                self.meta.setdefault(key, attributes["content"])
        elif tag == "link" and self.icon is None and attributes.get("href"):
            relations = set(attributes.get("rel", "").lower().split())
            if relations & {"icon", "apple-touch-icon"}:
                self.icon = attributes["href"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)


def _first(meta: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = " ".join(meta.get(key, "").split())
        if value:
            return value
    return None


def parse_preview(html: str, base_url: str) -> dict[str, str | None]:
    """Extract card data from HTML; relative URLs resolve against base_url."""
    parser = _PreviewParser()
    parser.feed(html)
    parser.close()
    title = _first(parser.meta, "og:title", "twitter:title") or " ".join("".join(parser.title_parts).split()) or None
    description = _first(parser.meta, "og:description", "twitter:description", "description")
    return {
        "title": title[:TITLE_LIMIT] if title else None,
        "description": description[:DESCRIPTION_LIMIT] if description else None,
        "site_name": _first(parser.meta, "og:site_name"),
        "image_url": _http_url(base_url, _first(parser.meta, "og:image", "twitter:image")),
        "icon_url": _http_url(base_url, parser.icon or "/favicon.ico"),
    }


def _check_host(url: str) -> None:
    """Resolve the host and refuse it when any address is not public."""
    parts = urlsplit(url)
    infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                               type=socket.SOCK_STREAM)
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        # An IPv4-mapped IPv6 address is judged as the IPv4 address it wraps.
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast
                or address.is_reserved or address.is_unspecified):
            raise AddressRefused(REFUSED)


async def _fetch(url: str) -> dict[str, str | None]:
    """Fetch url, following up to five redirects by hand with a host check before each request."""
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False,
                                 headers={"User-Agent": "Stellis link preview"}) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await asyncio.to_thread(_check_host, current)
            async with client.stream("GET", current) as response:
                location = response.headers.get("location")
                if response.status_code in (301, 302, 303, 307, 308) and location:
                    current = urldefrag(urljoin(current, location))[0]
                    if urlsplit(current).scheme not in ("http", "https"):
                        raise ValueError("Redirect to an unsupported address")
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind != "text/html":
                    return {"final_url": current}
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) >= MAX_BYTES:
                        break
                text = bytes(body[:MAX_BYTES]).decode(response.charset_encoding or "utf-8", errors="replace")
                return {"final_url": current, **parse_preview(text, current)}
    raise ValueError("Too many redirects")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cached(url: str) -> dict[str, str | None] | None:
    with storage.connect() as conn:
        row = conn.execute("SELECT * FROM link_previews WHERE url = ?", (url,)).fetchone()
    if row is None:
        return None
    age = _now() - datetime.fromisoformat(row["fetched_at"])
    if age >= (OK_TTL if row["status"] == "ok" else FAILED_TTL):
        return None
    return {field: row[field] for field in FIELDS}


def _store(url: str, data: dict[str, str | None], status: str, error: str | None) -> None:
    with storage.connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO link_previews
               (url, final_url, title, description, site_name, image_url, icon_url, status, error, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, data.get("final_url"), data.get("title"), data.get("description"), data.get("site_name"),
             data.get("image_url"), data.get("icon_url"), status, error, _now().isoformat()))


async def preview(raw_url: str) -> dict[str, str | None]:
    """Return the card data for a URL: cached when fresh, otherwise fetched and stored.

    Raises ValueError for a malformed or non-http(s) URL and AddressRefused for a blocked address.
    """
    url = normalize_url(raw_url)
    cached = await asyncio.to_thread(_cached, url)
    if cached is not None:
        return cached
    try:
        data = await _fetch(url)
        status, error = "ok", None
    except AddressRefused:
        raise
    except (httpx.HTTPError, OSError, ValueError) as exc:
        data, status, error = {}, "failed", str(exc) or type(exc).__name__
    await asyncio.to_thread(_store, url, data, status, error)
    return {field: (url if field == "url" else status if field == "status" else data.get(field)) for field in FIELDS}
