"""Placeholders for direct identifiers in every request to a hosted model, restored in its replies.

Before text leaves for Token Factory, names (from the account, setup answers, saved people and
"Dr Vale" or "Patient: Maya Chen" in the text), emails, phone numbers, labelled record and health
card numbers and labelled birth dates become placeholders such as "[PERSON_1]". The reply's
placeholders are put back before any Stellis code reads it, including streamed replies where a
placeholder arrives split across chunks. Clinical dates and values stay, because answers need them,
so this is pseudonymization of direct identifiers rather than full de-identification.

Requests to a loopback model (local mode) are left unchanged: they never leave the device.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
from typing import Callable, Iterator, AsyncIterator

import httpx

_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ'’-]+")
_TITLED = re.compile(r"\b(?:Dr|Doctor|Mr|Mrs|Ms|Mx|Prof|Nurse|Midwife)\.?\s+((?:[A-Z][\w'’-]+)(?:\s+[A-Z][\w'’-]+)?)")
_LABELLED_NAME = re.compile(r"\b(?:Patient|Name|Patient name|Provider|Physician|Clinician)\s*[:|/]\s*((?:[A-Z][\w'’-]+)(?:\s+[A-Z][\w'’-]+){0,2})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<![\w.])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w.])")
_ID = re.compile(r"\b((?:MRN|Medical record(?: number)?|Record(?: no\.?| number| #)?|Patient ID|Health card(?: number)?|"
                 r"OHIP|Insurance(?: ID| number)?|Member ID|Chart(?: number)?)\s*[:#|/]?\s*)"
                 # An identifier holds a digit, so prose such as "a record that fits" keeps its words.
                 r"((?=[A-Z-]*\d)[A-Z0-9][A-Z0-9-]{3,})", re.I)
_BIRTH = re.compile(r"\b((?:DOB|Date of birth|Birth date|Born)(?:\s*/\s*age)?\s*[:|/]?\s*)"
                    r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})")
_PLACEHOLDER = re.compile(r"\[(?:PERSON|EMAIL|PHONE|ID|BIRTH_DATE)_\d+\]")
# A reply chunk ending in what may be the start of a placeholder waits for the next chunk.
_PARTIAL = re.compile(r"\[[A-Z_]*\d*$")
# Words that name the medicine or the care, not the person, even after a title.
_NOT_NAMES = frozenset({"the", "and", "for", "with", "results", "result", "report", "office", "clinic"})


class Pseudonyms:
    """One request's identifiers and their placeholders, in both directions."""

    def __init__(self, names: list[str]) -> None:
        self.forward: dict[str, str] = {}
        self.back: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        # Whole names first, then their single words, so "Maya Chen" and a later "Maya" share a person.
        # Equal lengths sort by spelling: set order changes per process, and the numbering must not, or
        # one question hashes differently for every demo visitor and never matches its stored reply.
        def order(name: str) -> tuple[int, str, str]:
            return -len(name), name.casefold(), name
        full = sorted({n.strip() for n in names if len(n.strip()) >= 3}, key=order)
        self.names = full + sorted({w for n in full for w in _WORD.findall(n)
                                    if len(w) >= 3 and w.casefold() not in _NOT_NAMES}, key=order)

    def _token(self, kind: str, value: str) -> str:
        key = kind + ":" + value.casefold()
        if key not in self.forward:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            token = f"[{kind}_{self.counts[kind]}]"
            self.forward[key], self.back[token] = token, value
        return self.forward[key]

    def redact(self, text: str) -> str:
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _BIRTH.sub(lambda m: m.group(1) + self._token("BIRTH_DATE", m.group(2)), text)
        text = _ID.sub(lambda m: m.group(1) + self._token("ID", m.group(2)), text)
        text = _PHONE.sub(lambda m: self._token("PHONE", m.group(0)), text)
        for name in self.names:
            pattern = re.compile(r"(?<![\w\[])" + re.escape(name) + r"(?![\w\]])", re.I)
            text = pattern.sub(lambda m: self._person(m.group(0)), text)
        return text

    def _person(self, surface: str) -> str:
        # A first name alone maps to the full name's placeholder when one was already seen.
        for key, token in self.forward.items():
            if key.startswith("PERSON:") and surface.casefold() in key[7:].split():
                return token
        return self._token("PERSON", surface)

    def restore(self, text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: self.back.get(m.group(0), m.group(0)), text)


def known_names() -> list[str]:
    """Names the account itself knows: profile, setup answers and saved people."""
    import os
    names: list[str] = []
    try:
        from stellis import auth
        account = auth.profile(int(os.environ.get("STELLIS_ACCOUNT_ID", "1")))
        names += [str(account.get("name") or ""), str(account.get("username") or "")]
    except Exception:
        pass
    try:
        from stellis import onboarding_profile
        names.append(str(onboarding_profile.status()["answers"].get("name") or ""))
    except Exception:
        pass
    try:
        from stellis import people
        names += [str(person["name"]) for person in people.list_people()]
    except Exception:
        pass
    return [name for name in names if name.strip()]


