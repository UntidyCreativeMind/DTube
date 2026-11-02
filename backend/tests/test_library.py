from pathlib import Path
from xml.etree import ElementTree as ET

from dtube import db, library, naming, plex


def video(video_id, channel="UC1", title=None):
    return {"video_id": video_id, "title": title or f"Video {video_id}", "channel_id": channel,
            "channel": f"Channel {channel}", "upload_date": "20240131", "description": "A & B <c>"}


def make_show(playlist_id="PL1", title="My Playlist"):
    with db.transaction() as conn:
        return library.get_or_create_show(conn, playlist_id, title)["id"]


def place(v, show_id, tmp_path):
    return library.allocate(v, "tv", show_id, ".mp4", str(tmp_path), str(tmp_path))


def test_clean_removes_unsafe_characters():
    assert naming.clean('a/b\\c:d*e?"f<g>h|i') == "a b c d e f g h i"
    assert naming.clean("...") == "Untitled"
    assert ".." not in naming.clean("../../etc")


def test_clean_cuts_on_a_character_boundary():
    assert naming.clean("é" * 100, 11) == "é" * 5


def test_episode_name_format():
    assert naming.episode_base("Show", 1, 2, "Title") == "Show - S01E02 - Title"
    assert naming.episode_base("Show", 1, 120, "T") == "Show - S01E120 - T"


def test_channels_become_seasons_and_episodes_count_up(tmp_path):
    show = make_show()
    first = place(video("a", "UC1"), show, tmp_path)
    second = place(video("b", "UC2"), show, tmp_path)
    third = place(video("c", "UC1"), show, tmp_path)
    assert (first["season"], first["episode"]) == (1, 1)
    assert (second["season"], second["episode"]) == (2, 1)
    assert (third["season"], third["episode"]) == (1, 2)
    assert Path(third["path"]) == tmp_path / "My Playlist" / "Season 01" / "My Playlist - S01E02 - Video c.mp4"


def test_redownload_keeps_numbers(tmp_path):
    show = make_show()
    first = place(video("a"), show, tmp_path)
    place(video("b"), show, tmp_path)
    again = library.allocate(video("a"), "tv", show, ".webm", str(tmp_path), str(tmp_path))
    assert (again["season"], again["episode"]) == (first["season"], first["episode"])
    assert again["path"].endswith("S01E01 - Video a.webm")
    assert again["old_path"] == first["path"]


def test_episode_numbers_are_not_reused_after_history_removal(tmp_path):
    show = make_show()
    place(video("a"), show, tmp_path)
    with db.connect() as conn:
        conn.execute("DELETE FROM placements WHERE video_id = 'a'")
    assert place(video("b"), show, tmp_path)["episode"] == 2


def test_playlists_with_the_same_title_get_separate_folders():
    with db.transaction() as conn:
        one = library.get_or_create_show(conn, "PL1", "Same")
        two = library.get_or_create_show(conn, "PL2", "Same")
    assert one["folder"] != two["folder"]


def test_movie_title_collision_uses_video_id(tmp_path):
    one = library.allocate(video("a", title="Same"), "movie", 0, ".mp4", "", str(tmp_path))
    two = library.allocate(video("b", title="Same"), "movie", 0, ".mp4", "", str(tmp_path))
    assert Path(one["path"]).parent.name == "Same"
    assert Path(two["path"]).parent.name == "Same [b]"


def test_filing_writes_valid_nfos_and_poster(tmp_path):
    show = make_show()
    v = video("a")
    placement = place(v, show, tmp_path)
    src = tmp_path / "in.mp4"
    thumb = tmp_path / "in.jpg"
    src.write_bytes(b"video")
    thumb.write_bytes(b"image")

    library.install_video(src, placement)
    library.install_thumbnail(thumb, placement)
    library.write_metadata(v, placement, str(tmp_path))

    path = Path(placement["path"])
    assert path.read_bytes() == b"video"
    assert path.with_suffix(".jpg").exists()
    assert (path.parent.parent / "poster.jpg").exists()
    episode = ET.parse(path.with_suffix(".nfo")).getroot()
    assert episode.tag == "episodedetails"
    assert episode.findtext("plot") == "A & B <c>"
    assert episode.findtext("aired") == "2024-01-31"
    assert (episode.findtext("season"), episode.findtext("episode")) == ("1", "1")
    tvshow = ET.parse(path.parent.parent / "tvshow.nfo").getroot()
    assert tvshow.find("namedseason").attrib["number"] == "1"
    assert tvshow.findtext("namedseason") == "Channel UC1"


def test_second_playlist_links_existing_file(tmp_path):
    first_show, second_show = make_show("PL1", "One"), make_show("PL2", "Two")
    placement = place(video("a"), first_show, tmp_path)
    Path(placement["path"]).parent.mkdir(parents=True)
    Path(placement["path"]).write_bytes(b"video")

    existing = library.find_existing_file("a", second_show)
    assert existing == Path(placement["path"])
    linked = place(video("a"), second_show, tmp_path)
    library.install_video(existing, linked, link=True)
    assert Path(linked["path"]).read_bytes() == b"video"
    assert Path(placement["path"]).exists()


def test_plex_path_mapping():
    settings = {"plex_path_from": "/media", "plex_path_to": "/data/"}
    assert plex.map_path("/media/tv/Show", settings) == "/data/tv/Show"
    assert plex.map_path("/media2/tv", settings) == "/media2/tv"
    assert plex.map_path("/media/tv", {"plex_path_from": "", "plex_path_to": ""}) == "/media/tv"
