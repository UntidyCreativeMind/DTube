"""Runs yt-dlp as a subprocess, so an update applies without restarting DTube."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import CONFIG_DIR

PACKAGES = CONFIG_DIR / "packages"
COOKIES = CONFIG_DIR / "cookies.txt"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
PROGRESS = re.compile(r"^DTUBE\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)$")
UNAVAILABLE = re.compile(
    r"private video|video unavailable|has been removed|has been terminated|members[- ]only|"
    r"not available in your country|sign in to confirm your age",
    re.IGNORECASE,
)
HIDDEN_TITLES = {"[Private video]", "[Deleted video]"}
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".m4a"}
SUBTITLES_FAILED = "Saved without subtitles, because YouTube wouldn't provide them. Re-download from History to try again."
AD_CATEGORIES = {
    "off": [],
    "conservative": ["selfpromo", "interaction"],
    "aggressive": ["selfpromo", "interaction", "sponsor"],
}


class YtdlpError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def check_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "").lower() not in YOUTUBE_HOSTS:
        raise ValueError("Enter a YouTube video or playlist URL")
    return url


def is_playlist_url(url: str) -> bool:
    parsed = urlparse(url)
    playlist = parse_qs(parsed.query).get("list", [""])[0]
    if parsed.path.rstrip("/") == "/playlist":
        return True
    # Auto-generated mixes (RD...) are endless, so treat those as the single video.
    return bool(playlist) and not playlist.startswith("RD")


def _env() -> dict:
    env = dict(os.environ)
    if PACKAGES.is_dir():
        env["PYTHONPATH"] = str(PACKAGES)
    return env


def _base_args() -> list:
    args = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-warnings", "--sleep-requests", "1"]
    if COOKIES.exists():
        args += ["--cookies", str(COOKIES)]
    return args


def _error_text(output: str) -> str:
    errors = [line for line in output.splitlines() if line.startswith("ERROR:")]
    text = (errors[-1] if errors else output.strip()[-300:]) or "yt-dlp failed"
    if "Unable to communicate with SponsorBlock" in text:
        return "Couldn't reach SponsorBlock, so ads couldn't be removed. The video wasn't filed."
    text = re.sub(r"^ERROR:\s*(\[\w+\] [\w-]+: )?", "", text)
    text = re.split(r" If the owner| Use --cookies| See  ?https?://", text)[0].strip()
    if "not a bot" in text or "HTTP Error 429" in text:
        text += " (YouTube is limiting requests. Wait a while, or add cookies in Settings.)"
    return text


def probe(url: str) -> dict:
    """Return video or playlist details without downloading."""
    args = _base_args() + ["-J", "--flat-playlist"]
    args.append("--yes-playlist" if is_playlist_url(url) else "--no-playlist")
    result = subprocess.run(
        args + ["--", url], capture_output=True, text=True, timeout=600, env=_env()
    )
    if result.returncode != 0:
        raise YtdlpError(_error_text(result.stderr))
    info = json.loads(result.stdout)
    if info.get("_type") != "playlist":
        return {"type": "video", "video_id": info["id"], "title": info.get("title") or info["id"],
                "channel": info.get("channel") or info.get("uploader") or "",
                "thumbnail": info.get("thumbnail") or "", "count": 1, "entries": []}

    entries = []
    for entry in info.get("entries") or []:
        if not entry or entry.get("_type") == "playlist":
            raise YtdlpError("That page has several playlists. Paste one playlist URL.", permanent=True)
        if entry.get("id") and entry.get("title") not in HIDDEN_TITLES:
            entries.append({"video_id": entry["id"], "title": entry.get("title") or entry["id"]})
    thumbs = info.get("thumbnails") or []
    return {"type": "playlist", "playlist_id": info["id"], "title": info.get("title") or info["id"],
            "channel": info.get("channel") or info.get("uploader") or "",
            "description": info.get("description") or "",
            "thumbnail": thumbs[-1].get("url", "") if thumbs else "",
            "count": len(entries), "entries": entries}


def format_selector(quality: str, audio_lang: str = "original") -> str:
    """Pick streams, falling back to the original audio if a language isn't available."""
    lang = "" if audio_lang == "original" else f"[language^={audio_lang}]"
    if quality == "max":
        preferred = f"bv+ba{lang}/" if lang else ""
        return f"{preferred}bv*+ba/b"
    q = f"[height<={quality}]"
    # Plain bv, because bv* can pick a format with its own audio and drop the chosen language.
    preferred = f"bv{q}[vcodec^=avc1]+ba{lang}[ext=m4a]/bv{q}+ba{lang}/" if lang else ""
    return f"{preferred}bv*{q}[vcodec^=avc1]+ba[ext=m4a]/b{q}[vcodec^=avc1]/bv*{q}+ba/b{q}/b"