def _texts(value) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _texts(item)


def _map_strings(value, change: Callable[[str], str], skip: frozenset[str] = frozenset()):
    if isinstance(value, str):
        return change(value)
    if isinstance(value, dict):
        return {key: item if key in skip else _map_strings(item, change, skip) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, change, skip) for item in value]
    return value


# Request fields that are configuration, never text about the person.
_SETTINGS = frozenset({"model", "role", "type", "tool_choice", "response_format", "name", "id", "tool_call_id"})


def pseudonymize(payload: dict, names: list[str] | None = None) -> tuple[dict, Pseudonyms]:
    """The payload with identifiers replaced, and the mapping that restores them."""
    found = list(names if names is not None else known_names())
    for text in _texts(payload.get("messages") or payload.get("input") or []):
        found += [m.group(1) for m in _TITLED.finditer(text)] + [m.group(1) for m in _LABELLED_NAME.finditer(text)]
    mapping = Pseudonyms(found)
    redacted = {key: (value if key in _SETTINGS or key in {"tools", "functions"}
                      else _map_strings(value, mapping.redact, _SETTINGS))
                for key, value in payload.items()}
    return redacted, mapping


def _local(url: httpx.URL) -> bool:
    host = url.host
    if host in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _prepare(request: httpx.Request) -> tuple[httpx.Request, Pseudonyms | None]:
    if (request.method != "POST" or _local(request.url)
            or not request.url.path.endswith(("/chat/completions", "/responses"))):
        return request, None
    try:
        payload = json.loads(request.content)
    except (ValueError, UnicodeDecodeError):
        return request, None
    redacted, mapping = pseudonymize(payload)
    if not mapping.back:
        return request, None
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"content-length", "accept-encoding"}}
    headers["accept-encoding"] = "identity"  # Replies are rewritten, so they must arrive uncompressed.
    return httpx.Request(request.method, request.url, headers=headers,
                         content=json.dumps(redacted).encode(), extensions=request.extensions), mapping


def _restored_headers(response: httpx.Response) -> list[tuple[str, str]]:
    return [(k, v) for k, v in response.headers.items() if k.lower() not in {"content-length", "content-encoding"}]


