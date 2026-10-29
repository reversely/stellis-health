"""Loopback-only browser app and health endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import threading
from typing import Literal
from urllib.parse import urlsplit, urlencode

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import StrictBool, BaseModel, Field

from stellis import reproductive_routes, milestone_routes, trackers_routes, trace_routes
from stellis import care_events, photo_readings, dashboard, notebook_work, agent_artifacts, agent_runtime, agents, ai, auth, calendar, categorization, clean, collections, documents, goals, health_facts, health_summary, enhanced_notes, export as folder_export, generation_review, images, library, linkpreview, notebook_chat, notebook_sources, notebooks, notes, pages, people, processing, readings, recording, recording_ai, recording_preview, search, sorting, source_search, storage, suggestions, tavily, timeline, todos, transcript_agent
from stellis import agent_gateway
from stellis.importer import ImportFailure, import_upload

app = FastAPI(title="Stellis Health", docs_url=None, redoc_url=None)


@app.exception_handler(agent_gateway.GatewayError)
async def model_unavailable(_request: Request, error: Exception) -> JSONResponse:
    """A model service outage reaches the page as a readable message to try again, not a plain 500."""
    return JSONResponse({"detail": str(error)}, status_code=503)
api = APIRouter()
_retention_stop = threading.Event()
_retention_thread: threading.Thread | None = None
_demo_call_tasks: dict[asyncio.Task, asyncio.Event] = {}


@app.middleware("http")
async def session_gate(request: Request, call_next):
    """Gate all workspace and API routes in explicit local login mode."""
    if not auth.enabled():
        return await demo_request(request, call_next)
    path = request.url.path
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Origin rejected"}, status_code=403)
        # Refuse an oversized body before multipart parsing spools it to the volume.
        from stellis import importer
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > importer.MAX_UPLOAD_BYTES + 1_000_000:
            return JSONResponse({"detail": f"The upload is larger than the {importer.MAX_UPLOAD_LABEL} limit."},
                                status_code=413)
        # Browsers label every request's initiator; a write started by another site is refused
        # even when the browser omits Origin. Clients without the header (scripts, tests) pass.
        if request.headers.get("sec-fetch-site", "same-origin") not in {"same-origin", "none"}:
            return JSONResponse({"detail": "Origin rejected"}, status_code=403)
    if (path.startswith(("/static/", "/shared/")) or path in {"/auth", "/privacy", "/api/health", "/api/v1/health"}
            or path.startswith("/api/v1/auth/")
            or (request.method == "GET" and path in {
                "/api/v1/calendar/google/callback", "/api/v1/calendar/microsoft/callback"})):
        return await call_next(request)
    token = request.cookies.get(auth.COOKIE_NAME)
    account_id = auth.current_account_id(token)
    bound_id = os.environ.get("STELLIS_ACCOUNT_ID")
    dev_session = not bound_id and auth.dev_enabled() and auth.current_account(token) == "dev"
    if (account_id is None and not dev_session) or (bound_id and account_id != int(bound_id)):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse("/auth", status_code=303)
    if (request.method == "GET" and not path.startswith("/api/") and path not in {"/onboarding", "/accuracy"}
            and auth.needs_onboarding(account_id)):
        return RedirectResponse("/onboarding", status_code=303)
    return await demo_request(request, call_next)


async def demo_request(request: Request, call_next):
    from stellis import demo_patient
    path = request.url.path
    if path.startswith('/api/') and not path.startswith('/api/v1/'):
        path = '/api/v1/' + path.removeprefix('/api/')
    demo_endpoint = path.startswith('/api/v1/demo/')
    # Setup describes the installer, and behind the account gateway the gateway answers it itself
    # without any example workspace, so scoping it would read as a cleared demo and drop the cookie.
    scoped = (request.cookies.get(demo_patient.COOKIE) == 'maya'
              and path.startswith('/api/v1/') and not path.startswith(('/api/v1/auth/', '/api/v1/setup')))
    if not (demo_endpoint or scoped):
        return await call_next(request)
    clearing = request.method == 'DELETE' and path == '/api/v1/demo/history'
    if clearing and not demo_patient.begin_clear():
        return JSONResponse({'detail':'Demo clearing is already in progress'}, status_code=409)
    try:
        if clearing:
            from stellis.demo_routes import cancel_pending
            # An Ask request holds the response lock while inference runs.
            # Interrupt it before waiting for the lock, then drain file reads.
            await cancel_pending()
        async with demo_patient.request_lock():
            if not clearing and demo_patient.clearing():
                return JSONResponse({'detail':'Demo clearing is in progress'}, status_code=409)
            response = await _demo_response(request, call_next, path)
            _sync_story_cookie(request, response)
            # FileResponse opens its source after response headers. Finish that
            # read before Clear removes this generation's files and database.
            if hasattr(response, 'body_iterator'):
                body = b''.join([part async for part in response.body_iterator])
                buffered = Response(body, status_code=response.status_code, background=response.background)
                buffered.raw_headers = response.raw_headers
                return buffered
            return response
    finally:
        if clearing:
            demo_patient.finish_clear()


def _sync_story_cookie(request: Request, response: Response) -> None:
    """Keeps the browser's story-clock cookie equal to the demo workspace's offset in days (#252)."""
    from stellis import demo_patient, story_clock
    # Seeding or entering the demo sets its cookie on this response, so the request does not carry it yet.
    starting = any(value.startswith(demo_patient.COOKIE.encode() + b'=maya') for key, value in response.raw_headers if key == b'set-cookie')
    active = starting or request.cookies.get(demo_patient.COOKIE) == 'maya'
    workspace = demo_patient.existing_workspace() if active else None
    days = story_clock.days(story_clock.stored(workspace)) if workspace is not None else 0
    if str(days) != request.cookies.get(demo_patient.STORY_COOKIE, '0'):
        if days:
            response.set_cookie(demo_patient.STORY_COOKIE, str(days), samesite='strict', path='/')
        else:
            response.delete_cookie(demo_patient.STORY_COOKIE, path='/')


async def _demo_response(request: Request, call_next, path: str):
    from stellis import demo_patient
    if (request.cookies.get('stellis-demo') == 'maya' and path.startswith('/api/v1/')
            and not path.startswith(('/api/v1/demo/', '/api/v1/auth/', '/api/v1/setup'))):
        if ((path.startswith('/api/v1/calendar/') and not path.startswith('/api/v1/calendar/milestones') and not (request.method == 'GET' and path in {'/api/v1/calendar/events','/api/v1/calendar/meetings','/api/v1/calendar/connections','/api/v1/calendar/milestones'})) or path.startswith(('/api/v1/onboarding','/api/v1/account'))):
            return JSONResponse({'detail':'Use your real workspace for account connections'}, status_code=403)
        if request.method not in {'GET','HEAD','OPTIONS'} and path.startswith(('/api/v1/recording','/api/v1/recorder')):
            return JSONResponse({'detail':'Recording is off in the demo. Upload the example visit recording instead.'}, status_code=422)
        if request.method not in {'GET','HEAD','OPTIONS'} and path.startswith(('/api/v1/ai/', '/api/v1/models', '/api/v1/inference')):
            return JSONResponse({'detail':'Inference settings are shared with your account'}, status_code=403)
        workspace = demo_patient.existing_workspace()
        from stellis import story_clock
        if (workspace is not None and request.method not in {'GET','HEAD','OPTIONS'}
                and story_clock.looking_back(workspace) is not None):
            # The scrub shows an earlier day as read-only; changes wait until it moves forward (#252).
            return JSONResponse({'detail':'Move the demo date to the latest records to make changes'}, status_code=409)
        if workspace is None:
            response = JSONResponse({'detail':'Demo cleared. Reload to return to your records'}, status_code=410)
            response.delete_cookie(demo_patient.COOKIE, path='/')
            return response
        with storage.demo_workspace(workspace):
            return await call_next(request)
    return await call_next(request)


@app.middleware("http")
async def development_cache_policy(request: Request, call_next):
    """Revalidate mutable assets so a normal reload uses the current local build, and set security headers."""
    from stellis import security_headers
    response = await call_next(request)
    if request.url.path.startswith('/components/measurements/'):
        # Isolate dynamic Mantine/Plotly styling to this app-owned body frame.
        policy=security_headers.CONTENT_SECURITY_POLICY.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'").replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        policy=policy.replace("script-src 'self'", "script-src 'self' "+' '.join(_measurement_dash.csp_hashes()))
        response.headers['Content-Security-Policy']=policy
        response.headers['Cache-Control']='no-store'
    security_headers.apply(response.headers, https=request.url.scheme == "https")
    path = request.url.path
    if path.startswith(("/static/", "/shared/")):
        response.headers["Cache-Control"] = "private, no-cache"
    elif path.startswith(("/api/v1/calendar/", "/api/calendar/")):
        response.headers["Cache-Control"] = "no-store"
    elif (path in {"/", "/demo", "/auth", "/onboarding", "/recording", "/vocabulary", "/settings", "/todos"} or
          path.startswith(("/conversation/", "/person/", "/vocabulary/", "/notebook/", "/page/"))):
        response.headers["Cache-Control"] = "no-store"
    return response


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=1024)
    invite_code: str | None = Field(default=None, max_length=200)
    keep_debug_data: bool = False


class LoginCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


def _session_response(token: str, username: str) -> JSONResponse:
    response = JSONResponse({"username": username, "onboarding_required": auth.needs_onboarding(auth.current_account_id(token))})
    response.set_cookie(auth.COOKIE_NAME, token, max_age=auth.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=auth.secure_cookies(), path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth", include_in_schema=False)
def auth_page() -> FileResponse:
    return FileResponse(_STATIC / "auth.html")


@app.get("/api/v1/auth/status")
def auth_status(request: Request) -> dict[str, object]:
    return {"required": auth.enabled(), "registration_open": auth.registration_open(),
            "invite_required": auth.invite_required(), "has_accounts": auth.has_account(),
            "locked_workspaces": os.environ.get("STELLIS_LOCKED_WORKSPACES") == "1",
            "onboarding_required": auth.needs_onboarding(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME))) if auth.enabled() else False,
            "username": auth.current_account(request.cookies.get(auth.COOKIE_NAME)) if auth.enabled() else None,
            "demo_available": _guests_isolated() and _demo_patient.available(),
            "guest": auth.is_guest(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME))) if auth.enabled() else False}


