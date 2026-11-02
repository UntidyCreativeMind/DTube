from urllib.parse import urlencode

import httpx

PLEX_TV = "https://plex.tv/api/v2"
PRODUCT = "DTube"


class PinExpired(Exception):
    pass


def _pin_headers(client_id: str) -> dict:
    return {"Accept": "application/json", "X-Plex-Product": PRODUCT, "X-Plex-Client-Identifier": client_id}


def create_pin(client_id: str) -> tuple:
    response = httpx.post(f"{PLEX_TV}/pins", params={"strong": "true"}, headers=_pin_headers(client_id), timeout=15)
    response.raise_for_status()
    pin = response.json()
    return pin["id"], pin["code"]


def sign_in_url(client_id: str, code: str) -> str:
    query = urlencode({"clientID": client_id, "code": code, "context[device][product]": PRODUCT})
    return f"https://app.plex.tv/auth#?{query}"


def check_pin(client_id: str, pin_id: int, code: str):
    """Return the account token once the person has signed in, otherwise None."""
    response = httpx.get(
        f"{PLEX_TV}/pins/{pin_id}", params={"code": code}, headers=_pin_headers(client_id), timeout=15
    )
    if response.status_code == 404:
        raise PinExpired
    response.raise_for_status()
    return response.json().get("authToken")


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
