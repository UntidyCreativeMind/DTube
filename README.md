# DTube

Self-hosted YouTube downloader for Plex, built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Paste a URL, and DTube downloads the video and files it into Plex as a TV show or a movie. All configuration happens in the web UI.

- **Playlists** download in TV mode or Movie mode, chosen each time.
- **Single videos** always download as movies.
- **Download history** stops videos downloading twice unless you ask.
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

On the machine that will run DTube:

```sh
git clone https://github.com/UntidyCreativeMind/DTube.git
cd DTube
```

Edit one line in `docker-compose.yml` so it points at the parent folder of your media:

```yaml
- /your/media/folder:/media
```

Then start it:

```sh
docker compose up -d --build
```

Open `http://<host>:3434` and create your admin login straight away. Until you do, anyone who can reach the port can create it, so do this before adding a reverse proxy.

Then go to **Settings**:

1. Choose your temporary, TV and movie folders. These must be inside the folder you mounted.
2. Set the file owner to a user that can write to those folders and that Plex can read from.
3. Connect Plex and pick your two libraries.

## Plex setup

Create two new Plex libraries instead of reusing your existing ones. Otherwise Plex may match playlists to unrelated real shows.

| Library type | Agent (under Advanced) |
|---|---|
| TV Shows | Plex NFO Series |
| Movies | Plex NFO Movie |

The NFO agents need Plex Media Server 1.43.1 or newer. Libraries using them don't work with Plex's watch state and ratings sync.

## Reverse proxy

DTube serves plain HTTP on port 3434. In Nginx Proxy Manager, turn on **Websockets Support** for the proxy host so live download progress works.

See [docs/DESIGN.md](docs/DESIGN.md) for the design.

DTube isn't affiliated with YouTube or Plex. You're responsible for how you use it.
