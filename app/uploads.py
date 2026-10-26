import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from app.config import Settings
from app.db import new_id, now

CHUNK = 1024 * 1024
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}


def safe_filename(name: str) -> str:
    name = Path(name or "file").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"
    return stem[:120]


def media_kind(content_type: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/") or ext in VIDEO_EXT:
        return "video"
    if content_type.startswith("audio/") or ext in AUDIO_EXT:
        return "audio"
    return "file"


async def save_upload(file: UploadFile, settings: Settings, user_id: str) -> dict[str, Any]:
    uid = new_id("u")
    filename = safe_filename(file.filename or "file")
    content_type = (
        file.content_type
        if file.content_type and file.content_type != "application/octet-stream"
        else mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )
    target_dir = settings.uploads_dir / uid
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with target.open("wb") as out:
        while chunk := await file.read(CHUNK):
            size += len(chunk)
            if size > limit:
                out.close()
                target.unlink(missing_ok=True)
                target_dir.rmdir()
                raise HTTPException(413, f"{filename} is larger than {settings.max_upload_mb} MB")
            out.write(chunk)
    return {
        "id": uid,
        "filename": filename,
        "content_type": content_type,
        "size": size,
        "kind": media_kind(content_type, filename),
        "url": f"/media/uploads/{uid}/{filename}",
        "user_id": user_id,
        "created_at": now(),
    }
