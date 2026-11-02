import re
import unicodedata

INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean(name: str, limit: int = 120, fallback: str = "Untitled") -> str:
    """Make a title safe for a file name, cut to `limit` UTF-8 bytes."""
    name = INVALID.sub(" ", unicodedata.normalize("NFC", name or ""))
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name.encode()[:limit].decode(errors="ignore").strip(" .")
    return name or fallback


def show_folder(title: str) -> str:
    return clean(title, 80)


def episode_base(show: str, season: int, episode: int, title: str) -> str:
    return f"{clean(show, 80)} - S{season:02d}E{episode:02d} - {clean(title)}"


def track_base(track: int, title: str) -> str:
    return f"{track:02d} - {clean(title)}"


def season_folder(season: int) -> str:
    return f"Season {season:02d}"


def iso_date(upload_date: str) -> str:
    """Turn yt-dlp's YYYYMMDD into YYYY-MM-DD, or '' if it isn't one."""
    if re.fullmatch(r"\d{8}", upload_date or ""):
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return ""