def _paused(wait: int, activity: str) -> HTTPException:
    minutes = max(1, (wait + 59) // 60)
    return HTTPException(status_code=429, headers={"Retry-After": str(wait)},
                         detail=f"{activity} is paused for {minutes} minute{'s' if minutes != 1 else ''} after too many attempts")


@app.post("/api/v1/auth/register")
def auth_register(credentials: Credentials, request: Request) -> JSONResponse:
    from stellis import rate_limit
    if not auth.enabled():
        raise HTTPException(status_code=404, detail="Login is disabled")
    if not auth.registration_open():
        raise HTTPException(status_code=403, detail="Sign-up is closed")
    client = request.client.host if request.client else "unknown"
    if wait := rate_limit.SIGNUPS_BY_CLIENT.retry_after(client):
        raise _paused(wait, "Sign-up")
    if not auth.invite_accepted(credentials.invite_code):
        raise HTTPException(status_code=403, detail="That invite code is not valid")
    try:
        username = auth.register(credentials.username, credentials.password, credentials.keep_debug_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    rate_limit.SIGNUPS_BY_CLIENT.record(client)
    return _session_response(auth.create_session(auth.account_id_for_username(username)), username)


class GuestRequest(BaseModel):
    password: str | None = Field(default=None, min_length=12, max_length=1024)


def _guests_isolated() -> bool:
    """Guests need the account gateway: it gives every account its own process and data folder."""
    import sys
    return auth.enabled() and "stellis.account_gateway" in sys.modules


@app.post("/api/v1/auth/demo")
def auth_demo(request: Request, guest: GuestRequest | None = None) -> JSONResponse:
    """Signs a visitor into a new demo guest (#247); the demo replaces sign-up and onboarding."""
    from stellis import rate_limit
    if not _guests_isolated():
        raise HTTPException(status_code=404, detail="The demo is not available here")
    client = request.client.host if request.client else "unknown"
    if wait := rate_limit.GUESTS_BY_CLIENT.retry_after(client):
        raise _paused(wait, "The demo")
    username, _ = auth.register_guest(guest.password if guest else None)
    rate_limit.GUESTS_BY_CLIENT.record(client)
    return _session_response(auth.create_session(auth.account_id_for_username(username)), username)


@app.post("/api/v1/auth/login")
def auth_login(credentials: LoginCredentials, request: Request) -> JSONResponse:
    from stellis import rate_limit
    if not auth.enabled():
        raise HTTPException(status_code=404, detail="Login is disabled")
    client = request.client.host if request.client else "unknown"
    username = credentials.username.strip().casefold()
    if wait := max(rate_limit.LOGIN_FAILURES_BY_USERNAME.retry_after(username),
                   rate_limit.LOGIN_FAILURES_BY_CLIENT.retry_after(client)):
        raise _paused(wait, "Sign-in")
    token = auth.login(credentials.username, credentials.password)
    if token is None:
        rate_limit.LOGIN_FAILURES_BY_USERNAME.record(username)
        rate_limit.LOGIN_FAILURES_BY_CLIENT.record(client)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    rate_limit.LOGIN_FAILURES_BY_USERNAME.clear(username)
    return _session_response(token, credentials.username)


class ProfileUpdate(BaseModel):
    name: str = Field(max_length=100)
    email: str = Field(max_length=254)


class DebugDataChoice(BaseModel):
    keep: bool


class UsernameChange(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class AccountDeletion(BaseModel):
    # Required for every account except a demo guest, which has no password it knows.
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    confirm: str


def _account_request(request: Request) -> tuple[str, int]:
    """Return the session token for account changes, refusing open mode and the shared dev login."""
    if not auth.enabled():
        raise HTTPException(status_code=404, detail="Login is disabled")
    token = request.cookies.get(auth.COOKIE_NAME)
    if auth.dev_enabled() and auth.current_account(token) == "dev":
        raise HTTPException(status_code=403, detail="The dev login has no account settings")
    account_id = auth.current_account_id(token)
    if token is None or account_id is None:
        raise HTTPException(status_code=401, detail="Login required")
    return token, account_id


def _account_errors(action):
    try:
        return action()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="That username is taken") from exc


@app.get("/api/v1/account")
def account_profile(request: Request) -> dict[str, object]:
    _, account_id = _account_request(request)
    return _account_errors(lambda: auth.profile(account_id))


@app.put("/api/v1/account/debug-data")
def account_debug_data(request: Request, choice: DebugDataChoice) -> dict[str, object]:
    _, account_id = _account_request(request)
    return _account_errors(lambda: auth.set_keep_debug_data(choice.keep, account_id))


@app.get("/api/v1/account/debug-data")
def account_debug_data_download(request: Request) -> Response:
    from stellis import debug_data
    _account_request(request)
    return Response(debug_data.archive(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="stellis-debug-data.zip"', "Cache-Control": "no-store"})


@app.patch("/api/v1/account")
def account_update(request: Request, update: ProfileUpdate) -> dict[str, object]:
    _, account_id = _account_request(request)
    return _account_errors(lambda: auth.update_profile(update.name, update.email, account_id))


@app.post("/api/v1/account/username")
def account_username(request: Request, change: UsernameChange) -> dict[str, object]:
    _, account_id = _account_request(request)
    return _account_errors(lambda: auth.change_username(change.username, change.password, account_id))


@app.post("/api/v1/account/password")
def account_password(request: Request, change: PasswordChange) -> dict[str, bool]:
    token, account_id = _account_request(request)
    _account_errors(lambda: auth.change_password(change.current_password, change.new_password, token, account_id))
    return {"changed": True}


@app.delete("/api/v1/account")
def account_delete(request: Request, deletion: AccountDeletion) -> JSONResponse:
    _, account_id = _account_request(request)
    if deletion.confirm != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm")
    # A demo guest (#247) never sees its password; leaving the demo deletes the guest outright.
    if not auth.is_guest(account_id):
        _account_errors(lambda: auth.verify_password(deletion.password or "", account_id))
    try:
        erased = library.erase_all()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for provider in calendar.PROVIDERS:
        calendar.disconnect(provider)
    calendar.clear_assignments()
    ai.remove_key()
    tavily.remove_key()
    auth.delete_account(account_id)
    response = JSONResponse({"deleted": True, **erased})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/v1/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    auth.revoke(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


def _retention_loop() -> None:
    while not _retention_stop.is_set():
        try:
            result = library.delete_expired()
            if result["deleted"] or result["deferred"]:
                logging.getLogger(__name__).info("Recently Deleted retention sweep: %s", result)
        except Exception:
            logging.getLogger(__name__).exception("Recently Deleted retention sweep failed")
        if _retention_stop.wait(3600):
            break


class SegmentEdit(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=1)


class SegmentChange(BaseModel):
    segment_id: str
    text: str = Field(min_length=1, max_length=4000)


class TranscriptBatchEdit(BaseModel):
    edits: list[SegmentChange] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=1)


class SpeakerLabelEdit(BaseModel):
    scope: str
    target_id: str
    name: str | None = Field(default=None, max_length=100)
    expected_revision: int = Field(ge=1)


class VocabularyTerm(BaseModel):
    term: str = Field(min_length=1, max_length=100)


class VocabularyCreate(VocabularyTerm):
    kind: Literal["name", "other"] = "other"


class VocabularyBatch(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=200)
    kind: Literal["name", "other"] = "other"


class VocabularySelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


class VocabularyMove(VocabularySelection):
    kind: Literal["name", "other"]


class CategoryName(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CategoryAutomationSetting(BaseModel):
    enabled: bool


class PersonName(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PersonVisibility(BaseModel):
    hidden: bool


class PersonLink(BaseModel):
    person_id: str | None = None
    name: str | None = Field(default=None, max_length=100)
    segment_id: str
    scope: str
    expected_revision: int = Field(ge=1)


class PersonUnlink(BaseModel):
    segment_id: str
    scope: str
    expected_revision: int = Field(ge=1)


class PersonMerge(BaseModel):
    source_id: str


class PersonMove(BaseModel):
    conversation_id: str
    target_id: str


class HighlightSelection(BaseModel):
    segment_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    expected_revision: int = Field(ge=1)


class CommentSelection(HighlightSelection):
    body: str = Field(min_length=1, max_length=2000)


class CommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class RecordingStart(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    media_type: str
    notebook_id: str | None = None
    calendar_provider: str | None = None
    calendar_event_id: str | None = Field(default=None, max_length=1024)


class RecordingRename(BaseModel):
    title: str = Field(min_length=1, max_length=180)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=180)


class RecordingEvent(BaseModel):
    kind: str
    media_position_ms: int = Field(ge=0)


class TodoDue(BaseModel):
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    due_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class NotesWrite(BaseModel):
    markdown: str = Field(max_length=200_000)
    expected_revision: int = Field(ge=0)
    todo_dates: dict[str, TodoDue | None] | None = None


class TodoStatus(BaseModel):
    status: Literal["open", "done", "canceled"]


class PageCreate(BaseModel):
    title: str | None = None
    notebook_id: str | None = None
    markdown: str = ""


class PageRename(BaseModel):
    title: str | None = None


class PageWrite(BaseModel):
    markdown: str
    expected_revision: int = Field(ge=0)
    todo_dates: dict[str, TodoDue | None] | None = None


class ImageCopy(BaseModel):
    scope: Literal["page", "conversation", "recording"]
    owner_id: str


class ImageCaption(BaseModel):
    caption: str = Field(max_length=280)


class EnhancedNotesRequest(BaseModel):
    force: bool = False


class RecordingQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AIRunRequest(BaseModel):
    kind: str
    question: str | None = Field(default=None, max_length=500)
    force: bool = False


class CleanSegmentEdit(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)


class CleanBatchEdit(BaseModel):
    edits: list[SegmentChange] = Field(min_length=1, max_length=500)
    expected_revision: int = Field(ge=0)


class CleanReview(BaseModel):
    expected_revision: int = Field(ge=0)


class WordAlignmentRequest(BaseModel):
    source_generation_version: str | None = None


_STATIC = Path(__file__).parent / "static"
from stellis import demo_patient as _demo_patient
if _demo_patient.available():
    # Served at the addresses its guide uses, from wherever the deployment keeps it; mounted before /static.
    app.mount("/static/maya", StaticFiles(directory=_demo_patient.STATIC), name="demo")
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
app.mount("/shared", StaticFiles(directory=Path(__file__).parent / "shared"), name="shared")


@app.on_event("startup")
def start_speech_worker() -> None:
    global _retention_thread
    library.recover_staged_media()
    images.recover_orphans()
    storage.fail_interrupted_ai_runs()
    agents.interrupt_running_jobs()
    from stellis import runtime_trace
    runtime_trace.interrupt_running()
    storage.fail_interrupted_clean_runs()
    categorization.fail_interrupted()
    enhanced_notes.fail_interrupted()
    transcript_agent.fail_interrupted()
    recording_ai.fail_interrupted()
    generation_review.resolve_pending()
    processing.start_worker()
    processing.index_missing_voices()
    if _retention_thread is None or not _retention_thread.is_alive():
        _retention_stop.clear()
        _retention_thread = threading.Thread(target=_retention_loop, name="stellis-retention", daemon=True)
        _retention_thread.start()


@app.on_event("shutdown")
async def stop_retention_worker() -> None:
    from stellis import daily_brief
    await daily_brief.shutdown()
    _retention_stop.set()
    # Wait for an in-flight sweep so it cannot touch the data directory after shutdown returns.
    if _retention_thread is not None:
        _retention_thread.join(timeout=30)
    processing.stop_worker()


def _public(record: dict[str, object]) -> dict[str, object]:
    return {key: record.get(key) for key in ("id", "title", "duration_ms", "created_at", "recorded_at", "notebook_id")} | {
        "category_ids": library.assignments(str(record["id"])).get(str(record["id"]), [])}


@app.get("/api/health")
def health() -> dict[str, str]:
    """Return a small readiness response for a clean install check."""
    return {"status": "ok", "mode": "local", "version": version("stellis")}


@api.get("/health")
def versioned_health() -> dict[str, str]:
    return {**health(), "api_version": "v1"}


@api.get("/sync-config")
def sync_config() -> dict[str, str | None]:
    value = os.environ.get("STELLIS_SYNC_ORIGIN", "").strip()
    if not value:
        return {"origin": None}
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port is None or parsed.path or parsed.query or parsed.fragment
            or parsed.username is not None or parsed.password is not None):
        raise HTTPException(status_code=500, detail="Invalid sync origin configuration")
    return {"origin": value}


class AgentUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, max_length=160)
    instructions: str | None = Field(default=None, max_length=4000)


class AgentMemory(BaseModel):
    fact: str = Field(min_length=1, max_length=2000)
    notebook_id: str | None = None
    source_id: str | None = Field(default=None, max_length=500)


class AgentQuestion(BaseModel):
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    question: str = Field(min_length=1, max_length=2000)
    notebook_id: str | None = None
    source_handoff_id:str|None=Field(default=None,max_length=64)


def _agent_result(action):
    try:
        return action()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/agents")
def list_agents() -> list[dict[str, object]]:
    return agents.list_agents()


@api.get("/agents/runtime")
def agent_runtime_status() -> dict[str, bool]:
    return {"configured": agent_runtime.configured()}


@api.get("/agents/{agent_id}")
def get_agent(agent_id: str) -> dict[str, object]:
    return _agent_result(lambda: agents.get(agent_id))


@api.patch("/agents/{agent_id}")
def update_agent(agent_id: str, request: AgentUpdate) -> dict[str, object]:
    return _agent_result(lambda: agents.update(agent_id, name=request.name, role=request.role,
                                                instructions=request.instructions, enabled=request.enabled))


@api.get("/agents/{agent_id}/memories")
def agent_memories(agent_id: str) -> list[dict[str, object]]:
    return _agent_result(lambda: agents.memories(agent_id))


@api.post("/agents/{agent_id}/memories")
def add_agent_memory(agent_id: str, request: AgentMemory) -> dict[str, object]:
    return _agent_result(lambda: agents.save_memory(agent_id, request.fact,
                                                     request.notebook_id, source_id=request.source_id))


@api.put("/agents/{agent_id}/memories/{memory_id}")
def update_agent_memory(agent_id: str, memory_id: str, request: AgentMemory) -> dict[str, object]:
    return _agent_result(lambda: agents.save_memory(agent_id, request.fact,
                                                     request.notebook_id, memory_id, request.source_id))


@api.delete("/agents/{agent_id}/memories/{memory_id}")
def remove_agent_memory(agent_id: str, memory_id: str) -> dict[str, bool]:
    _agent_result(lambda: agents.delete_memory(agent_id, memory_id))
    return {"deleted": True}


@api.get("/agents/{agent_id}/messages")
def agent_messages(agent_id: str) -> list[dict[str, object]]:
    return _agent_result(lambda: agents.messages(agent_id))


class ArtifactRevision(BaseModel):
    revision: int


class ArtifactEdit(ArtifactRevision):
    title: str
    body: str


@api.get("/agents/{agent_id}/artifacts")
def agent_drafts(agent_id: str):
    return _agent_result(lambda: agent_artifacts.list_for(agent_id))


@api.get("/artifacts/{artifact_id}")
def get_agent_draft(artifact_id: str):
    return _agent_result(lambda: agent_artifacts.get(artifact_id))


@api.patch("/artifacts/{artifact_id}")
def edit_agent_draft(artifact_id: str, request: ArtifactEdit):
    return _agent_result(lambda: agent_artifacts.edit(artifact_id, request.revision, request.title, request.body))


@api.post("/artifacts/{artifact_id}/discard")
def discard_agent_draft(artifact_id: str, request: ArtifactRevision):
    return _agent_result(lambda: agent_artifacts.discard(artifact_id, request.revision))


@api.post("/artifacts/{artifact_id}/note")
def file_agent_draft(artifact_id: str, request: ArtifactRevision):
    return _agent_result(lambda: agent_artifacts.save_note(artifact_id, request.revision))


@api.get("/agents/{agent_id}/jobs")
def agent_jobs(agent_id: str) -> list[dict[str, object]]:
    return _agent_result(lambda: agents.jobs(agent_id))


@api.post("/agents/{agent_id}/jobs/{job_id}/stop")
async def stop_agent_job(agent_id: str, job_id: str):
    return _agent_result(lambda: agent_runtime.stop_job(agent_id, job_id))


@api.post("/agents/{agent_id}/ask")
async def ask_agent(agent_id: str, request: AgentQuestion) -> dict[str, object]:
    try:
        from stellis import chat_attachments
        passages = chat_attachments.passages('agent', agent_id, request.attachment_ids, request.question)
        sources = [{'source_id': item.source_id, 'title': item.title,
                    'excerpt': item.text, 'target': item.target, 'start_ms': item.start_ms}
                   for item in passages]
        from stellis import source_handoffs
        if request.source_handoff_id:
            context=source_handoffs.resolve(request.source_handoff_id,'chat')
            agents.require_enabled(agent_id,context['notebook_id'])
            sources.extend(source_handoffs.sources(request.source_handoff_id))
        if request.source_handoff_id:
            result=await agent_runtime.ask(agent_id,request.question,request.notebook_id,sources,answer_check=lambda answer:source_handoffs.check(request.source_handoff_id))
        else:result=await agent_runtime.ask(agent_id,request.question,request.notebook_id,sources)
        if request.attachment_ids:
            chat_attachments.bind('agent', agent_id, request.attachment_ids, result['user_message']['id'])
        return result
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=409, detail="Reply stopped") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except agent_runtime.AgentRunError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _calendar_origin(request: Request) -> str | None:
    """Pick the origin OAuth returns to: the configured public origin, or this loopback address."""
    origin = calendar.public_origin()
    if origin:
        return origin
    if request.url.hostname in {"127.0.0.1", "localhost"} and request.url.port is not None:
        return str(request.base_url).rstrip("/")
    return None


@api.get("/calendar/connections")
def calendar_connections(request: Request) -> list[dict[str, object]]:
    try:
        reachable = _calendar_origin(request) is not None
    except calendar.CalendarError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [{**row, "connectable": row["configured"] and reachable} for row in calendar.connections()]


@api.get("/calendar/{provider}/authorize")
def calendar_authorize(provider: str, request: Request,
                       return_to: Literal["/", "/settings", "/onboarding"] = "/") -> dict[str, str]:
    try:
        origin = _calendar_origin(request)
        if origin is None:
            raise calendar.CalendarError("Calendar connection is not available on the web app yet")
        return {"url": calendar.authorize(provider, request.url.port, origin + return_to)}
    except calendar.CalendarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/calendar/{provider}/callback", include_in_schema=False)
async def calendar_callback(provider: str, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    return_origin = ""
    try:
        verifier, uri, return_origin = calendar.consume_state(provider, state)
        if error or not code:
            raise calendar.CalendarError("Calendar connection was cancelled")
        await calendar.exchange(provider, code, verifier, uri)
        result = "connected"
    except calendar.CalendarError as exc:
        return_origin = return_origin or getattr(exc, "return_origin", "/onboarding")
        result = "failed"
        message = str(exc)
    # return_origin carries the page that started the connection, home or Settings.
    query = urlencode({"calendar": result, **({"connection_error": message} if result == "failed" else {})})
    response = RedirectResponse(f"{return_origin or '/onboarding'}?{query}", status_code=303)
    response.headers["Cache-Control"] = "no-store"
    return response


@api.post("/calendar/{provider}/disconnect")
async def calendar_disconnect(provider: str) -> dict[str, bool]:
    try:
        await calendar.disconnect_grant(provider)
    except calendar.CalendarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"disconnected": True}


@api.get("/calendar/meetings")
async def calendar_meetings() -> dict[str, object]:
    return await calendar.meetings()


class EventNotebookSave(BaseModel):
    provider: str
    event_id: str = Field(min_length=1, max_length=1024)
    series_id: str = Field(default="", max_length=1024)
    title: str = Field(max_length=180)
    attendees: list[str] = Field(default_factory=list, max_length=100)
    notebook_id: str | None = None
    scope: str = "event"


@api.put("/calendar/notebook")
def save_calendar_notebook(request: EventNotebookSave) -> dict[str, object]:
    try:
        return calendar.save_notebook_assignment({"provider": request.provider, "id": request.event_id,
            "series_id": request.series_id, "title": request.title, "attendees": request.attendees},
            request.notebook_id, request.scope)
    except calendar.CalendarError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class EventNotesSave(BaseModel):
    provider: str
    event_id: str = Field(min_length=1, max_length=1024)
    markdown: str
    expected_revision: int = Field(ge=0)


@api.get("/calendar/notes")
def calendar_event_notes(provider: str, event_id: str) -> dict[str, object]:
    try:
        return notes.get_event(provider, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.put("/calendar/notes")
def save_calendar_event_notes(request: EventNotesSave) -> dict[str, object]:
    try:
        return notes.save_event(request.provider, request.event_id, request.markdown, request.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/privacy", include_in_schema=False)
def privacy_page() -> HTMLResponse:
    """Public policy follows the research route and confirmed retention settings."""
    from stellis import research
    page = (_STATIC / "privacy.html").read_text()
    if research.tavily_only():
        page = page.replace(
            "Stellis searches MedlinePlus and PubMed first and uses Tavily when a key is available and they return too few results.",
            "Stellis searches Tavily directly when a key is available and otherwise searches MedlinePlus and PubMed.")
    if os.environ.get("STELLIS_TOKEN_FACTORY_ZDR") == "confirmed":
        page = page.replace('data-zdr="off"', 'data-zdr="on"', 1)
    return HTMLResponse(page)


@app.get("/accuracy", include_in_schema=False)
def accuracy_page():
    return FileResponse(_STATIC / "accuracy.html")


@app.get("/onboarding", include_in_schema=False)
def onboarding_page(request: Request):
    from stellis import onboarding
    account_id = auth.current_account_id(request.cookies.get(auth.COOKIE_NAME))
    finished = (request.query_params.get('finished') == '1'
                and onboarding.status(account_id).get('step') == 'complete')
    if not onboarding.available(account_id) and not finished:
        return RedirectResponse("/", status_code=303)
    return FileResponse(_STATIC / "onboarding.html")


class OnboardingChoices(BaseModel):
    uses: list[str] = Field(default_factory=list, max_length=8)
    step: Literal["welcome", "health", "call", "records", "uses", "review"] = "review"
    task_choices: dict[str, StrictBool] | None = None
    notebook_choices: list[dict] | None = Field(default=None, max_length=100)


@api.get("/onboarding")
def onboarding_state(request: Request):
    from stellis import onboarding, onboarding_preview
    onboarding_preview.warm()
    return onboarding.status(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)))


@api.get("/onboarding/preview")
def onboarding_preview():
    from stellis import onboarding_preview
    return onboarding_preview.snapshot()


@api.get("/onboarding/profile")
def onboarding_profile_state():
    from stellis import onboarding_profile
    return onboarding_profile.status()


class ProfileAnswer(BaseModel):
    field: str = Field(max_length=40)
    # Goals arrive as {id, amount, times_per_week}; other lists are IDs.
    value: str | list[str | dict[str, str | int | float | None]] | None = None


@api.put("/onboarding/profile")
def onboarding_profile_answer(answer: ProfileAnswer, request: Request):
    from stellis import onboarding_profile
    try:
        return onboarding_profile.save(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)),
                                       answer.field, answer.value)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.put("/onboarding")
def onboarding_choices(choices: OnboardingChoices, request: Request):
    from stellis import onboarding
    try:
        if choices.notebook_choices is not None:
            for group in choices.notebook_choices:
                OnboardingRecordGroup.model_validate(group)
        return onboarding.choose(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)), choices.uses, choices.step, choices.task_choices, choices.notebook_choices)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/onboarding/reopen")
