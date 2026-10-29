"""Separate development identity origin for encrypted text and media."""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from stellis import sync_identity as identity

app = FastAPI(title="Stellis Sync Identity Dev", docs_url=None, redoc_url=None)
_STATIC = Path(__file__).parent / "sync_static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
app.mount("/shared", StaticFiles(directory=Path(__file__).parent / "shared"), name="shared")


def _device_origin() -> str | None:
    value = os.environ.get("STELLIS_DEVICE_ORIGIN", "").strip()
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None or parsed.path or parsed.query or parsed.fragment
            or parsed.username is not None or parsed.password is not None):
        raise RuntimeError("STELLIS_DEVICE_ORIGIN must be a loopback origin with a port")
    return value


DEVICE_ORIGIN = _device_origin()


@app.middleware("http")
async def identity_gate(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0].lower()
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "Loopback only"}, status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in {f"{request.url.scheme}://{request.headers.get('host')}", DEVICE_ORIGIN}:
            return JSONResponse({"detail": "Origin rejected"}, status_code=403)
    path = request.url.path
    if path == "/" or path.startswith(("/static/", "/shared/")) or path in {"/sync/v1/health", "/sync/v1/auth/register", "/sync/v1/auth/login", "/sync/v1/auth/logout"}:
        return await call_next(request)
    user = identity.current_user(request.cookies.get(identity.COOKIE))
    if user is None:
        return JSONResponse({"detail": "Login required"}, status_code=401)
    request.state.user = user
    return await call_next(request)


app.add_middleware(CORSMiddleware, allow_origins=[DEVICE_ORIGIN] if DEVICE_ORIGIN else [],
                   allow_credentials=True, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["content-type"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=1024)


class DeviceName(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class VaultSetup(BaseModel):
    salt: str = Field(min_length=20, max_length=32)
    check_iv: str = Field(min_length=16, max_length=24)
    check_ciphertext: str = Field(min_length=24, max_length=512)


class CiphertextEntry(BaseModel):
    id: str
    item_id: str
    iv: str = Field(min_length=16, max_length=24)
    ciphertext: str = Field(min_length=24, max_length=90000)


class MediaChunk(BaseModel):
    iv: str = Field(min_length=16, max_length=24)
    ciphertext: str = Field(min_length=24, max_length=350000)


def _bytes(value: str, length: int | None = None, maximum: int | None = None) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid ciphertext encoding") from exc
    if length is not None and len(decoded) != length or maximum is not None and len(decoded) > maximum:
        raise HTTPException(status_code=400, detail="Invalid ciphertext size")
    return decoded


def _uuid(value: str) -> None:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid entry ID") from exc


def _session(user_id: str, username: str) -> JSONResponse:
    response = JSONResponse({"id": user_id, "username": username})
    response.set_cookie(identity.COOKIE, identity.new_session(user_id), max_age=identity.SESSION_DAYS * 86400,
                        httponly=True, samesite="strict", secure=False, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/sync/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "identity-dev"}


@app.post("/sync/v1/auth/register")
def register(credentials: Credentials) -> JSONResponse:
    try:
        user_id = identity.register(credentials.username, credentials.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session(user_id, credentials.username.strip())


@app.post("/sync/v1/auth/login")
def login(credentials: Credentials) -> JSONResponse:
    user_id = identity.login(credentials.username.strip(), credentials.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return _session(user_id, credentials.username.strip())


@app.post("/sync/v1/auth/logout")
def logout(request: Request) -> JSONResponse:
    identity.revoke(request.cookies.get(identity.COOKIE))
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(identity.COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/sync/v1/me")
def me(request: Request) -> dict[str, str]:
    return request.state.user


@app.post("/sync/v1/sessions/revoke-all")
def revoke_all(request: Request) -> JSONResponse:
    identity.revoke_all(request.state.user["id"])
    response = JSONResponse({"revoked": True})
    response.delete_cookie(identity.COOKIE, path="/")
    return response


@app.get("/sync/v1/devices")
def devices(request: Request) -> list[dict[str, str]]:
    return identity.devices(request.state.user["id"])


@app.post("/sync/v1/devices")
def add_device(request: Request, name: DeviceName) -> dict[str, str]:
    try:
        return identity.add_device(request.state.user["id"], name.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/sync/v1/devices/{device_id}")
def revoke_device(request: Request, device_id: str) -> dict[str, bool]:
    if not identity.revoke_device(request.state.user["id"], device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"revoked": True}


@app.get("/sync/v1/vault")
def vault(request: Request) -> dict[str, str]:
    result = identity.vault(request.state.user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail="Vault not created")
    return result


@app.post("/sync/v1/vault")
def create_vault(request: Request, setup: VaultSetup) -> dict[str, str]:
    _bytes(setup.salt, length=16)
    _bytes(setup.check_iv, length=12)
    _bytes(setup.check_ciphertext, maximum=256)
    try:
        return identity.create_vault(request.state.user["id"], setup.salt,
                                     setup.check_iv, setup.check_ciphertext)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/sync/v1/entries")
def append_entry(request: Request, entry: CiphertextEntry) -> dict[str, object]:
    if identity.vault(request.state.user["id"]) is None:
        raise HTTPException(status_code=409, detail="Create a vault first")
    _uuid(entry.id)
    _uuid(entry.item_id)
    _bytes(entry.iv, length=12)
    _bytes(entry.ciphertext, maximum=65536)
    try:
        return identity.append_entry(request.state.user["id"], entry.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/sync/v1/entries")
def entries(request: Request, after: int = Query(default=0, ge=0),
            limit: int = Query(default=100, ge=1, le=100)) -> dict[str, object]:
    values = identity.list_entries(request.state.user["id"], after, limit)
    return {"entries": values, "cursor": values[-1]["seq"] if values else after}


@app.post("/sync/v1/media/{media_id}/chunks/{index}")
def put_media_chunk(request: Request, media_id: str, index: int, chunk: MediaChunk) -> dict[str, bool]:
    if identity.vault(request.state.user["id"]) is None:
        raise HTTPException(status_code=409, detail="Create a vault first")
    _uuid(media_id)
    if not 0 <= index < 512:
        raise HTTPException(status_code=400, detail="Invalid media chunk index")
    _bytes(chunk.iv, length=12)
    ciphertext = _bytes(chunk.ciphertext, maximum=256 * 1024 + 16)
    if len(ciphertext) < 17:
        raise HTTPException(status_code=400, detail="Invalid ciphertext size")
    try:
        identity.append_media_chunk(request.state.user["id"], media_id, index,
                                    chunk.iv, chunk.ciphertext)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"stored": True}


@app.get("/sync/v1/media/{media_id}/chunks/{index}")
def get_media_chunk(request: Request, media_id: str, index: int) -> dict[str, str]:
    _uuid(media_id)
    if not 0 <= index < 512:
        raise HTTPException(status_code=400, detail="Invalid media chunk index")
    chunk = identity.media_chunk(request.state.user["id"], media_id, index)
    if chunk is None:
        raise HTTPException(status_code=404, detail="Media chunk not found")
    return chunk
