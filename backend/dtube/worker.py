"""One background worker downloads a video at a time, in the order queued.

Running one at a time keeps episode numbers in playlist order and is gentler on YouTube.
"""
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from . import db, library, plex, ytdlp

log = logging.getLogger("dtube")
RETRY_DELAYS = [60, 600]
ACTIVE = ("queued", "downloading", "processing")
# YouTube can't notify DTube about new videos, so "immediate" is a short polling interval.
CHECK_INTERVALS = {"immediate": 300, "hourly": 3600, "daily": 86400}

state = {"paused": ""}
_current_proc = None
_cancel_requested = False
_plex_pending = {}


def enqueue(conn, video_id, title, mode, show_id=0, source_id=None, force=False, ad_removal="off") -> bool:
    if not force:
        if library.get_placement(conn, video_id, show_id):
            return False
        if conn.execute("SELECT 1 FROM unavailable WHERE video_id = ?", (video_id,)).fetchone():
            return False
    active = conn.execute(
        f"SELECT 1 FROM jobs WHERE video_id = ? AND show_id = ? AND status IN {ACTIVE}",
        (video_id, show_id),
    ).fetchone()
    if active:
        return False
    now = time.time()
    conn.execute(
        "INSERT INTO jobs (video_id, title, mode, show_id, source_id, force, ad_removal, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (video_id, title, mode, show_id, source_id, int(force), ad_removal, now, now),
    )
    return True


def refresh_source(source_id: int) -> int:
    """Queue any videos in a playlist that haven't been downloaded."""
    with db.connect() as conn:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    info = ytdlp.probe(source["url"])
    return queue_entries(source, info["entries"])


def queue_entries(source, entries) -> int:
    added = 0
    with db.transaction() as conn:
        for entry in entries:
            added += enqueue(
                conn, entry["video_id"], entry["title"], source["mode"], source["show_id"], source["id"],
                ad_removal=source["ad_removal"],
            )
        conn.execute("UPDATE sources SET last_checked = ? WHERE id = ?", (time.time(), source["id"]))
    return added


def cancel(job_id: int) -> bool:
    global _cancel_requested
    with db.transaction() as conn:
        job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job or job["status"] not in ACTIVE:
            return False
        if job["status"] == "queued":
            conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = ?", (job_id,))
            return True
    _cancel_requested = True
    if _current_proc:
        _current_proc.terminate()
    return True


def _update(job_id: int, **fields):
    fields["updated_at"] = time.time()
    columns = ", ".join(f"{key} = ?" for key in fields)
    with db.connect() as conn:
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*fields.values(), job_id))


def _pause_reason(settings: dict) -> str:
    if not all(settings[k] for k in ("temp_dir", "tv_dir", "movie_dir")):
        return "Choose your folders in Settings to start downloading."
    minimum = float(settings["min_free_gb"]) * 1024**3
    for key in ("temp_dir", "tv_dir", "movie_dir"):
        try:
            if shutil.disk_usage(settings[key]).free < minimum:
                return f"Paused: less than {settings['min_free_gb']} GB free in {settings[key]}."
        except OSError:
            return f"Folder not available: {settings[key]}"
    return ""


def _save_video(info: dict) -> dict:
    video = {
        "video_id": info["id"],
        "title": info.get("title") or info["id"],
        "channel_id": info.get("channel_id") or info.get("uploader_id") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "upload_date": info.get("upload_date") or "",
        "description": info.get("description") or "",
    }
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO videos VALUES "
            "(:video_id, :title, :channel_id, :channel, :upload_date, :description)",
            video,
        )
    return video


def _load_video(video_id: str):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return dict(row) if row else None


def _queue_plex(placement: dict, settings: dict):
    section = settings["plex_tv_section" if placement["mode"] == "tv" else "plex_movie_section"]
    if section and settings["plex_url"] and settings["plex_token"]:
        path = Path(placement["path"]).parent
        _plex_pending[(section, str(path))] = True


def _flush_plex(settings: dict):
    for section, path in list(_plex_pending):
        try:
            plex.refresh(settings, section, path)
        except Exception as exc:
            log.warning("Plex refresh failed: %s", exc)
        _plex_pending.pop((section, path), None)


def _file_from_existing(job, settings) -> dict:
    """Reuse a copy already on disk, if this video was filed somewhere else."""
    if job["force"]:
        return {}
    existing = library.find_existing_file(job["video_id"], job["show_id"], job["ad_removal"])
    video = _load_video(job["video_id"])
    if not existing or not video:
        return {}
    placement = library.allocate(
        video, job["mode"], job["show_id"], existing.suffix, settings["tv_dir"], settings["movie_dir"],
        job["ad_removal"],
    )
    library.install_video(existing, placement, link=True)
    library.install_thumbnail(library.sidecar(existing, ".jpg"), placement, link=True)
    library.write_metadata(video, placement, settings["tv_dir"])
    return placement


def _download(job, settings) -> dict:
    global _current_proc
    folder = Path(settings["temp_dir"]) / f"dtube-{job['id']}"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)

    def started(proc):
        global _current_proc
        _current_proc = proc

    def progress(percent, speed, eta):
        _update(job["id"], progress=round(percent, 1), speed=speed, eta=eta)

    try:
        result = ytdlp.download(
            job["video_id"], folder, int(settings["quality"]), job["ad_removal"], progress, started
        )
        _update(job["id"], status="processing", progress=100, speed=None, eta=None)
        video = _save_video(result["info"])
        placement = library.allocate(
            video, job["mode"], job["show_id"], result["video"].suffix,
            settings["tv_dir"], settings["movie_dir"], job["ad_removal"],
        )
        library.install_video(result["video"], placement)
        library.install_thumbnail(result["thumbnail"], placement)
        library.write_metadata(video, placement, settings["tv_dir"])
        return placement
    finally:
        _current_proc = None
        shutil.rmtree(folder, ignore_errors=True)


def _run(job):
    global _cancel_requested
    settings = db.get_settings()
    _cancel_requested = False
    _update(job["id"], status="downloading", attempts=job["attempts"] + 1, error=None)
    try:
        placement = _file_from_existing(job, settings) or _download(job, settings)
        _update(job["id"], status="done", progress=100)
        with db.connect() as conn:
            conn.execute("DELETE FROM unavailable WHERE video_id = ?", (job["video_id"],))
        _queue_plex(placement, settings)
    except Exception as exc:
        if _cancel_requested:
            _update(job["id"], status="cancelled", speed=None, eta=None)
            return
        log.exception("Job %s failed", job["id"])
        permanent = getattr(exc, "permanent", False)
        attempts = job["attempts"]
        if permanent:
            with db.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO unavailable VALUES (?)", (job["video_id"],))
        if permanent or attempts >= len(RETRY_DELAYS):
            _update(job["id"], status="failed", error=str(exc), speed=None, eta=None)
        else:
            _update(job["id"], status="queued", error=str(exc), progress=0, speed=None, eta=None,
                    run_after=time.time() + RETRY_DELAYS[attempts])


def _next_job():
    with db.connect() as conn:
        return conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' AND run_after <= ? ORDER BY id LIMIT 1",
            (time.time(),),
        ).fetchone()


def _worker_loop():
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'queued', progress = 0 WHERE status IN ('downloading', 'processing')")
    while True:
        try:
            settings = db.get_settings()
            state["paused"] = _pause_reason(settings)
            job = None if state["paused"] else _next_job()
            if job:
                _run(job)
            else:
                _flush_plex(settings)
                time.sleep(2)
        except Exception:
            log.exception("Worker error")
            time.sleep(10)


def due_sources(now: float) -> list:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, check_frequency, last_checked FROM sources WHERE check_frequency != 'off'"
        ).fetchall()
    return [
        row["id"] for row in rows
        if now - (row["last_checked"] or 0) >= CHECK_INTERVALS[row["check_frequency"]]
    ]


def _scheduler_loop():
    last_update = 0
    while True:
        try:
            settings = db.get_settings()
            for source_id in due_sources(time.time()):
                # Recorded first, so a failed check waits for its next turn.
                with db.connect() as conn:
                    conn.execute("UPDATE sources SET last_checked = ? WHERE id = ?", (time.time(), source_id))
                try:
                    refresh_source(source_id)
                except Exception as exc:
                    log.warning("Checking source %s failed: %s", source_id, exc)
            if settings["ytdlp_auto_update"] and time.time() - last_update > 86400:
                last_update = time.time()
                ytdlp.update()
        except Exception:
            log.exception("Scheduler error")
        time.sleep(60)


def scan_library() -> int:
    """Mark history entries whose files were removed outside DTube."""
    changed = 0
    with db.transaction() as conn:
        for row in conn.execute("SELECT id, path, missing FROM placements").fetchall():
            missing = int(not os.path.isfile(row["path"]))
            if missing != row["missing"]:
                conn.execute("UPDATE placements SET missing = ? WHERE id = ?", (missing, row["id"]))
                changed += 1
    return changed


def start():
    threading.Thread(target=_worker_loop, daemon=True, name="worker").start()
    threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
