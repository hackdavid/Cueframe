"""Run a subprocess from async code on any event loop.

`asyncio.create_subprocess_exec` raises NotImplementedError on uvicorn's Windows
SelectorEventLoop, so every ffmpeg/ffprobe/remotion call goes through here: a
blocking subprocess.run in a worker thread behaves identically everywhere.
"""

import asyncio
import subprocess
from pathlib import Path


async def out(*cmd: str, cwd: Path | None = None, timeout: float | None = None) -> str:
    """Combined stdout+stderr, decoded; return code is the caller's business."""
    p = await asyncio.to_thread(
        subprocess.run, [*cmd], cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout,
    )  # fmt: skip
    return (p.stdout or b"").decode(errors="replace")


async def checked(*cmd: str, cwd: Path | None = None, timeout: float | None = None) -> None:
    """Like `out` but raises on a non-zero exit, with the output tail in the message."""
    p = await asyncio.to_thread(
        subprocess.run, [*cmd], cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout,
    )  # fmt: skip
    if p.returncode:
        tail = (p.stdout or b"").decode(errors="replace")[-800:]
        raise RuntimeError(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''} failed: {tail}")


async def raw(
    *cmd: str, cwd: Path | None = None, timeout: float | None = None
) -> tuple[int, bytes]:
    """(return code, stdout bytes) with stderr discarded — for binary output."""
    p = await asyncio.to_thread(
        subprocess.run, [*cmd], cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=timeout,
    )  # fmt: skip
    return p.returncode, p.stdout or b""
