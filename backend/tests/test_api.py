import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dtube import auth, db, main, options, worker, ytdlp

PLAYLIST = {
    "type": "playlist", "playlist_id": "PL1", "title": "My Playlist", "channel": "C",
    "description": "", "thumbnail": "", "count": 2,
    "entries": [{"video_id": "a", "title": "A"}, {"video_id": "b", "title": "B"}],
}
VIDEO = {"type": "video", "video_id": "v", "title": "Solo", "channel": "C", "thumbnail": "", "count": 1, "entries": []}
PASSWORD = "correct-horse-battery"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL1"


@pytest.fixture
def client(monkeypatch):
    auth._failures.clear()
    monkeypatch.setattr(worker, "start", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin(client):
    assert client.post("/api/auth/setup", json={"username": "admin", "password": PASSWORD}).status_code == 200
    return client


class Stop(Exception):
    pass


@pytest.fixture
def captured_args(monkeypatch):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        raise Stop

    monkeypatch.setattr(ytdlp.subprocess, "Popen", fake_popen)
    return captured


def args_for(captured, tmp_path, audio_only=False, **changes):
    with pytest.raises(Stop):
        ytdlp.download("a", tmp_path, {**options.DEFAULTS, **changes}, audio_only, None, None)
    return captured["args"]


def probe_as(monkeypatch, info):
    monkeypatch.setattr(ytdlp, "probe", lambda url: dict(info))


def job_rows():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()


def job_options(index=0):
    return options.parse(job_rows()[index]["options"])


def use_media_folders():
    media = db.MEDIA_ROOT
    for name in ("temp", "tv", "movies", "music"):
        (media / name).mkdir(exist_ok=True)
    db.set_settings({"temp_dir": str(media / "temp"), "tv_dir": str(media / "tv"),
                     "movie_dir": str(media / "movies"), "music_dir": str(media / "music"), "min_free_gb": 0})
    return media


def test_api_requires_login(client):
    for path in ("/api/settings", "/api/queue", "/api/history", "/api/fs", "/api/cookies", "/api/system"):
        assert client.get(path).status_code == 401


def test_setup_only_works_once(admin):
    response = admin.post("/api/auth/setup", json={"username": "other", "password": PASSWORD})
    assert response.status_code == 409


def test_setup_requires_long_password(client):
    assert client.post("/api/auth/setup", json={"username": "a", "password": "short"}).status_code == 422


def test_login_rate_limit(admin):
    admin.cookies.clear()
    for _ in range(5):
        assert admin.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    response = admin.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
    assert response.status_code == 429


def test_logout_ends_session(admin):
    admin.post("/api/auth/logout")
    assert admin.get("/api/settings").status_code == 401


def test_rejects_non_youtube_urls(admin):
    assert admin.post("/api/preview", json={"url": "http://192.168.1.1/admin"}).status_code == 400
    assert admin.post("/api/preview", json={"url": "--exec=rm"}).status_code == 400


def test_playlist_url_detection():
    assert ytdlp.is_playlist_url("https://www.youtube.com/playlist?list=PL1")
    assert ytdlp.is_playlist_url("https://www.youtube.com/watch?v=a&list=PL1")
    assert not ytdlp.is_playlist_url("https://www.youtube.com/watch?v=a")
    assert not ytdlp.is_playlist_url("https://www.youtube.com/watch?v=a&list=RDa")


def test_single_video_is_a_movie_unless_audio(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    url = "https://youtu.be/v"
    assert admin.post("/api/sources", json={"url": url, "mode": "tv"}).json()["added"] == 1
    assert job_rows()[0]["mode"] == "movie"
    assert job_rows()[0]["show_id"] == 0

    assert admin.post("/api/sources", json={"url": url, "mode": "audio"}).json()["added"] == 1
    assert job_rows()[1]["mode"] == "audio"
    assert job_rows()[1]["show_id"] > 0


def test_invalid_add_requests_are_rejected(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    for extra in ({"mode": "podcast"}, {"episode_order": "random"}, {"check_frequency": "weekly"},
                  {"options": {"quality": "4k"}}, {"options": {"subtitles": "maybe"}}, {"options": {"nope": 1}}):
        assert admin.post("/api/sources", json={"url": PLAYLIST_URL, **extra}).status_code == 400


def test_playlist_in_tv_mode_creates_show_and_queues_in_order(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"}).json()["added"] == 2
    rows = job_rows()
    assert [r["video_id"] for r in rows] == ["a", "b"]
    assert all(r["mode"] == "tv" and r["show_id"] > 0 for r in rows)


def test_reverse_order_queues_the_last_video_first(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    body = {"url": PLAYLIST_URL, "mode": "tv", "episode_order": "reverse"}
    admin.post("/api/sources", json=body)
    assert [r["video_id"] for r in job_rows()] == ["b", "a"]
    assert admin.get("/api/sources").json()[0]["episode_order"] == "reverse"


def test_new_videos_stay_in_order_after_a_reversed_import(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv", "episode_order": "reverse"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    newest_first = [{"video_id": "d", "title": "D"}, {"video_id": "c", "title": "C"}, *PLAYLIST["entries"]]
    probe_as(monkeypatch, {**PLAYLIST, "entries": newest_first})
    admin.post(f"/api/sources/{source_id}/check")
    assert [r["video_id"] for r in job_rows()] == ["b", "a", "c", "d"]


def test_episode_order_can_be_changed_for_later_videos(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    admin.patch(f"/api/sources/{source_id}", json={"episode_order": "reverse"})
    assert admin.get("/api/sources").json()[0]["episode_order"] == "reverse"
    assert admin.patch(f"/api/sources/{source_id}", json={"episode_order": "x"}).status_code == 400


def test_playlist_in_movie_mode_goes_to_movie_library(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "movie"})
    assert all(r["mode"] == "movie" and r["show_id"] == 0 for r in job_rows())


def test_one_playlist_can_be_tv_and_audio_at_once(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "audio"}).json()["added"] == 2
    assert len(admin.get("/api/sources").json()) == 2
    assert {r["mode"] for r in job_rows()} == {"tv", "audio"}


def test_adding_twice_does_not_duplicate_or_redownload(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"}).json()["added"] == 0
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done'")
        for video_id in ("a", "b"):
            conn.execute(
                "INSERT INTO placements (video_id, mode, show_id, path, created_at) VALUES (?, 'tv', 1, 'x', 0)",
                (video_id,),
            )
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"}).json()["added"] == 0


def test_redownload_is_explicit(admin):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO placements (id, video_id, mode, show_id, path, created_at) VALUES (1, 'a', 'movie', 0, 'x', 0)"
        )
    assert admin.post("/api/history/1/redownload").json()["queued"] is True
    assert job_rows()[0]["force"] == 1


def test_options_default_to_settings_and_can_be_overridden(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    defaults = {"quality": "max", "ad_removal": "conservative", "subtitles": "auto", "audio_lang": "es"}
    assert admin.put("/api/settings", json={"default_options": defaults}).status_code == 200
    admin.post("/api/sources", json={"url": "https://youtu.be/v"})
    assert job_options() == {**options.DEFAULTS, **defaults}
    with db.connect() as conn:
        conn.execute("DELETE FROM jobs")
    admin.post("/api/sources", json={"url": "https://youtu.be/v", "options": {"quality": "480"}})
    assert job_options()["quality"] == "480"
    assert job_options()["ad_removal"] == "conservative"


def test_settings_reject_bad_default_options(admin):
    assert admin.put("/api/settings", json={"default_options": {"quality": "4k"}}).status_code == 400
    assert admin.put("/api/settings", json={"default_options": {"nope": 1}}).status_code == 400


def test_playlist_options_apply_to_later_checks(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv", "options": {"quality": "720"}})
    assert job_options()["quality"] == "720"
    source_id = admin.get("/api/sources").json()[0]["id"]
    admin.patch(f"/api/sources/{source_id}", json={"options": {"ad_removal": "aggressive", "subtitles": "manual"}})
    source = admin.get("/api/sources").json()[0]
    assert source["options"]["quality"] == "720"
    assert source["options"]["ad_removal"] == "aggressive"
    assert source["check_frequency"] == "off"
    assert admin.patch(f"/api/sources/{source_id}", json={"options": {"quality": "8k"}}).status_code == 400
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled'")
    admin.post(f"/api/sources/{source_id}/check")
    assert job_options(-1)["ad_removal"] == "aggressive"
    assert job_options(-1)["subtitles"] == "manual"


def test_redownload_keeps_or_changes_options(admin):
    stored = options.dump({**options.DEFAULTS, "quality": "720", "ad_removal": "conservative"})
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO placements (id, video_id, mode, show_id, path, created_at, options) "
            "VALUES (1, 'a', 'movie', 0, 'x', 0, ?)", (stored,),
        )
    admin.post("/api/history/1/redownload")
    assert job_options()["quality"] == "720"
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done'")
    admin.post("/api/history/1/redownload", json={"options": {"quality": "max"}})
    assert job_options(1)["quality"] == "max"
    assert job_options(1)["ad_removal"] == "conservative"


def test_ad_levels_map_to_sponsorblock_categories(tmp_path, captured_args):
    for level, expected in (("conservative", "selfpromo,interaction"),
                            ("aggressive", "selfpromo,interaction,sponsor")):
        args = args_for(captured_args, tmp_path, ad_removal=level)
        assert args[args.index("--sponsorblock-remove") + 1] == expected
        assert args.index("--sponsorblock-remove") < args.index("--")
    assert "--sponsorblock-remove" not in args_for(captured_args, tmp_path, ad_removal="off")


def test_quality_maps_to_format_and_container(tmp_path, captured_args):
    for quality, selector_part, container in (("max", "bv*+ba", "mkv"), ("720", "[height<=720]", "mp4"),
                                              ("360", "[height<=360]", "mp4")):
        args = args_for(captured_args, tmp_path, quality=quality)
        assert selector_part in args[args.index("-f") + 1]
        assert args[args.index("--merge-output-format") + 1] == container
    assert "avc1" in ytdlp.format_selector("1080")
    assert "avc1" not in ytdlp.format_selector("max")


def test_audio_language_is_preferred_with_a_fallback():
    assert "language" not in ytdlp.format_selector("1080")
    assert "language" not in ytdlp.audio_selector()
    for selector in (ytdlp.format_selector("1080", "es"), ytdlp.format_selector("max", "es"),
                     ytdlp.audio_selector("es")):
        first, *rest = selector.split("/")
        assert "[language^=es]" in first
        assert "bv*" not in first
        assert "language" not in rest[-1]


def test_chapters_are_embedded_when_chosen(tmp_path, captured_args):
    assert "--embed-chapters" in args_for(captured_args, tmp_path)
    assert "--embed-chapters" not in args_for(captured_args, tmp_path, chapters=False)


def test_subtitle_arguments(tmp_path, captured_args):
    off = args_for(captured_args, tmp_path)
    assert "--write-subs" not in off and "--write-auto-subs" not in off

    manual = args_for(captured_args, tmp_path, subtitles="manual", subtitle_lang="es")
    assert "--write-subs" in manual and "--write-auto-subs" not in manual
    assert manual[manual.index("--sub-langs") + 1] == "es(-.*)?"
    assert manual[manual.index("--convert-subs") + 1] == "srt"

    assert "--write-auto-subs" in args_for(captured_args, tmp_path, subtitles="auto")
    assert "--ignore-errors" in args_for(captured_args, tmp_path, subtitles="auto")
    assert "--ignore-errors" not in off


class FakeProcess:
    def __init__(self, lines, returncode=0):
        self.stdout = lines
        self.returncode = returncode

    def wait(self):
        return self.returncode


def test_failed_subtitles_keep_the_video_and_warn(tmp_path, monkeypatch):
    (tmp_path / "video.mp4").write_bytes(b"data")
    (tmp_path / "video.info.json").write_text('{"id": "a"}')
    lines = ["WARNING: Unable to download video subtitles for 'en': HTTP Error 429\n"]
    monkeypatch.setattr(ytdlp.subprocess, "Popen", lambda *a, **k: FakeProcess(lines))
    options_on = {**options.DEFAULTS, "subtitles": "auto"}
    result = ytdlp.download("a", tmp_path, options_on, False, None, lambda p: None)
    assert result["video"] == tmp_path / "video.mp4"
    assert result["warning"] == ytdlp.SUBTITLES_FAILED
    assert result["subtitles"] == {}


def test_no_warning_when_another_subtitle_variant_worked(tmp_path, monkeypatch):
    (tmp_path / "video.mp4").write_bytes(b"data")
    (tmp_path / "video.info.json").write_text('{"id": "a"}')
    (tmp_path / "video.en.srt").write_text("subs")
    lines = ["WARNING: Unable to download video subtitles for 'en-orig': HTTP Error 429\n"]
    monkeypatch.setattr(ytdlp.subprocess, "Popen", lambda *a, **k: FakeProcess(lines))
    options_on = {**options.DEFAULTS, "subtitles": "auto"}
    result = ytdlp.download("a", tmp_path, options_on, False, None, lambda p: None)
    assert result["warning"] is None
    assert list(result["subtitles"]) == ["en"]


def test_a_failed_video_with_subtitles_on_still_fails(tmp_path, monkeypatch):
    lines = ["ERROR: [youtube] a: Video unavailable\n"]
    monkeypatch.setattr(ytdlp.subprocess, "Popen", lambda *a, **k: FakeProcess(lines, 1))
    with pytest.raises(ytdlp.YtdlpError):
        ytdlp.download("a", tmp_path, {**options.DEFAULTS, "subtitles": "auto"}, False, None, lambda p: None)


def test_no_video_file_is_an_error_even_with_a_clean_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(ytdlp.subprocess, "Popen", lambda *a, **k: FakeProcess([]))
    with pytest.raises(ytdlp.YtdlpError):
        ytdlp.download("a", tmp_path, {**options.DEFAULTS, "subtitles": "auto"}, False, None, lambda p: None)


def test_subtitle_warning_shows_on_a_finished_job(admin, monkeypatch):
    use_media_folders()
    fake = fake_download_factory()

    def warned(*args):
        return {**fake(*args), "warning": ytdlp.SUBTITLES_FAILED}

    monkeypatch.setattr(ytdlp, "download", warned)
    with db.transaction() as conn:
        worker.enqueue(conn, "w", "W", "movie")
    worker._run(worker._next_job())
    job = admin.get("/api/queue").json()["jobs"][0]
    assert job["status"] == "done"
    assert job["error"] == ytdlp.SUBTITLES_FAILED


def test_audio_only_arguments(tmp_path, captured_args):
    args = args_for(captured_args, tmp_path, audio_only=True, subtitles="auto", ad_removal="aggressive")
    assert args[args.index("--audio-format") + 1] == "m4a"
    assert "-x" in args and "--sponsorblock-remove" in args
    for flag in ("--write-subs", "--embed-chapters", "--merge-output-format"):
        assert flag not in args


def test_picks_the_closest_subtitle(tmp_path):
    for name in ("video.en-orig.srt", "video.en.srt", "video.en-GB.srt"):
        (tmp_path / name).write_text("x")
    assert ytdlp._pick_subtitle(tmp_path, "en") == {"en": tmp_path / "video.en.srt"}
    (tmp_path / "video.en.srt").unlink()
    assert ytdlp._pick_subtitle(tmp_path, "en") == {"en": tmp_path / "video.en-GB.srt"}
    assert ytdlp._pick_subtitle(tmp_path, "fr") != {}
    for path in tmp_path.iterdir():
        path.unlink()
    assert ytdlp._pick_subtitle(tmp_path, "en") == {}


def test_settings_hide_the_plex_token(admin):
    admin.put("/api/settings", json={"plex_token": "secret", "plex_url": "http://plex:32400"})
    body = admin.get("/api/settings")
    assert "secret" not in body.text
    assert body.json()["plex_token_set"] is True
    admin.put("/api/settings", json={"plex_token": ""})
    assert db.get_settings()["plex_token"] == "secret"


def test_settings_reject_folders_outside_media(admin):
    assert admin.put("/api/settings", json={"temp_dir": str(db.CONFIG_DIR)}).status_code == 400
    assert admin.put("/api/settings", json={"music_dir": str(db.CONFIG_DIR)}).status_code == 400
    assert admin.get("/api/fs", params={"path": str(db.MEDIA_ROOT.parent)}).status_code == 400


def test_settings_reject_bad_values(admin):
    assert admin.put("/api/settings", json={"unknown": 1}).status_code == 400
    assert admin.put("/api/settings", json={"check_hours": 6}).status_code == 400
    assert admin.put("/api/settings", json={"min_free_gb": -1}).status_code == 400


def test_mkdir_rejects_paths(admin):
    root = str(db.MEDIA_ROOT)
    assert admin.post("/api/fs/mkdir", json={"path": root, "name": "../evil"}).status_code == 400
    assert admin.post("/api/fs/mkdir", json={"path": root, "name": ".."}).status_code == 400
    assert admin.post("/api/fs/mkdir", json={"path": root, "name": "good"}).status_code == 200


def test_cookie_validation(admin):
    assert admin.post("/api/cookies", json={"content": "not cookies"}).status_code == 400
    good = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tNAME\tVALUE\n"
    assert admin.post("/api/cookies", json={"content": good}).json()["present"] is True
    assert "VALUE" not in admin.get("/api/cookies").text
    assert admin.delete("/api/cookies").json()["present"] is False


def fake_download_factory(calls=None):
    def fake_download(video_id, folder, chosen, audio_only, on_progress, on_start):
        if calls is not None:
            calls.append((video_id, chosen, audio_only))
        on_progress(50, 1000, 5)
        media = folder / ("video.m4a" if audio_only else "video.mp4")
        media.write_bytes(b"data")
        subtitle = folder / "video.en.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        thumb = folder / "video.jpg"
        thumb.write_bytes(b"jpg")
        info = {"id": video_id, "title": video_id.upper(), "channel_id": "UC1", "channel": "Chan",
                "upload_date": "20240131", "description": "d"}
        return {"info": info, "video": media, "thumbnail": thumb,
                "subtitles": {} if audio_only or chosen["subtitles"] == "off" else {"en": subtitle}}

    return fake_download


def test_download_pipeline_files_a_tv_episode(admin, monkeypatch):
    media = use_media_folders()
    probe_as(monkeypatch, PLAYLIST)
    calls = []
    monkeypatch.setattr(ytdlp, "download", fake_download_factory(calls))
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv",
                                     "options": {"ad_removal": "aggressive", "subtitles": "auto"}})
    for _ in range(2):
        worker._run(worker._next_job())

    assert calls[0][1]["ad_removal"] == "aggressive"
    assert calls[0][2] is False
    assert [r["status"] for r in job_rows()] == ["done", "done"]
    season = media / "tv" / "My Playlist" / "Season 01"
    assert (season / "My Playlist - S01E01 - A.mp4").exists()
    assert (season / "My Playlist - S01E02 - B.mp4").exists()
    assert (season / "My Playlist - S01E02 - B.nfo").exists()
    assert (season / "My Playlist - S01E02 - B.en.srt").exists()
    assert (media / "tv" / "My Playlist" / "tvshow.nfo").exists()
    assert list((media / "temp").iterdir()) == []
    history = admin.get("/api/history").json()
    assert history["total"] == 2
    assert history["items"][0]["options"]["ad_removal"] == "aggressive"
    assert worker._next_job() is None


def test_audio_tracks_are_filed_in_the_music_folder(admin, monkeypatch):
    media = use_media_folders()
    probe_as(monkeypatch, PLAYLIST)
    calls = []
    written = []
    monkeypatch.setattr(ytdlp, "download", fake_download_factory(calls))
    monkeypatch.setattr(main.library.tags, "write_audio_tags", lambda *args: written.append(args))
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "audio", "episode_order": "reverse"})
    for _ in range(2):
        worker._run(worker._next_job())

    assert all(call[2] is True for call in calls)
    album = media / "music" / "My Playlist"
    assert (album / "01 - B.m4a").exists()
    assert (album / "02 - A.m4a").exists()
    assert (album / "cover.jpg").exists()
    assert not list(album.glob("*.nfo"))
    assert [args[2:5] for args in written] == [("My Playlist", "C", 1), ("My Playlist", "C", 2)]
    assert [r["status"] for r in job_rows()] == ["done", "done"]


def test_a_second_album_gets_its_own_copy_not_a_hardlink(admin, monkeypatch):
    media = use_media_folders()
    probe_as(monkeypatch, {**PLAYLIST, "entries": PLAYLIST["entries"][:1]})
    calls = []
    monkeypatch.setattr(ytdlp, "download", fake_download_factory(calls))
    monkeypatch.setattr(main.library.tags, "write_audio_tags", lambda *args: None)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "audio"})
    worker._run(worker._next_job())
    probe_as(monkeypatch, {**PLAYLIST, "playlist_id": "PL2", "title": "Other", "entries": PLAYLIST["entries"][:1]})
    admin.post("/api/sources", json={"url": "https://www.youtube.com/playlist?list=PL2", "mode": "audio"})
    worker._run(worker._next_job())

    assert len(calls) == 1
    assert (media / "music" / "My Playlist" / "01 - A.m4a").read_bytes() == b"data"
    assert (media / "music" / "Other" / "01 - A.m4a").read_bytes() == b"data"


