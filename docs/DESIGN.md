# DTube design

## Principles

- Paste a URL and it works.
- Everything is configured in the UI. Compose holds only what Docker requires, and there is no `.env`.
- One container, one admin login.

## Stack

- **Backend:** Python and FastAPI, using yt-dlp as a library.
- **Database:** SQLite in `/config`.
- **Frontend:** React, TypeScript, Vite and Tailwind, served by the backend.
- **Image:** includes ffmpeg and Deno, because yt-dlp needs a JavaScript runtime for full YouTube support.

## Workflow

1. A URL is submitted. A playlist URL requires a mode (TV or Movie). A single video URL is always Movie mode.
2. Each video not already in the history is queued.
3. The video downloads to the temp folder.
4. It is moved into the library folder with its NFO and artwork.
5. The history is updated and Plex is asked to refresh.

## TV mode

| YouTube | Plex | Identity |
|---|---|---|
| Playlist | Show | Playlist ID |
| Channel | Season | Channel ID, per show |
| Video | Episode | Video ID |

- Season numbers are assigned in the order channels are first seen in a show.
- Episode numbers count up within a season and are never reused.
- A first import follows playlist order. New videos are added at the end.
- Renaming a playlist or reordering it changes nothing on disk.

```text
<TV folder>/My Playlist/
├── tvshow.nfo
├── poster.jpg
└── Season 01/
    ├── My Playlist - S01E01 - Video Title.mp4
    ├── My Playlist - S01E01 - Video Title.nfo
    └── My Playlist - S01E01 - Video Title.jpg
```

`tvshow.nfo` carries `namedseason` entries so seasons can show the channel name. This needs checking on a real Plex server. The fallback is plain "Season 01" naming, which needs no change.

## Movie mode

```text
<Movie folder>/Video Title/
├── Video Title.mp4
├── Video Title.jpg
└── movie.nfo
```

## Download history

- History is keyed by video ID, so a video is never downloaded twice unless asked.
- A video that appears in a second playlist gets a hardlink, with a copy as the fallback.
- Re-download is an explicit action.
- A scan marks videos whose files were removed outside DTube.

## Plex

Separate libraries with the Plex NFO Series and Plex NFO Movie agents avoid false matches. `uniqueid` holds the YouTube ID, which keeps watch history stable across rescans. The UI takes the server address and token, tests the connection and lists libraries. A path mapping setting covers Plex seeing different paths than DTube.

## Settings held in the UI

Temp, TV and movie folders, Plex connection and libraries, path mapping, file owner, cookies, video quality, schedule, disk space limit and yt-dlp updates.

## Defaults

- 1080p H.264/AAC in MP4, for direct play on most Plex clients.
- Request throttling is on.
- Downloads pause when free space falls below the limit.

## Security

- The first run creates the admin login. There is no default password.
- Passwords are hashed with Argon2, sessions use HttpOnly cookies and login attempts are rate limited.
- HTTPS comes from the reverse proxy. Client addresses are read from proxy headers.
- Generated secrets and cookies are stored in `/config`.

## Later

SponsorBlock, subtitles, notifications, cleanup rules, and an optional PO token helper.
