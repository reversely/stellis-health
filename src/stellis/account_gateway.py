"""Route authenticated browser requests to isolated account-local Stellis processes."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from stellis import auth, calendar, intake_voice, models, server, storage, vault, workspaces
from stellis.processing import model_paths

_PUBLIC = {"/auth", "/privacy", "/api/health", "/api/v1/health"}
_WEBSOCKETS = {"/components/measurements/_dash-ws-callback", "/api/v1/intake/call", "/api/v1/coach/call", "/api/v1/tour/call"}
_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailers", "transfer-encoding", "upgrade"}


class AccountGateway:
    """One account registry in front of separate local workspace processes."""

    def __init__(self) -> None:
        self.children: dict[int, subprocess.Popen] = {}
        self.locks: dict[int, asyncio.Lock] = {}
        self.root = storage.data_dir()
        runtime = os.environ.get("STELLIS_ACCOUNT_SOCKET_DIR")
        self.socket_dir = Path(runtime) if runtime else self.root / ".account-sockets"
        self.socket_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.socket_dir.chmod(0o700)
        # Locked workspaces: records at rest are encrypted with each account's password (see vault.py).
        self.workspaces = workspaces.Workspaces(self.root) if workspaces.enabled() else None
        self.tending: asyncio.Task | None = None
        self.sweeping: asyncio.Task | None = None

    def _socket(self, account_id: int) -> Path:
        return self.socket_dir / f"{account_id}.sock"

    async def _ready(self, account_id: int) -> Path:
        lock = self.locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            process = self.children.get(account_id)
            socket = self._socket(account_id)
            if process and process.poll() is None and socket.exists():
                return socket
            if process and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, 10)
            socket.unlink(missing_ok=True)
            if self.workspaces is not None:
                if not self.workspaces.unlocked(account_id):
                    raise RuntimeError("Account workspace is locked")
                account_root = self.workspaces.path(account_id)
            else:
                account_root = self.root if account_id == 1 else self.root / "accounts" / str(account_id)
            account_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            account_root.chmod(0o700)
            env = os.environ.copy()
            model_files = model_paths()  # Resolve shared device models before changing the child data root.
            online, offline = models.live_paths()
            env.update(STELLIS_DATA_DIR=str(account_root), STELLIS_AUTH_DB_DIR=str(self.root),
                       STELLIS_ACCOUNT_ID=str(account_id), STELLIS_AUTH_MODE="required",
                       STELLIS_WHISPER_MODEL=str(model_files[0]),
                       STELLIS_SEGMENTATION_MODEL=str(model_files[1]),
                       STELLIS_EMBEDDING_MODEL=str(model_files[2]),
                       STELLIS_ONLINE_ASR_MODEL_DIR=str(online), STELLIS_PARAKEET_MODEL_DIR=str(offline),
                       STELLIS_CALL_MODELS=str(intake_voice.directory()),
                       # Every demo guest replays the same fictional records, so their model replies are
                       # shared by exact request (#258); real accounts never read or write this cache.
                       STELLIS_DEMO_CACHE_DIR=str(self.root / "demo-model-cache"))
            if account_id != 1:
                shared = ("NEBIUS_API_KEY", "TAVILY_API_KEY") if env.get("STELLIS_SHARED_PROVIDER_KEYS") == "1" else ()
                for key in ("NEBIUS_API_KEY", "TAVILY_API_KEY",
                            "STELLIS_NEMOCLAW_SANDBOX", "STELLIS_TAVILY_MCP_READY",
                            "STELLIS_AGENT_GATEWAY_URL", "STELLIS_AGENT_GATEWAY_TOKEN",
                            "STELLIS_AGENT_GATEWAY_ACCOUNT_ID", "STELLIS_AGENT_GATEWAY_TAVILY_READY"):
                    if key not in shared:
                        env.pop(key, None)
            command = [sys.executable, "-m", "uvicorn", "stellis.server:app", "--uds", str(socket),
                       "--no-access-log"]
            if os.environ.get("STELLIS_FORWARDED_ALLOW_IPS", "").strip():
                # Only this gateway reaches the socket, and it passes on the TLS proxy's headers.
                command += ["--proxy-headers", "--forwarded-allow-ips", "*"]
            else:
                command += ["--no-proxy-headers"]
            process = subprocess.Popen(command, env=env, cwd=Path.cwd(),
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.children[account_id] = process
            for _ in range(150):
                if process.poll() is not None:
                    break
                if socket.exists():
                    try:
                        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(socket)),
                                                     timeout=1) as client:
                            response = await client.get("http://stellis/api/health")
                        if response.status_code == 200:
                            return socket
                    except (httpx.HTTPError, OSError):
                        pass
                await asyncio.sleep(0.1)
            raise RuntimeError("Account workspace could not start")

    async def _stop(self, account_id: int) -> None:
        """End an account's process and remove its socket."""
        process = self.children.pop(account_id, None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        self._socket(account_id).unlink(missing_ok=True)

    async def _proxy(self, request: Request, account_id: int, body: bytes | None = None) -> Response:
        client = None
        try:
            socket = await self._ready(account_id)
            client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(socket)), timeout=180)
            headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS}
            scope_query = request.scope.get("query_string", b"").decode("latin-1")
            url = f"http://stellis{request.url.path}" + (f"?{scope_query}" if scope_query else "")
            outbound = client.build_request(request.method, url, headers=headers,
                                            content=request.stream() if body is None else body)
            upstream = await client.send(outbound, stream=True)
        except (RuntimeError, httpx.HTTPError, OSError):
            if client is not None:
                await client.aclose()
            return JSONResponse({"detail": "Account workspace unavailable"}, status_code=503)

        deleted = request.method == "DELETE" and request.url.path == "/api/v1/account" and upstream.status_code == 200

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()
            if deleted:
                await self._stop(account_id)
                if self.workspaces is not None:
                    # Destroying the wrapped key leaves the saved versions unrecoverable; they go too.
                    await self.workspaces.destroy(account_id)
                elif account_id != 1:
                    await asyncio.to_thread(shutil.rmtree, self.root / "accounts" / str(account_id), True)

        # Raw pairs keep repeated headers apart: two Set-Cookie headers joined by a comma read as one cookie.
        proxied = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, background=BackgroundTask(close))
        proxied.raw_headers = [(key.lower(), value) for key, value in upstream.headers.raw
                               if key.lower().decode("latin-1") not in _HOP_HEADERS]
        return proxied

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    if self.workspaces is not None:
                        self.tending = asyncio.create_task(self.workspaces.tend(self._stop))
                    self.sweeping = asyncio.create_task(self._sweep_guests())
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    for process in self.children.values():
                        if process.poll() is None:
                            process.terminate()
                    for process in self.children.values():
                        if process.poll() is None:
                            try:
                                await asyncio.to_thread(process.wait, 10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                    if self.sweeping is not None:
                        self.sweeping.cancel()
                    if self.workspaces is not None:
                        if self.tending is not None:
                            self.tending.cancel()
                        # Save every open workspace before the container stops; the RAM copy is lost then.
                        for account_id in list(self.workspaces.keys):
                            await self.workspaces.close(account_id, self._stop)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await Response(status_code=404)(scope, receive, send)
            return
        request = Request(scope, receive)
        path = request.url.path
        if self.workspaces is not None and request.method == "POST" and path in {
                "/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/auth/logout", "/api/v1/auth/demo"}:
            await self._auth(request, scope, send)
            return
        if self.workspaces is not None and request.method == "POST" and path == "/api/v1/auth/recover":
            response = await self._recover(request)
            await response(scope, receive, send)
            return
        if (path in _PUBLIC or path.startswith(("/static/", "/shared/", "/api/v1/auth/"))):
            await server.app(scope, receive, send)
            return
        if request.method == "GET" and path in {
                "/api/v1/calendar/google/callback", "/api/v1/calendar/microsoft/callback"}:
            account_id = calendar.callback_account(path.split("/")[-2], request.query_params.get("state", ""))
            if account_id is None:
                await RedirectResponse("/onboarding?calendar=expired", status_code=303)(scope, receive, send)
                return
        else:
            account_id = auth.current_account_id(request.cookies.get(auth.COOKIE_NAME))
            if account_id is None:
                response = (JSONResponse({"detail": "Login required"}, status_code=401)
                            if path.startswith("/api/") else RedirectResponse("/auth", status_code=303))
                await response(scope, receive, send)
                return
        if path == "/api/v1/setup":
            # The gateway owns the installer; account processes have separate state.
            await server.app(scope, receive, send)
            return
        if self.workspaces is not None:
            if not self.workspaces.unlocked(account_id):
                # After a restart or idle lock the key is gone; signing in again unlocks the records.
                auth.revoke(request.cookies.get(auth.COOKIE_NAME))
                response = (JSONResponse({"detail": "Sign in again to unlock your records"}, status_code=401)
                            if path.startswith("/api/") else RedirectResponse("/auth", status_code=303))
                await response(scope, receive, send)
                return
            self.workspaces.touch(account_id)
            if request.method == "POST" and path == "/api/v1/account/password":
                body = await request.body()
                response = await self._proxy(request, account_id, body)
                if response.status_code == 200:
                    await self.workspaces.rewrap(account_id, str(json.loads(body)["new_password"]))
                await response(scope, receive, send)
                return
        response = await self._proxy(request, account_id)
        await response(scope, receive, send)

    async def _auth(self, request: Request, scope, send) -> None:
        """Sign-in and sign-up unlock the account's workspace; sign-out saves and closes it."""
        body = await request.body()
        if request.url.path.endswith("/auth/demo"):
            # A demo guest's password unlocks its workspace and is never shown to anyone (#247). The
            # browser sends no body, so the request's headers are set to describe this one.
            body = json.dumps({"password": secrets.token_urlsafe(32)}).encode()
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k.lower() not in (b"content-type", b"content-length")]
                     + [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]}
        signed_out = None
        if request.url.path.endswith("/logout"):
            signed_out = auth.current_account_id(request.cookies.get(auth.COOKIE_NAME))
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        messages: list[dict] = []

        async def capture(message) -> None:
            messages.append(message)

        await server.app(scope, replay, capture)
        start = next(message for message in messages if message["type"] == "http.response.start")
        if signed_out is not None:
            await self.workspaces.close(signed_out, self._stop)
        elif start["status"] == 200:
            payload = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
            account_id = auth.account_id_for_username(str(payload["username"]))
            try:
                opened = await self.workspaces.open(account_id, str(json.loads(body)["password"]))
            except vault.VaultError:
                for header, value in start["headers"]:
                    if header.lower() == b"set-cookie" and value.startswith(auth.COOKIE_NAME.encode() + b"="):
                        auth.revoke(value.split(b";", 1)[0].split(b"=", 1)[1].decode())
                await JSONResponse({"detail": "Your records could not be unlocked"}, status_code=503)(scope, replay, send)
                return
            if opened["head"]:
                # The browser remembers the newest version it has seen, so an older copy can't pass unnoticed.
                start["headers"] = [*start["headers"],
                                    (b"x-stellis-workspace-version", str(opened["head"]["version"]).encode())]
            if "recovery_code" in opened:
                # Shown once: only this code or the password can unlock the records later.
                payload["recovery_code"] = opened["recovery_code"]
                content = json.dumps(payload).encode()
                start["headers"] = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
                start["headers"].append((b"content-length", str(len(content)).encode()))
                messages = [start, {"type": "http.response.body", "body": content, "more_body": False}]
        for message in messages:
            await send(message)

    async def remove_guest(self, account_id: int) -> None:
        """Erases a demo guest: its process, its workspace or folder, then the account and its sessions."""
        await self._stop(account_id)
        if self.workspaces is not None:
            await self.workspaces.destroy(account_id)
        elif account_id != 1:
            await asyncio.to_thread(shutil.rmtree, self.root / "accounts" / str(account_id), True)
        await asyncio.to_thread(auth.delete_account, account_id)

    async def _sweep_guests(self) -> None:
        """Every ten minutes: remove demo guests whose day is over (#247)."""
        while True:
            for account_id in await asyncio.to_thread(auth.expired_guests):
                try:
                    await self.remove_guest(account_id)
                except Exception:  # One stuck guest must not stop the sweep; the next pass retries it.
                    continue
            await asyncio.sleep(600)

    async def _recover(self, request: Request) -> Response:
        """Forgotten password: the recovery key unlocks the records and sets a new password."""
        from stellis import rate_limit
        try:
            body = json.loads(await request.body())
            username, code, password = str(body["username"]).strip(), str(body["recovery_key"]), str(body["new_password"])
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"detail": "Enter your username, recovery key and a new password"}, status_code=400)
        if not 12 <= len(password) <= 1024:
            return JSONResponse({"detail": "Your new password must be at least 12 characters"}, status_code=400)
        client = request.client.host if request.client else "unknown"
        folded = username.casefold()
        if max(rate_limit.LOGIN_FAILURES_BY_USERNAME.retry_after(folded), rate_limit.LOGIN_FAILURES_BY_CLIENT.retry_after(client)):
            return JSONResponse({"detail": "Recovery is paused after too many attempts"}, status_code=429)
        try:
            account_id = auth.account_id_for_username(username)
            if not vault.exists(self.root, account_id) or not vault.has_recovery(self.root, account_id):
                raise LookupError(username)
            opened = await self.workspaces.recover(account_id, code, password)
        except (LookupError, vault.VaultError):
            rate_limit.LOGIN_FAILURES_BY_USERNAME.record(folded)
            rate_limit.LOGIN_FAILURES_BY_CLIENT.record(client)
            return JSONResponse({"detail": "That username and recovery key do not match"}, status_code=401)
        rate_limit.LOGIN_FAILURES_BY_USERNAME.clear(folded)
        auth.reset_password(password, account_id)
        response = server._session_response(auth.create_session(account_id), username)
        if opened["head"]:
            response.headers["X-Stellis-Workspace-Version"] = str(opened["head"]["version"])
        return response

    async def _websocket(self, scope, receive, send) -> None:
        """Relays call WebSockets to the authenticated account process."""
        from starlette.websockets import WebSocket, WebSocketDisconnect
        from websockets.asyncio.client import unix_connect
        from websockets.exceptions import ConnectionClosed, InvalidHandshake

        websocket = WebSocket(scope, receive, send)
        origin = websocket.headers.get("origin")
        account_id = auth.current_account_id(websocket.cookies.get(auth.COOKIE_NAME))
        if (scope["path"] not in _WEBSOCKETS or account_id is None
                or (self.workspaces is not None and not self.workspaces.unlocked(account_id))
                or (origin and origin.split("://", 1)[-1] != websocket.headers.get("host"))):
            await websocket.close(code=4401)
            return
        try:
            socket = await self._ready(account_id)
            headers = {key: value for key, value in websocket.headers.items()
                       if key.lower() in {"cookie", "origin", "user-agent"}}
            # The URL sets Host, so the account process sees the browser's host.
            query = scope.get("query_string", b"").decode("ascii")
            target = scope["path"] + ("?" + query if query else "")
            upstream = await unix_connect(
                str(socket), f"ws://{websocket.headers.get('host', 'stellis')}{target}",
                additional_headers=headers, max_size=2**22)
        except (RuntimeError, OSError, ConnectionClosed, InvalidHandshake, ValueError):
            await websocket.close(code=1011)
            return
        await websocket.accept()

        async def browser_to_account() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                await upstream.send(message["bytes"] if message.get("bytes") is not None
                                    else message.get("text") or "")

        async def account_to_browser() -> None:
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        tasks = [asyncio.create_task(browser_to_account()), asyncio.create_task(account_to_browser())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await upstream.close()
            try:
                await websocket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass  # The browser already left.


app = AccountGateway()