def test_paused_only_when_the_next_job_needs_a_missing_folder(monkeypatch):
    settings = db.get_settings()
    job = {"mode": "audio"}
    assert worker._pause_reason(settings, None) == ""
    assert "Choose your folders" in worker._pause_reason(settings, job)
    media = use_media_folders()
    settings = db.get_settings()
    assert worker._pause_reason(settings, job) == ""
    settings["music_dir"] = ""
    assert "Choose your folders" in worker._pause_reason(settings, job)
    assert worker._pause_reason(settings, {"mode": "tv"}) == ""
    assert media.exists()


def test_failed_download_retries_then_fails(admin, monkeypatch):
    use_media_folders()

    def broken(*args):
        raise ytdlp.YtdlpError("network")

    monkeypatch.setattr(ytdlp, "download", broken)
    with db.transaction() as conn:
        worker.enqueue(conn, "a", "A", "movie")
    for expected in ("queued", "queued", "failed"):
        with db.connect() as conn:
            job = conn.execute("SELECT * FROM jobs").fetchone()
        worker._run(job)
        assert job_rows()[0]["status"] == expected


def test_unavailable_video_fails_without_retry(admin, monkeypatch):
    use_media_folders()

    def gone(*args):
        raise ytdlp.YtdlpError("Private video", permanent=True)

    monkeypatch.setattr(ytdlp, "download", gone)
    with db.transaction() as conn:
        worker.enqueue(conn, "a", "A", "movie")
    worker._run(worker._next_job())
    assert job_rows()[0]["status"] == "failed"

    with db.transaction() as conn:
        assert worker.enqueue(conn, "a", "A", "movie") is False
        assert worker.enqueue(conn, "a", "A", "movie", force=True) is True


