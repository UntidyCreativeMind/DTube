import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, db, library, plex, worker, ytdlp
from .db import MEDIA_ROOT

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FOLDER_KEYS = ("temp_dir", "tv_dir", "movie_dir")
EDITABLE = set(db.DEFAULTS)
NUMBER_KEYS = {"file_uid": 0, "file_gid": 0, "quality": 144, "min_free_gb": 0, "check_hours": 1}
RECENT_JOBS = 100



@asynccontextmanager
async def lifespan(_):
    db.init()
    worker.start()
    yield


app = FastAPI(title="DTube", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_user)])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupRequest(Credentials):
    password: str = Field(min_length=10, max_length=256)


class PasswordChange(BaseModel):
    password: str = Field(min_length=10, max_length=256)


class UrlRequest(BaseModel):
    url: str


class AddRequest(UrlRequest):
    mode: str = "tv"
    subscribe: bool = False


class SourceUpdate(BaseModel):
    subscribed: bool


class PlexRequest(BaseModel):
    url: str = ""
    token: str = ""


class CookieUpload(BaseModel):
    content: str = Field(max_length=2_000_000)


class FolderRequest(BaseModel):
    path: str
    name: str


def _set_session(request: Request, response: Response, user_id: int):
    response.set_cookie(
        auth.COOKIE,
        auth.create_session(user_id),
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="strict",
        secure=request.headers.get("x-forwarded-proto") == "https",
    )


@app.get("/api/auth/status")
def auth_status(request: Request):
    setup = auth.needs_setup()
    logged_in = False
    if not setup:
        try:
            auth.require_user(request)
            logged_in = True
        except HTTPException:
            pass
    return {"needs_setup": setup, "logged_in": logged_in}


@app.post("/api/auth/setup")
def setup(body: SetupRequest, request: Request, response: Response):
    if not auth.create_admin(body.username, body.password):
        raise HTTPException(409, "Setup is already complete")
    user = auth.verify(body.username, body.password)
    _set_session(request, response, user["id"])
    return {"ok": True}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    ip = auth.client_ip(request)
    auth.check_rate_limit(ip)
    user = auth.verify(body.username, body.password)
    if not user:
        auth.record_failure(ip)
        raise HTTPException(401, "Wrong username or password")
    _set_session(request, response, user["id"])
    return {"ok": True}


@api.post("/auth/logout")
def logout(request: Request, response: Response):
    auth.delete_session(request.cookies.get(auth.COOKIE, ""))
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@api.post("/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response,
                    user=Depends(auth.require_user)):
    auth.change_password(user["id"], body.password)
    _set_session(request, response, user["id"])
    return {"ok": True}


def _checked_url(url: str) -> str:
    try:
        return ytdlp.check_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _probe(url: str) -> dict:
    try:
        return ytdlp.probe(url)
    except (ytdlp.YtdlpError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, str(exc))


@api.post("/preview")
def preview(body: UrlRequest):
    info = _probe(_checked_url(body.url))
    info.pop("description", None)
    entries = info.pop("entries")
    with db.connect() as conn:
        ids = [e["video_id"] for e in entries] or [info.get("video_id")]
        downloaded = sum(
            1 for video_id in ids
            if conn.execute("SELECT 1 FROM placements WHERE video_id = ?", (video_id,)).fetchone()
        )
    return {**info, "downloaded": downloaded}


@api.post("/sources")
def add_source(body: AddRequest):
    if body.mode not in ("tv", "movie"):
        raise HTTPException(400, "Mode must be tv or movie")
    url = _checked_url(body.url)
    info = _probe(url)

    if info["type"] == "video":
        with db.transaction() as conn:
            added = worker.enqueue(conn, info["video_id"], info["title"], "movie")
        return {"type": "video", "added": int(added)}

    with db.transaction() as conn:
        show_id = 0
        if body.mode == "tv":
            show = library.get_or_create_show(conn, info["playlist_id"], info["title"], info["description"])
            show_id = show["id"]
        conn.execute(
            "INSERT INTO sources (url, playlist_id, title, mode, show_id, subscribed) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (playlist_id, mode) "
            "DO UPDATE SET url = excluded.url, title = excluded.title, "
            "subscribed = MAX(subscribed, excluded.subscribed)",
            (url, info["playlist_id"], info["title"], body.mode, show_id, int(body.subscribe)),
        )
        source = conn.execute(
            "SELECT * FROM sources WHERE playlist_id = ? AND mode = ?", (info["playlist_id"], body.mode)
        ).fetchone()
    added = worker.queue_entries(source, info["entries"])
    return {"type": "playlist", "added": added, "total": info["count"]}


