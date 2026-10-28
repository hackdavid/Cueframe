"""Artifact storage: Cloudflare R2 (S3 API) when configured, else the local data dir.
The DB only keeps keys/URLs; media bytes never go into SQLite."""

import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.heal import retry
from app.log import fields, get_logger

log = get_logger("storage")


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._s3: Any = None
        if settings.r2_bucket and settings.r2_endpoint and settings.r2_key_id:
            import boto3
            from botocore.config import Config

            self._s3 = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint,
                aws_access_key_id=settings.r2_key_id,
                aws_secret_access_key=settings.r2_secret,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )

    @property
    def backend(self) -> str:
        return "r2" if self._s3 else "local"

    def _key(self, key: str) -> str:
        prefix = self.settings.r2_prefix.strip("/")
        return f"{prefix}/{key}" if prefix else key

    async def put(self, key: str, data: bytes, content_type: str) -> dict[str, Any]:
        if self._s3:
            full = self._key(key)
            await retry(
                lambda: asyncio.to_thread(
                    self._s3.put_object,
                    Bucket=self.settings.r2_bucket, Key=full, Body=data, ContentType=content_type,
                ),
                f"Upload of {key}",
            )  # fmt: skip
            log.info("stored", extra=fields(backend="r2", key=full, bytes=len(data)))
            return {"backend": "r2", "key": full, "url": f"/media/r2/{full}", "bytes": len(data)}
        path = self.settings.artifacts_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(Path(path).write_bytes, data)
        url = f"/media/artifacts/{key}"
        return {"backend": "local", "key": key, "url": url, "bytes": len(data)}

    def ref(self, key: str) -> dict[str, Any]:
        """The `put` result for `key`, without uploading (to `get` a known object)."""
        return {"backend": self.backend, "key": self._key(key) if self._s3 else key}

    async def get(self, stored: dict[str, Any]) -> bytes | None:
        """Bytes of an object returned by `put`, or None if it is gone."""
        try:
            if stored["backend"] == "r2":
                if not self._s3:
                    return None
                obj = await asyncio.to_thread(
                    self._s3.get_object, Bucket=self.settings.r2_bucket, Key=stored["key"]
                )
                return await asyncio.to_thread(obj["Body"].read)
            return await asyncio.to_thread((self.settings.artifacts_dir / stored["key"]).read_bytes)
        except Exception:
            log.warning("storage miss", extra=fields(key=stored.get("key")))
            return None

    def presign(self, key: str, seconds: int = 3600) -> str:
        if not self._s3:
            raise RuntimeError("R2 is not configured")
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.settings.r2_bucket, "Key": key}, ExpiresIn=seconds
        )
