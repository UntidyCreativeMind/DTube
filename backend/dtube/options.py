"""Per-download choices, stored as JSON on playlists, jobs and history entries."""
import json

LANGUAGES = ("en", "es", "fr", "de", "it", "pt", "ja", "ko", "zh", "hi", "ar", "ru")

DEFAULTS = {
    "quality": "1080",
    "ad_removal": "off",
    "subtitles": "off",
    "subtitle_lang": "en",
    "audio_lang": "original",
    "chapters": True,
}

ALLOWED = {
    "quality": ("max", "1080", "720", "480", "360"),
    "ad_removal": ("off", "conservative", "aggressive"),
    "subtitles": ("off", "manual", "auto"),
    "subtitle_lang": LANGUAGES,
    "audio_lang": ("original", *LANGUAGES),
    "chapters": (True, False),
}


def parse(text: str) -> dict:
    stored = json.loads(text or "{}")
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def dump(options: dict) -> str:
    return json.dumps(options, sort_keys=True)


def merged(changes: dict, base: dict) -> dict:
    """Apply `changes` to `base`, raising ValueError for anything not allowed."""
    for key, value in changes.items():
        if key not in ALLOWED:
            raise ValueError(f"Unknown option: {key}")
        if value not in ALLOWED[key]:
            raise ValueError(f"{key} must be one of: {', '.join(map(str, ALLOWED[key]))}")
    return {**base, **changes}