@api.get("/sources")
def list_sources():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM jobs j WHERE j.source_id = s.id AND j.status = 'done') AS done "
            "FROM sources s ORDER BY s.title"
        ).fetchall()
    return [dict(r) for r in rows]


@api.patch("/sources/{source_id}")
def update_source(source_id: int, body: SourceUpdate):
    with db.connect() as conn:
        conn.execute("UPDATE sources SET subscribed = ? WHERE id = ?", (int(body.subscribed), source_id))
    return {"ok": True}


@api.post("/sources/{source_id}/check")
def check_source(source_id: int):
    with db.connect() as conn:
        if not conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone():
            raise HTTPException(404, "Playlist not found")
    try:
        return {"added": worker.refresh_source(source_id)}
    except (ytdlp.YtdlpError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, str(exc))


@api.delete("/sources/{source_id}")
def delete_source(source_id: int):
    """Stops tracking a playlist. Files, numbering and history are kept."""
    with db.connect() as conn:
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"ok": True}


def queue_snapshot() -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, video_id, title, mode, status, progress, speed, eta, error FROM jobs "
            f"ORDER BY status IN {worker.ACTIVE} DESC, "
            "CASE WHEN status = 'queued' THEN id END, updated_at DESC LIMIT ?",
            (RECENT_JOBS,),
        ).fetchall()
    return {"paused": worker.state["paused"], "jobs": [dict(r) for r in rows]}


@api.get("/queue")
def queue():
    return queue_snapshot()


@api.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    if not worker.cancel(job_id):
        raise HTTPException(409, "That download isn't active")
    return {"ok": True}


@api.post("/jobs/{job_id}/retry")
def retry_job(job_id: int):
    with db.connect() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status = 'queued', attempts = 0, run_after = 0, progress = 0, error = NULL "
            "WHERE id = ? AND status IN ('failed', 'cancelled')",
            (job_id,),
        )
    if not cursor.rowcount:
        raise HTTPException(409, "Only failed or cancelled downloads can be retried")
    return {"ok": True}


@api.delete("/jobs/finished")
def clear_finished():
    with db.connect() as conn:
        conn.execute(f"DELETE FROM jobs WHERE status NOT IN {worker.ACTIVE}")
    return {"ok": True}


@api.get("/history")
def history(q: str = "", page: int = 1, size: int = 50):
    size = min(max(size, 1), 200)
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = "FROM placements p LEFT JOIN videos v ON v.video_id = p.video_id " \
            "LEFT JOIN shows s ON s.id = p.show_id " \
            "WHERE COALESCE(v.title, p.path) LIKE ? ESCAPE '\\' OR COALESCE(s.title, '') LIKE ? ESCAPE '\\'"
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {where}", (pattern, pattern)).fetchone()[0]
        rows = conn.execute(
            "SELECT p.id, p.video_id, p.mode, p.season, p.episode, p.path, p.missing, p.created_at, "
            f"COALESCE(v.title, p.video_id) AS title, v.channel, s.title AS show {where} "
            "ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            (pattern, pattern, size, (max(page, 1) - 1) * size),
        ).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


@api.post("/history/scan")
def scan_history():
    return {"changed": worker.scan_library()}


@api.post("/history/{placement_id}/redownload")
def redownload(placement_id: int):
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM placements WHERE id = ?", (placement_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not in history")
        video = conn.execute("SELECT title FROM videos WHERE video_id = ?", (row["video_id"],)).fetchone()
        title = video["title"] if video else row["video_id"]
        queued = worker.enqueue(conn, row["video_id"], title, row["mode"], row["show_id"], force=True)
    return {"queued": queued}


def _public_settings() -> dict:
    settings = db.get_settings()
    settings["plex_token_set"] = bool(settings.pop("plex_token"))
    return settings


def _inside_media(path: str) -> Path:
    resolved = Path(path).resolve()
    root = MEDIA_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(400, f"Folders must be inside {MEDIA_ROOT}")
    return resolved


def _restart_soon():
    """Exit so Docker restarts DTube under the new file owner."""
    threading.Timer(1, os._exit, args=(0,)).start()


@api.get("/settings")
def get_settings():
    return _public_settings()


