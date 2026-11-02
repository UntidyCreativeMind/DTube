"""Show, season and track numbering, and filing downloads into library folders."""
import os
import shutil
import time
from pathlib import Path

from . import naming, nfo, tags
from . import options as opts
from .db import connect, transaction

AUDIO_FOLDER_PREFIX = "audio/"
VARIOUS_ARTISTS = "Various Artists"


def get_or_create_show(conn, playlist_id: str, title: str, description: str = "", artist: str = "",
                       folder_prefix: str = ""):
    row = conn.execute("SELECT * FROM shows WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if row:
        return row
    folder = folder_prefix + naming.show_folder(title)
    if conn.execute("SELECT 1 FROM shows WHERE folder = ?", (folder,)).fetchone():
        folder = f"{folder} [{playlist_id.split(':')[-1]}]"
    conn.execute(
        "INSERT INTO shows (playlist_id, title, folder, description, artist) VALUES (?, ?, ?, ?, ?)",
        (playlist_id, title, folder, description, artist),
    )
    return conn.execute("SELECT * FROM shows WHERE playlist_id = ?", (playlist_id,)).fetchone()


def get_or_create_album(conn, playlist_id: str, title: str, artist: str = ""):
    """An album is numbered like a show, separately from any TV version of the same playlist."""
    return get_or_create_show(
        conn, f"audio:{playlist_id}", title, artist=artist or VARIOUS_ARTISTS,
        folder_prefix=AUDIO_FOLDER_PREFIX,
    )


def get_or_create_season(conn, show_id: int, channel_id: str, channel_title: str):
    query = "SELECT * FROM seasons WHERE show_id = ? AND channel_id = ?"
    row = conn.execute(query, (show_id, channel_id)).fetchone()
    if row:
        return row
    number = conn.execute(
        "SELECT COALESCE(MAX(number), 0) + 1 FROM seasons WHERE show_id = ?", (show_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO seasons (show_id, channel_id, channel_title, number) VALUES (?, ?, ?, ?)",
        (show_id, channel_id, channel_title or channel_id, number),
    )
    return conn.execute(query, (show_id, channel_id)).fetchone()


def get_placement(conn, video_id: str, show_id: int):
    return conn.execute(
        "SELECT * FROM placements WHERE video_id = ? AND show_id = ?", (video_id, show_id)
    ).fetchone()


def _unique_movie_folder(conn, root: Path, title: str, video_id: str) -> Path:
    folder = root / naming.clean(title)
    taken = conn.execute(
        "SELECT 1 FROM placements WHERE show_id = 0 AND video_id != ? AND path LIKE ? ESCAPE '\\'",
        (video_id, _like_prefix(folder)),
    ).fetchone()
    if taken or (folder.exists() and any(folder.iterdir())):
        return root / f"{naming.clean(title)} [{video_id}]"
    return folder


def _like_prefix(folder: Path) -> str:
    prefix = f"{folder}{os.sep}"
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _next_number(conn, season) -> tuple:
    conn.execute("UPDATE seasons SET next_episode = next_episode + 1 WHERE id = ?", (season["id"],))
    return season["number"], season["next_episode"]


def allocate(video, mode: str, show_id: int, ext: str, settings: dict, options: dict,
             source_id: int | None = None) -> dict:
    """Return where a video belongs, reserving its numbers the first time it's seen.

    A re-download keeps the same numbers and folder, so nothing shifts in Plex.
    """
    stored = opts.dump(options)
    with transaction() as conn:
        existing = get_placement(conn, video["video_id"], show_id)
        if existing:
            base = Path(existing["path"]).with_suffix("")
            path = str(base.with_name(base.name + ext))
            conn.execute(
                "UPDATE placements SET path = ?, missing = 0, options = ? WHERE id = ?",
                (path, stored, existing["id"]),
            )
            return {**dict(existing), "path": path, "old_path": existing["path"], "options": stored}

        season = episode = None
        if mode == "tv":
            show = conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            row = get_or_create_season(conn, show_id, video["channel_id"] or "unknown", video["channel"])
            season, episode = _next_number(conn, row)
            base = naming.episode_base(show["title"], season, episode, video["title"])
            path = Path(settings["tv_dir"]) / show["folder"] / naming.season_folder(season) / (base + ext)
        elif mode == "audio":
            show = conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            row = get_or_create_season(conn, show_id, "album", show["title"])
            season, episode = _next_number(conn, row)
            path = album_folder(show, settings) / f"{naming.track_base(episode, video['title'])}{ext}"
        else:
            folder = _unique_movie_folder(conn, Path(settings["movie_dir"]), video["title"], video["video_id"])
            path = folder / (naming.clean(video["title"]) + ext)

        conn.execute(
            "INSERT INTO placements (video_id, mode, show_id, season, episode, path, created_at, options, source_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (video["video_id"], mode, show_id, season, episode, str(path), time.time(), stored, source_id),
        )
        return dict(get_placement(conn, video["video_id"], show_id)) | {"old_path": None}


def is_downloading(conn, placement) -> bool:
    return conn.execute(
        "SELECT 1 FROM jobs WHERE video_id = ? AND show_id = ? AND status IN ('downloading', 'processing')",
        (placement["video_id"], placement["show_id"]),
    ).fetchone() is not None


def remove_placement(conn, placement):
    """Take a video out of the history. Its episode number stays used, so nothing shifts."""
    video_id, show_id = placement["video_id"], placement["show_id"]
    conn.execute(
        "INSERT OR REPLACE INTO removed (video_id, show_id, source_id) VALUES (?, ?, ?)",
        (video_id, show_id, placement["source_id"]),
    )
    conn.execute("DELETE FROM jobs WHERE video_id = ? AND show_id = ? AND status = 'queued'", (video_id, show_id))
    conn.execute("DELETE FROM placements WHERE id = ?", (placement["id"],))


def forget_source(conn, source) -> int:
    """Clear what DTube remembers about a playlist, so tracking it again starts fresh.

    Only database rows go. Files on disk are left for the person to remove.
    """
    source_id, show_id = source["id"], source["show_id"]
    conn.execute(
        "DELETE FROM unavailable WHERE video_id IN (SELECT video_id FROM jobs WHERE source_id = ?)",
        (source_id,),
    )
    forgotten = conn.execute(
        "DELETE FROM placements WHERE source_id = ? OR (show_id != 0 AND show_id = ?)", (source_id, show_id)
    ).rowcount
    conn.execute("DELETE FROM jobs WHERE source_id = ? OR (show_id != 0 AND show_id = ?)", (source_id, show_id))
    conn.execute("DELETE FROM removed WHERE source_id = ? OR (show_id != 0 AND show_id = ?)", (source_id, show_id))
    conn.execute("DELETE FROM seasons WHERE show_id = ?", (show_id,))
    conn.execute("DELETE FROM shows WHERE id = ?", (show_id,))
    return forgotten


def album_folder(show, settings: dict) -> Path:
    return Path(settings["music_dir"]) / show["folder"].removeprefix(AUDIO_FOLDER_PREFIX)


def find_existing_file(video_id: str, skip_show_id: int, audio: bool, options: dict):
    """A matching copy of this video, already filed in another show, album or movie."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT path, mode, options FROM placements WHERE video_id = ? AND show_id != ?",
            (video_id, skip_show_id),
        ).fetchall()
    for row in rows:
        same_kind = (row["mode"] == "audio") == audio
        if same_kind and opts.parse(row["options"]) == options and os.path.isfile(row["path"]):
            return Path(row["path"])
    return None


def sidecar(path: Path, ext: str) -> Path:
    return path.with_name(path.stem + ext)


def subtitle_files(path: Path) -> dict:
    """Subtitles beside a video, keyed by language."""
    if not path.parent.is_dir():
        return {}
    prefix = path.stem + "."
    return {
        p.name[len(prefix):-len(".srt")]: p
        for p in path.parent.iterdir()
        if p.suffix == ".srt" and p.name.startswith(prefix)
    }


def _install(src: Path, dst: Path, how: str):
    """Put a file in place in one step, so Plex never sees a partial file.

    `how` is "move", "link" (a hardlink, or a copy if that isn't possible) or "copy".
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.unlink(missing_ok=True)
    if how == "move":
        shutil.move(str(src), tmp)
    elif how == "copy":
        shutil.copy2(src, tmp)
    else:
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
    tmp.replace(dst)


def install_video(src: Path, placement: dict, how: str = "move"):
    dst = Path(placement["path"])
    _install(src, dst, how)
    old = placement.get("old_path")
    if old and old != str(dst):
        Path(old).unlink(missing_ok=True)


def install_thumbnail(src, placement: dict, how: str = "move"):
    if not src or not Path(src).exists():
        return
    path = Path(placement["path"])
    if placement["mode"] != "audio":
        _install(Path(src), sidecar(path, ".jpg"), how)
    elif not (path.parent / "cover.jpg").exists():
        # Copied, because the same image is embedded in the track afterwards.
        _install(Path(src), path.parent / "cover.jpg", "copy")


def install_subtitles(subtitles: dict, placement: dict, how: str = "move"):
    path = Path(placement["path"])
    for old in subtitle_files(path).values():
        old.unlink()
    for lang, src in subtitles.items():
        _install(Path(src), sidecar(path, f".{lang}.srt"), how)


def write_metadata(video, placement: dict, settings: dict, thumbnail=None):
    path = Path(placement["path"])
    if placement["mode"] == "movie":
        nfo.write_movie(path.parent / "movie.nfo", video)
        return
    with connect() as conn:
        show = conn.execute("SELECT * FROM shows WHERE id = ?", (placement["show_id"],)).fetchone()
        seasons = conn.execute(
            "SELECT * FROM seasons WHERE show_id = ? ORDER BY number", (placement["show_id"],)
        ).fetchall()
    if placement["mode"] == "audio":
        tags.write_audio_tags(path, video, show["title"], show["artist"], placement["episode"], thumbnail)
        return
    nfo.write_episode(sidecar(path, ".nfo"), video, placement["season"], placement["episode"])
    folder = Path(settings["tv_dir"]) / show["folder"]
    nfo.write_tvshow(folder / "tvshow.nfo", show, seasons)
    poster = folder / "poster.jpg"
    thumb = sidecar(path, ".jpg")
    if not poster.exists() and thumb.exists():
        shutil.copy2(thumb, poster)
