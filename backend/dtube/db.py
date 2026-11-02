import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("DTUBE_CONFIG", "/config"))
MEDIA_ROOT = Path(os.environ.get("DTUBE_MEDIA", "/media"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY,
    playlist_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    folder TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY,
    show_id INTEGER NOT NULL REFERENCES shows(id),
    channel_id TEXT NOT NULL,
    channel_title TEXT NOT NULL,
    number INTEGER NOT NULL,
    next_episode INTEGER NOT NULL DEFAULT 1,
    UNIQUE (show_id, channel_id),
    UNIQUE (show_id, number)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    playlist_id TEXT NOT NULL,
    title TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('tv', 'movie')),
    show_id INTEGER NOT NULL DEFAULT 0,
    subscribed INTEGER NOT NULL DEFAULT 0,
    last_checked REAL,
    UNIQUE (playlist_id, mode)
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    upload_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT ''
);

-- Private or removed videos, so scheduled checks don't keep queueing them.
CREATE TABLE IF NOT EXISTS unavailable (video_id TEXT PRIMARY KEY);

-- show_id 0 means the movie library.
CREATE TABLE IF NOT EXISTS placements (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    show_id INTEGER NOT NULL DEFAULT 0,
    season INTEGER,
    episode INTEGER,
    path TEXT NOT NULL,
    missing INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    UNIQUE (video_id, show_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    mode TEXT NOT NULL,
    show_id INTEGER NOT NULL DEFAULT 0,
    source_id INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    force INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    speed REAL,
    eta INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_after REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, run_after);
"""

DEFAULTS = {
    "temp_dir": "",
    "tv_dir": "",
    "movie_dir": "",
    "plex_url": "",
    "plex_token": "",
    "plex_tv_section": "",
    "plex_movie_section": "",
    "plex_path_from": "",
    "plex_path_to": "",
    "file_uid": 1000,
    "file_gid": 1000,
    "quality": 1080,
    "min_free_gb": 10,
    "check_hours": 6,
    "ytdlp_auto_update": True,
}


@contextmanager
def connect():
    conn = sqlite3.connect(CONFIG_DIR / "dtube.db", timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction():
    """A write transaction. BEGIN IMMEDIATE keeps number allocation race free."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def init():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def get_settings() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {**DEFAULTS, **{r["key"]: json.loads(r["value"]) for r in rows}}


def set_settings(values: dict):
    with transaction() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
