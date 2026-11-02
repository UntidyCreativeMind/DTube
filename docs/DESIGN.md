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

1. A URL is submitted with a mode. A playlist can be TV, Movie or Audio. A single video can be Movie or Audio.
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
- Each playlist has an episode order: playlist order, or reversed. New videos are added at the end either way.
- Playlist listings have no upload dates, and fetching them one video at a time is too slow for large playlists. Reversing gives oldest-first on playlists that list the newest video first, without any extra requests.
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

`tvshow.nfo` carries `namedseason` entries so seasons show the channel name. Plex needs Use Season Titles turned on in the library's Advanced settings.

## Movie mode

```text
<Movie folder>/Video Title/
├── Video Title.mp4
├── Video Title.jpg
└── movie.nfo

A title that would collide with another video's folder gets the video ID added.
```

## Audio mode

```text
<Audio folder>/My Playlist/
├── cover.jpg
├── 01 - Track Title.m4a
└── 02 - Track Title.m4a
```

- Audio has its own folder and its own numbering, so one playlist can be both a show and an album.
- Files are M4A, with the title, artist (channel), album (playlist), album artist (playlist channel), track number, year and cover stored as tags.
- Single videos go in an album called Singles, with Various Artists as the album artist.
- Tags live in the file, so an album never hardlinks a track from another album.

## Download options

Each download carries a set of options, stored as JSON on the playlist, the job and the history entry. Settings holds the defaults. A playlist's options apply to its later checks, and History can re-download a video with different ones.

| Option | Choices |
|---|---|
| Quality | Max, 1080p, 720p, 480p, 360p |
| Ad removal | Off, Conservative, Aggressive |
| Audio language | Original, or a language, falling back to the original if it isn't offered |
| Subtitles | Off, creator-made only, creator-made or auto-generated |
| Subtitle language | One language, saved as `<video>.<language>.srt` |
| Chapters | Embedded, or not |

Audio mode uses ad removal and audio language only. Files are hardlinked between shows only when all options match.

## New video checks

Each playlist has its own check frequency: off, immediate, hourly or daily. YouTube has no notification for playlist changes, so immediate means checking every 5 minutes. A failed check waits for its next turn. Scheduled checks skip videos you cancelled, while Check now brings them back.

Stopping tracking removes a playlist's waiting downloads and never touches files on disk. It offers two choices:

- **Keep history:** numbering and history stay, so adding the playlist again doesn't re-download anything.
- **Forget everything:** the playlist's history, numbering and show or album records are cleared, so adding it again downloads everything from the start. Files stay on disk for the person to delete. This is refused while one of its videos is downloading.

History entries record their playlist. Movie downloads from before that was added can only be matched through a finished job in the queue, so ones already cleared from the queue stay in History.

## Ad removal

YouTube's own ad breaks are never in a download. Each download picks a level for the segments creators build into videos, which yt-dlp cuts using SponsorBlock:

| Level | SponsorBlock categories |
|---|---|
| Off | None |
| Conservative | `selfpromo`, `interaction` |
| Aggressive | `selfpromo`, `interaction`, `sponsor` |

- Changing a level affects new downloads. Re-downloading from History applies a level to an existing video.
- Segments are community submitted, so very new videos may have none marked yet.

## Home

The homepage shows library statistics, refreshed every 30 seconds. Total size counts a hardlinked file once. Runtime uses each video's YouTube length, so it doesn't reflect ad cuts, and only covers videos downloaded since length was recorded.

## Download history

- History is keyed by video ID, so a video is never downloaded twice unless asked.
- A video that appears in a second playlist gets a hardlink, with a copy as the fallback.
- Re-download is an explicit action.
- A scan marks videos whose files were removed outside DTube. It runs when History opens.
- A video with a missing file can be re-downloaded or removed from DTube. Removing is refused while the file exists. Scheduled checks then leave it alone, and Check now brings it back. Its episode number isn't reused.

## Plex

Separate libraries with the Plex NFO Series and Plex NFO Movie agents avoid false matches. `uniqueid` holds the YouTube ID, which keeps watch history stable across rescans. The UI takes the server address, gets a token through Plex's PIN sign-in or a pasted value, tests the connection and lists libraries. A path mapping setting covers Plex seeing different paths than DTube.

## Settings held in the UI

Temp, TV, movie and audio folders, Plex connection and libraries, path mapping, file owner, cookies, default download options, disk space limit and yt-dlp updates.

## Image

GitHub Actions runs the tests, builds the image, starts it and checks it responds, then publishes it to `ghcr.io/untidycreativemind/dtube`. The image is published only if those steps pass.

## Defaults

- 1080p and below prefer H.264/AAC in MP4, for direct play on most Plex clients.
- Max available takes the best streams in MKV, because they are usually VP9 or AV1 with Opus.
- Request throttling is on.
- Downloads pause when free space falls below the limit.

## Security

- The first run creates the admin login. There is no default password.
- Passwords are hashed with Argon2, sessions use HttpOnly cookies and login attempts are rate limited.
- HTTPS comes from the reverse proxy. Client addresses are read from proxy headers.
- Generated secrets and cookies are stored in `/config`.

## Later

Notifications, cleanup rules, and an optional PO token helper.
