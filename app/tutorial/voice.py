"""ElevenLabs client: narration with word timings (for captions) and sound effects."""

import base64
import os
import re
from typing import Any

import httpx

from app.log import fields, get_logger
from app.tutorial.contracts import Word

log = get_logger("voice")
API = "https://api.elevenlabs.io/v1"


def _key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    return key


def words_from_alignment(al: dict[str, Any]) -> list[Word]:
    chars, starts, ends = (
        al["characters"],
        al["character_start_times_seconds"],
        al["character_end_times_seconds"],
    )
    words: list[Word] = []
    for m in re.finditer(r"\S+", "".join(chars)):
        words.append(Word(text=m.group(0), t=starts[m.start()], end=ends[m.end() - 1]))
    return words


async def speak(
    text: str, voice_id: str | None = None, seed: int | None = None
) -> tuple[bytes, list[Word]]:
    """MP3 bytes plus per-word timings."""
    vid = voice_id or os.environ["ELEVENLABS_VOICE_ID"]
    body: dict[str, Any] = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3},
    }
    if seed is not None:
        body["seed"] = seed
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/text-to-speech/{vid}/with-timestamps?output_format=mp3_44100_128",
            headers={"xi-api-key": _key()},
            json=body,
        )
    r.raise_for_status()
    data = r.json()
    words = words_from_alignment(data["alignment"])
    log.info("tts", extra=fields(chars=len(text), words=len(words)))
    return base64.b64decode(data["audio_base64"]), words


async def sound(prompt: str, seconds: float, influence: float = 0.5) -> bytes:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/sound-generation",
            headers={"xi-api-key": _key()},
            json={"text": prompt, "duration_seconds": seconds, "prompt_influence": influence},
        )
    r.raise_for_status()
    log.info("sfx", extra=fields(prompt=prompt[:40], seconds=seconds))
    return r.content
