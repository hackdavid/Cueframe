"""Runs inside the render sandbox: `python -m app.tutorial.remote_render /work`.

Reads /work/manifest.json (tutorial graph, R2 asset keys, force list), pulls the
assets straight from the bucket, renders with the same code as the local runner,
checks the finished video and pushes video/captions/thumbnail back to the bucket.
The server polls /work/progress.json while this runs and reads /work/result.json
at the end (or /work/failed.txt on a crash).
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from app.log import fields, get_logger
from app.tutorial import qa
from app.tutorial.contracts import Tutorial
from app.tutorial.render import render

log = get_logger("render.sandbox")


def _s3() -> tuple[object, str]:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    ), os.environ["R2_BUCKET_NAME"]


async def _main(work: Path) -> None:
    t0 = time.monotonic()
    manifest = json.loads((work / "manifest.json").read_text())
    tid = manifest["tid"]
    tut = Tutorial.model_validate(manifest["tutorial"])
    s3, bucket = _s3()
    cache = f"{os.environ.get('CUEFRAME_R2_PREFIX', 'cueframe').strip('/')}/tutorials/{tid}/"

    pub = work / "public" / tid
    (pub / "audio").mkdir(parents=True, exist_ok=True)
    total = 0
    for key, rel in manifest["assets"]:
        dest = work / "public" / tid / rel
        data = await asyncio.to_thread(
            lambda key=key: s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        total += len(data)
    log.info(
        "assets staged",
        extra=fields(tutorial=tid, files=len(manifest["assets"]), mb=round(total / 1e6, 1)),
    )

    async def fetch(name: str) -> bytes | None:
        # The renderer bundle is cached globally (assets/), segments per tutorial.
        key = f"{os.environ.get('CUEFRAME_R2_PREFIX', 'cueframe').strip('/')}/assets/{name}" \
            if name.startswith("bundle-") else cache + name  # fmt: skip
        try:
            return await asyncio.to_thread(
                lambda: s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            )
        except Exception:
            return None

    async def save(name: str, path: Path) -> None:
        if name.startswith("bundle-"):
            key = f"{os.environ.get('CUEFRAME_R2_PREFIX', 'cueframe').strip('/')}/assets/{name}"
            await asyncio.to_thread(s3.put_object, Bucket=bucket, Key=key,
                                    Body=path.read_bytes(), ContentType="application/gzip")  # fmt: skip
            return
        await asyncio.to_thread(
            s3.put_object, Bucket=bucket, Key=cache + name, Body=path.read_bytes(),
            ContentType="video/mp4",
        )

    def progress(done: int, total: int, state: str | None = None) -> None:
        (work / "progress.json").write_text(
            json.dumps({"done": done, "total": total, "state": state})
        )

    progress(0, 0)
    res = await render(tut, work, fetch, save, progress, force=set(manifest["force"]))
    issues = await qa.final(tut, res["video"], res["captions"], res["stats"]["duration"])
    out = {}
    for name, path in (("video", res["video"]), ("captions", res["captions"]),
                       ("thumbnail", res["thumbnail"])):  # fmt: skip
        key = f"{cache}render-out/{path.name}"
        await asyncio.to_thread(s3.put_object, Bucket=bucket, Key=key,
                                Body=path.read_bytes())  # fmt: skip
        out[name] = key
    result = {"stats": res["stats"], "qa_issues": issues, "out": out}
    (work / "result.json").write_text(json.dumps(result))
    log.info(
        "render done",
        extra=fields(tutorial=tid, seconds=round(time.monotonic() - t0, 1),
                     qa_issues=len(issues)),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    work = Path(sys.argv[1] if len(sys.argv) > 1 else "/work")
    try:
        asyncio.run(_main(work))
    except Exception:
        (work / "failed.txt").write_text(traceback.format_exc()[-3000:])
        raise


if __name__ == "__main__":
    main()
