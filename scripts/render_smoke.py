"""Live smoke test of the render sandbox image: boots it, verifies the toolchain
(Python driver import, Remotion + headless browser, ffmpeg, R2 env) and terminates.

    uv run --env-file .env python scripts/render_smoke.py
"""

import asyncio
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.log import setup_logging
from app.sandbox.modal_box import ModalSandbox

CHECKS = [
    ("python driver import", "cd /opt/cueframe && python -c "
     "'from app.tutorial import remote_render; print(\"driver import ok\")'"),
    ("remotion + browser", "cd /opt/cueframe/renderer && npx remotion versions"),
    ("ffmpeg", "ffmpeg -version | head -1"),
    ("R2 env present", "env | grep -c '^R2_' "),
]


async def main() -> None:
    setup_logging("INFO")
    settings = get_settings()
    cfg = dataclasses.replace(settings, sandbox_image="render",
                              sandbox_cpu=settings.render_cpu,
                              sandbox_memory_mb=settings.render_memory_mb,
                              sandbox_max_seconds=600)  # fmt: skip
    t0 = time.monotonic()
    box = await ModalSandbox.create(cfg)
    print(f"render sandbox up in {time.monotonic() - t0:.1f}s ({box.provider_id})")
    try:
        for name, cmd in CHECKS:
            res = await box.exec(cmd, timeout=120)
            status = "ok" if res.returncode == 0 else f"FAILED rc={res.returncode}"
            print(f"  {name}: {status} :: {res.stdout.strip().splitlines()[-1][:100] if res.stdout.strip() else res.stderr.strip()[-100:]}")
            if res.returncode != 0:
                raise SystemExit(1)
    finally:
        await box.terminate()
        await ModalSandbox.release(settings, ended=(box.provider_id,))
    print("render image smoke: OK")


if __name__ == "__main__":
    asyncio.run(main())
