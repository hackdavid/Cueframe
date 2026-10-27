"""Provider-neutral sandbox interface. A sandbox is one isolated Linux desktop
(Xvfb + window manager + Chromium + terminal) that Cueframe drives remotely."""

import re
import shlex
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Collection

from app.config import Settings
from app.sandbox.contracts import Action, ActionRecord, ExecResult

OnOutput = Callable[[str], Awaitable[None]]

MARK = "__CF"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


class Sandbox(ABC):
    provider: str = "base"
    provider_id: str = ""
    live_url: str | None = None
    width: int = 1280
    height: int = 720

    @abstractmethod
    async def exec(self, script: str, timeout: float = 60) -> ExecResult: ...

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    @abstractmethod
    async def terminate(self) -> None: ...

    @classmethod
    async def release(cls, settings: Settings, ended: Collection[str] = ()) -> bool:
        """Called when no sandbox is live here: free provider-level resources.
        `ended` are provider ids this process already terminated. Returns False
        if it held off because something else may still be running."""
        return True

    async def screenshot(self) -> bytes:
        await self._must(
            f"ffmpeg -y -loglevel error -f x11grab -video_size {self.width}x{self.height} "
            "-i :99 -frames:v 1 /tmp/cf/shot.png"
        )
        return await self.read_file("/tmp/cf/shot.png")

    async def start_recording(self) -> float:
        """Start one continuous recording for the whole job; returns its t0."""
        await self._must(
            "rm -f /tmp/cf/rec.mp4; (date +%s.%N > /tmp/cf/rec_t0; exec ffmpeg -y -loglevel error "
            f"-f x11grab -framerate 30 -video_size {self.width}x{self.height} -draw_mouse 1 -i :99 "
            "-c:v libx264 -preset veryfast -g 30 -pix_fmt yuv420p "
            "-movflags +frag_keyframe+empty_moov /tmp/cf/rec.mp4) >/tmp/cf/rec.log 2>&1 &"
            " sleep 0.5; cat /tmp/cf/rec_t0"
        )
        return float((await self._must("cat /tmp/cf/rec_t0")).stdout.strip())

    async def stop_recording(self) -> bytes:
        await self._must(
            "pkill -INT -x ffmpeg; for i in $(seq 50); do pgrep -x ffmpeg >/dev/null || break;"
            " sleep 0.2; done; test -s /tmp/cf/rec.mp4",
            timeout=30,
        )
        return await self.read_file("/tmp/cf/rec.mp4")

    async def run(
        self, index: int, action: Action, on_output: OnOutput | None = None
    ) -> ActionRecord:
        if action.kind == "terminal":
            return await self._terminal(index, action, on_output)
        script = _script(action)
        res = await self.exec(
            f"export DISPLAY=:99; s=$(date +%s.%N); {{ {script} ; }} >/tmp/cf/act.log 2>&1; "
            f"rc=$?; e=$(date +%s.%N); st=$(/opt/cf/settle.sh); "
            f'echo "{MARK} $s $e $st $rc"; tail -c 400 /tmp/cf/act.log',
            timeout=max(60, action.seconds + 30),
        )
        return _record(index, action, res)

    async def _terminal(
        self, index: int, action: Action, on_output: OnOutput | None
    ) -> ActionRecord:
        import asyncio

        cmd = action.command or ""
        res = await self.exec(
            "export DISPLAY=:99; /opt/cf/term.sh >/dev/null; n0=$(cat /tmp/cf/prompt_n);"
            f" s=$(date +%s.%N); xdotool type --delay 35 -- {shlex.quote(cmd)};"
            " xdotool key Return; echo $n0 $s"
        )
        n0, started = res.stdout.split()[-2:]
        waited, last = 0.0, ""
        while waited < action.timeout:
            await asyncio.sleep(2)
            waited += 2
            poll = await self.exec(
                "cat /tmp/cf/prompt_n; tail -c 300 /tmp/cf/term.log | tr -d '\\r'"
            )
            head, _, tail = ANSI.sub("", poll.stdout).partition("\n")
            if tail.strip() and tail != last and on_output:
                await on_output(tail.strip().splitlines()[-1][-160:])
            last = tail
            if head.strip() != n0:
                break
        else:
            return ActionRecord(
                index=index, action=action, ok=False, returncode=124, started=float(started),
                ended=float(started) + waited, settled=float(started) + waited,
                output_tail=last[-400:], error=f"Timed out after {action.timeout:.0f}s",
            )  # fmt: skip
        done = await self.exec(
            "export DISPLAY=:99; e=$(cat /tmp/cf/prompt_ts); st=$(/opt/cf/settle.sh);"
            f' echo "{MARK} {started} $e $st $(cat /tmp/cf/rc)"; tail -c 400 /tmp/cf/term.log'
        )
        return _record(index, action, done)

    async def _must(self, script: str, timeout: float = 60) -> ExecResult:
        res = await self.exec(script, timeout)
        if res.returncode != 0:
            raise RuntimeError(f"sandbox command failed ({res.returncode}): {res.stderr[-300:]}")
        return res


def _move(x: int, y: int) -> str:
    return f"/opt/cf/move.sh {int(x)} {int(y)}"


def _script(a: Action) -> str:
    q = shlex.quote
    if a.kind == "open_url":
        return (
            "/opt/cf/focus.sh chromium; xdotool key ctrl+l; "
            f"xdotool type --delay 35 -- {q(a.url or '')}; xdotool key Return"
        )
    if a.kind in ("click", "double_click"):
        n = 2 if a.kind == "double_click" else 1
        return f"{_move(a.x or 0, a.y or 0)}; xdotool click --repeat {n} 1"
    if a.kind == "type":
        return f"xdotool type --delay 45 -- {q(a.text or '')}"
    if a.kind == "key":
        return f"xdotool key -- {q(a.keys or '')}"
    if a.kind == "scroll":
        button = 5 if a.dy > 0 else 4
        return f"{_move(a.x or 0, a.y or 0)}; xdotool click --repeat {abs(a.dy) or 1} {button}"
    if a.kind == "wait":
        return f"sleep {max(0.0, a.seconds)}"
    raise ValueError(f"Unsupported action: {a.kind}")


def _record(index: int, action: Action, res: ExecResult) -> ActionRecord:
    lines = res.stdout.splitlines()
    mark = next((ln for ln in lines if ln.startswith(MARK + " ")), None)
    if mark is None:
        return ActionRecord(
            index=index, action=action, ok=False, returncode=res.returncode, started=0, ended=0,
            settled=0, error=(res.stderr or res.stdout)[-300:] or "no timing marker",
        )  # fmt: skip
    _, s, e, st, rc = mark.split()
    tail = "\n".join(lines[lines.index(mark) + 1 :])[-400:]
    return ActionRecord(
        index=index, action=action, ok=rc == "0", returncode=int(rc), started=float(s),
        ended=float(e), settled=max(float(st), float(e)), output_tail=tail,
        error=None if rc == "0" else f"exit code {rc}: {tail[-200:]}",
    )  # fmt: skip
