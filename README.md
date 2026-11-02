# DTube

Self-hosted YouTube downloader for Plex, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Paste a URL, and DTube downloads the video and files it into Plex as a TV show or a movie. All configuration happens in the web UI.

- **Playlists** download as a TV show, movies or audio only, chosen each time.
- **Single videos** download as a movie or audio only.
- **Download history** stops videos downloading twice unless you ask.
- **Quality** is chosen for each download: max available, 1080p, 720p, 480p or 360p.
- **Subtitles** can be creator-made only or include auto-generated ones, in a language you choose.
- **Audio language** can stay as the original or prefer a language, for videos with dubbed tracks.
- **Chapters** from YouTube are embedded in the video file.
- **Episode order** follows the playlist or reverses it, for playlists that list the newest video first.
- **New video checks** are set per playlist: off, immediate (every 5 minutes), hourly or daily.
- **Ad removal** cuts creator-inserted ads with [SponsorBlock](https://sponsor.ajay.app/), at a level you pick for each download.
- **Cookies** are optional. DTube works without them.

## How TV mode maps to Plex

| YouTube | Plex |
|---|---|
| Playlist | TV show |
| Channel | Season |
| Video title | Episode title |

Episode numbers are permanent. Reordering a playlist or deleting a video never renames anything.

## Install

GitHub builds the image on every push to `main`, and it runs on 64-bit x86 machines. On the machine that will run DTube, put [`docker-compose.yml`](docker-compose.yml) in a folder and edit one line so it points at the parent folder of your media:

```yaml
- /your/media/folder:/media
```

Then start it:

```sh
docker compose up -d
```

To update later:

```sh
docker compose pull
docker compose up -d
```

Open `http://<host>:3434` and create your admin login straight away. Until you do, anyone who can reach the port can create it, so do this before adding a reverse proxy.

Then go to **Settings**:

1. Choose your temporary, TV, movie and audio folders. These must be inside the folder you mounted. You only need the ones you'll use.
2. Set the file owner to a user that can write to those folders and that Plex can read from.
3. Enter your Plex address, click **Sign in with Plex** or paste a token, then pick your libraries.

## Plex setup

Create new Plex libraries instead of reusing your existing ones. Otherwise Plex may match playlists to unrelated real shows.

| Library type | Agent (under Advanced) |
|---|---|
| TV Shows | Plex NFO Series |
| Movies | Plex NFO Movie |
| Music (only for audio) | Plex's music agent, using the tags in each file |

The NFO agents need Plex Media Server 1.43.1 or newer. Libraries using them don't work with Plex's watch state and ratings sync.

To show channel names as season names, turn on **Use Season Titles** in the TV library's Advanced settings, then refresh the library's metadata. A season Plex already created may need its show refreshed or rematched.

Audio is saved as M4A in `Playlist/01 - Title.m4a`, tagged with the playlist as the album and the channel as the artist. Single videos go in an album called `Singles`.

## Reverse proxy

DTube serves plain HTTP on port 3434. In Nginx Proxy Manager, turn on **Websockets Support** for the proxy host so live download progress works.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

DTube isn't affiliated with YouTube or Plex. You're responsible for how you use it.