def test_error_text_drops_yt_dlp_help():
    output = "ERROR: [youtube] abc-1_x: Private video. If the owner has granted you access, sign in."
    assert ytdlp._error_text(output) == "Private video."
    assert "cookies in Settings" in ytdlp._error_text("ERROR: [youtube] a: Sign in to confirm you're not a bot.")
    down = "ERROR: Preprocessing: Unable to communicate with SponsorBlock API: HTTPConnection(host='x')"
    assert ytdlp._error_text(down).startswith("Couldn't reach SponsorBlock")


def test_frontend_routes(client, monkeypatch, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>DTube</title>")
    (tmp_path / "assets" / "app.js").write_text("js")
    (tmp_path.parent / "secret.txt").write_text("secret")
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    assert "DTube" in client.get("/").text
    assert client.get("/assets/app.js").text == "js"
    assert "DTube" in client.get("/settings").text
    assert "secret" not in client.get("/%2e%2e/secret.txt").text
    assert client.get("/api/missing").status_code == 404


def test_live_queue_needs_session_and_same_origin(admin):
    good = {"origin": "http://testserver"}
    with admin.websocket_connect("/api/ws", headers=good) as socket:
        assert "jobs" in socket.receive_json()
    for headers in ({"origin": "http://evil.example"}, {}):
        with pytest.raises(Exception):
            with admin.websocket_connect("/api/ws", headers=headers) as socket:
                socket.receive_json()
    admin.cookies.clear()
    with pytest.raises(Exception):
        with admin.websocket_connect("/api/ws", headers=good) as socket:
            socket.receive_json()


def test_scan_marks_missing_files(admin):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO placements (video_id, mode, show_id, path, created_at) VALUES ('a', 'movie', 0, 'gone.mp4', 0)"
        )
    assert admin.post("/api/history/scan").json()["changed"] == 1
    assert admin.get("/api/history").json()["items"][0]["missing"] == 1


