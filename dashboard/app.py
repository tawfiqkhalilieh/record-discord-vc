import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from threading import Lock
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from shared.storage import Storage, validate_id

log = logging.getLogger("dashboard")
directory = Path(__file__).parent
templates = Jinja2Templates(directory=str(directory / "templates"))
attempts = defaultdict(deque)
attempt_lock = Lock()


@asynccontextmanager
async def lifespan(app):
    password = os.environ.get("ADMIN_PASSWORD", "")
    secret = os.environ.get("SESSION_SECRET", "")
    if len(password) < 12 or len(secret) < 32 or password.startswith("replace-") or secret.startswith("replace-"):
        raise RuntimeError("Set a unique ADMIN_PASSWORD (12+ characters) and SESSION_SECRET (32+ characters)")
    app.state.storage = Storage()
    app.state.storage.check()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", ""),
                   session_cookie="stash_session", max_age=8 * 60 * 60, same_site="strict",
                   https_only=os.environ.get("COOKIE_SECURE", "false").lower() == "true")
app.mount("/static", StaticFiles(directory=str(directory / "static")), name="static")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; media-src 'self'; form-action 'self'; frame-ancestors 'none'"
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def admin(request: Request):
    if not request.session.get("admin"):
        if request.url.path.startswith("/api/"):
            raise HTTPException(401, "Admin login required")
        raise HTTPException(303, "Admin login required", headers={"Location": "/login"})


def csrf(request: Request, token: str):
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(expected.encode(), token.encode()):
        raise HTTPException(403, "Invalid form token")


def login_page(request: Request, error=None, status=200):
    request.session.setdefault("csrf", secrets.token_urlsafe(32))
    return templates.TemplateResponse(request=request, name="login.html",
                                      context={"csrf": request.session["csrf"], "error": error}, status_code=status)


def s3_error(error):
    if isinstance(error, ClientError) and error.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
        raise HTTPException(404, "Recording not found; it may still be processing")
    log.error("S3 request failed: %s", type(error).__name__)
    raise HTTPException(503, "Storage unavailable; try again shortly")


@app.get("/health")
def health(request: Request):
    try:
        request.app.state.storage.check()
    except (BotoCoreError, ClientError) as error:
        s3_error(error)
    return {"status": "ok"}


@app.get("/login")
def login(request: Request):
    if request.session.get("admin"):
        return RedirectResponse("/", status_code=303)
    return login_page(request)


@app.post("/login")
def submit_login(request: Request, password: str = Form(max_length=1024), token: str = Form()):
    csrf(request, token)
    current = time.monotonic()
    client = request.client.host if request.client else "unknown"
    with attempt_lock:
        # Bound the in-memory map and expire inactive clients.
        for key in list(attempts):
            while attempts[key] and attempts[key][0] < current - 60:
                attempts[key].popleft()
            if not attempts[key]:
                del attempts[key]
        if len(attempts) >= 10000 and client not in attempts or len(attempts[client]) >= 5:
            return login_page(request, "Too many login attempts. Wait a minute.", 429)
        attempts[client].append(current)
    supplied = hashlib.sha256(password.encode()).digest()
    expected = hashlib.sha256(os.environ["ADMIN_PASSWORD"].encode()).digest()
    if not hmac.compare_digest(supplied, expected):
        return login_page(request, "Incorrect password.", 401)
    with attempt_lock:
        attempts.pop(client, None)
    request.session.clear()
    request.session.update(admin=True, csrf=secrets.token_urlsafe(32))
    return RedirectResponse("/", status_code=303)


@app.post("/logout", dependencies=[Depends(admin)])
def logout(request: Request, token: str = Form()):
    csrf(request, token)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", dependencies=[Depends(admin)])
def index(request: Request):
    try:
        recordings = request.app.state.storage.list()
    except (BotoCoreError, ClientError) as error:
        s3_error(error)
    return templates.TemplateResponse(request=request, name="index.html", context={"recordings": recordings})


@app.get("/api/recordings", dependencies=[Depends(admin)])
def recording_list(request: Request):
    try:
        return request.app.state.storage.list()
    except (BotoCoreError, ClientError) as error:
        s3_error(error)


def metadata(request, recording_id):
    try:
        return request.app.state.storage.get(recording_id)
    except ValueError:
        raise HTTPException(404, "Recording not found")
    except (BotoCoreError, ClientError) as error:
        s3_error(error)


@app.get("/recordings/{recording_id}", dependencies=[Depends(admin)])
def watch(request: Request, recording_id: str):
    recording = metadata(request, recording_id)
    return templates.TemplateResponse(request=request, name="watch.html", context={"recording": recording})


def parse_range(value, size):
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError("Unsupported range")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        suffix = int(last)
        if suffix <= 0:
            raise ValueError("Invalid suffix")
        start, end = max(0, size - suffix), size - 1
    if start < 0 or start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, end


@app.api_route("/recordings/{recording_id}/video", methods=["GET", "HEAD"], dependencies=[Depends(admin)])
def video(request: Request, recording_id: str, download: bool = False):
    # Resolve metadata first: unpublished/incomplete videos never become accessible.
    metadata(request, recording_id)
    storage = request.app.state.storage
    key = f"recordings/{validate_id(recording_id)}/video.mp4"
    try:
        obj = storage.client.head_object(Bucket=storage.bucket, Key=key)
        size = obj["ContentLength"]
        start, end, status = 0, size - 1, 200
        headers = {"Accept-Ranges": "bytes", "Content-Type": "video/mp4",
                   "Content-Disposition": f"{'attachment' if download else 'inline'}; filename=recording-{recording_id}.mp4"}
        if request.headers.get("range"):
            try:
                start, end = parse_range(request.headers["range"], size)
            except ValueError:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(max(0, end - start + 1))
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers)
        options = {"Bucket": storage.bucket, "Key": key}
        if status == 206:
            options["Range"] = f"bytes={start}-{end}"
        body = storage.client.get_object(**options)["Body"]
    except (BotoCoreError, ClientError) as error:
        s3_error(error)

    def chunks():
        try:
            yield from body.iter_chunks(chunk_size=256 * 1024)
        finally:
            body.close()

    return StreamingResponse(chunks(), status_code=status, headers=headers)
