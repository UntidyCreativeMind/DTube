import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import auth, db, library, plex, stats, worker, ytdlp
from . import options as opts
from .db import MEDIA_ROOT

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FOLDER_KEYS = ("temp_dir", "tv_dir", "movie_dir", "music_dir")
MODES = ("tv", "movie", "audio")
EPISODE_ORDERS = ("playlist", "reverse")
EDITABLE = set(db.DEFAULTS) - {"plex_client_id"}
NUMBER_KEYS = {"file_uid": 0, "file_gid": 0, "min_free_gb": 0}
RECENT_JOBS = 100
_plex_pins = {}



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
    check_frequency: str = "off"
    episode_order: str = "playlist"
    options: dict = {}


class SourceUpdate(BaseModel):
    check_frequency: str | None = None
    episode_order: str | None = None
    options: dict | None = None


class RedownloadRequest(BaseModel):
    options: dict = {}


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


def _checked_options(changes: dict, base: dict) -> dict:
    try:
        return opts.merged(changes, base)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def _checked_order(value: str) -> str:
    if value not in EPISODE_ORDERS:
        raise HTTPException(400, "Episode order must be playlist or reverse")
    return value


def _checked_frequency(value: str) -> str:
    if value != "off" and value not in worker.CHECK_INTERVALS:
        raise HTTPException(400, "Check frequency must be off, immediate, hourly or daily")
    return value


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
    if body.mode not in MODES:
        raise HTTPException(400, "Mode must be tv, movie or audio")
    options = _checked_options(body.options, db.get_settings()["default_options"])
    frequency = _checked_frequency(body.check_frequency)
    order = _checked_order(body.episode_order)
    url = _checked_url(body.url)
    info = _probe(url)

    if info["type"] == "video":
        with db.transaction() as conn:
            mode, show_id = "movie", 0
            if body.mode == "audio":
                mode = "audio"
                show_id = library.get_or_create_album(conn, "singles", "Singles")["id"]
            added = worker.enqueue(conn, info["video_id"], info["title"], mode, show_id, options=options)
        return {"type": "video", "added": int(added)}

    with db.transaction() as conn:
        show_id = 0
        if body.mode == "tv":
            show = library.get_or_create_show(conn, info["playlist_id"], info["title"], info["description"])
            show_id = show["id"]
        elif body.mode == "audio":
            album = library.get_or_create_album(conn, info["playlist_id"], info["title"], info["channel"])
            show_id = album["id"]
        conn.execute(
            "INSERT INTO sources (url, playlist_id, title, mode, show_id, check_frequency, episode_order, options) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (playlist_id, mode) "
            "DO UPDATE SET url = excluded.url, title = excluded.title, options = excluded.options, "
            "check_frequency = CASE excluded.check_frequency WHEN 'off' THEN check_frequency "
            "ELSE excluded.check_frequency END",
            (url, info["playlist_id"], info["title"], body.mode, show_id, frequency, order, opts.dump(options)),
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
    return [{**dict(r), "options": opts.parse(r["options"])} for r in rows]


@api.patch("/sources/{source_id}")
def update_source(source_id: int, body: SourceUpdate):
    with db.connect() as conn:
        if body.check_frequency:
            frequency = _checked_frequency(body.check_frequency)
            conn.execute("UPDATE sources SET check_frequency = ? WHERE id = ?", (frequency, source_id))
        if body.episode_order:
            order = _checked_order(body.episode_order)
            conn.execute("UPDATE sources SET episode_order = ? WHERE id = ?", (order, source_id))
        if body.options:
            row = conn.execute("SELECT options FROM sources WHERE id = ?", (source_id,)).fetchone()
            if row:
                options = _checked_options(body.options, opts.parse(row["options"]))
                conn.execute("UPDATE sources SET options = ? WHERE id = ?", (opts.dump(options), source_id))
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
def delete_source(source_id: int, forget: bool = False):
    """Stops tracking a playlist and drops its waiting videos. Files on disk are never touched.

    History and numbering are kept unless `forget` is set, which makes tracking it again start fresh.
    """
    forgotten = 0
    with db.transaction() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if source and forget:
            running = conn.execute(
                "SELECT 1 FROM jobs WHERE source_id = ? AND status IN ('downloading', 'processing')",
                (source_id,),
            ).fetchone()
            if running:
                raise HTTPException(409, "A video from this playlist is downloading. Cancel it first.")
            forgotten = library.forget_source(conn, source)
        conn.execute("DELETE FROM jobs WHERE source_id = ? AND status = 'queued'", (source_id,))
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"ok": True, "forgotten": forgotten}