class _Stream:
    """Restores placeholders in server-sent events, holding back a placeholder split across chunks."""

    def __init__(self, mapping: Pseudonyms) -> None:
        self.mapping, self.carry, self.buffer, self.last = mapping, {}, b"", None

    def _text(self, path: str, value: str) -> str:
        text = self.carry.pop(path, "") + value
        partial = _PARTIAL.search(text)
        if partial:
            self.carry[path], text = text[partial.start():], text[:partial.start()]
        return self.mapping.restore(text)

    def _event(self, event: dict) -> list[dict]:
        out: list[dict] = []
        for index, choice in enumerate(event.get("choices") or []):
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                delta["content"] = self._text(f"choice:{index}", delta["content"])
            for call in delta.get("tool_calls") or []:
                function = call.get("function") or {}
                if isinstance(function.get("arguments"), str):
                    function["arguments"] = self._text(f"tool:{index}:{call.get('index', 0)}", function["arguments"])
            if choice.get("finish_reason"):
                if self.carry.get(f"choice:{index}"):
                    delta["content"] = (delta.get("content") or "") + self.mapping.restore(self.carry.pop(f"choice:{index}"))
                for path in [p for p in self.carry if p.startswith(f"tool:{index}:")]:
                    delta.setdefault("tool_calls", []).append(
                        {"index": int(path.rsplit(":", 1)[1]), "function": {"arguments": self.mapping.restore(self.carry.pop(path))}})
                choice["delta"] = delta
        kind = str(event.get("type") or "")
        if kind.endswith(".delta") and isinstance(event.get("delta"), str):
            event["delta"] = self._text(kind + str(event.get("item_id")), event["delta"])
            self.last = event
        elif kind.endswith(".done"):
            held = [(path, text) for path, text in self.carry.items() if path.startswith(kind[:-5] + ".delta")]
            for path, text in held:
                self.carry.pop(path)
                out.append({**(self.last or {"type": kind[:-5] + ".delta"}), "delta": self.mapping.restore(text)})
        out.append(_map_strings(event, self.mapping.restore) if not kind.endswith(".delta") and "choices" not in event else event)
        return out

    def feed(self, chunk: bytes) -> bytes:
        self.buffer += chunk
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        out = []
        for line in lines:
            if line.startswith(b"data:") and line[5:].strip() not in {b"", b"[DONE]"}:
                try:
                    events = self._event(json.loads(line[5:]))
                    for event in events[:-1]:
                        out += [b"data: " + json.dumps(event).encode(), b""]
                    out.append(b"data: " + json.dumps(events[-1]).encode())
                    continue
                except ValueError:
                    pass
            if line.startswith(b"data:") and line[5:].strip() == b"[DONE]" and self.carry:
                rest = "".join(self.mapping.restore(t) for t in self.carry.values())
                self.carry.clear()
                out.append(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": rest}}]}).encode())
                out.append(b"")
            out.append(line)
        return b"".join(part + b"\n" for part in out)

    def close(self) -> bytes:
        tail, self.buffer = self.buffer, b""
        return self.feed(tail + b"\n") if tail else b""


def _rewrite(response: httpx.Response, request: httpx.Request, mapping: Pseudonyms, content: bytes) -> httpx.Response:
    try:
        body = json.dumps(_map_strings(json.loads(content), mapping.restore)).encode()
    except (ValueError, UnicodeDecodeError):
        body = mapping.restore(content.decode("utf-8", "replace")).encode()
    return httpx.Response(response.status_code, headers=_restored_headers(response), content=body,
                          request=request, extensions=response.extensions)


def _raw(response: httpx.Response):
    """Body chunks as received; a response already read in full (as in tests) yields its content."""
    try:
        yield from response.iter_raw()
    except httpx.StreamConsumed:
        yield response.content


async def _araw(response: httpx.Response):
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    except httpx.StreamConsumed:
        yield response.content


class _SyncBody(httpx.SyncByteStream):
    def __init__(self, inner: httpx.Response, stream: _Stream) -> None:
        self.inner, self.stream = inner, stream

    def __iter__(self):
        for chunk in _raw(self.inner):
            yield self.stream.feed(chunk)
        yield self.stream.close()

    def close(self) -> None:
        self.inner.close()


class _AsyncBody(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.Response, stream: _Stream) -> None:
        self.inner, self.stream = inner, stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in _araw(self.inner):
            yield self.stream.feed(chunk)
        yield self.stream.close()

    async def aclose(self) -> None:
        await self.inner.aclose()


# The example workspace replays Maya's fixed records through the real pipeline for every visitor, so its
# model replies are cached by the exact request (owner, 2026-10-28: "this thing called caching exists").
# Only demo workspaces read or write the cache; a real account never shares a reply. The key is the
# request as sent, after pseudonymizing, so a change there never serves an older reply. A repeat of the
# same request within two minutes is a caller's retry after a reply it could not use: it goes to the model
# and replaces the cached reply.
_RETRY_SECONDS = 120
_served: dict[str, float] = {}
_indexed: set[str] = set()


def _cache_path(request: httpx.Request) -> "Path | None":
    from pathlib import Path
    from stellis import storage
    if (not storage.in_demo() or request.method != "POST"
            or not request.url.path.endswith(("/chat/completions", "/responses"))):
        return None
    try:
        payload = json.loads(request.content)
        if payload.get("stream"):
            return None  # Live conversation stays live.
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None
    sent = json.dumps(pseudonymize(payload)[0], sort_keys=True)
    # Record ids are new for every visitor's copy of the demo, so the key numbers them by order instead.
    for number, value in enumerate(_ids(sent), 1):
        sent = sent.replace(value, f"[UUID_{number}]")
    key = hashlib.sha256(request.url.path.encode() + b"\0" + sent.encode()).hexdigest()
    return _cache_root() / key[:2] / f"{key}.json"


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _ids(text: str) -> list[str]:
    return list(dict.fromkeys(_UUID.findall(text)))


def _cache_root() -> "Path":
    from pathlib import Path
    from stellis import storage
    shared = os.environ.get("STELLIS_DEMO_CACHE_DIR")
    return Path(shared) if shared else Path(storage.base_dir()) / "demo-model-cache"


def index_stored(url: str, body: dict, question: str, scope: str) -> None:
    """Notes which question and record a stored demo reply answers, so it can be offered once the budget is spent."""
    path = _cache_path(httpx.Request("POST", url, json=body))
    if path is None or not path.is_file() or path.stem in _indexed:
        return
    _indexed.add(path.stem)  # A replay does not add the same answer again.
    entry = json.dumps({"key": path.stem, "question": question[:300], "scope": scope[:200]})
    with (_cache_root() / "index.jsonl").open("a") as index:
        index.write(entry + "\n")


def stored_questions(scope: str) -> list[str]:
    """Questions about one notebook or report that the demo can answer from stored replies, newest first."""
    index = _cache_root() / "index.jsonl"
    if not index.is_file():
        return []
    seen: dict[str, None] = {}
    for line in reversed(index.read_text().splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("scope") == scope and (_cache_root() / entry["key"][:2] / f"{entry['key']}.json").is_file():
            seen.setdefault(entry["question"], None)
    return list(seen)[:8]


def replayable(url: str, body: dict) -> bool:
    """Whether the demo would replay a stored reply for this model request, so it costs nothing.

    A repeat within the retry window goes to the model (see ``_cached``), so it is not replayable.
    """
    path = _cache_path(httpx.Request("POST", url, json=body))
    return (path is not None and path.is_file()
            and time.monotonic() - _served.get(str(path), -_RETRY_SECONDS) >= _RETRY_SECONDS)


def _over_budget(request: httpx.Request) -> httpx.Response | None:
    """Refuses a new hosted model call once the deployment's budget is spent, whichever caller sent it.

    Callers that check the budget first show its message; this catches the ones that do not, so
    nothing is spent past the limit.
    """
    from stellis import deployment
    if (request.method != "POST" or _local(request.url)
            or not request.url.path.endswith(("/chat/completions", "/responses")) or not deployment.exhausted()):
        return None
    return httpx.Response(429, json={"error": {"message": deployment.MESSAGE}}, request=request)


def _cached(path: "Path | None", request: httpx.Request) -> httpx.Response | None:
    if path is None or not path.is_file():
        return None
    if time.monotonic() - _served.get(str(path), -_RETRY_SECONDS) < _RETRY_SECONDS:
        return None  # A retry: the caller could not use the reply it was given.
    _served[str(path)] = time.monotonic()
    saved = json.loads(path.read_text())
    body = saved["body"]
    # A reply that names a record id gets this visitor's id for it.
    for stored, current in zip(saved.get("ids", []), _ids(request.content.decode("utf-8", "replace"))):
        body = body.replace(stored, current)
    try:
        # A replayed reply cost nothing, so it carries no usage for the daily allowance to count.
        reply = json.loads(body)
        if isinstance(reply, dict) and reply.pop("usage", None) is not None:
            body = json.dumps(reply)
    except ValueError:
        pass
    return httpx.Response(saved["status"], headers={"content-type": saved["type"], "x-stellis-cache": "hit"},
                          content=body.encode(), request=request)


def _keep(path: "Path | None", response: httpx.Response) -> httpx.Response:
    if path is None or response.status_code != 200:
        return response
    body = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps({"status": 200, "type": response.headers.get("content-type", "application/json"),
                                "body": body.decode("utf-8", "replace"),
                                "ids": _ids(response.request.content.decode("utf-8", "replace"))}))
    temp.replace(path)
    _served[str(path)] = time.monotonic()
    return response


async def _akeep(path: "Path | None", response: httpx.Response) -> httpx.Response:
    if path is not None and response.status_code == 200:
        await response.aread()
    return _keep(path, response)


class Transport(httpx.BaseTransport):
    """Pseudonymizes hosted model requests made through a synchronous client."""

    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport(trust_env=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        cache = _cache_path(request)
        if (hit := _cached(cache, request)) is not None:
            return hit
        if (refused := _over_budget(request)) is not None:
            return refused
        return _keep(cache, self._send(request))

    def _send(self, request: httpx.Request) -> httpx.Response:
        request, mapping = _prepare(request)
        response = self.inner.handle_request(request)
        if mapping is None:
            return response
        if "text/event-stream" in response.headers.get("content-type", ""):
            return httpx.Response(response.status_code, headers=_restored_headers(response),
                                  stream=_SyncBody(response, _Stream(mapping)), request=request,
                                  extensions=response.extensions)
        return _rewrite(response, request, mapping, b"".join(_raw(response)))

    def close(self) -> None:
        self.inner.close()


class AsyncTransport(httpx.AsyncBaseTransport):
    """Pseudonymizes hosted model requests made through an asynchronous client."""

    def __init__(self) -> None:
        self.inner = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        cache = _cache_path(request)
        if (hit := _cached(cache, request)) is not None:
            return hit
        if (refused := _over_budget(request)) is not None:
            return refused
        return await _akeep(cache, await self._send(request))

    async def _send(self, request: httpx.Request) -> httpx.Response:
        request, mapping = _prepare(request)
        response = await self.inner.handle_async_request(request)
        if mapping is None:
            return response
        if "text/event-stream" in response.headers.get("content-type", ""):
            return httpx.Response(response.status_code, headers=_restored_headers(response),
                                  stream=_AsyncBody(response, _Stream(mapping)), request=request,
                                  extensions=response.extensions)
        return _rewrite(response, request, mapping, b"".join([chunk async for chunk in _araw(response)]))

    async def aclose(self) -> None:
        await self.inner.aclose()
