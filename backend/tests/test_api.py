import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dtube import auth, db, main, worker, ytdlp

PLAYLIST = {
    "type": "playlist", "playlist_id": "PL1", "title": "My Playlist", "channel": "C",
    "description": "", "thumbnail": "", "count": 2,
    "entries": [{"video_id": "a", "title": "A"}, {"video_id": "b", "title": "B"}],
}
VIDEO = {"type": "video", "video_id": "v", "title": "Solo", "channel": "C", "thumbnail": "", "count": 1, "entries": []}
PASSWORD = "correct-horse-battery"


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


def probe_as(monkeypatch, info):
    monkeypatch.setattr(ytdlp, "probe", lambda url: dict(info))


def job_rows():
    with db.connect() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()


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


def test_single_video_is_always_a_movie(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    url = "https://youtu.be/v"
    assert admin.post("/api/sources", json={"url": url, "mode": "tv"}).json()["added"] == 1
    assert job_rows()[0]["mode"] == "movie"


def test_playlist_in_tv_mode_creates_show_and_queues_in_order(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    url = "https://www.youtube.com/playlist?list=PL1"
    assert admin.post("/api/sources", json={"url": url, "mode": "tv"}).json()["added"] == 2
    rows = job_rows()
    assert [r["video_id"] for r in rows] == ["a", "b"]
    assert all(r["mode"] == "tv" and r["show_id"] > 0 for r in rows)


def test_playlist_in_movie_mode_goes_to_movie_library(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    url = "https://www.youtube.com/playlist?list=PL1"
    admin.post("/api/sources", json={"url": url, "mode": "movie"})
    assert all(r["mode"] == "movie" and r["show_id"] == 0 for r in job_rows())


def test_adding_twice_does_not_duplicate_or_redownload(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    url = "https://www.youtube.com/playlist?list=PL1"
    admin.post("/api/sources", json={"url": url, "mode": "tv"})
    assert admin.post("/api/sources", json={"url": url, "mode": "tv"}).json()["added"] == 0
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done'")
        for video_id in ("a", "b"):
            conn.execute(
                "INSERT INTO placements (video_id, mode, show_id, path, created_at) VALUES (?, 'tv', 1, 'x', 0)",
                (video_id,),
            )
    assert admin.post("/api/sources", json={"url": url, "mode": "tv"}).json()["added"] == 0


def test_redownload_is_explicit(admin):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO placements (id, video_id, mode, show_id, path, created_at) VALUES (1, 'a', 'movie', 0, 'x', 0)"
        )
    assert admin.post("/api/history/1/redownload").json()["queued"] is True
    row = job_rows()[0]
    assert row["force"] == 1


def test_ad_removal_defaults_to_the_setting(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    admin.put("/api/settings", json={"default_ad_removal": "conservative"})
    admin.post("/api/sources", json={"url": "https://youtu.be/v"})
    assert job_rows()[0]["ad_removal"] == "conservative"


def test_ad_removal_rejects_unknown_levels(admin, monkeypatch):
    probe_as(monkeypatch, VIDEO)
    body = {"url": "https://youtu.be/v", "ad_removal": "extreme"}
    assert admin.post("/api/sources", json=body).status_code == 400
    assert admin.put("/api/settings", json={"default_ad_removal": "extreme"}).status_code == 400
    assert admin.put("/api/settings", json={"default_ad_removal": ""}).status_code == 400


def test_playlist_level_applies_to_later_checks(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    url = "https://www.youtube.com/playlist?list=PL1"
    admin.post("/api/sources", json={"url": url, "mode": "tv"})
    source_id = admin.get("/api/sources").json()[0]["id"]
    admin.patch(f"/api/sources/{source_id}", json={"ad_removal": "aggressive"})
    source = admin.get("/api/sources").json()[0]
    assert source["ad_removal"] == "aggressive"
    assert source["check_frequency"] == "off"
    admin.delete("/api/jobs/finished")
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'cancelled'")
    admin.post(f"/api/sources/{source_id}/check")
    assert [r["ad_removal"] for r in job_rows()][-2:] == ["aggressive", "aggressive"]


def test_redownload_keeps_or_changes_the_level(admin):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO placements (id, video_id, mode, show_id, path, created_at, ad_removal) "
            "VALUES (1, 'a', 'movie', 0, 'x', 0, 'conservative')"
        )
    admin.post("/api/history/1/redownload")
    assert job_rows()[0]["ad_removal"] == "conservative"
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET status = 'done'")
    admin.post("/api/history/1/redownload", json={"ad_removal": "aggressive"})
    assert job_rows()[1]["ad_removal"] == "aggressive"


def test_ad_levels_map_to_sponsorblock_categories(tmp_path, monkeypatch):
    captured = {}

    class Stop(Exception):
        pass

    def fake_popen(args, **kwargs):
        captured["args"] = args
        raise Stop

    monkeypatch.setattr(ytdlp.subprocess, "Popen", fake_popen)
    for level, expected in (("conservative", "selfpromo,interaction"),
                            ("aggressive", "selfpromo,interaction,sponsor")):
        with pytest.raises(Stop):
            ytdlp.download("a", tmp_path, 1080, level, None, None)
        args = captured["args"]
        assert args[args.index("--sponsorblock-remove") + 1] == expected
        assert args.index("--sponsorblock-remove") < args.index("--")
    with pytest.raises(Stop):
        ytdlp.download("a", tmp_path, 1080, "off", None, None)
    assert "--sponsorblock-remove" not in captured["args"]


def test_old_database_is_upgraded():
    with db.connect() as conn:
        conn.execute("DROP TABLE sources")
        conn.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY, title TEXT, subscribed INTEGER)")
        conn.execute("INSERT INTO sources (title, subscribed) VALUES ('checked', 1), ('manual', 0)")
    db.init()
    db.init()
    with db.connect() as conn:
        rows = conn.execute("SELECT ad_removal, check_frequency FROM sources ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("off", "daily"), ("off", "off")]


def test_retired_settings_do_not_come_back():
    db.set_settings({"check_hours": 6})
    assert "check_hours" not in db.get_settings()


def test_each_playlist_has_its_own_check_frequency(admin, monkeypatch):
    probe_as(monkeypatch, PLAYLIST)
    url = "https://www.youtube.com/playlist?list=PL1"
    body = {"url": url, "mode": "tv", "check_frequency": "hourly"}
    admin.post("/api/sources", json=body)
    source = admin.get("/api/sources").json()[0]
    assert source["check_frequency"] == "hourly"

    admin.post("/api/sources", json={"url": url, "mode": "tv"})
    assert admin.get("/api/sources").json()[0]["check_frequency"] == "hourly"

    admin.patch(f"/api/sources/{source['id']}", json={"check_frequency": "daily"})
    assert admin.get("/api/sources").json()[0]["check_frequency"] == "daily"
    response = admin.patch(f"/api/sources/{source['id']}", json={"check_frequency": "weekly"})
    assert response.status_code == 400
    assert admin.post("/api/sources", json={**body, "check_frequency": "weekly"}).status_code == 400


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


def test_settings_hide_the_plex_token(admin):
    admin.put("/api/settings", json={"plex_token": "secret", "plex_url": "http://plex:32400"})
    body = admin.get("/api/settings")
    assert "secret" not in body.text
    assert body.json()["plex_token_set"] is True
    admin.put("/api/settings", json={"plex_token": ""})
    assert db.get_settings()["plex_token"] == "secret"


def test_settings_reject_folders_outside_media(admin):
    assert admin.put("/api/settings", json={"temp_dir": str(db.CONFIG_DIR)}).status_code == 400
    assert admin.get("/api/fs", params={"path": str(db.MEDIA_ROOT.parent)}).status_code == 400


def test_settings_reject_bad_values(admin):
    assert admin.put("/api/settings", json={"unknown": 1}).status_code == 400
    assert admin.put("/api/settings", json={"check_hours": 6}).status_code == 400
    assert admin.put("/api/settings", json={"min_free_gb": -1}).status_code == 400
    assert admin.put("/api/settings", json={"quality": "best"}).status_code == 400


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


def test_download_pipeline_files_a_tv_episode(admin, monkeypatch, tmp_path):
    media = db.MEDIA_ROOT
    for name in ("temp", "tv", "movies"):
        (media / name).mkdir(exist_ok=True)
    db.set_settings({"temp_dir": str(media / "temp"), "tv_dir": str(media / "tv"),
                     "movie_dir": str(media / "movies"), "min_free_gb": 0})
    probe_as(monkeypatch, PLAYLIST)

    def fake_download(video_id, folder, quality, ad_removal, on_progress, on_start):
        assert ad_removal == "aggressive"
        on_progress(50, 1000, 5)
        video = folder / "video.mp4"
        video.write_bytes(b"data")
        info = {"id": video_id, "title": video_id.upper(), "channel_id": "UC1", "channel": "Chan",
                "upload_date": "20240131", "description": "d"}
        return {"info": info, "video": video, "thumbnail": None}

    monkeypatch.setattr(ytdlp, "download", fake_download)
    admin.post("/api/sources", json={"url": "https://www.youtube.com/playlist?list=PL1", "mode": "tv",
                                     "ad_removal": "aggressive"})
    for _ in range(2):
        worker._run(worker._next_job())

    assert [r["status"] for r in job_rows()] == ["done", "done"]
    season = media / "tv" / "My Playlist" / "Season 01"
    assert (season / "My Playlist - S01E01 - A.mp4").exists()
    assert (season / "My Playlist - S01E02 - B.mp4").exists()
    assert (season / "My Playlist - S01E02 - B.nfo").exists()
    assert (media / "tv" / "My Playlist" / "tvshow.nfo").exists()
    assert list((media / "temp").iterdir()) == []
    assert admin.get("/api/history").json()["total"] == 2
    assert worker._next_job() is None


def test_failed_download_retries_then_fails(admin, monkeypatch):
    db.set_settings({"temp_dir": str(db.MEDIA_ROOT), "tv_dir": str(db.MEDIA_ROOT), "movie_dir": str(db.MEDIA_ROOT)})

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
    db.set_settings({"temp_dir": str(db.MEDIA_ROOT), "tv_dir": str(db.MEDIA_ROOT), "movie_dir": str(db.MEDIA_ROOT)})

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
