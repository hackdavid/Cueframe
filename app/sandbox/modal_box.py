"""Modal implementation: one Modal Sandbox per job running the desktop image."""

import asyncio
import base64
import os
import sys
import time
from collections.abc import Collection
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
    "dbus-x11", "at-spi2-core", "python3-pyatspi", "libatk-adaptor",
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
        .run_commands("chmod +x /opt/cf/*.sh /opt/cf/*.py && mkdir -p /work /tmp/cf")
    )


BOARD_APT = [
    "ffmpeg", "texlive-latex-base", "texlive-latex-recommended", "texlive-latex-extra",
    "texlive-fonts-recommended", "dvisvgm", "libcairo2-dev", "libpango1.0-dev", "pkg-config",
    "build-essential", "fonts-dejavu-core", "fonts-noto-core",  # Noto: Hindi etc. captions
]  # fmt: skip
BOARD_PIP = ["manim==0.22.1", "sympy==1.14.0", "pydantic==2.14.1"]


def board_image(modal: Any) -> Any:
    """Headless image for the math board: Manim, LaTeX and SymPy, no desktop. The
    board code is mounted at start, so code changes don't rebuild the image."""
    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install(*BOARD_APT)
        .pip_install(*BOARD_PIP)
        .run_commands("mkdir -p /opt/board/app /work && touch /opt/board/app/__init__.py")
        .add_local_dir(Path(__file__).parents[1] / "board", "/opt/board/app/board")
    )


class ModalSandbox(Sandbox):
    provider = "modal"

    def __init__(self, sb: Any, settings: Settings):
        self._sb = sb
        self.provider_id = sb.object_id
        self.width, self.height = settings.sandbox_width, settings.sandbox_height

    @staticmethod
    async def _create_in(modal: Any, app: Any, settings: Settings, start_url: str) -> Any:
        if settings.sandbox_image == "board":
            return await modal.Sandbox.create.aio(
                "sleep", "infinity", app=app, image=board_image(modal),
                cpu=settings.sandbox_cpu, memory=settings.sandbox_memory_mb,
                timeout=settings.sandbox_max_seconds, idle_timeout=settings.sandbox_idle_seconds,
            )  # fmt: skip
        return await modal.Sandbox.create.aio(
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
                "CF_SCALE": str(settings.sandbox_scale),
                "CF_TERM_FS": str(settings.sandbox_term_font),
            },
        )

    @classmethod
    async def create(cls, settings: Settings, start_url: str = "about:blank") -> "ModalSandbox":
        modal = modal_module(settings)
        t0 = time.monotonic()
        sb = None
        for attempt in range(2):
            # A fresh lookup per attempt: another worker may have just stopped the app.
            app = await modal.App.lookup.aio(settings.modal_app, create_if_missing=True)
            try:
                sb = await cls._create_in(modal, app, settings, start_url)
                break
            except Exception:
                if attempt:
                    raise
                log.warning("sandbox create failed; retrying with a fresh app lookup")
        box = cls(sb, settings)
        if settings.sandbox_image == "board":
            log.info(
                "board sandbox ready",
                extra=fields(sandbox=box.provider_id, seconds=round(time.monotonic() - t0, 1)),
            )
            return box
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
        # Bytes mode: `tail -c` can cut a multi-byte character (e.g. tree's box drawing).
        p = await self._sb.exec.aio("bash", "-c", script, timeout=int(timeout), text=False)
        out = (await p.stdout.read.aio()).decode(errors="replace")
        err = (await p.stderr.read.aio()).decode(errors="replace")
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
    async def release(cls, settings: Settings, ended: Collection[str] = ()) -> bool:
        """Stop the Modal app once its last sandbox is gone, so the dashboard shows
        nothing deployed. The image build cache is per workspace and survives this."""
        modal = modal_module(settings)
        try:
            app = await modal.App.lookup.aio(settings.modal_app)
        except modal.exception.NotFoundError:
            return True
        # Modal is the shared source of truth across workers. Sandboxes we just
        # terminated can still report running for a few seconds; ignore those.
        async for sb in modal.Sandbox.list.aio(app_id=app.app_id):
            if sb.object_id not in ended and await sb.poll.aio() is None:
                return False  # another worker's, or one Modal hasn't marked ended yet
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
        return True


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