def audio_selector(audio_lang: str = "original") -> str:
    lang = "" if audio_lang == "original" else f"[language^={audio_lang}]"
    preferred = f"ba{lang}[ext=m4a]/ba{lang}/" if lang else ""
    return f"{preferred}ba[ext=m4a]/ba/b"


def _pick_subtitle(folder: Path, lang: str) -> dict:
    """yt-dlp can fetch several variants of a language, so keep the closest match."""
    files = sorted(
        folder.glob("video.*.srt"),
        key=lambda p: (p.name != f"video.{lang}.srt", len(p.name), p.name),
    )
    return {lang: files[0]} if files else {}


def download(video_id: str, folder: Path, options: dict, audio_only: bool, on_progress, on_start) -> dict:
    """Download into `folder` and return the info and the paths of what was fetched."""
    template = (
        "download:DTUBE|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
        "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s"
    )
    # Warnings stay on here, because they're how a failed subtitle download shows up.
    args = [arg for arg in _base_args() if arg != "--no-warnings"] + [
        "--no-playlist", "--progress", "--newline", "--no-part", "--no-mtime",
        "--sleep-interval", "2", "--max-sleep-interval", "5",
        "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--progress-template", template, "-o", str(folder / "video.%(ext)s"),
    ]
    if audio_only:
        args += ["-f", audio_selector(options["audio_lang"]), "-x", "--audio-format", "m4a"]
    else:
        quality = options["quality"]
        # The best streams are usually VP9 or AV1 with Opus, which MKV holds reliably.
        args += ["-f", format_selector(quality, options["audio_lang"]),
                 "--merge-output-format", "mkv" if quality == "max" else "mp4"]
        if options["chapters"]:
            args.append("--embed-chapters")
        if options["subtitles"] != "off":
            # Without --ignore-errors, a failed subtitle download would fail the whole video.
            args += ["--write-subs", "--sub-langs", f"{options['subtitle_lang']}(-.*)?",
                     "--convert-subs", "srt", "--sleep-subtitles", "2", "--ignore-errors"]
            if options["subtitles"] == "auto":
                args.append("--write-auto-subs")
    if AD_CATEGORIES[options["ad_removal"]]:
        args += ["--sponsorblock-remove", ",".join(AD_CATEGORIES[options["ad_removal"]])]
    args += ["--", f"https://www.youtube.com/watch?v={video_id}"]
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env(),
        errors="replace",
    )
    on_start(proc)
    tail = []
    subtitles_failed = False
    for line in proc.stdout:
        line = line.rstrip()
        match = PROGRESS.match(line)
        if match:
            on_progress(*_parse_progress(match.groups()))
        else:
            tail = (tail + [line])[-40:]
            subtitles_failed = subtitles_failed or "Unable to download video subtitles" in line
    if proc.wait() != 0:
        output = "\n".join(tail)
        raise YtdlpError(_error_text(output), permanent=bool(UNAVAILABLE.search(output)))

    info_file = folder / "video.info.json"
    videos = [p for p in folder.glob("video.*") if p.suffix in MEDIA_EXTS]
    if not videos or not info_file.exists():
        raise YtdlpError("yt-dlp finished but no video file was found")
    thumb = folder / "video.jpg"
    subtitles = _pick_subtitle(folder, options["subtitle_lang"])
    return {"info": json.loads(info_file.read_text(encoding="utf-8")), "video": videos[0],
            "thumbnail": thumb if thumb.exists() else None, "subtitles": subtitles,
            # One variant, such as en-orig, can fail while another worked.
            "warning": SUBTITLES_FAILED if subtitles_failed and not subtitles else None}


def _number(value):
    try:
        return float(value)
    except ValueError:
        return None


def _parse_progress(fields):
    done, total, estimate, speed, eta = map(_number, fields)
    size = total or estimate
    return ((done / size * 100) if done and size else 0, speed, int(eta) if eta else None)


def version() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, text=True, env=_env()
    )
    return result.stdout.strip() or "unknown"


def update() -> str:
    PACKAGES.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--no-cache-dir",
         "--target", str(PACKAGES), "yt-dlp[default]"],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise YtdlpError(result.stderr.strip()[-300:] or "Update failed")
    return version()
