"""Show, season and episode numbering, and filing videos into library folders."""
import os
import shutil
import time
from pathlib import Path

from . import naming, nfo
from .db import connect, transaction


def get_or_create_show(conn, playlist_id: str, title: str, description: str = ""):
    row = conn.execute("SELECT * FROM shows WHERE playlist_id = ?", (playlist_id,)).fetchone()
    if row:
        return row
    folder = naming.show_folder(title)
    if conn.execute("SELECT 1 FROM shows WHERE folder = ?", (folder,)).fetchone():
        folder = f"{folder} [{playlist_id}]"
    conn.execute(
        "INSERT INTO shows (playlist_id, title, folder, description) VALUES (?, ?, ?, ?)",
        (playlist_id, title, folder, description),
    )
    return conn.execute("SELECT * FROM shows WHERE playlist_id = ?", (playlist_id,)).fetchone()


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


def allocate(video, mode: str, show_id: int, ext: str, tv_dir: str, movie_dir: str,
             ad_removal: str = "off") -> dict:
    """Return where a video belongs, reserving its numbers the first time it's seen.

    A re-download keeps the same numbers and folder, so nothing shifts in Plex.
    """
    with transaction() as conn:
        existing = get_placement(conn, video["video_id"], show_id)
        if existing:
            base = Path(existing["path"]).with_suffix("")
            path = str(base.with_name(base.name + ext))
            conn.execute(
                "UPDATE placements SET path = ?, missing = 0, ad_removal = ? WHERE id = ?",
                (path, ad_removal, existing["id"]),
            )
            return {**dict(existing), "path": path, "old_path": existing["path"], "ad_removal": ad_removal}

        season = episode = None
        if mode == "tv":
            show = conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
            row = get_or_create_season(conn, show_id, video["channel_id"] or "unknown", video["channel"])
            season, episode = row["number"], row["next_episode"]
            conn.execute("UPDATE seasons SET next_episode = next_episode + 1 WHERE id = ?", (row["id"],))
            base = naming.episode_base(show["title"], season, episode, video["title"])
            path = Path(tv_dir) / show["folder"] / naming.season_folder(season) / (base + ext)
        else:
            folder = _unique_movie_folder(conn, Path(movie_dir), video["title"], video["video_id"])
            path = folder / (naming.clean(video["title"]) + ext)

        conn.execute(
            "INSERT INTO placements (video_id, mode, show_id, season, episode, path, created_at, ad_removal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (video["video_id"], mode, show_id, season, episode, str(path), time.time(), ad_removal),
        )
        return dict(get_placement(conn, video["video_id"], show_id)) | {"old_path": None}


def find_existing_file(video_id: str, skip_show_id: int, ad_removal: str):
    """A copy of this video with the same cuts, already in another show or the movie library."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT path FROM placements WHERE video_id = ? AND show_id != ? AND ad_removal = ?",
            (video_id, skip_show_id, ad_removal),
        ).fetchall()
    for row in rows:
        if os.path.isfile(row["path"]):
            return Path(row["path"])
    return None


def sidecar(path: Path, ext: str) -> Path:
    return path.with_name(path.stem + ext)


def _install(src: Path, dst: Path, link: bool):
    """Put a file in place in one step, so Plex never sees a partial file."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.unlink(missing_ok=True)
    if not link:
        shutil.move(str(src), tmp)
    else:
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
    tmp.replace(dst)


def install_video(src: Path, placement: dict, link: bool = False):
    dst = Path(placement["path"])
    _install(src, dst, link)
    old = placement.get("old_path")
    if old and old != str(dst):
        Path(old).unlink(missing_ok=True)


def install_thumbnail(src, placement: dict, link: bool = False):
    if src and Path(src).exists():
        _install(Path(src), sidecar(Path(placement["path"]), ".jpg"), link)


def write_metadata(video, placement: dict, tv_dir: str):
    path = Path(placement["path"])
    if placement["mode"] == "movie":
        nfo.write_movie(path.parent / "movie.nfo", video)
        return
    nfo.write_episode(sidecar(path, ".nfo"), video, placement["season"], placement["episode"])
    with connect() as conn:
        show = conn.execute("SELECT * FROM shows WHERE id = ?", (placement["show_id"],)).fetchone()
        seasons = conn.execute(
            "SELECT * FROM seasons WHERE show_id = ? ORDER BY number", (placement["show_id"],)
        ).fetchall()
    folder = Path(tv_dir) / show["folder"]
    nfo.write_tvshow(folder / "tvshow.nfo", show, seasons)
    poster = folder / "poster.jpg"
    thumb = sidecar(path, ".jpg")
    if not poster.exists() and thumb.exists():
        shutil.copy2(thumb, poster)
