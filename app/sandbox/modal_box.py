"""Modal implementation: one Modal Sandbox per job running the desktop image."""

import asyncio
import base64
import os
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.log import fields, get_logger
from app.sandbox.base import Sandbox
from app.sandbox.contracts import ExecResult

log = get_logger("sandbox.modal")
BIN = Path(__file__).parent / "bin"
APT = [
    "xvfb", "x11vnc", "novnc", "websockify", "fluxbox", "xdotool", "ffmpeg", "chromium",
    "xterm", "fonts-dejavu-core", "fonts-noto-color-emoji", "x11-utils", "procps", "curl",
]  # fmt: skip
CHUNK = 6 * 1024 * 1024


def modal_module(settings: Settings) -> Any:
    """Import modal bound to the configured profile. Saved MODAL_TOKEN_* env vars
    would otherwise override the profile and point at another workspace."""
    if settings.modal_profile:
        os.environ["MODAL_PROFILE"] = settings.modal_profile
        os.environ.pop("MODAL_TOKEN_ID", None)
        os.environ.pop("MODAL_TOKEN_SECRET", None)
    import modal

    return modal


def desktop_image(modal: Any, settings: Settings) -> Any:
    return (
        modal.Image.debian_slim()
        .apt_install(*APT, *settings.sandbox_apt_extra)
        .add_local_dir(BIN, "/opt/cf", copy=True)
        .run_commands("chmod +x /opt/cf/*.sh && mkdir -p /work /tmp/cf")
    )


class ModalSandbox(Sandbox):
    provider = "modal"

    def __init__(self, sb: Any, settings: Settings):
        self._sb = sb
        self.provider_id = sb.object_id
        self.width, self.height = settings.sandbox_width, settings.sandbox_height

    @classmethod
    async def create(cls, settings: Settings, start_url: str = "about:blank") -> "ModalSandbox":
        modal = modal_module(settings)
        t0 = time.monotonic()
        app = await modal.App.lookup.aio(settings.modal_app, create_if_missing=True)
        sb = await modal.Sandbox.create.aio(
            "/opt/cf/start.sh",
            app=app,
            image=desktop_image(modal, settings),
            cpu=settings.sandbox_cpu,
            memory=settings.sandbox_memory_mb,
            timeout=settings.sandbox_max_seconds,
            idle_timeout=settings.sandbox_idle_seconds,
            encrypted_ports=[6080],
            env={
                "CF_W": str(settings.sandbox_width),
                "CF_H": str(settings.sandbox_height),
                "CF_START_URL": start_url,
            },
        )
        box = cls(sb, settings)
        tunnels = await sb.tunnels.aio()
        box.live_url = tunnels[6080].url + "/vnc.html?autoconnect=1&resize=scale&view_only=1"
        ready = await box.exec(
            "for i in $(seq 60); do DISPLAY=:99 xdotool search --onlyvisible --class chromium"
            " >/dev/null 2>&1 && exit 0; sleep 0.5; done; exit 1",
            timeout=60,
        )
        if ready.returncode != 0:
            await box.terminate()
            raise RuntimeError("Desktop did not come up (no Chromium window after 30 s)")
        log.info(
            "sandbox ready",
            extra=fields(sandbox=box.provider_id, seconds=round(time.monotonic() - t0, 1)),
        )
        return box

    async def exec(self, script: str, timeout: float = 60) -> ExecResult:
        t = time.monotonic()
        p = await self._sb.exec.aio("bash", "-c", script, timeout=int(timeout))
        out = await p.stdout.read.aio()
        err = await p.stderr.read.aio()
        rc = await p.wait.aio()
        return ExecResult(
            returncode=rc, stdout=out, stderr=err, seconds=round(time.monotonic() - t, 3)
        )

    async def read_file(self, path: str) -> bytes:
        size = int((await self._must(f"stat -c %s {path}")).stdout.strip())
        data = b""
        for i in range((size + CHUNK - 1) // CHUNK or 1):
            res = await self._must(
                f"dd if={path} bs={CHUNK} skip={i} count=1 status=none | base64 -w0", timeout=120
            )
            data += base64.b64decode(res.stdout.strip())
        return data

    async def terminate(self) -> None:
        await self._sb.terminate.aio()

    @classmethod
    async def release(cls, settings: Settings) -> None:
        """Stop the Modal app once its last sandbox is gone, so the dashboard shows
        nothing deployed. The image build cache is per workspace and survives this."""
        modal = modal_module(settings)
        try:
            app = await modal.App.lookup.aio(settings.modal_app)
        except modal.exception.NotFoundError:
            return
        async for _ in modal.Sandbox.list.aio(app_id=app.app_id):
            return  # another process still has a sandbox under this app
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "modal", "app", "stop", "-y", settings.modal_app,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )  # fmt: skip
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 60)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        log.info("modal app stop", extra=fields(rc=proc.returncode, out=out.decode()[-200:]))


async def terminate_by_id(settings: Settings, provider_id: str) -> None:
    modal = modal_module(settings)
    sb = await modal.Sandbox.from_id.aio(provider_id)
    await sb.terminate.aio()


async def reap_app(settings: Settings) -> list[str]:
    """Terminate every sandbox still running under the Cueframe Modal app."""
    modal = modal_module(settings)
    try:
        app = await modal.App.lookup.aio(settings.modal_app)
    except modal.exception.NotFoundError:
        return []
    killed = []
    async for sb in modal.Sandbox.list.aio(app_id=app.app_id):
        await sb.terminate.aio()
        killed.append(sb.object_id)
    return killed