def onboarding_reopen(request: Request):
    from stellis import onboarding
    return onboarding.reopen(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)))


@api.post("/onboarding/check")
async def onboarding_check(request: Request):
    from stellis import onboarding
    try:
        return await onboarding.check(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)))
    except (ValueError, agent_runtime.AgentRunError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class OnboardingRecordItem(BaseModel):
    kind: str = Field(max_length=20)
    id: str = Field(max_length=100)


class OnboardingRecordGroup(BaseModel):
    notebook_name: str = Field(default="", max_length=80)
    notebook_id: str | None = Field(default=None, max_length=100)
    template_id: str | None = Field(default=None, max_length=100)
    collection_id: str | None = Field(default=None, max_length=100)
    description: str = Field(default="", max_length=1000)
    collection_changed: StrictBool = False
    items: list[OnboardingRecordItem] = Field(default_factory=list, max_length=120)
    suggest_tasks: StrictBool = False
    goal: str = Field(default="", max_length=200)


class OnboardingFinish(BaseModel):
    proposal_id: str | None = Field(default=None, max_length=80)
    group_ids: list[str] = Field(default_factory=list, max_length=8)
    record_groups: list[OnboardingRecordGroup] = Field(default_factory=list, max_length=100)


class OnboardingRead(BaseModel):
    days: Literal[7, 30, 90] = 30


@api.get("/onboarding/sources")
def onboarding_source_state():
    from stellis import onboarding_sources
    return {"scan": onboarding_sources.status()}


@api.post("/onboarding/sources")
async def onboarding_read_sources(request: Request, choices: OnboardingRead):
    from stellis import onboarding_sources
    try:
        return onboarding_sources.start(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)), choices.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.put("/onboarding/sources")
def onboarding_select_sources(request: Request, choices: OnboardingFinish):
    from stellis import onboarding_sources
    if not choices.proposal_id:
        raise HTTPException(status_code=400, detail="Choose a current proposal")
    try:
        return onboarding_sources.select(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)), choices.proposal_id, choices.group_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.delete("/onboarding/sources")
