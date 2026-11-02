from pathlib import Path
from xml.etree import ElementTree as ET

from .naming import iso_date


def _add(parent, tag, text, **attrs):
    if text in (None, ""):
        return
    el = ET.SubElement(parent, tag, attrs)
    el.text = str(text)


def _write(root, path: Path):
    ET.indent(root)
    tmp = path.with_name(path.name + ".tmp")
    ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)


def write_tvshow(path: Path, show, seasons):
    root = ET.Element("tvshow")
    _add(root, "title", show["title"])
    _add(root, "plot", show["description"])
    _add(root, "uniqueid", show["playlist_id"], type="youtube", default="true")
    for season in seasons:
        _add(root, "namedseason", season["channel_title"], number=str(season["number"]))
    _write(root, path)


def write_episode(path: Path, video, season: int, episode: int):
    root = ET.Element("episodedetails")
    _add(root, "title", video["title"])
    _add(root, "season", season)
    _add(root, "episode", episode)
    _add(root, "plot", video["description"])
    _add(root, "aired", iso_date(video["upload_date"]))
    _add(root, "uniqueid", video["video_id"], type="youtube", default="true")
    _write(root, path)


def write_movie(path: Path, video):
    root = ET.Element("movie")
    _add(root, "title", video["title"])
    _add(root, "plot", video["description"])
    _add(root, "premiered", iso_date(video["upload_date"]))
    _add(root, "studio", video["channel"])
    _add(root, "uniqueid", video["video_id"], type="youtube", default="true")
    _write(root, path)