@api.put("/settings")
def put_settings(values: dict):
    unknown = set(values) - EDITABLE
    if unknown:
        raise HTTPException(400, f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, minimum in NUMBER_KEYS.items():
        if key in values:
            if type(values[key]) not in (int, float) or values[key] < minimum:
                raise HTTPException(400, f"{key} must be a number of at least {minimum}")
    for key in FOLDER_KEYS:
        if values.get(key):
            folder = _inside_media(values[key])
            if not folder.is_dir() or not os.access(folder, os.W_OK):
                raise HTTPException(400, f"DTube can't write to {folder}. Check the file owner in Settings.")
            values[key] = str(folder)
    if not values.get("plex_token", True):
        values.pop("plex_token")
    current = db.get_settings()
    db.set_settings(values)
    restart = any(int(values.get(k, current[k])) != int(current[k]) for k in ("file_uid", "file_gid"))
    if restart:
        _restart_soon()
    return {**_public_settings(), "restarting": restart}


@api.get("/fs")
def browse(path: str = ""):
    folder = _inside_media(path or str(MEDIA_ROOT))
    try:
        names = sorted(
            (e.name for e in os.scandir(folder) if e.is_dir() and not e.name.startswith(".")),
            key=str.lower,
        )
    except OSError as exc:
        raise HTTPException(400, f"Can't open {folder}: {exc.strerror}")
    parent = str(folder.parent) if folder != MEDIA_ROOT.resolve() else None
    return {"path": str(folder), "parent": parent, "folders": names}


@api.post("/fs/mkdir")
def make_folder(body: FolderRequest):
    if not re.fullmatch(r"[^/\\\x00]+", body.name) or body.name in (".", ".."):
        raise HTTPException(400, "Enter a folder name, not a path")
    folder = _inside_media(str(Path(body.path) / body.name))
    try:
        folder.mkdir(exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"Can't create the folder: {exc.strerror}")
    return {"path": str(folder)}


@api.post("/plex/libraries")
def plex_libraries(body: PlexRequest):
    settings = db.get_settings()
    url = body.url or settings["plex_url"]
    token = body.token or settings["plex_token"]
    if not url or not token:
        raise HTTPException(400, "Enter the Plex address and token")
    try:
        return plex.list_libraries(url, token)
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach Plex: {type(exc).__name__}")


def _looks_like_cookies(content: str) -> bool:
    lines = [l for l in content.splitlines() if l.strip() and not l.startswith("# ")]
    lines = [l.removeprefix("#HttpOnly_") for l in lines if l != "# Netscape HTTP Cookie File"]
    return bool(lines) and all(len(l.split("\t")) == 7 for l in lines if not l.startswith("#"))


@api.get("/cookies")
def cookie_status():
    return {"present": ytdlp.COOKIES.exists()}


@api.post("/cookies")
def upload_cookies(body: CookieUpload):
    if not _looks_like_cookies(body.content):
        raise HTTPException(400, "That isn't a Netscape format cookies.txt file")
    fd = os.open(ytdlp.COOKIES, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(body.content)
    return {"present": True}


@api.delete("/cookies")
def delete_cookies():
    ytdlp.COOKIES.unlink(missing_ok=True)
    return {"present": False}


@api.get("/system")
def system():
    usage = shutil.disk_usage(MEDIA_ROOT) if MEDIA_ROOT.exists() else None
    return {
        "ytdlp": ytdlp.version(),
        "media_free_gb": round(usage.free / 1024**3, 1) if usage else None,
        "media_total_gb": round(usage.total / 1024**3, 1) if usage else None,
    }


@api.post("/system/update-ytdlp")
def update_ytdlp():
    try:
        return {"ytdlp": ytdlp.update()}
    except (ytdlp.YtdlpError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(502, str(exc))


app.include_router(api)


@app.websocket("/api/ws")
async def live_queue(socket: WebSocket):
    if not auth.websocket_allowed(socket):
        await socket.close(code=4401)
        return
    await socket.accept()
    last = None
    try:
        while True:
            payload = json.dumps(await asyncio.to_thread(queue_snapshot))
            if payload != last:
                await socket.send_text(payload)
                last = payload
            await asyncio.sleep(1)
    except Exception:
        pass


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_not_found():
    raise HTTPException(404, "Not found")


@app.get("/{path:path}")
def frontend(path: str):
    candidate = (STATIC_DIR / path).resolve()
    if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(STATIC_DIR / "index.html")