def test_probe_handles_a_channel_page_and_nested_tabs(monkeypatch):
    nested = {"_type": "playlist", "id": "UC1", "title": "Chan",
              "entries": [{"_type": "playlist", "id": "UC1", "title": "Chan - Videos"}]}

    class Result:
        returncode = 0
        stdout = json.dumps(nested)
        stderr = ""

    monkeypatch.setattr(ytdlp.subprocess, "run", lambda *a, **k: Result)
    with pytest.raises(ytdlp.YtdlpError):
        ytdlp.probe("https://www.youtube.com/@chan")


def test_progress_parsing_handles_missing_values():
    assert ytdlp._parse_progress(("50", "100", "NA", "10", "3")) == (50.0, 10.0, 3)
    assert ytdlp._parse_progress(("NA", "NA", "NA", "NA", "NA")) == (0, None, None)
    assert ytdlp.PROGRESS.match("DTUBE|1|2|NA|4|5")


def old_schema(conn, mode_check=False):
    check = " CHECK (mode IN ('tv', 'movie'))" if mode_check else ""
    conn.execute("DROP TABLE sources")
    conn.execute(
        f"CREATE TABLE sources (id INTEGER PRIMARY KEY, url TEXT NOT NULL, playlist_id TEXT NOT NULL, "
        f"title TEXT NOT NULL, mode TEXT NOT NULL{check}, show_id INTEGER NOT NULL DEFAULT 0, "
        f"subscribed INTEGER NOT NULL DEFAULT 0, last_checked REAL, ad_removal TEXT NOT NULL DEFAULT 'off', "
        f"quality TEXT NOT NULL DEFAULT '1080', UNIQUE (playlist_id, mode))"
    )
    conn.execute(
        "INSERT INTO sources (url, playlist_id, title, mode, subscribed, ad_removal, quality) "
        "VALUES ('u', 'PL1', 'Checked', 'tv', 1, 'aggressive', '720'), ('u', 'PL2', 'Manual', 'movie', 0, 'off', 'max')"
    )


