import httpx


def _get(url: str, token: str, path: str, params=None):
    response = httpx.get(
        url.rstrip("/") + path,
        params=params,
        headers={"X-Plex-Token": token, "Accept": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    return response


def list_libraries(url: str, token: str) -> list:
    data = _get(url, token, "/library/sections").json()
    return [
        {"id": str(d["key"]), "title": d["title"], "type": d["type"], "agent": d.get("agent", "")}
        for d in data["MediaContainer"].get("Directory", [])
    ]


def map_path(path: str, settings: dict) -> str:
    src, dst = settings["plex_path_from"], settings["plex_path_to"]
    if src and dst and (path == src or path.startswith(src.rstrip("/") + "/")):
        return dst.rstrip("/") + path[len(src.rstrip("/")):]
    return path


def refresh(settings: dict, section: str, path: str):
    _get(
        settings["plex_url"],
        settings["plex_token"],
        f"/library/sections/{section}/refresh",
        {"path": map_path(path, settings)},
    )
