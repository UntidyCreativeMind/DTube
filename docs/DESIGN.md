# DTube design

## Principles

- Paste a URL and it works.
- Everything is configured in the UI. Compose holds only what Docker requires, and there is no `.env`.
- One container, one admin login.

## Stack

- **Backend:** Python and FastAPI. yt-dlp runs as a subprocess, so a UI update applies without a restart and a yt-dlp crash can't stop the app.
- **Database:** SQLite in `/config`.
- **Frontend:** React, TypeScript, Vite and Tailwind, served by the backend.
- **Image:** includes ffmpeg and Deno, because yt-dlp needs a JavaScript runtime for full YouTube support.

## Workflow

1. A URL is submitted. A playlist URL requires a mode (TV or Movie). A single video URL is always Movie mode.
2. Each video not already in the history is queued. One video downloads at a time, which keeps episode numbers in playlist order.
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

A title that would collide with another video's folder gets the video ID added.
```

## New video checks

Each playlist has its own check frequency: off, immediate, hourly or daily. YouTube has no notification for playlist changes, so immediate means checking every 5 minutes. A failed check waits for its next turn.

## Ad removal

YouTube's own ad breaks are never in a download. Each download picks a level for the segments creators build into videos, which yt-dlp cuts using SponsorBlock:

| Level | SponsorBlock categories |
|---|---|
| Off | None |
| Conservative | `selfpromo`, `interaction` |
| Aggressive | `selfpromo`, `interaction`, `sponsor` |

- A playlist remembers its level for later checks. Settings holds the default.
- Changing a level affects new downloads. Re-downloading from History applies a level to an existing video.
- Files are only hardlinked between shows when their levels match.
- Segments are community submitted, so very new videos may have none marked yet.

## Download history

- History is keyed by video ID, so a video is never downloaded twice unless asked.
- A video that appears in a second playlist gets a hardlink, with a copy as the fallback.
- Re-download is an explicit action.
- A scan marks videos whose files were removed outside DTube.

## Plex

Separate libraries with the Plex NFO Series and Plex NFO Movie agents avoid false matches. `uniqueid` holds the YouTube ID, which keeps watch history stable across rescans. The UI takes the server address and token, tests the connection and lists libraries. A path mapping setting covers Plex seeing different paths than DTube.

## Settings held in the UI

Temp, TV and movie folders, Plex connection and libraries, path mapping, file owner, cookies, video quality, default ad removal, disk space limit and yt-dlp updates.

## Image

GitHub Actions runs the tests, builds the image, starts it and checks it responds, then publishes it to `ghcr.io/untidycreativemind/dtube`. The image is published only if those steps pass.

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

Subtitles, notifications, cleanup rules, and an optional PO token helper.