def queue_snapshot() -> dict:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, video_id, title, mode, status, progress, speed, eta, error FROM jobs "
            f"ORDER BY status IN {worker.ACTIVE} DESC, "
            "CASE WHEN status = 'queued' THEN id END, updated_at DESC LIMIT ?",
            (RECENT_JOBS,),
        ).fetchall()
    return {"paused": worker.state["paused"], "jobs": [dict(r) for r in rows]}


@api.get("/stats")
def get_stats():
    return stats.collect()


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
def history(q: str = "", page: int = 1, size: int = 50, missing: bool = False):
    size = min(max(size, 1), 200)
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = "FROM placements p LEFT JOIN videos v ON v.video_id = p.video_id " \
            "LEFT JOIN shows s ON s.id = p.show_id " \
            "WHERE (COALESCE(v.title, p.path) LIKE ? ESCAPE '\\' OR COALESCE(s.title, '') LIKE ? ESCAPE '\\')"
    if missing:
        where += " AND p.missing = 1"
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {where}", (pattern, pattern)).fetchone()[0]
        rows = conn.execute(
            "SELECT p.id, p.video_id, p.mode, p.season, p.episode, p.path, p.missing, p.created_at, p.options, "
            f"COALESCE(v.title, p.video_id) AS title, v.channel, s.title AS show {where} "
            "ORDER BY p.created_at DESC LIMIT ? OFFSET ?",
            (pattern, pattern, size, (max(page, 1) - 1) * size),
        ).fetchall()
    return {"total": total, "items": [{**dict(r), "options": opts.parse(r["options"])} for r in rows]}


@api.post("/history/scan")
def scan_history():
    return {"changed": worker.scan_library()}


@api.post("/history/remove-missing")
def remove_missing():
    worker.scan_library()
    removed = 0
    with db.transaction() as conn:
        for row in conn.execute("SELECT * FROM placements WHERE missing = 1").fetchall():
            if not library.is_downloading(conn, row):
                library.remove_placement(conn, row)
                removed += 1
    return {"removed": removed}


@api.delete("/history/{placement_id}")
def remove_from_history(placement_id: int):
    """Removes an entry whose file has gone. A file still on disk is refused, so nothing is lost track of."""
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM placements WHERE id = ?", (placement_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not in history")
        if os.path.isfile(row["path"]):
            raise HTTPException(409, "That file is still on disk")
        if library.is_downloading(conn, row):
            raise HTTPException(409, "That video is downloading. Cancel it first.")
        library.remove_placement(conn, row)
    return {"ok": True}


@api.post("/history/{placement_id}/redownload")
def redownload(placement_id: int, body: RedownloadRequest | None = None):
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM placements WHERE id = ?", (placement_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Not in history")
        options = _checked_options(body.options if body else {}, opts.parse(row["options"]))
        video = conn.execute("SELECT title FROM videos WHERE video_id = ?", (row["video_id"],)).fetchone()
        title = video["title"] if video else row["video_id"]
        queued = worker.enqueue(conn, row["video_id"], title, row["mode"], row["show_id"], force=True,
                                options=options)
    return {"queued": queued}


def _public_settings() -> dict:
    settings = db.get_settings()
    settings["plex_token_set"] = bool(settings.pop("plex_token"))
    settings.pop("plex_client_id")
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
    if "default_options" in values:
        values["default_options"] = _checked_options(values["default_options"], opts.DEFAULTS)
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


def _plex_client_id() -> str:
    client_id = db.get_settings()["plex_client_id"]
    if not client_id:
        client_id = str(uuid.uuid4())
        db.set_settings({"plex_client_id": client_id})
    return client_id


@api.post("/plex/pin")
def start_plex_sign_in():
    client_id = _plex_client_id()
    try:
        pin_id, code = plex.create_pin(client_id)
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach Plex: {type(exc).__name__}")
    _plex_pins[pin_id] = code
    return {"id": pin_id, "auth_url": plex.sign_in_url(client_id, code)}


@api.get("/plex/pin/{pin_id}")
def check_plex_sign_in(pin_id: int):
    code = _plex_pins.get(pin_id)
    if not code:
        raise HTTPException(404, "Start the Plex sign-in again")
    try:
        token = plex.check_pin(_plex_client_id(), pin_id, code)
    except plex.PinExpired:
        _plex_pins.pop(pin_id, None)
        return {"signed_in": False, "expired": True}
    except Exception:
        # A brief network problem shouldn't end the sign-in, so the page just asks again.
        return {"signed_in": False, "expired": False}
    if not token:
        return {"signed_in": False, "expired": False}
    db.set_settings({"plex_token": token})
    _plex_pins.pop(pin_id, None)
    return {"signed_in": True, "expired": False}


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