@pytest.mark.parametrize("mode_check", [False, True])
def test_old_database_is_upgraded(mode_check):
    with db.connect() as conn:
        old_schema(conn, mode_check)
    db.init()
    db.init()
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        conn.execute("INSERT INTO sources (url, playlist_id, title, mode) VALUES ('u', 'PL3', 'A', 'audio')")
    assert [r["check_frequency"] for r in rows] == ["daily", "off"]
    assert [r["episode_order"] for r in rows] == ["playlist", "playlist"]
    first, second = (options.parse(r["options"]) for r in rows)
    assert (first["ad_removal"], first["quality"]) == ("aggressive", "720")
    assert second["quality"] == "max"
    assert first["subtitles"] == "off"


def test_old_default_settings_are_carried_over():
    db.set_settings({"default_quality": "max", "default_ad_removal": "conservative"})
    db.init()
    defaults = db.get_settings()["default_options"]
    assert (defaults["quality"], defaults["ad_removal"]) == ("max", "conservative")
    assert defaults["chapters"] is True


def test_retired_settings_do_not_come_back():
    db.set_settings({"check_hours": 6})
    assert "check_hours" not in db.get_settings()


def test_stop_tracking_drops_waiting_videos_only(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done' WHERE video_id = 'a'")
    admin.delete(f"/api/sources/{source_id}")
    assert admin.get("/api/sources").json() == []
    assert [(r["video_id"], r["status"]) for r in job_rows()] == [("a", "done")]
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"}).json()["added"] == 2


def download_playlist(admin, monkeypatch, mode):
    probe_as(monkeypatch, PLAYLIST)
    monkeypatch.setattr(ytdlp, "download", fake_download_factory())
    monkeypatch.setattr(main.library.tags, "write_audio_tags", lambda *args: None)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": mode})
    for _ in range(2):
        worker._run(worker._next_job())
    return admin.get("/api/sources").json()[0]["id"]


def media_files():
    return sorted(p for p in db.MEDIA_ROOT.rglob("*") if p.is_file())


def clear_media():
    for path in media_files():
        path.unlink()


@pytest.mark.parametrize("mode", ["tv", "audio", "movie"])
def test_keeping_history_means_no_redownload(admin, monkeypatch, mode):
    clear_media()
    use_media_folders()
    source_id = download_playlist(admin, monkeypatch, mode)
    result = admin.delete(f"/api/sources/{source_id}").json()
    assert result["forgotten"] == 0
    assert admin.get("/api/history").json()["total"] == 2
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": mode}).json()["added"] == 0


@pytest.mark.parametrize("mode", ["tv", "audio", "movie"])
def test_forgetting_clears_the_database_but_keeps_files(admin, monkeypatch, mode):
    clear_media()
    use_media_folders()
    source_id = download_playlist(admin, monkeypatch, mode)
    before = media_files()
    assert before

    assert admin.delete(f"/api/sources/{source_id}", params={"forget": True}).json()["forgotten"] == 2
    assert media_files() == before
    assert admin.get("/api/history").json()["total"] == 0
    assert admin.get("/api/sources").json() == []
    assert job_rows() == []
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM shows").fetchone()[0] == 0

    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": mode}).json()["added"] == 2


def test_forgetting_restarts_numbering(admin, monkeypatch):
    clear_media()
    media = use_media_folders()
    source_id = download_playlist(admin, monkeypatch, "tv")
    admin.delete(f"/api/sources/{source_id}", params={"forget": True})
    clear_media()
    download_playlist(admin, monkeypatch, "tv")
    assert (media / "tv" / "My Playlist" / "Season 01" / "My Playlist - S01E01 - A.mp4").exists()
    assert (media / "tv" / "My Playlist" / "Season 01" / "My Playlist - S01E02 - B.mp4").exists()


def test_forgetting_leaves_other_playlists_and_movies_alone(admin, monkeypatch):
    clear_media()
    use_media_folders()
    download_playlist(admin, monkeypatch, "tv")
    other_url = "https://www.youtube.com/playlist?list=PL2"
    entries = [{"video_id": "c", "title": "C"}]
    probe_as(monkeypatch, {**PLAYLIST, "playlist_id": "PL2", "title": "Other", "entries": entries})
    admin.post("/api/sources", json={"url": other_url, "mode": "movie"})
    worker._run(worker._next_job())
    probe_as(monkeypatch, VIDEO)
    admin.post("/api/sources", json={"url": "https://youtu.be/v"})
    worker._run(worker._next_job())

    movie_source = next(s for s in admin.get("/api/sources").json() if s["mode"] == "movie")
    assert admin.delete(f"/api/sources/{movie_source['id']}", params={"forget": True}).json()["forgotten"] == 1
    titles = {item["video_id"] for item in admin.get("/api/history").json()["items"]}
    assert titles == {"a", "b", "v"}


def test_forgetting_also_clears_unavailable_videos(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    with db.connect() as conn:
        conn.execute("INSERT INTO unavailable VALUES ('a')")
    admin.delete(f"/api/sources/{source_id}", params={"forget": True})
    assert admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"}).json()["added"] == 2


def test_cannot_forget_while_a_video_is_downloading(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'downloading' WHERE video_id = 'a'")
    assert admin.delete(f"/api/sources/{source_id}", params={"forget": True}).status_code == 409
    assert len(admin.get("/api/sources").json()) == 1
    assert len(job_rows()) == 2


def test_older_history_learns_its_playlist():
    with db.connect() as conn:
        conn.execute("ALTER TABLE placements RENAME TO placements_new")
        conn.execute(
            "CREATE TABLE placements (id INTEGER PRIMARY KEY, video_id TEXT NOT NULL, mode TEXT NOT NULL, "
            "show_id INTEGER NOT NULL DEFAULT 0, season INTEGER, episode INTEGER, path TEXT NOT NULL, "
            "missing INTEGER NOT NULL DEFAULT 0, options TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL)"
        )
        conn.execute("DROP TABLE placements_new")
        conn.execute("INSERT INTO placements (video_id, mode, path, created_at) VALUES ('a', 'movie', 'x', 0)")
        conn.execute("INSERT INTO placements (video_id, mode, path, created_at) VALUES ('b', 'movie', 'y', 0)")
        conn.execute(
            "INSERT INTO jobs (video_id, title, mode, source_id, status, created_at, updated_at) "
            "VALUES ('a', 'A', 'movie', 9, 'done', 0, 0)"
        )
    db.init()
    with db.connect() as conn:
        rows = conn.execute("SELECT video_id, source_id FROM placements ORDER BY video_id").fetchall()
    assert [tuple(r) for r in rows] == [("a", 9), ("b", None)]


def add_history(video_id, mode, path, show_id=0, created_at=None, channel="Chan", duration=100, ad_removal="off"):
    stored = options.dump({**options.DEFAULTS, "ad_removal": ad_removal})
    with db.connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO videos (video_id, title, channel, duration) VALUES (?, ?, ?, ?)",
            (video_id, video_id.upper(), channel, duration),
        )
        conn.execute(
            "INSERT INTO placements (video_id, mode, show_id, path, created_at, options) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, mode, show_id, str(path), time.time() if created_at is None else created_at, stored),
        )


def test_stats_need_a_login(client):
    assert client.get("/api/stats").status_code == 401


def test_stats_on_an_empty_library(admin):
    body = admin.get("/api/stats").json()
    assert body["videos"]["total"] == 0
    assert body["storage"]["total_bytes"] == 0
    assert body["top_channels"] == [] and body["recent"] == [] and body["activity"] == []


def test_stats_count_the_library(admin, tmp_path):
    episode, movie = tmp_path / "e.mp4", tmp_path / "m.mp4"
    episode.write_bytes(b"x" * 100)
    movie.write_bytes(b"x" * 50)
    add_history("a", "tv", episode, show_id=1, ad_removal="aggressive")
    add_history("b", "movie", movie, channel="Other", duration=None)
    add_history("c", "audio", tmp_path / "gone.m4a", show_id=2, created_at=time.time() - 40 * 86400)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO sources (url, playlist_id, title, mode, check_frequency) VALUES ('u', 'P1', 't', 'tv', 'daily'), "
            "('u', 'P2', 't', 'movie', 'off')"
        )
        for status in ("queued", "queued", "downloading", "failed"):
            conn.execute(
                "INSERT INTO jobs (video_id, title, mode, status, created_at, updated_at) "
                "VALUES ('z', 'Z', 'tv', ?, 0, 0)", (status,),
            )

    body = admin.get("/api/stats").json()
    assert body["playlists"] == {"total": 2, "tv": 1, "movie": 1, "audio": 0, "auto_checked": 1}
    videos = body["videos"]
    assert (videos["total"], videos["tv"], videos["movie"], videos["audio"]) == (3, 1, 1, 1)
    assert videos["ads_removed"] == 1 and videos["channels"] == 2
    storage = body["storage"]
    assert (storage["total_bytes"], storage["tv_bytes"], storage["movie_bytes"]) == (150, 100, 50)
    assert storage["missing"] == 1
    assert body["runtime"] == {"seconds": 200, "known": 2, "videos": 3}
    assert body["queue"] == {"queued": 2, "active": 1, "failed": 1}
    assert body["top_channels"][0] == {"channel": "Chan", "count": 2}
    assert len(body["activity"]) == 2
    assert len(body["recent"]) == 3


def test_stats_count_a_hardlinked_file_once(admin, tmp_path):
    first, second = tmp_path / "1.mp4", tmp_path / "2.mp4"
    first.write_bytes(b"x" * 100)
    os.link(first, second)
    add_history("a", "tv", first, show_id=1)
    add_history("a", "tv", second, show_id=2)
    body = admin.get("/api/stats").json()
    assert body["storage"]["total_bytes"] == 100
    assert body["videos"]["total"] == 2
    assert body["videos"]["unique"] == 1
    assert body["runtime"]["seconds"] == 100


def test_download_saves_the_video_length(admin, monkeypatch):
    use_media_folders()
    fake = fake_download_factory()

    def timed(*args):
        result = fake(*args)
        result["info"]["duration"] = 321
        return result

    monkeypatch.setattr(ytdlp, "download", timed)
    with db.transaction() as conn:
        worker.enqueue(conn, "t", "T", "movie")
    worker._run(worker._next_job())
    assert admin.get("/api/stats").json()["runtime"]["seconds"] == 321


def test_scheduled_checks_leave_cancelled_videos_alone(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled'")
    assert worker.refresh_source(source_id, scheduled=True) == 0
    assert worker.refresh_source(source_id) == 2


def test_cancelled_video_can_be_added_again_by_hand(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    admin.post("/api/sources", json={"url": "https://youtu.be/v"})
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled'")
    assert admin.post("/api/sources", json={"url": "https://youtu.be/v"}).json()["added"] == 1


def test_sign_in_with_plex_saves_the_token(admin, monkeypatch):
    seen = {}

    def fake_create(client_id):
        seen["client"] = client_id
        return 42, "pin-code"

    results = iter([None, "new-token"])

    def fake_check(client_id, pin_id, code):
        assert (client_id, pin_id, code) == (seen["client"], 42, "pin-code")
        return next(results)

    monkeypatch.setattr(main.plex, "create_pin", fake_create)
    monkeypatch.setattr(main.plex, "check_pin", fake_check)

    started = admin.post("/api/plex/pin").json()
    assert started["id"] == 42
    assert started["auth_url"].startswith("https://app.plex.tv/auth#?")
    assert "pin-code" in started["auth_url"]
    assert "context%5Bdevice%5D%5Bproduct%5D=DTube" in started["auth_url"]

    assert admin.get("/api/plex/pin/42").json() == {"signed_in": False, "expired": False}
    assert admin.get("/api/plex/pin/42").json() == {"signed_in": True, "expired": False}
    assert db.get_settings()["plex_token"] == "new-token"
    assert admin.get("/api/settings").json()["plex_token_set"] is True
    assert admin.get("/api/plex/pin/42").status_code == 404

    admin.post("/api/plex/pin")
    assert db.get_settings()["plex_client_id"] == seen["client"]


def test_plex_sign_in_handles_expiry_and_network_errors(admin, monkeypatch):
    monkeypatch.setattr(main.plex, "create_pin", lambda client_id: (7, "c"))

    def network_error(*args):
        raise OSError("down")

    def expired(*args):
        raise main.plex.PinExpired

    admin.post("/api/plex/pin")
    monkeypatch.setattr(main.plex, "check_pin", network_error)
    assert admin.get("/api/plex/pin/7").json() == {"signed_in": False, "expired": False}
    monkeypatch.setattr(main.plex, "check_pin", expired)
    assert admin.get("/api/plex/pin/7").json() == {"signed_in": False, "expired": True}
    assert admin.get("/api/plex/pin/7").status_code == 404


def test_plex_sign_in_requires_login(client):
    assert client.post("/api/plex/pin").status_code == 401
    assert client.get("/api/plex/pin/1").status_code == 401


def test_plex_client_id_stays_private_and_read_only(admin):
    db.set_settings({"plex_client_id": "client-secret"})
    assert "client-secret" not in admin.get("/api/settings").text
    assert admin.put("/api/settings", json={"plex_client_id": "x"}).status_code == 400


def test_each_playlist_has_its_own_check_frequency(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    body = {"url": PLAYLIST_URL, "mode": "tv", "check_frequency": "hourly"}
    admin.post("/api/sources", json=body)
    source = admin.get("/api/sources").json()[0]
    assert source["check_frequency"] == "hourly"

    admin.post("/api/sources", json={"url": PLAYLIST_URL, "mode": "tv"})
    assert admin.get("/api/sources").json()[0]["check_frequency"] == "hourly"

    admin.patch(f"/api/sources/{source['id']}", json={"check_frequency": "daily"})
    assert admin.get("/api/sources").json()[0]["check_frequency"] == "daily"
    response = admin.patch(f"/api/sources/{source['id']}", json={"check_frequency": "weekly"})
    assert response.status_code == 400


def test_due_sources_follow_each_frequency():
    now = 1_000_000
    rows = [
        ("off", now - 999_999), ("immediate", now - 299), ("immediate", now - 300),
        ("hourly", now - 3599), ("hourly", now - 3600),
        ("daily", now - 86399), ("daily", now - 86400), ("daily", None),
    ]
    with db.connect() as conn:
        for index, (frequency, checked) in enumerate(rows, 1):
            conn.execute(
                "INSERT INTO sources (id, url, playlist_id, title, mode, check_frequency, last_checked) "
                "VALUES (?, 'u', ?, 't', 'tv', ?, ?)",
                (index, f"PL{index}", frequency, checked),
            )
    assert worker.due_sources(now) == [3, 5, 7, 8]
