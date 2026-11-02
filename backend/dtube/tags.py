from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover


def write_audio_tags(path: Path, video, album: str, album_artist: str, track: int, cover=None):
    audio = MP4(path)
    audio["\xa9nam"] = [video["title"]]
    audio["\xa9ART"] = [video["channel"] or album_artist]
    audio["\xa9alb"] = [album]
    audio["aART"] = [album_artist]
    audio["trkn"] = [(track, 0)]
    if video["upload_date"]:
        audio["\xa9day"] = [video["upload_date"][:4]]
    if cover and Path(cover).exists():
        audio["covr"] = [MP4Cover(Path(cover).read_bytes(), imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
