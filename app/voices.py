"""The user's ElevenLabs voice library, normalized for the in-chat voice picker and
cached for an hour: the library changes rarely and previews come from ElevenLabs' own
URLs, so browsing voices costs no characters."""

import os
import time
from typing import Any

import httpx

from app.log import fields, get_logger

log = get_logger("voices")
TTL_SECONDS = 3600
_cache: tuple[float, list[dict[str, Any]]] | None = None


def _normalize(v: dict[str, Any]) -> dict[str, Any]:
    labels = v.get("labels") or {}
    return {
        "id": v["voice_id"],
        "name": v.get("name", ""),
        "gender": labels.get("gender", ""),
        "accent": labels.get("accent", ""),
        "age": labels.get("age", ""),
        "language": labels.get("language", "en"),
        "use_case": labels.get("use_case", ""),
        "description": v.get("description") or labels.get("description", ""),
        "preview_url": v.get("preview_url", ""),
        "category": v.get("category", ""),
    }


async def list_voices() -> list[dict[str, Any]]:
    """Every voice in the account's library, fresh at most once an hour."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < TTL_SECONDS:
        return _cache[1]
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key})
    r.raise_for_status()
    voices = [_normalize(v) for v in r.json().get("voices", [])]
    _cache = (time.monotonic(), voices)
    log.info("voice library refreshed", extra=fields(count=len(voices)))
    return voices


_shared_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _normalize_shared(v: dict[str, Any]) -> dict[str, Any]:
    """A community voice from the shared library. `id` is its shared voice_id; it must
    be added to the account (`add_shared`) before it can narrate."""
    return {
        "id": v["voice_id"],
        "public_user_id": v.get("public_user_id", ""),
        "name": v.get("name", ""),
        "gender": v.get("gender", ""),
        "accent": v.get("accent", ""),
        "age": v.get("age", ""),
        "language": v.get("language", ""),
        "use_case": v.get("use_case", ""),
        "description": v.get("description", ""),
        "preview_url": v.get("preview_url", ""),
        "category": "shared",
        "uses": v.get("cloned_by_count", 0),
    }


async def search_shared(language: str, limit: int = 24) -> list[dict[str, Any]]:
    """Community voices for a language (ISO 639-1, e.g. 'hi'), most-used first.
    Shared voice ids work for TTS directly — no need to add them to the library."""
    hit = _shared_cache.get(language)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            "https://api.elevenlabs.io/v1/shared-voices",
            params={"language": language, "page_size": limit, "sort": "cloned_by_count"},
            headers={"xi-api-key": key},
        )
    r.raise_for_status()
    voices = [_normalize_shared(v) for v in r.json().get("voices", [])]
    _shared_cache[language] = (time.monotonic(), voices)
    log.info("shared voices fetched", extra=fields(language=language, count=len(voices)))
    return voices
