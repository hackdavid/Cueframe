"""Modal implementation: one Modal Sandbox per job running the desktop image."""

import asyncio
import base64
import os
import subprocess
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


# Chrome headless shell's shared libraries (Remotion renders with it), plus Node and ffmpeg.
RENDER_APT = [
    "ffmpeg", "nodejs", "npm", "ca-certificates",
    "libnss3", "libdbus-1-3", "libatk1.0-0", "libatk-bridge2.0-0", "libcups2", "libdrm2",
    "libxcomposite1", "libxdamage1", "libxfixes3", "libxrandr2", "libgbm1",
    "libxkbcommon0", "libpango-1.0-0", "libcairo2", "libasound2",
    "fonts-dejavu-core", "fonts-noto-core", "fonts-noto-color-emoji",
]  # fmt: skip
RENDER_PIP = ["pydantic==2.14.1", "boto3>=1.43", "httpx>=0.28"]
REPO = Path(__file__).resolve().parents[2]


def _no_build_junk(p: Path) -> bool:
    """Exclude dirs that would bloat or poison the image (local installs, caches)."""
    return any(part in ("node_modules", "__pycache__", ".git") for part in p.parts)


def render_image(modal: Any) -> Any:
    """Headless tutorial renderer: the Remotion project baked in, Node deps installed
    and the headless browser downloaded at build time. The app source is MOUNTED at
    sandbox start (like the board image), so app code changes never rebuild the image."""
    return (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install(*RENDER_APT)
        .pip_install(*RENDER_PIP)
        .add_local_dir(REPO / "app", "/opt/cueframe/app", ignore=_no_build_junk)
        .add_local_dir(REPO / "renderer", "/opt/cueframe/renderer", copy=True,
                       ignore=_no_build_junk)
        .run_commands("mkdir -p /work /opt/cueframe/app")
        .run_commands("cd /opt/cueframe/renderer && npm ci && npx remotion browser ensure")
    )


def _render_env(settings: Settings) -> dict[str, str]:
    """R2 credentials for the render sandbox: it pulls assets and pushes the segment
    cache straight to the bucket, so nothing big flows through the server."""
    pairs = {
        "R2_ENDPOINT_URL": settings.r2_endpoint,
        "R2_ACCESS_KEY_ID": settings.r2_key_id,
        "R2_SECRET_ACCESS_KEY": settings.r2_secret,
        "R2_BUCKET_NAME": settings.r2_bucket,
        "CUEFRAME_R2_PREFIX": settings.r2_prefix,
    }
    return {k: v for k, v in pairs.items() if v}


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
        if settings.sandbox_image == "render":
            return await modal.Sandbox.create.aio(
                "sleep", "infinity", app=app, image=render_image(modal),
                cpu=settings.sandbox_cpu, memory=settings.sandbox_memory_mb,
                timeout=settings.sandbox_max_seconds, idle_timeout=settings.sandbox_idle_seconds,
                env=_render_env(settings),
            )  # fmt: skip
        return await modal.Sandbox.create.aio(
            "/opt/cf/start.sh",
            app=app,
            image=desktop_image(modal, settings),
            cpu=settings.sandbox_cpu,
            memory=settings.sandbox_memory_mb,
            timeout=settings.sandbox_max_seconds,
            idle_timeout=settings.sandbox_idle_seconds,
            encrypted_ports=[6080] if settings.live_view else [],
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
        if settings.sandbox_image != "desktop":
            log.info(
                f"{settings.sandbox_image} sandbox ready",
                extra=fields(sandbox=box.provider_id, seconds=round(time.monotonic() - t0, 1)),
            )
            return box
        if not settings.live_view:
            log.info(
                "sandbox ready (live view off)",
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
        self._note_active()
        # Bytes mode: `tail -c` can cut a multi-byte character (e.g. tree's box drawing).
        p = await self._sb.exec.aio("bash", "-c", script, timeout=int(timeout), text=False)
        out = (await p.stdout.read.aio()).decode(errors="replace")
        err = (await p.stderr.read.aio()).decode(errors="replace")
        rc = await p.wait.aio()
        return ExecResult(
            returncode=rc, stdout=out, stderr=err, seconds=round(time.monotonic() - t, 3)
        )

    async def beat(self) -> None:
        """Keepalive without touching last_active: Modal's own idle timer sees the
        exec, the manager's idle watchdog does not."""
        p = await self._sb.exec.aio("true", timeout=15, text=False)
        await p.wait.aio()

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
        # subprocess in a thread: asyncio subprocesses raise NotImplementedError on
        # uvicorn's Windows SelectorEventLoop.
        def _stop() -> "subprocess.CompletedProcess[bytes]":
            return subprocess.run(
                [sys.executable, "-m", "modal", "app", "stop", "-y", settings.modal_app],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )  # fmt: skip

        done = await asyncio.to_thread(_stop)
        out = done.stdout or b""
        log.info("modal app stop", extra=fields(rc=done.returncode, out=out.decode()[-200:]))
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
