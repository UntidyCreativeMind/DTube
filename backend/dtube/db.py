import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import options

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
    description TEXT NOT NULL DEFAULT '',
    artist TEXT NOT NULL DEFAULT ''
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
    mode TEXT NOT NULL,
    show_id INTEGER NOT NULL DEFAULT 0,
    check_frequency TEXT NOT NULL DEFAULT 'off',
    episode_order TEXT NOT NULL DEFAULT 'playlist',
    options TEXT NOT NULL DEFAULT '{}',
    last_checked REAL,
    UNIQUE (playlist_id, mode)
);

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    upload_date TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    duration REAL
);

-- Private or removed videos, so scheduled checks don't keep queueing them.
CREATE TABLE IF NOT EXISTS unavailable (video_id TEXT PRIMARY KEY);

-- Videos taken out of the history, so scheduled checks don't download them again.
CREATE TABLE IF NOT EXISTS removed (
    video_id TEXT NOT NULL,
    show_id INTEGER NOT NULL,
    source_id INTEGER,
    PRIMARY KEY (video_id, show_id)
);

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
    options TEXT NOT NULL DEFAULT '{}',
    source_id INTEGER,
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
    options TEXT NOT NULL DEFAULT '{}',
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
    "music_dir": "",
    "plex_url": "",
    "plex_token": "",
    "plex_client_id": "",
    "plex_tv_section": "",
    "plex_movie_section": "",
    "plex_music_section": "",
    "plex_path_from": "",
    "plex_path_to": "",
    "file_uid": 1000,
    "file_gid": 1000,
    "min_free_gb": 10,
    "ytdlp_auto_update": True,
    "default_options": options.DEFAULTS,
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


def _columns(conn, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn):
    """Bring a database from an earlier version up to the current schema."""
    if "artist" not in _columns(conn, "shows"):
        conn.execute("ALTER TABLE shows ADD COLUMN artist TEXT NOT NULL DEFAULT ''")
    if "duration" not in _columns(conn, "videos"):
        conn.execute("ALTER TABLE videos ADD COLUMN duration REAL")

    columns = _columns(conn, "sources")
    if "check_frequency" not in columns:
        conn.execute("ALTER TABLE sources ADD COLUMN check_frequency TEXT NOT NULL DEFAULT 'off'")
        if "subscribed" in columns:
            conn.execute("UPDATE sources SET check_frequency = 'daily' WHERE subscribed = 1")
    if "episode_order" not in columns:
        conn.execute("ALTER TABLE sources ADD COLUMN episode_order TEXT NOT NULL DEFAULT 'playlist'")

    for table in ("sources", "jobs", "placements"):
        columns = _columns(conn, table)
        if "options" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN options TEXT NOT NULL DEFAULT '{{}}'")
            legacy = sorted({"ad_removal", "quality"} & columns)
            if legacy:
                pairs = ", ".join(f"'{column}', {column}" for column in legacy)
                conn.execute(f"UPDATE {table} SET options = json_object({pairs})")

    if "source_id" not in _columns(conn, "placements"):
        conn.execute("ALTER TABLE placements ADD COLUMN source_id INTEGER")
        conn.execute(
            "UPDATE placements SET source_id = (SELECT j.source_id FROM jobs j "
            "WHERE j.video_id = placements.video_id AND j.show_id = placements.show_id "
            "AND j.status = 'done' AND j.source_id IS NOT NULL ORDER BY j.id DESC LIMIT 1)"
        )

    # Early versions limited sources.mode with a CHECK, which SQLite can't alter in place.
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sources'").fetchone()[0]
    if "CHECK" in sql:
        shared = "id, url, playlist_id, title, mode, show_id, check_frequency, episode_order, options, last_checked"
        conn.execute("ALTER TABLE sources RENAME TO sources_old")
        conn.executescript(SCHEMA)
        conn.execute(f"INSERT INTO sources ({shared}) SELECT {shared} FROM sources_old")
        conn.execute("DROP TABLE sources_old")

    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('default_quality', 'default_ad_removal')"
    ).fetchall()
    if rows:
        legacy = {row["key"].removeprefix("default_"): json.loads(row["value"]) for row in rows}
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('default_options', ?)",
            (json.dumps({**options.DEFAULTS, **legacy}),),
        )
        conn.execute("DELETE FROM settings WHERE key IN ('default_quality', 'default_ad_removal')")


def init():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)


def get_settings() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {**DEFAULTS, **{r["key"]: json.loads(r["value"]) for r in rows if r["key"] in DEFAULTS}}


def set_settings(values: dict):
    with transaction() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )
