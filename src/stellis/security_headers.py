"""Response headers that limit what a page may load, embed or send."""

from __future__ import annotations

import base64
import hashlib
import pathlib
import re

_STATIC = pathlib.Path(__file__).parent / "static"
# Video hosts that note embeds (notes-embeds.js) may frame.
_FRAME_HOSTS = ("https://www.youtube-nocookie.com https://player.vimeo.com "
                "https://www.loom.com")
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def _inline_script_hashes() -> list[str]:
    """Hashes each inline script in the HTML pages so the policy allows them.

    Returns:
        Sorted CSP source expressions such as ``'sha256-...'``.
    """
    hashes = set()
    for page in sorted(_STATIC.glob("*.html")):
        for body in _INLINE_SCRIPT.findall(page.read_text()):
            digest = hashlib.sha256(body.encode()).digest()
            hashes.add(f"'sha256-{base64.b64encode(digest).decode()}'")
    return sorted(hashes)


CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    # WebAssembly compilation for in-browser camera vitals (vitallens).
    "script-src 'self' 'wasm-unsafe-eval' " + " ".join(_inline_script_hashes()),
    "style-src 'self'",
    "img-src 'self' data: blob: https:",
    "media-src 'self' blob:",
    # data: lets vitallens load its embedded WebAssembly; it reaches no host.
    "connect-src 'self' data:",
    "font-src 'self'",
    f"frame-src 'self' {_FRAME_HOSTS}",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))


def apply(headers, https: bool) -> None:
    """Sets the security headers on a response unless it already has them.

    Args:
        headers: The response's mutable header mapping.
        https: Whether the site is served over HTTPS; only then is HSTS sent.
    """
    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "same-origin")
    headers.setdefault("Permissions-Policy",
                       "microphone=(self), camera=(self), geolocation=()")
    if https:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
