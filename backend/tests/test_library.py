from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from dtube import db, library, naming, options, plex


def settings_for(root):
    return {"tv_dir": str(root), "movie_dir": str(root), "music_dir": str(root / "music")}


def video(video_id, channel="UC1", title=None):
    return {"video_id": video_id, "title": title or f"Video {video_id}", "channel_id": channel,
            "channel": f"Channel {channel}", "upload_date": "20240131", "description": "A & B <c>"}


def make_show(playlist_id="PL1", title="My Playlist"):
    with db.transaction() as conn:
        return library.get_or_create_show(conn, playlist_id, title)["id"]


def place(v, show_id, tmp_path):
    return library.allocate(v, "tv", show_id, ".mp4", settings_for(tmp_path), options.DEFAULTS)


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
    again = library.allocate(video("a"), "tv", show, ".webm", settings_for(tmp_path), options.DEFAULTS)
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
    one = library.allocate(video("a", title="Same"), "movie", 0, ".mp4", settings_for(tmp_path), options.DEFAULTS)
    two = library.allocate(video("b", title="Same"), "movie", 0, ".mp4", settings_for(tmp_path), options.DEFAULTS)
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
    library.write_metadata(v, placement, settings_for(tmp_path))

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

    existing = library.find_existing_file("a", second_show, False, options.DEFAULTS)
    assert existing == Path(placement["path"])
    for change in ({"ad_removal": "aggressive"}, {"quality": "max"}, {"subtitles": "auto"},
                   {"audio_lang": "es"}, {"chapters": False}):
        assert library.find_existing_file("a", second_show, False, {**options.DEFAULTS, **change}) is None
    assert library.find_existing_file("a", second_show, True, options.DEFAULTS) is None
    linked = place(video("a"), second_show, tmp_path)
    library.install_video(existing, linked, "link")
    assert Path(linked["path"]).read_bytes() == b"video"
    assert Path(placement["path"]).exists()


def test_copying_leaves_the_original_and_is_independent(tmp_path):
    first, second = make_show("PL1", "One"), make_show("PL2", "Two")
    placement = place(video("a"), first, tmp_path)
    original = Path(placement["path"])
    original.parent.mkdir(parents=True)
    original.write_bytes(b"video")

    copied = place(video("a"), second, tmp_path)
    library.install_video(original, copied, "copy")
    Path(copied["path"]).write_bytes(b"changed")
    assert original.read_bytes() == b"video"


def test_subtitles_sit_beside_the_video_and_are_replaced(tmp_path):
    placement = place(video("a"), make_show(), tmp_path)
    path = Path(placement["path"])
    src = tmp_path / "one.srt"
    src.write_text("one")
    library.install_subtitles({"en": src}, placement)
    subtitle = path.parent / "My Playlist - S01E01 - Video a.en.srt"
    assert subtitle.read_text() == "one"
    assert library.subtitle_files(path) == {"en": subtitle}

    other = tmp_path / "two.srt"
    other.write_text("two")
    library.install_subtitles({"es": other}, placement)
    assert not subtitle.exists()
    assert list(library.subtitle_files(path)) == ["es"]

    library.install_subtitles({}, placement)
    assert library.subtitle_files(path) == {}


def test_albums_number_tracks_and_stay_apart_from_tv(tmp_path):
    with db.transaction() as conn:
        tv = library.get_or_create_show(conn, "PL1", "Songs")
        album = library.get_or_create_album(conn, "PL1", "Songs", "Artist")
        again = library.get_or_create_album(conn, "PL1", "Songs", "Artist")
        same_title = library.get_or_create_album(conn, "PL2", "Songs")
    assert album["id"] == again["id"] != tv["id"]
    assert album["artist"] == "Artist"
    assert same_title["artist"] == library.VARIOUS_ARTISTS
    assert library.album_folder(album, settings_for(tmp_path)).name == "Songs"
    assert library.album_folder(same_title, settings_for(tmp_path)).name == "Songs [PL2]"

    settings = settings_for(tmp_path)
    first = library.allocate(video("a", "UC1"), "audio", album["id"], ".m4a", settings, options.DEFAULTS)
    second = library.allocate(video("b", "UC2"), "audio", album["id"], ".m4a", settings, options.DEFAULTS)
    assert (first["season"], first["episode"]) == (1, 1)
    assert (second["season"], second["episode"]) == (1, 2)
    assert Path(second["path"]) == tmp_path / "music" / "Songs" / "02 - Video b.m4a"
    tv_track = library.allocate(video("a"), "tv", tv["id"], ".mp4", settings, options.DEFAULTS)
    assert tv_track["episode"] == 1


def test_options_reject_unknown_values():
    assert options.merged({"quality": "720"}, options.DEFAULTS)["quality"] == "720"
    for bad in ({"quality": "4k"}, {"subtitles": "yes"}, {"audio_lang": "xx"}, {"unknown": 1}, {"chapters": "no"}):
        with pytest.raises(ValueError):
            options.merged(bad, options.DEFAULTS)


def test_stored_options_fill_in_new_defaults():
    assert options.parse("{}") == options.DEFAULTS
    assert options.parse('{"quality": "720", "retired": 1}') == {**options.DEFAULTS, "quality": "720"}


def test_plex_path_mapping():
    settings = {"plex_path_from": "/media", "plex_path_to": "/data/"}
    assert plex.map_path("/media/tv/Show", settings) == "/data/tv/Show"
    assert plex.map_path("/media2/tv", settings) == "/media2/tv"
    assert plex.map_path("/media/tv", {"plex_path_from": "", "plex_path_to": ""}) == "/media/tv"