async def onboarding_cancel_sources(request: Request):
    from stellis import onboarding_sources
    try:
        return await onboarding_sources.cancel(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/onboarding/complete")
def onboarding_complete(request: Request, choices: OnboardingFinish | None = None):
    from stellis import onboarding
    try:
        return onboarding.complete(auth.current_account_id(request.cookies.get(auth.COOKIE_NAME)),
                                   choices.proposal_id if choices else None, choices.group_ids if choices else [],
                                   [group.model_dump() for group in choices.record_groups] if choices else [])
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class IntakeNotesSection(BaseModel):
    heading: str = Field(max_length=40)
    items: list[str] = Field(default_factory=list, max_length=40)


class IntakeNotes(BaseModel):
    sections: list[IntakeNotesSection] = Field(max_length=12)
    revision: int = Field(ge=0)


class IntakeAnswer(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@api.get("/intake")
def intake_state():
    from stellis import intake, intake_voice
    return {**intake.load(), "voice": intake_voice.status(), "agent_name": intake.AGENT_NAME}


@api.get("/intake/library")
def intake_library_search(category: Literal["goals", "health", "conditions", "medicines"] = "goals",
                          q: str = Query(default="", max_length=100)):
    from stellis import intake_library
    return intake_library.search(category, q)


@api.put("/intake/notes")
def intake_notes(request: IntakeNotes):
    from stellis import intake
    try:
        return intake.save_notes([section.model_dump() for section in request.sections], request.revision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/intake/answer")
async def intake_answer(request: IntakeAnswer):
    """A typed turn, for a call without a microphone or speech models."""
    from stellis import intake
    try:
        return await intake.run_text_turn(request.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except intake.IntakeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.post("/intake/start")
def intake_start():
    """Starts or resumes a typed call: records the agent's opening line."""
    from stellis import intake
    return {"text": intake.opening(), **intake.load()}


@api.post("/intake/end")
def intake_end():
    from stellis import intake
    return intake.end()


class NotebookRevision(BaseModel):
    request: str = Field(default="", max_length=2000)
    layout: list[dict] | None = Field(default=None, max_length=10)


@api.post("/intake/notebooks")
async def intake_notebooks(revision: NotebookRevision | None = None):
    """Proposes notebooks, or revises the reviewed layout as the person asks."""
    from stellis import intake
    revision = revision or NotebookRevision()
    try:
        return await intake.propose_notebooks(revision.request, revision.layout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except intake.IntakeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _websocket_allowed(websocket: WebSocket) -> bool:
    """The same session and origin checks the HTTP gate applies.

    A socket passes once, when it connects. The answer is kept on the socket because a demo call
    checks again inside the demo's storage, where the session cannot be looked up (#258).
    """
    if getattr(websocket.state, "stellis_allowed", False):
        return True
    allowed = _session_allowed(websocket)
    if allowed:
        websocket.state.stellis_allowed = True
    return allowed


def _session_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != websocket.headers.get("host"):
        return False
    if not auth.enabled():
        return True
    token = websocket.cookies.get(auth.COOKIE_NAME)
    account_id = auth.current_account_id(token)
    bound_id = os.environ.get("STELLIS_ACCOUNT_ID")
    if bound_id:
        return account_id == int(bound_id)
    return account_id is not None or (auth.dev_enabled() and auth.current_account(token) == "dev")


async def intake_call(websocket: WebSocket) -> None:
    """The intake call: microphone audio in, the agent's speech and notes out."""
    from stellis import intake
    await _run_call(websocket, intake)


async def coach_call(websocket: WebSocket) -> None:
    """A coach call; the query names its kind and the person's local day and time."""
    from stellis import coach_call as script
    query = websocket.query_params
    if not _websocket_allowed(websocket):
        await websocket.close(code=4401)
        return
    if query.get("kind") == "report":
        from stellis.report_call import ReportCall
        try:
            report = await asyncio.to_thread(ReportCall, query.get("document", ""),
                                            query.get("map", ""), query.get("region", ""))
        except (ValueError, LookupError, documents.DocumentConflict):
            await websocket.close(code=4400)
            return
        await _run_call(websocket, report)
        return
    try:
        await script.begin(query.get("kind", "call"), query.get("today", ""),
                           query.get("now", ""), int(query.get("utc_offset", "0")),query.get("context") or None)
    except (ValueError,LookupError):
        await websocket.close(code=4400)
        return
    await _run_call(websocket, script)


async def tour_call(websocket: WebSocket) -> None:
    """The guided tour, on a call docked over the app."""
    from stellis import tour
    if not _websocket_allowed(websocket):
        await websocket.close(code=4401)
        return
    await asyncio.to_thread(tour.begin)
    await _run_call(websocket, tour)


async def _run_call(websocket: WebSocket, script) -> None:
    """Runs a voice call through ``script``, a module as ``intake_voice.Call`` takes it."""
    from stellis import intake_voice
    if not _websocket_allowed(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    engine = None
    if websocket.query_params.get('voice') != '0':
        try:
            engine = await asyncio.to_thread(intake_voice.acquire)
        except Exception:  # Missing or unloadable models fall back to a typed call.
            logging.getLogger(__name__).info("Call voice unavailable; the call runs by text")
    call = intake_voice.Call(websocket.send_json, websocket.send_bytes, engine, script)
    call.speaker = websocket.query_params.get('voice') != '0'
    try:
        await call.start()
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await call.audio(message["bytes"])
            elif message.get("text"):
                try:
                    data = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(data, dict):
                    if data.get("type") == "end":
                        break
                    await call.message(data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await call.close()
        if engine is not None:
            intake_voice.release()
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


def demo_scoped_call(handler):
    """Pin the same account-local demo scope for call setup and its entire socket."""
    async def endpoint(websocket: WebSocket):
        from stellis import demo_patient
        if not _websocket_allowed(websocket):
            await websocket.close(code=4401)
            return
        if websocket.cookies.get(demo_patient.COOKIE) != 'maya':
            await handler(websocket)
            return
        workspace = demo_patient.existing_workspace()
        if workspace is None:
            await websocket.close(code=4410)
            return
        task = asyncio.current_task()
        drained = asyncio.Event()
        _demo_call_tasks[task] = drained
        try:
            with storage.demo_workspace(workspace):
                await handler(websocket)
        except asyncio.CancelledError:
            # Clear demo deliberately drains these sockets before deleting files.
            pass
        finally:
            _demo_call_tasks.pop(task,None)
            drained.set()
    return endpoint


async def cancel_demo_calls():
    pending = list(_demo_call_tasks.items())
    for task, _ in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*(drained.wait() for _,drained in pending))


app.add_api_websocket_route("/api/v1/intake/call", demo_scoped_call(intake_call))
app.add_api_websocket_route("/api/v1/coach/call", demo_scoped_call(coach_call))
app.add_api_websocket_route("/api/v1/tour/call", demo_scoped_call(tour_call))


class MorningAnswer(BaseModel):
    day: str = Field(max_length=10)
    answer: Literal["answered", "declined", "snoozed"]
    now: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


@api.get("/coach")
def coach_state(today: str, now: str) -> dict[str, object]:
    """Whether the morning check-in is due, what it covers, and the latest call's transcript."""
    from stellis import coach_call, goals
    script = coach_call
    latest = script.load()
    due = script.morning_due(today, now)
    agenda = _agent_result(lambda: script.agenda(today, goals.overview(today, 0, now))) if due else []
    return {"morning_due": due, "agenda": agenda, "snooze_minutes": script.SNOOZE_MINUTES,
            "latest": {"kind": latest["kind"], "started_at": latest["started_at"],
                       "ended_at": latest["ended_at"],
                       "turns": [{"role": turn["role"], "text": turn["text"]}
                                 for turn in latest["turns"] if turn["text"]]}}


@api.post("/coach/morning")
def coach_morning(request: MorningAnswer) -> dict[str, bool]:
    from stellis import coach_call as script
    _agent_result(lambda: script.answer_morning(request.day, request.answer, request.now))
    return {"saved": True}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the browser workspace shell."""
    return FileResponse(_STATIC / "index.html")


@app.get("/conversation/{conversation_id}", include_in_schema=False)
def conversation_page(conversation_id: str) -> FileResponse:
    """Serve the same browser workspace for a direct conversation link."""
    return FileResponse(_STATIC / "index.html")


@app.get("/recording", include_in_schema=False)
def recording_page() -> FileResponse:
    """Serve the recording workspace on reload."""
    return FileResponse(_STATIC / "index.html")


@app.get("/person/{person_id}", include_in_schema=False)
@app.get("/page/{page_id}", include_in_schema=False)
@app.get("/document/{document_id}", include_in_schema=False)
def person_page(person_id: str | None = None, page_id: str | None = None, document_id: str | None = None) -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/demo", include_in_schema=False)
def legacy_demo_page():
    """Old story links open the actual workspace, without a separate frontend."""
    return RedirectResponse("/", status_code=303)


@app.get("/settings", include_in_schema=False)
@app.get("/todos", include_in_schema=False)
@app.get("/timeline", include_in_schema=False)
@app.get("/records", include_in_schema=False)
@app.get("/summary", include_in_schema=False)
@app.get("/trends", include_in_schema=False)
@app.get("/chat", include_in_schema=False)
@app.get("/chat/{agent_id}", include_in_schema=False)
@app.get("/notebook/{notebook_id}", include_in_schema=False)
def settings_or_notebook_page(notebook_id: str | None = None, agent_id: str | None = None) -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/vocabulary", include_in_schema=False)
@app.get("/vocabulary/{section}", include_in_schema=False)
def vocabulary_page(section: str | None = None) -> FileResponse:
    if section not in {None, "names", "other"}:
        raise HTTPException(status_code=404, detail="Page not found")
    return FileResponse(_STATIC / "index.html")


@api.get("/conversations")
def conversations() -> list[dict[str, object]]:
    assigned = library.assignments()
    items = storage.list_conversations()
    lines = notebooks.summary_points([str(item["id"]) for item in items])
    return [{**item, "category_ids": assigned.get(str(item["id"]), []),
             "summary_line": lines.get(str(item["id"]), {}).get("summary_line"),
             "topic_line": lines.get(str(item["id"]), {}).get("topic_line"),
             **_processing_state(str(item["id"]))} for item in items]


def _processing_state(conversation_id: str) -> dict[str, object]:
    """Let the library show work started anywhere: queued, running, or stopped."""
    job = storage.get_latest_job_for_conversation(conversation_id)
    return {"processing_job": processing.public_job(job) if job and job["state"] != "succeeded" else None}


class NotebookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = None


class CatalogueSelection(BaseModel):
    template_id: str | None = Field(default=None, max_length=100)
    collection_id: str | None = Field(default=None, max_length=100)
    name: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=1000)


class CatalogueActivation(BaseModel):
    selections: list[CatalogueSelection] = Field(min_length=1, max_length=100)


class RecordLink(BaseModel):
    source_id: str = Field(min_length=1, max_length=150)


@api.get("/notebooks/{notebook_id}/brief")
def notebook_brief_view(notebook_id: str, today: str | None = Query(default=None, max_length=10)):
    from stellis import notebook_brief
    try:
        day = datetime.fromisoformat(today).date() if today else None
        return notebook_brief.brief(notebook_id, day)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api.get("/notebooks/{notebook_id}/health")
def notebook_health_context(notebook_id: str):
    from stellis import notebook_health
    try:
        return notebook_health.context(notebook_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/health-catalogue")
def health_catalogue_view():
    from stellis import health_catalogue
    return health_catalogue.view()


@api.get("/health-catalogue/recommendations")
def health_catalogue_recommendations():
    from stellis import health_catalogue
    return health_catalogue.recommend()


@api.post("/health-catalogue/activate")
def health_catalogue_activate(request: CatalogueActivation):
    from stellis import health_catalogue
    try:
        return {"notebooks":health_catalogue.activate([s.model_dump() for s in request.selections])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/notebooks/{notebook_id}/links")
def link_notebook_record(notebook_id: str, request: RecordLink):
    try:
        return notebook_sources.link(notebook_id, request.source_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, LookupError) else 400, detail=str(exc)) from exc


@api.delete("/notebooks/{notebook_id}/links")
def unlink_notebook_record(notebook_id: str, request: RecordLink):
    try:
        return notebook_sources.unlink(notebook_id, request.source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class NotebookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    archived: StrictBool | None = None
    capabilities: list[str] | None = Field(default=None, max_length=10)
    todo_list: Literal["inherit", "on", "off"] | None = None


class NotebookAssignment(BaseModel):
    notebook_id: str | None = None


class LinkPreviewRequest(BaseModel):
    url: str = Field(max_length=2048)


@api.post("/link-previews")
async def link_preview(request: LinkPreviewRequest) -> dict[str, str | None]:
    try:
        return await linkpreview.preview(request.url)
    except linkpreview.AddressRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@api.get("/export")
def export_folder(scope: Literal["collection", "notebook", "all"] = "all", id: str | None = None,
                  format: Literal["markdown", "stellis"] = "markdown") -> FileResponse:
    """Download pages and conversations as a ZIP of Markdown files; the temporary file is deleted after sending."""
    if scope != "all" and not id:
        raise HTTPException(status_code=400, detail="Choose a collection or notebook to export")
    try:
        path = folder_export.build(scope, id, format)
        name = folder_export.archive_name(scope, id, format)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(path.unlink, missing_ok=True))


class NotebookInstructions(BaseModel):
    instructions: str = Field(max_length=4000)
    revision: int = Field(ge=0)


class SourceSelection(BaseModel):
    excluded: list[str] = Field(max_length=500)


class IncludedSources(BaseModel):
    notebook_id: str = Field(max_length=100)
    source_ids: list[str] = Field(max_length=notebook_chat.UNREAD_SOURCES)


class NotebookQuestion(BaseModel):
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    question: str = Field(max_length=notebook_chat.MAX_QUESTION)
    source_ids: list[str] = Field(max_length=500)
    agent_id: str | None = Field(default=None, max_length=100)
    # Sources from other notebooks, read for this question only.
    include: list[IncludedSources] = Field(default_factory=list, max_length=notebook_chat.UNREAD_NOTEBOOKS)
    # Also search public sources, with personal details kept out of each query.
    research: bool = False
    # The page's ID for this question, for reading its search progress while it waits.
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{8,64}$")


@api.get("/daily-brief")
def daily_brief_status():
    from stellis import daily_brief
    return daily_brief.status()


@api.post("/daily-brief")
async def prepare_daily_brief():
    from stellis import daily_brief
    return _agent_result(daily_brief.start)


@api.post("/daily-brief/stop")
async def stop_daily_brief():
    from stellis import daily_brief
    return daily_brief.stop()


async def _comparison_call(action):
    try:
        return await action()
    except asyncio.CancelledError as exc:
        raise HTTPException(status_code=409, detail="Comparison stopped") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except agent_runtime.AgentRunError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.post("/notebooks/{notebook_id}/review-sources")
async def review_notebook_sources(notebook_id: str):
    from stellis import source_comparisons
    return await _comparison_call(lambda: source_comparisons.review(notebook_id))


@api.post("/notebooks/{notebook_id}/comparisons/{identifier}/run")
async def compare_notebook_sources(notebook_id: str, identifier: str):
    from stellis import source_comparisons
    return await _comparison_call(lambda: source_comparisons.compare(notebook_id, identifier))


@api.post("/notebooks/{notebook_id}/comparisons/{identifier}/dismiss")
def dismiss_notebook_comparison(notebook_id: str, identifier: str):
    from stellis import source_comparisons
    return _agent_result(lambda: source_comparisons.dismiss(notebook_id, identifier))


@api.get("/notebooks/work")
def notebook_work_overview():
    return notebook_work.overview()


@api.get("/notebooks/{notebook_id}/activity")
def notebook_activity(notebook_id: str):
    return _agent_result(lambda: notebook_work.activity(notebook_id))


@api.post("/notebooks/{notebook_id}/filings/{filing_id}/undo")
def undo_notebook_filing(notebook_id: str, filing_id: str):
    from stellis import notebook_filing
    return _agent_result(lambda: notebook_filing.undo(notebook_id, filing_id))


@api.get("/notebooks/{notebook_id}/sources")
def notebook_sources_list(notebook_id: str):
    return _agent_result(lambda: {
        "sources": notebook_sources.list_sources(notebook_id),
        "limit": notebook_sources.LIMIT,
    })


@api.put("/notebooks/{notebook_id}/sources/selection")
def notebook_sources_selection(notebook_id: str, request: SourceSelection):
    return _agent_result(lambda: {
        "sources": notebook_sources.save_selection(notebook_id,
                                                   request.excluded),
        "limit": notebook_sources.LIMIT,
    })


@api.get("/notebooks/{notebook_id}/chat")
def notebook_chat_messages(notebook_id: str):
    return _agent_result(
        lambda: {"messages": notebook_chat.messages(notebook_id)})


@api.post("/notebooks/{notebook_id}/ask")
async def notebook_chat_ask(notebook_id: str, request: NotebookQuestion):
    try:
        return await notebook_chat.ask(notebook_id, request.question,
                                       request.source_ids, request.agent_id, request.attachment_ids,
                                       [item.model_dump() for item in request.include],
                                       request.research, request.request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except notebook_chat.ChatError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/notebooks/{notebook_id}/ask/{request_id}/progress")
def notebook_chat_progress(notebook_id: str, request_id: str):
    return notebook_chat.progress(notebook_id, request_id)


@api.put("/notebooks/{notebook_id}/instructions")
def notebook_instructions(notebook_id: str, request: NotebookInstructions):
    return _agent_result(lambda: notebook_work.save_instructions(notebook_id, request.instructions, request.revision))


@api.get("/notebooks")
def list_notebooks() -> dict[str, object]:
    return {"palette": list(notebooks.PALETTE), "presets": list(notebooks.PRESETS), "notebooks": notebooks.list_notebooks()}


@api.post("/notebooks")
def create_notebook(request: NotebookCreate) -> dict[str, object]:
    try:
        return notebooks.create(request.name, request.color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.patch("/notebooks/{notebook_id}")
def update_notebook(notebook_id: str, request: NotebookUpdate) -> dict[str, object]:
    try:
        return notebooks.update(notebook_id, name=request.name, color=request.color, todo_list=request.todo_list, description=request.description, archived=request.archived, capabilities=request.capabilities)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.delete("/notebooks/{notebook_id}")
def delete_notebook(notebook_id: str) -> dict[str, bool]:
    if not notebooks.delete(notebook_id):
        raise HTTPException(status_code=404, detail="Notebook not found")
    return {"deleted": True}


@api.put("/conversations/{conversation_id}/notebook")
def assign_notebook(conversation_id: str, request: NotebookAssignment) -> dict[str, object]:
    _room(request.notebook_id, "conversation:" + conversation_id)
    try:
        return notebooks.assign(conversation_id, request.notebook_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = None


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    todo_list: Literal["on", "off"] | None = None


class CollectionAssignment(BaseModel):
    collection_id: str | None = None


def _collection_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409 if isinstance(exc, collections.NameTaken) else 400, detail=str(exc))


@api.get("/collections")
def list_collections() -> dict[str, object]:
    return {"palette": list(notebooks.PALETTE), "presets": list(notebooks.PRESETS), "collections": collections.list_collections()}


@api.post("/collections", status_code=201)
def create_collection(request: CollectionCreate) -> dict[str, object]:
    try:
        return collections.create(request.name, request.color)
    except ValueError as exc:
        raise _collection_error(exc) from exc


@api.patch("/collections/{collection_id}")
def update_collection(collection_id: str, request: CollectionUpdate) -> dict[str, object]:
    try:
        return collections.update(collection_id, name=request.name, color=request.color, todo_list=request.todo_list)
    except (LookupError, ValueError) as exc:
        raise _collection_error(exc) from exc


@api.delete("/collections/{collection_id}")
def delete_collection(collection_id: str) -> dict[str, bool]:
    if not collections.delete(collection_id):
        raise HTTPException(status_code=404, detail="Collection not found")
    return {"deleted": True}


@api.put("/notebooks/{notebook_id}/collection")
def assign_notebook_collection(notebook_id: str, request: CollectionAssignment) -> dict[str, object]:
    try:
        return collections.assign_notebook(notebook_id, request.collection_id)
    except LookupError as exc:
        raise _collection_error(exc) from exc


def _recorded_at(value: str | None) -> str | None:
    """Accept a browser-reported capture time only when it is a plausible past instant."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    now = datetime.now(timezone.utc)
    if not datetime(1990, 1, 1, tzinfo=timezone.utc) <= stamp <= now + timedelta(minutes=5):
        return None
    return stamp.astimezone(timezone.utc).isoformat()


@api.get("/categories")
def categories() -> list[dict[str, object]]:
    return library.categories()


class NebiusKey(BaseModel):
    key: str = Field(min_length=1, max_length=512)


def _operator_only() -> None:
    """Refuse provider and model changes from accounts that use the deployment's shared keys."""
    from stellis import runtime_profile
    if not runtime_profile.operator():
        raise HTTPException(status_code=403, detail="The deployment owner manages AI settings")


@api.get("/settings/inference")
def inference_settings() -> dict[str, object]:
    from stellis import runtime_settings
    return runtime_settings.status()


@api.post("/settings/inference/check", dependencies=[Depends(_operator_only)])
def check_inference_settings() -> dict[str, object]:
    from stellis import runtime_settings
    return runtime_settings.check()


class LocalModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200)


@api.put("/settings/inference/model", dependencies=[Depends(_operator_only)])
def select_local_model(request: LocalModelSelection) -> dict[str, object]:
    from stellis import runtime_settings
    try:
        return runtime_settings.switch_model(request.model)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class ModelRouteSelection(BaseModel):
    routes: dict[str, str]


@api.put("/settings/inference/routes", dependencies=[Depends(_operator_only)])
def select_model_routes(request: ModelRouteSelection) -> dict[str, object]:
    from stellis import runtime_settings
    try:
        return runtime_settings.save_routes(request.routes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.delete("/settings/inference/traces")
def clear_inference_traces() -> dict[str, object]:
    from stellis import runtime_settings, runtime_trace
    runtime_trace.clear()
    return runtime_settings.status()


@api.get("/settings/runtime-profile")
def runtime_profile_status() -> dict[str, object]:
    from stellis import runtime_profile
    local = runtime_profile.local_inference()
    return {"profile": runtime_profile.selected(), "local_inference": local,
            "label": runtime_profile.label(),
            "model": runtime_profile.model_id(), "agent_configured": agent_runtime.configured(),
            "operator": runtime_profile.operator()}


@api.get("/settings/nebius-key")
def nebius_key_status() -> dict[str, object]:
    return ai.key_status()


@api.put("/settings/nebius-key", dependencies=[Depends(_operator_only)])
def save_nebius_key(request: NebiusKey) -> dict[str, object]:
    try:
        return ai.save_key(request.key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.delete("/settings/nebius-key", dependencies=[Depends(_operator_only)])
def remove_nebius_key() -> dict[str, object]:
    try:
        return ai.remove_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class TavilyKey(BaseModel):
    key: str = Field(min_length=1, max_length=512)


@api.get("/settings/tavily-key")
def tavily_key_status() -> dict[str, object]:
    return tavily.key_status()


@api.put("/settings/tavily-key", dependencies=[Depends(_operator_only)])
def save_tavily_key(request: TavilyKey) -> dict[str, object]:
    try:
        return tavily.save_key(request.key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Could not save the Tavily key") from exc


@api.delete("/settings/tavily-key", dependencies=[Depends(_operator_only)])
def remove_tavily_key() -> dict[str, object]:
    try:
        return tavily.remove_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Could not remove the Tavily key") from exc


@api.get("/setup")
def setup_status() -> dict[str, object]:
    from stellis import models
    return {**models.status(), "missing": models.missing()}


@api.post("/setup")
def start_setup() -> dict[str, object]:
    """Starts or retries the shared speech-model installer."""
    from stellis import models
    models.ensure_in_background()
    return setup_status()


@api.get("/category-groups")
def category_groups() -> list[dict[str, object]]:
    return notebooks.category_groups()


@api.get("/persons")
def persons() -> list[dict[str, object]]:
    return people.list_people()


@api.post("/persons")
def create_person(request: PersonName) -> dict[str, object]:
    try:
        return people.create_person(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/persons/{person_id}")
def person_profile(person_id: str) -> dict[str, object]:
    result = people.get_person(person_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return result


@api.patch("/persons/{person_id}")
def rename_person(person_id: str, request: PersonName) -> dict[str, object]:
    try:
        return people.rename_person(person_id, request.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.patch("/persons/{person_id}/visibility")
def set_person_visibility(person_id: str, request: PersonVisibility) -> dict[str, object]:
    try:
        return people.set_person_hidden(person_id, request.hidden)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.delete("/persons/{person_id}")
def delete_person(person_id: str) -> dict[str, object]:
    try:
        return people.delete_person(person_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/persons/{person_id}/merge")
def merge_people(person_id: str, request: PersonMerge) -> dict[str, object]:
    try:
        return people.merge_people(person_id, request.source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/persons/{person_id}/move-conversation")
def move_person_conversation(person_id: str, request: PersonMove) -> dict[str, object]:
    try:
        return people.move_conversation_links(person_id, request.conversation_id, request.target_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.delete("/persons/{person_id}/conversations/{conversation_id}")
def remove_person_conversation(person_id: str, conversation_id: str) -> dict[str, object]:
    try:
        return people.remove_conversation_links(person_id, conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.put("/conversations/{conversation_id}/person-links")
def confirm_person(conversation_id: str, request: PersonLink) -> dict[str, object]:
    try:
        return people.confirm_person(conversation_id, request.segment_id, request.person_id,
                                     request.scope, request.expected_revision, name=request.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/person-links/remove")
def remove_person(conversation_id: str, request: PersonUnlink) -> dict[str, object]:
    try:
        return people.remove_person(conversation_id, request.segment_id, request.scope,
                                    request.expected_revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/categories/automation")
def category_automation_settings() -> dict[str, object]:
    return categorization.settings()


@api.put("/categories/automation")
def set_category_automation(request: CategoryAutomationSetting) -> dict[str, object]:
    return categorization.set_enabled(request.enabled)


@api.get("/conversations/{conversation_id}/categorization")
def categorization_status(conversation_id: str) -> dict[str, object]:
    try:
        return categorization.status(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/categorization")
def retry_categorization(conversation_id: str) -> dict[str, object]:
    try:
        return categorization.request(conversation_id, retry=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/categories")
def create_category(request: CategoryName) -> dict[str, object]:
    try:
        return library.create_category(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.patch("/categories/{category_id}")
def rename_category(category_id: str, request: CategoryName) -> dict[str, object]:
    try:
        return library.rename_category(category_id, request.name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.delete("/categories/{category_id}")
def delete_category(category_id: str) -> dict[str, bool]:
    if not library.delete_category(category_id):
        raise HTTPException(status_code=404, detail="Category not found")
    return {"deleted": True}


@api.put("/conversations/{conversation_id}/categories/{category_id}")
def assign_category(conversation_id: str, category_id: str) -> dict[str, object]:
    try:
        return {"category_ids": library.set_category(conversation_id, category_id, True)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.delete("/conversations/{conversation_id}/categories/{category_id}")
def remove_category(conversation_id: str, category_id: str) -> dict[str, object]:
    try:
        return {"category_ids": library.set_category(conversation_id, category_id, False)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/trash")
def trash() -> list[dict[str, object]]:
    assigned = library.assignments()
    return [{**item, "category_ids": assigned.get(str(item["id"]), []),
             "expires_at": library.expires_at(str(item["trashed_at"])), "kind": "conversation"}
            for item in storage.list_conversations(trashed=True)] + [
        {**item, "expires_at": library.expires_at(str(item["trashed_at"])), "kind": "page"}
        for item in pages.trashed()]


@api.post("/conversations/{conversation_id}/trash")
def move_to_trash(conversation_id: str) -> dict[str, object]:
    try:
        return library.move_to_trash(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/trash/{conversation_id}/restore")
def restore_from_trash(conversation_id: str) -> dict[str, object]:
    try:
        return library.restore(conversation_id)
    except LookupError as exc:
        try:
            return pages.restore(conversation_id)
        except LookupError:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.delete("/trash/{conversation_id}")
def delete_from_trash(conversation_id: str) -> dict[str, object]:
    try:
        return library.delete_permanently(conversation_id)
    except LookupError as exc:
        try:
            return pages.delete_permanently(conversation_id)
        except LookupError:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/search")
def library_search(q: str = Query(min_length=2, max_length=120), category_id: str | None = None) -> dict[str, object]:
    try:
        return search.search_library(q, category_id=category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/search/sources")
def source_search_results(q: str = Query(min_length=1, max_length=200), notebook_id: str | None = None) -> dict[str, object]:
    try:
        return {"results": source_search.search_sources(q, notebook_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _room(notebook_id: str | None, source_id: str | None = None) -> None:
    """Refuses a new source for a full notebook with a 409.

    An unknown notebook passes here, so the caller still answers with its own
    404.
    """
    try:
        notebook_sources.require_room(notebook_id, source_id)
    except LookupError:
        return
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/documents")
def list_documents() -> list[dict[str, object]]:
    return documents.list_all()


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=documents.MAX_TITLE)
    record_date: str | None = Field(default=None, max_length=10)
    notebook_id: str | None = None
    move: bool = False


@api.post("/documents")
async def upload_document(
        file: UploadFile = File(...),
        notebook_id: str | None = Form(None)) -> dict[str, object]:
    content = await file.read(documents.MAX_BYTES + 1)
    demo_source = None
    if storage.in_demo():
        from stellis import demo_patient
        digest = hashlib.sha256(content).hexdigest()
        guide = demo_patient.guide()
        # Her five years, plus follow-up records the short tour adds after them.
        demo_source = next((s for s in [*guide['steps'], *guide.get('followups', [])] if s['sha256']==digest),None)
        if demo_source is None:
            raise HTTPException(422,'Choose an original Maya report from the demo ZIP. Use My records for your own files.')
    _room(notebook_id or None)
    try:
        record, duplicate = await asyncio.to_thread(documents.create,
                                                    file.filename or "",
                                                    content, notebook_id or
                                                    None)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except documents.DocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if demo_source and not duplicate:
        record = documents.update(record['id'], title=demo_source['title'])
    await asyncio.to_thread(health_facts.prepare, record['id'])
    from stellis import routine_review
    # A demo guest adds Maya's 70 records at once; preparing each care review ahead is a model call per
    # record that no cache can share (each prompt names the guest's own record), and it used up the guest's
    # daily allowance before sorting (#258). In the demo the review runs when asked for instead.
    if not duplicate and not storage.in_demo():routine_review.kickoff('document',record['id'])
    return {"document": record, "duplicate": duplicate}


@api.get("/documents/{document_id}")
def get_document(document_id: str):
    return _agent_result(lambda: documents.get(document_id))


class DocumentPageText(BaseModel):
    text: str = Field(max_length=documents.MAX_PAGE_TEXT)
    revision: int = Field(ge=0)


@api.patch("/documents/{document_id}/pages/{number}")
def update_document_page(document_id: str, number: int, request: DocumentPageText):
    try:
        return documents.save_page(document_id, number, request.text, request.revision)
    except documents.DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/documents/{document_id}/pages/{number}/read")
def read_document_page(document_id: str, number: int):
    from stellis import document_ocr
    try:
        record = document_ocr.read_page(document_id, number)
        health_facts.prepare(document_id)
        return record
    except documents.DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except document_ocr.OcrError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DocumentTitleUndo(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)


@api.post("/documents/{document_id}/title/undo")
def undo_document_title(document_id: str, request: DocumentTitleUndo):
    from stellis import document_naming
    try:
        return document_naming.undo(document_id, request.run_id)
    except documents.DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/documents/{document_id}/title/retry")
def retry_document_title(document_id: str):
    from stellis import document_naming
    try:
        return document_naming.enrich(document_id, retry=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/documents/{document_id}/file")
def document_file(document_id: str) -> FileResponse:
    try:
        path, media_type = documents.file_path(document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path,
                        media_type=media_type,
                        headers={"Cache-Control": "private, no-store"})


@api.patch("/documents/{document_id}")
def update_document(document_id: str, request: DocumentUpdate):
    if request.move:
        _room(request.notebook_id, "document:" + document_id)
    return _agent_result(lambda: documents.update(
        document_id,
        title=request.title,
        record_date=request.record_date,
        notebook_id=request.notebook_id,
        move=request.move))


@api.delete("/documents/{document_id}")
def delete_document(document_id: str) -> dict[str, bool]:
    _agent_result(lambda: documents.delete(document_id))
    return {"deleted": True}


@api.get("/documents/{document_id}/facts")
def document_facts(document_id: str):
    _agent_result(lambda: documents.get(document_id))
    receipt = health_facts.prepare(document_id)
    return {"facts": health_facts.list_for(document_id), "receipt": receipt}


@api.post("/documents/{document_id}/facts/extract")
async def extract_document_facts(document_id: str):
    try:
        return await health_facts.extract(document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except health_facts.ExtractionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class SortItem(BaseModel):
    kind: str = Field(max_length=20)
    id: str = Field(max_length=100)


class SortGroup(BaseModel):
    notebook_name: str = Field(default="", max_length=sorting.MAX_NAME)
    notebook_id: str | None = Field(default=None, max_length=100)
    collection_id: str | None = Field(default=None, max_length=100)
    items: list[SortItem] = Field(max_length=sorting.MAX_ITEMS)


class RecordMove(BaseModel):
    notebook_id: str | None = Field(default=None, max_length=100)


@api.get("/records/unsorted")
def unsorted_records():
    return {"items": [item.public() for item in sorting.unsorted()]}


@api.post("/records/sort/suggest")
async def suggest_sorting():
    try:
        return await sorting.suggest()
    except sorting.SortingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.post("/records/sort/apply")
def apply_sorting(request: SortGroup):
    return _agent_result(lambda: sorting.apply(
        request.notebook_name, [item.model_dump() for item in request.items],
        request.notebook_id, request.collection_id))


@api.put("/records/{kind}/{item_id}/notebook")
def move_record(kind: str, item_id: str, request: RecordMove):
    _agent_result(lambda: sorting.move(kind, item_id, request.notebook_id))
    return {"kind": kind, "id": item_id, "notebook_id": request.notebook_id}


class DashboardChoices(BaseModel):
    notebooks: list[str] = Field(max_length=3)
    measurements: list[str] = Field(max_length=3)
    care_contexts: list[str] = Field(max_length=3)


@api.get('/home/care')
def home_care_view(day: str | None = None, utc_offset: int = 0):
    from stellis import home_care
    return _agent_result(lambda:home_care.view(day,utc_offset))

class HomeContextIn(BaseModel):
    tracker_id: str | None = Field(default=None,max_length=80)

@api.put('/home/context')
def home_care_context(request: HomeContextIn):
    from stellis import home_care
    return _agent_result(lambda:home_care.choose(request.tracker_id))

@api.get("/home/focus")
def home_focus(today: str | None = None) -> dict[str, object]:
    """The most pressing items across the notebooks, for the top of My Health."""
    from stellis import home_focus as focus
    try:
        day = datetime.fromisoformat(today).date() if today else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Use a date such as 2026-10-26") from exc
    try:
        results = focus.latest_results()
    except Exception:  # The tiles are extra; For you now still loads without them.
        results = []
    if focus.has_history():
        return {"items": focus.items(day), "results": results, "empty": False}
    # No records yet: Home leads with Ask Stellis and first steps chosen from what is missing. The first
    # steps already offer Add a record, so each empty notebook does not offer it again.
    from stellis import storage
    return {"items": [item for item in focus.items(day) if item["kind"] != "empty"], "results": results, "empty": True,
            "ideas": focus.starting_ideas(in_example=storage.in_demo())}


@api.get("/dashboard/preferences")
def dashboard_preferences():
    return dashboard.preferences()


@api.put("/dashboard/preferences")
def save_dashboard_preferences(request: DashboardChoices):
    return _agent_result(lambda: dashboard.save(request.model_dump()))


@api.get("/summary")
def health_summary_latest():
    return {
        "summary": health_summary.latest(),
        "document_count": health_summary.ready_count()
    }


@api.post("/summary/refresh")
async def health_summary_refresh():
    try:
        return {"summary": await health_summary.refresh()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except notebook_chat.ChatError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/timeline")
def health_timeline(notebook_id: str | None = None, kinds: str | None = None):
    selected = tuple(kinds.split(",")) if kinds else timeline.KINDS
    return _agent_result(lambda: {
        "entries": timeline.entries(notebook_id or None, selected)
    })


class ReadingIn(BaseModel):
    notebook_id: str | None = Field(default=None, max_length=80)
    capture_id: str | None = Field(default=None, min_length=1, max_length=80)
    measure: str = Field(max_length=40)
    value: float
    value2: float | None = None
    taken_at: str | None = Field(default=None, max_length=40)
    source: str = Field(default="self", max_length=10)
    quality: float | None = None
    note: str = Field(default="", max_length=readings.MAX_NOTE)


@api.get("/readings")
def list_readings(notebook_id: str | None = Query(default=None, max_length=80)):
    if notebook_id is not None:
        return _agent_result(lambda: readings.notebook_measurements(notebook_id))
    return {"measures": readings.series(), "suggestions": []}


@api.post("/readings")
def add_reading(request: ReadingIn):
    return _agent_result(lambda: readings.add(
        request.measure, request.value, request.value2, request.taken_at,
        request.source, request.quality, request.note,
        notebook_id=request.notebook_id, capture_id=request.capture_id))


class PhotoReadingIn(ReadingIn):
    document_id: str = Field(max_length=80)
    unit: str = Field(max_length=12)
    capture_id: str = Field(min_length=1, max_length=80)


@api.post("/photo-readings/{document_id}/extract")
def photo_candidates(document_id: str, measure: str):
    return _agent_result(lambda: photo_readings.extract(document_id, measure))


@api.post("/photo-readings")
def confirm_photo_reading(request: PhotoReadingIn):
    def save():
        record = documents.get(request.document_id)
        if not record['media_type'].startswith('image/'):
            raise ValueError('Choose a photo for a device reading')
        value = photo_readings.normalize(request.measure, request.value, request.unit)
        return readings.add(request.measure, value, request.value2, request.taken_at,
                            note=request.note, notebook_id=request.notebook_id,
                            document_id=request.document_id, capture_id=request.capture_id)
    return _agent_result(save)


class CareEventIn(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    starts_at: str = Field(max_length=40)
    status: str = Field(max_length=12)
    notebook_id: str | None = Field(default=None, max_length=80)
    note: str = Field(default='', max_length=1000)


@api.get('/care-events')
def list_care_events():
    return {'events': care_events.list_all()}


@api.post('/care-events')
def add_care_event(request: CareEventIn):
    return _agent_result(lambda: care_events.add(**request.model_dump()))


@api.delete('/care-events/{identifier}')
def delete_care_event(identifier: str):
    _agent_result(lambda: care_events.delete(identifier))
    return {'deleted': True}


@api.delete("/readings/{reading_id}")
def delete_reading(reading_id: str) -> dict[str, bool]:
    _agent_result(lambda: readings.delete(reading_id))
    return {"deleted": True}


class GoalIn(BaseModel):
    activity: str = Field(max_length=40)
    amount: float | None = None
    medicine: str = Field(default="", max_length=goals.MAX_NAME)
    dose_amount: float | None = None
    dose_unit: str = Field(default="", max_length=20)
    times_per_week: int
    reminder: str | None = Field(default=None, max_length=5)


class GoalDay(BaseModel):
    done: bool


class GoalDescription(BaseModel):
    text: str = Field(max_length=2000)


@api.get("/goals")
def list_goals(today: str = Query(max_length=10), now: str = Query(max_length=5),
               utc_offset: int = 0):
    return _agent_result(lambda: goals.overview(today, utc_offset, now))


@api.post("/goals")
def create_goal(request: GoalIn):
    return _agent_result(lambda: goals.create(request.model_dump()))


@api.post("/goals/drafts")
async def draft_goals(request: GoalDescription):
    from stellis import coach
    try:
        return await coach.draft_goals(request.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except coach.CoachError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.put("/goals/{goal_id}")
def update_goal(goal_id: str, request: GoalIn) -> dict[str, bool]:
    _agent_result(lambda: goals.update(goal_id, request.model_dump()))
    return {"updated": True}


@api.delete("/goals/{goal_id}")
def delete_goal(goal_id: str) -> dict[str, bool]:
    _agent_result(lambda: goals.delete(goal_id))
    return {"deleted": True}


@api.put("/goals/{goal_id}/days/{day}")
def mark_goal_day(goal_id: str, day: str, request: GoalDay) -> dict[str, bool]:
    _agent_result(lambda: goals.mark(goal_id, day, request.done))
    return {"saved": True}


@api.post("/goals/{goal_id}/days/{day}/not-yet")
def goal_not_yet(goal_id: str, day: str) -> dict[str, bool]:
    _agent_result(lambda: goals.not_yet(goal_id, day))
    return {"saved": True}


@api.get("/facts")
def accepted_facts():
    return {"facts": health_facts.accepted()}


@api.post("/documents/{document_id}/facts/accept")
def accept_document_facts(document_id: str):
    _agent_result(lambda: documents.get(document_id))
    return {"accepted": health_facts.accept_all(document_id)}


@api.post("/facts/{fact_id}/accept")
def accept_fact(fact_id: str):
    return _agent_result(lambda: health_facts.accept(fact_id))


@api.delete("/facts/{fact_id}")
def reject_fact(fact_id: str) -> dict[str, bool]:
    _agent_result(lambda: health_facts.reject(fact_id))
    return {"deleted": True}


@api.post("/conversations/import")
async def import_conversation(file: UploadFile = File(...), recorded_at: str | None = Form(None),
                              notebook_id: str | None = Form(None)) -> dict[str, object]:
    if notebook_id and not notebooks.exists(notebook_id):
        raise HTTPException(status_code=404, detail="Notebook not found")
    _room(notebook_id or None)
    try:
        record, duplicate = await import_upload(file, recorded_at=_recorded_at(recorded_at), notebook_id=notebook_id or None)
    except ImportFailure as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = storage.get_latest_job_for_conversation(str(record["id"]))
    if storage.get_transcript(str(record["id"])) is None:
        ready = processing.capabilities()["features"]
        if ready["transcribe"] and ready["diarize"]:
            job = processing.request_processing(str(record["id"]))
    else:
        try:
            enhanced_notes.request(str(record["id"]))
        except (LookupError, ValueError, ai.AIError):
            pass
    return {
        "conversation": _public(record),
        "duplicate": duplicate,
        "processing_job": processing.public_job(job) if job else None,
    }


@api.get("/recordings")
def recordings() -> list[dict[str, object]]:
    return recording.list_sessions()


@api.delete("/recordings/{recording_id}")
def discard_recording(recording_id: str) -> dict[str, bool]:
    try:
        if not recording.discard_session(recording_id):
            raise HTTPException(status_code=404, detail="Recording session not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": True}


@api.post("/recordings")
def start_recording(request: RecordingStart) -> dict[str, object]:
    try:
        if request.notebook_id and not notebooks.exists(request.notebook_id):
            raise ValueError("Notebook not found")
        event = (request.calendar_provider, request.calendar_event_id) if request.calendar_provider and request.calendar_event_id else None
        return recording.create_session(request.title, request.media_type, request.notebook_id, event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not reserve local recording storage") from exc


@api.patch("/recordings/{recording_id}")
def rename_recording(recording_id: str, request: RecordingRename) -> dict[str, object]:
    try:
        return recording.rename_session(recording_id, request.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.put("/recordings/{recording_id}/notebook")
def recording_notebook(recording_id: str, request: NotebookAssignment) -> dict[str, object]:
    try:
        return recording.assign_notebook(recording_id, request.notebook_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.put("/recordings/{recording_id}/chunks/{sequence}")
async def store_recording_chunk(recording_id: str, sequence: int, request: Request) -> dict[str, object]:
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > recording.MAX_CHUNK_BYTES:
            raise HTTPException(status_code=413, detail="Recording chunk exceeds 8 MB")
    try:
        return recording.save_chunk(recording_id, sequence, bytes(data))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not store the recording chunk") from exc


@api.post("/recordings/{recording_id}/events")
def recording_event(recording_id: str, event: RecordingEvent) -> dict[str, object]:
    try:
        return recording.record_event(recording_id, event.kind, event.media_position_ms)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/recordings/{recording_id}/finalize")
async def finalize_recording(recording_id: str) -> dict[str, object]:
    try:
        result = await recording.finalize_session(recording_id)
        conversation_id = str(result["conversation"]["id"])
        if storage.get_transcript(conversation_id) is None:
            ready = processing.capabilities()["features"]
            if ready["transcribe"] and ready["diarize"]:
                try:
                    processing.request_processing(conversation_id)
                except RuntimeError:
                    pass  # Finalization succeeded; a pending review can block another speech job.
        else:
            try:
                enhanced_notes.request(conversation_id)
            except (LookupError, ValueError, ai.AIError):
                pass
        return {"conversation": _public(result["conversation"]), "duplicate": result["duplicate"]}
    except (ValueError, ImportFailure) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not finalize the local recording") from exc


@api.get("/recordings/{recording_id}/preview")
def recording_preview_latest(recording_id: str) -> dict[str, object]:
    try:
        return recording_preview.latest(recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/recordings/{recording_id}/preview")
def recording_preview_generate(recording_id: str) -> dict[str, object]:
    try:
        return recording_preview.generate(recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Local preview transcription failed") from exc


def _read_notes(scope: str, item_id: str) -> dict[str, object]:
    try:
        return notes.get(scope, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _dates(request) -> dict | None:
    """The editor's resolved due dates by task ID; a null entry clears the date."""
    if request.todo_dates is None:
        return None
    return {key: value.model_dump() if value else None for key, value in request.todo_dates.items()}


def _write_notes(scope: str, item_id: str, request: NotesWrite) -> dict[str, object]:
    try:
        return notes.save(scope, item_id, request.markdown, request.expected_revision, _dates(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class PageCategories(BaseModel):
    category_ids: list[str] = Field(max_length=200)


def _page_call(call, *args):
    try:
        return call(*args)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except pages.TooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/pages")
def list_pages(notebook_id: str | None = None) -> list[dict[str, object]]:
    return pages.list_pages(notebook_id)


@api.post("/pages", status_code=201)
def create_page(request: PageCreate) -> dict[str, object]:
    _room(request.notebook_id)
    return _page_call(pages.create, request.title, request.notebook_id, request.markdown)


@api.get("/pages/{page_id}")
def read_page(page_id: str) -> dict[str, object]:
    return _page_call(pages.get, page_id)


@api.patch("/pages/{page_id}")
def rename_page(page_id: str, request: PageRename) -> dict[str, object]:
    return _page_call(pages.rename, page_id, request.title)


@api.put("/pages/{page_id}/markdown")
def save_page(page_id: str, request: PageWrite) -> dict[str, object]:
    return _page_call(pages.save, page_id, request.markdown, request.expected_revision, _dates(request))


@api.put("/pages/{page_id}/notebook")
def assign_page_notebook(page_id: str, request: NotebookAssignment) -> dict[str, object]:
    _room(request.notebook_id, "page:" + page_id)
    return _page_call(pages.assign_notebook, page_id, request.notebook_id)


@api.put("/pages/{page_id}/categories")
def set_page_categories(page_id: str, request: PageCategories) -> dict[str, object]:
    return _page_call(pages.set_categories, page_id, request.category_ids)


@api.post("/pages/{page_id}/trash")
def trash_page(page_id: str) -> dict[str, object]:
    return _page_call(pages.move_to_trash, page_id)


@api.get("/todos")
def gather_todos(notebook_id: str = "all", status: Literal["open", "done", "canceled"] = "open") -> dict[str, object]:
    return todos.gather(notebook_id, status)


@api.patch("/todos/{todo_id}")
def update_todo(todo_id: str, request: TodoStatus) -> dict[str, object]:
    try:
        return todos.set_status(todo_id, request.status)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class SuggestionIds(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class SuggestionPreview(SuggestionIds):
    decision: Literal["accept", "reject"]


class SuggestionAmend(BaseModel):
    values: dict


def _suggestion_call(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/suggestions")
def list_suggestions(target_kind: Literal["conversation", "page"] | None = None, target_id: str | None = None,
                     notebook_id: str | None = None) -> dict[str, object]:
    return {"suggestions": _suggestion_call(suggestions.list_pending, target_kind, target_id, notebook_id)}


@api.patch("/suggestions/{suggestion_id}")
def amend_suggestion(suggestion_id: str, request: SuggestionAmend) -> dict[str, object]:
    return _suggestion_call(suggestions.amend, suggestion_id, request.values)


@api.post("/suggestions/accept")
def accept_suggestions(request: SuggestionIds) -> dict[str, list[str]]:
    return _suggestion_call(suggestions.accept, request.ids)


@api.post("/suggestions/reject")
def reject_suggestions(request: SuggestionIds) -> dict[str, list[str]]:
    return _suggestion_call(suggestions.reject, request.ids)


@api.post("/suggestions/preview")
def preview_suggestions(request: SuggestionPreview) -> dict[str, object]:
    return _suggestion_call(suggestions.preview, request.ids, request.decision)


@api.get("/recordings/{recording_id}/notes")
def recording_notes(recording_id: str) -> dict[str, object]:
    return _read_notes("recording", recording_id)


@api.put("/recordings/{recording_id}/notes")
def save_recording_notes(recording_id: str, request: NotesWrite) -> dict[str, object]:
    return _write_notes("recording", recording_id, request)


@api.get("/conversations/{conversation_id}/notes")
def conversation_notes(conversation_id: str) -> dict[str, object]:
    return _read_notes("conversation", conversation_id)


@api.put("/conversations/{conversation_id}/notes")
def save_conversation_notes(conversation_id: str, request: NotesWrite) -> dict[str, object]:
    return _write_notes("conversation", conversation_id, request)


def _list_images(scope: str, item_id: str) -> list[dict[str, object]]:
    try:
        return images.list_images(scope, item_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _add_image(scope: str, item_id: str, file: UploadFile,
                     media_position_ms: int, segment_id: str | None, caption: str,
                     start_offset: int | None = None, end_offset: int | None = None,
                     expected_revision: int | None = None) -> dict[str, object]:
    try:
        data = await file.read(images.MAX_UPLOAD_BYTES + 1)
        if len(data) > images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Image must be smaller than 12 MB")
        return images._public(images.add(scope, item_id, data, media_position_ms, segment_id, caption,
                                         start_offset, end_offset, expected_revision))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not save image locally") from exc


def _image_file(scope: str, item_id: str, image_id: str, download: bool) -> FileResponse:
    try:
        record = images.get(scope, item_id, image_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    path = images.path_for(record)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image file is missing")
    return FileResponse(path, media_type=str(record["media_type"]), filename=f"meeting-image-{image_id}.{path.suffix[1:]}",
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


def _update_image_caption(scope: str, item_id: str, image_id: str, caption: str) -> dict[str, object]:
    try:
        return images.update_caption(scope, item_id, image_id, caption)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _delete_image(scope: str, item_id: str, image_id: str) -> dict[str, bool]:
    try:
        images.delete(scope, item_id, image_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True}


@api.get("/recordings/{recording_id}/images")
def recording_images(recording_id: str) -> list[dict[str, object]]:
    return _list_images("recording", recording_id)


@api.post("/recordings/{recording_id}/images")
async def add_recording_image(recording_id: str, file: UploadFile = File(...),
                              media_position_ms: int = Form(...), caption: str = Form("")) -> dict[str, object]:
    return await _add_image("recording", recording_id, file, media_position_ms, None, caption)


@api.get("/conversations/{conversation_id}/images")
def conversation_images(conversation_id: str) -> list[dict[str, object]]:
    return _list_images("conversation", conversation_id)


@api.post("/conversations/{conversation_id}/images")
async def add_conversation_image(conversation_id: str, file: UploadFile = File(...),
                                 media_position_ms: int = Form(...), segment_id: str | None = Form(None),
                                 caption: str = Form(""), start_offset: int | None = Form(None),
                                 end_offset: int | None = Form(None), expected_revision: int | None = Form(None)) -> dict[str, object]:
    return await _add_image("conversation", conversation_id, file, media_position_ms, segment_id, caption,
                            start_offset, end_offset, expected_revision)


@api.get("/pages/{page_id}/images")
def page_images(page_id: str) -> list[dict[str, object]]:
    return _list_images("page", page_id)


@api.post("/pages/{page_id}/images")
async def add_page_image(page_id: str, file: UploadFile = File(...), caption: str = Form("")) -> dict[str, object]:
    try:
        data = await file.read(images.MAX_UPLOAD_BYTES + 1)
        if len(data) > images.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Image must be smaller than 12 MB")
        return images._public(images.add_page_image(page_id, data, caption))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not save image locally") from exc


@api.get("/pages/{page_id}/images/{image_id}/file")
def page_image_file(page_id: str, image_id: str, download: bool = False) -> FileResponse:
    return _image_file("page", page_id, image_id, download)


@api.patch("/pages/{page_id}/images/{image_id}")
def edit_page_image(page_id: str, image_id: str, request: ImageCaption) -> dict[str, object]:
    return _update_image_caption("page", page_id, image_id, request.caption)


@api.delete("/pages/{page_id}/images/{image_id}")
def remove_page_image(page_id: str, image_id: str) -> dict[str, bool]:
    return _delete_image("page", page_id, image_id)


@api.post("/images/{image_id}/copy", status_code=201)
def copy_image(image_id: str, request: ImageCopy) -> dict[str, object]:
    """Copy an image to another owner when a `stellis-image` reference is pasted there."""
    try:
        return {"id": images.copy(image_id, request.scope, request.owner_id)["id"]}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail="Could not save image locally") from exc


@api.get("/recordings/{recording_id}/images/{image_id}/file")
def recording_image_file(recording_id: str, image_id: str, download: bool = False) -> FileResponse:
    return _image_file("recording", recording_id, image_id, download)


@api.get("/conversations/{conversation_id}/images/{image_id}/file")
def conversation_image_file(conversation_id: str, image_id: str, download: bool = False) -> FileResponse:
    return _image_file("conversation", conversation_id, image_id, download)


@api.patch("/recordings/{recording_id}/images/{image_id}")
def edit_recording_image(recording_id: str, image_id: str, request: ImageCaption) -> dict[str, object]:
    return _update_image_caption("recording", recording_id, image_id, request.caption)


@api.patch("/conversations/{conversation_id}/images/{image_id}")
def edit_conversation_image(conversation_id: str, image_id: str, request: ImageCaption) -> dict[str, object]:
    return _update_image_caption("conversation", conversation_id, image_id, request.caption)


@api.delete("/recordings/{recording_id}/images/{image_id}")
def remove_recording_image(recording_id: str, image_id: str) -> dict[str, bool]:
    return _delete_image("recording", recording_id, image_id)


@api.delete("/conversations/{conversation_id}/images/{image_id}")
def remove_conversation_image(conversation_id: str, image_id: str) -> dict[str, bool]:
    return _delete_image("conversation", conversation_id, image_id)


@api.get("/recordings/{recording_id}/chat")
def recording_chat(recording_id: str) -> list[dict[str, object]]:
    try:
        return recording_ai.list_runs(recording_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/recordings/{recording_id}/chat")
def ask_during_recording(recording_id: str, request: RecordingQuestion) -> dict[str, object]:
    try:
        return recording_ai.request(recording_id, request.question)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}/recording-chat")
def conversation_recording_chat(conversation_id: str) -> list[dict[str, object]]:
    try:
        return recording_ai.list_conversation_runs(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/recording-chat/{run_id}")
def recording_chat_run(run_id: str) -> dict[str, object]:
    try:
        return recording_ai.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}/agent-runs")
def list_agent_runs(conversation_id: str) -> list[dict[str, object]]:
    try:
        return transcript_agent.list_runs(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}/enhanced-notes")
def list_enhanced_notes(conversation_id: str) -> list[dict[str, object]]:
    try:
        return enhanced_notes.list_runs(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/enhanced-notes")
def generate_enhanced_notes(conversation_id: str, request: EnhancedNotesRequest) -> dict[str, object]:
    try:
        return enhanced_notes.request(conversation_id, force=request.force)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/enhanced-notes/{run_id}")
def enhanced_note(run_id: str) -> dict[str, object]:
    try:
        return enhanced_notes.get_run(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.put("/enhanced-notes/{run_id}")
def edit_enhanced_note(run_id: str, request: NotesWrite) -> dict[str, object]:
    try:
        return enhanced_notes.edit(run_id, request.markdown, request.expected_revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}")
def conversation(conversation_id: str) -> dict[str, object]:
    record = storage.get_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _public(record)


@api.patch("/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, request: ConversationRename) -> dict[str, object]:
    try:
        return _public(storage.rename_conversation(conversation_id, request.title))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}/audio")
def conversation_audio(conversation_id: str) -> FileResponse:
    record = storage.get_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    path = storage.data_dir() / "media" / str(record["audio_filename"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(path, media_type=str(record["audio_media_type"]), headers={"Cache-Control": "no-store"})


@api.get("/capabilities")
def capabilities() -> dict[str, object]:
    return {**processing.capabilities(), "ai": ai.capabilities()}


@api.get("/conversations/{conversation_id}/ai-runs")
def ai_runs(conversation_id: str) -> list[dict[str, object]]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [ai.public_run(item) for item in storage.list_ai_runs(conversation_id)]


@api.post("/conversations/{conversation_id}/ai-runs")
def start_ai_run(conversation_id: str, request: AIRunRequest) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return ai.public_run(ai.request_run(conversation_id, request.kind, request.question, request.force))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@api.get("/ai-runs/{run_id}")
def ai_run(run_id: str) -> dict[str, object]:
    record = storage.get_ai_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="AI run not found")
    return ai.public_run(record)


@api.get("/conversations/{conversation_id}/processing-job")
def conversation_job(conversation_id: str) -> dict[str, object] | None:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    job = storage.get_latest_job_for_conversation(conversation_id)
    return processing.public_job(job) if job else None


@api.post("/conversations/{conversation_id}/processing-jobs")
def process_conversation(conversation_id: str) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    ready = processing.capabilities()["features"]
    if not ready["transcribe"] or not ready["diarize"]:
        raise HTTPException(status_code=409, detail="Install the local speech models before processing")
    try:
        return processing.public_job(processing.request_processing(conversation_id))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/speaker-rematch")
def rematch_speakers(conversation_id: str) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not processing.model_paths()[2].is_file():
        raise HTTPException(status_code=409, detail="Install the local speaker embedding model")
    try:
        return processing.public_job(processing.request_rematch(conversation_id))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/word-alignment")
def align_conversation_words(conversation_id: str, request: WordAlignmentRequest | None = None) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return processing.public_job(processing.request_word_alignment(
            conversation_id, request.source_generation_version if request else None))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/conversations/{conversation_id}/silence-scan")
def scan_conversation_silence(conversation_id: str) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return processing.public_job(processing.request_silence_scan(conversation_id))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.get("/jobs/{job_id}")
def job(job_id: str) -> dict[str, object]:
    record = storage.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return processing.public_job(record)


@api.get("/conversations/{conversation_id}/transcript")
def transcript(conversation_id: str) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = storage.get_display_transcript(conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Transcript is not ready")
    return result


@api.get("/conversations/{conversation_id}/import-reference")
def import_reference(conversation_id: str) -> dict[str, object] | None:
    """Returns the imported transcript a recording came with, or null for one recorded or uploaded here."""
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return storage.get_import_reference(conversation_id)


@api.post("/conversations/{conversation_id}/highlights")
def add_highlight(conversation_id: str, selection: HighlightSelection) -> dict[str, object]:
    try:
        return storage.add_highlight(conversation_id, selection.segment_id,
                                     selection.start_offset, selection.end_offset,
                                     selection.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.delete("/conversations/{conversation_id}/highlights/{highlight_id}")
def delete_highlight(conversation_id: str, highlight_id: str) -> dict[str, bool]:
    if not storage.delete_highlight(conversation_id, highlight_id):
        raise HTTPException(status_code=404, detail="Highlight not found")
    return {"deleted": True}


@api.post("/conversations/{conversation_id}/comments")
def add_comment(conversation_id: str, request: CommentSelection) -> dict[str, object]:
    try:
        return storage.add_comment(conversation_id, request.segment_id, request.start_offset,
                                   request.end_offset, request.body, request.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.patch("/conversations/{conversation_id}/comments/{comment_id}")
def update_comment(conversation_id: str, comment_id: str, request: CommentBody) -> dict[str, object]:
    try:
        return storage.update_comment(conversation_id, comment_id, request.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.delete("/conversations/{conversation_id}/comments/{comment_id}")
def delete_comment(conversation_id: str, comment_id: str) -> dict[str, bool]:
    if not storage.delete_comment(conversation_id, comment_id):
        raise HTTPException(status_code=404, detail="Comment not found")
    return {"deleted": True}


@api.get("/conversations/{conversation_id}/transcript/generations")
def transcript_generations(conversation_id: str) -> list[dict[str, object]]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return storage.list_transcript_generations(conversation_id)


@api.get("/conversations/{conversation_id}/transcript/generations/{version}")
def transcript_generation(conversation_id: str, version: str) -> dict[str, object]:
    record = storage.get_transcript_generation(conversation_id, version)
    if record is None:
        raise HTTPException(status_code=404, detail="Transcript generation not found")
    return {
        "conversation_id": conversation_id, "pipeline_version": version,
        "revision": record["revision"], "language": record["language"],
        "segments": json.loads(str(record["segments_json"])),
        "turns": json.loads(str(record["turns_json"])),
        "created_at": record["created_at"],
    }


@api.patch("/conversations/{conversation_id}/transcript/segments")
def edit_segments(conversation_id: str, request: TranscriptBatchEdit) -> dict[str, int]:
    edits = [{"segment_id": item.segment_id, "text": item.text.strip()} for item in request.edits]
    if any(not item["text"] for item in edits):
        raise HTTPException(status_code=400, detail="Transcript text cannot be empty")
    try:
        revision = storage.edit_transcript_segments(conversation_id, edits, request.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"revision": revision}


@api.patch("/conversations/{conversation_id}/transcript/segments/{segment_id}")
def edit_segment(conversation_id: str, segment_id: str, edit: SegmentEdit) -> dict[str, int]:
    cleaned = edit.text.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Transcript text cannot be empty")
    try:
        revision = storage.edit_transcript_segment(conversation_id, segment_id, cleaned, edit.expected_revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"revision": revision}


@api.put("/conversations/{conversation_id}/speakers/label")
def label_speaker(conversation_id: str, edit: SpeakerLabelEdit) -> dict[str, int]:
    cleaned = edit.name.strip() if edit.name is not None else None
    if cleaned == "":
        cleaned = None
    try:
        revision = storage.set_speaker_label(conversation_id, edit.scope, edit.target_id,
                                             cleaned, edit.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"revision": revision}


@api.get("/vocabulary")
def vocabulary(kind: Literal["name", "other"] | None = None) -> list[dict[str, object]]:
    return storage.list_vocabulary(kind)


@api.post("/vocabulary")
def add_vocabulary(term: VocabularyCreate) -> dict[str, object]:
    cleaned = term.term.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Vocabulary term cannot be empty")
    try:
        return storage.add_vocabulary(cleaned, term.kind)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Vocabulary term already exists") from exc


@api.patch("/vocabulary/{term_id}")
def update_vocabulary(term_id: str, term: VocabularyTerm) -> dict[str, object]:
    cleaned = term.term.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Vocabulary term cannot be empty")
    try:
        result = storage.update_vocabulary(term_id, cleaned)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Vocabulary term already exists") from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Vocabulary term not found")
    return result


@api.delete("/vocabulary/{term_id}")
def delete_vocabulary(term_id: str) -> dict[str, bool]:
    if not storage.delete_vocabulary(term_id):
        raise HTTPException(status_code=404, detail="Vocabulary term not found")
    return {"deleted": True}


@api.post("/vocabulary/batch")
def add_vocabulary_batch(request: VocabularyBatch) -> dict[str, object]:
    terms = [term.strip() for term in request.terms]
    if any(not term or len(term) > 100 for term in terms):
        raise HTTPException(status_code=400, detail="Each term must be 1 to 100 characters")
    return storage.add_vocabulary_batch(terms, request.kind)


@api.post("/vocabulary/move")
def move_vocabulary(request: VocabularyMove) -> dict[str, int]:
    ids = list(dict.fromkeys(request.ids))
    try:
        return {"moved": storage.move_vocabulary(ids, request.kind)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.post("/vocabulary/delete")
def delete_vocabulary_selected(request: VocabularySelection) -> dict[str, int]:
    ids = list(dict.fromkeys(request.ids))
    try:
        return {"deleted": storage.delete_vocabulary_batch(ids)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api.get("/conversations/{conversation_id}/clean-runs")
def clean_runs(conversation_id: str) -> list[dict[str, object]]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [clean.public_run(item) for item in storage.list_clean_runs(conversation_id)]


@api.post("/conversations/{conversation_id}/clean-runs")
def start_clean_run(conversation_id: str) -> dict[str, object]:
    if storage.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    try:
        return clean.public_run(clean.request_run(conversation_id))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ai.AIError as exc:
        raise HTTPException(status_code=503 if exc.code == "not_configured" else 400, detail=str(exc)) from exc


@api.get("/clean-runs/{run_id}")
def clean_run(run_id: str) -> dict[str, object]:
    record = storage.get_clean_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Edited transcript not found")
    return clean.public_run(record)


@api.patch("/clean-runs/{run_id}/segments")
def edit_clean_segments(run_id: str, edit: CleanBatchEdit) -> dict[str, int]:
    edits = [{"segment_id": item.segment_id, "text": item.text.strip()} for item in edit.edits]
    if any(not item["text"] for item in edits):
        raise HTTPException(status_code=400, detail="Enter transcript words")
    try:
        revision = storage.set_clean_run_edits(run_id, edits, edit.expected_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"edit_revision": revision}


@api.put("/clean-runs/{run_id}/segments/{segment_id}/review")
def review_clean_segment(run_id: str, segment_id: str, review: CleanReview) -> dict[str, int]:
    try:
        revision = storage.set_clean_run_review(run_id, segment_id, review.expected_revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"edit_revision": revision}


@api.patch("/clean-runs/{run_id}/segments/{segment_id}")
def edit_clean_segment(run_id: str, segment_id: str, edit: CleanSegmentEdit) -> dict[str, object]:
    value = edit.text.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Enter transcript words")
    try:
        revision = storage.set_clean_run_edit(run_id, segment_id, value, edit.expected_revision)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"edit_revision": revision}


from stellis.demo_routes import router as demo_router
api.include_router(demo_router)
from stellis.chat_attachment_routes import router as chat_attachment_router
api.include_router(chat_attachment_router)
from stellis.report_routes import router as report_router
api.include_router(report_router)
api.include_router(reproductive_routes.router)
from stellis import routine_routes
api.include_router(routine_routes.router)
api.include_router(trackers_routes.router)
api.include_router(trace_routes.router)
api.include_router(milestone_routes.router)

app.include_router(api, prefix="/api/v1")
app.include_router(api, prefix="/api", include_in_schema=False)

# A component body, served by the same account process and session gate.
from stellis.dash_measurements import install as install_measurements
_measurement_dash = install_measurements(app)
