"""Library statistics for the homepage."""
import os
import shutil
import time
from collections import Counter

from . import options as opts
from .db import MEDIA_ROOT, connect

ACTIVITY_DAYS = 30
TOP_CHANNELS = 5
RECENT_DOWNLOADS = 6


def _storage(placements) -> dict:
    """Size on disk per kind. A hardlinked file takes its space once, so it's counted once."""
    sizes = Counter()
    seen = set()
    missing = 0
    for row in placements:
        try:
            stat = os.stat(row["path"])
        except OSError:
            missing += 1
            continue
        identity = (stat.st_dev, stat.st_ino)
        if identity not in seen:
            seen.add(identity)
            sizes[row["mode"]] += stat.st_size
    return {
        "total_bytes": sum(sizes.values()),
        "tv_bytes": sizes["tv"],
        "movie_bytes": sizes["movie"],
        "audio_bytes": sizes["audio"],
        "missing": missing,
    }


def _disk() -> dict:
    try:
        usage = shutil.disk_usage(MEDIA_ROOT)
    except OSError:
        return {"free_bytes": None, "total_bytes": None}
    return {"free_bytes": usage.free, "total_bytes": usage.total}


def collect() -> dict:
    now = time.time()
    with connect() as conn:
        placements = conn.execute(
            "SELECT p.video_id, p.mode, p.path, p.options, p.created_at, v.duration, v.channel, "
            "COALESCE(v.title, p.video_id) AS title "
            "FROM placements p LEFT JOIN videos v ON v.video_id = p.video_id ORDER BY p.created_at DESC"
        ).fetchall()
        sources = conn.execute("SELECT mode, check_frequency FROM sources").fetchall()
        statuses = Counter(dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall()))

    modes = Counter(row["mode"] for row in placements)
    source_modes = Counter(row["mode"] for row in sources)
    unique = {row["video_id"]: row for row in placements}
    durations = [row["duration"] for row in unique.values() if row["duration"]]
    channels = Counter(row["channel"] for row in unique.values() if row["channel"])
    cutoff = now - ACTIVITY_DAYS * 86400

    return {
        "playlists": {
            "total": len(sources),
            "tv": source_modes["tv"],
            "movie": source_modes["movie"],
            "audio": source_modes["audio"],
            "auto_checked": sum(row["check_frequency"] != "off" for row in sources),
        },
        "videos": {
            "total": len(placements),
            "unique": len(unique),
            "tv": modes["tv"],
            "movie": modes["movie"],
            "audio": modes["audio"],
            "ads_removed": sum(opts.parse(row["options"])["ad_removal"] != "off" for row in placements),
            "channels": len(channels),
        },
        "storage": _storage(placements),
        "disk": _disk(),
        "runtime": {"seconds": sum(durations), "known": len(durations), "videos": len(unique)},
        "queue": {
            "queued": statuses["queued"],
            "active": statuses["downloading"] + statuses["processing"],
            "failed": statuses["failed"],
        },
        "top_channels": [{"channel": name, "count": count} for name, count in channels.most_common(TOP_CHANNELS)],
        # Raw times, so the page can group them into days in the viewer's own time zone.
        "activity": [row["created_at"] for row in placements if row["created_at"] >= cutoff],
        "recent": [
            {"title": row["title"], "mode": row["mode"], "created_at": row["created_at"]}
            for row in placements[:RECENT_DOWNLOADS]
        ],
    }
